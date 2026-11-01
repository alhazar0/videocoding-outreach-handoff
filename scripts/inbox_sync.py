#!/usr/bin/env python3
"""Background Microsoft 365 inbox sync for the outreach dashboard."""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token
    from mail_delivery import is_sender_restriction, record_restriction, RESTRICTION_SUMMARY
except ImportError:
    from scripts.outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token
    from scripts.mail_delivery import is_sender_restriction, record_restriction, RESTRICTION_SUMMARY


CAMPAIGN_START = datetime(2026, 7, 26, tzinfo=timezone.utc)
DEFAULT_LOCK = Path(__file__).resolve().parents[1] / ".wrangler" / "inbox-sync.lock"
SELF_EMAIL = "team@videocoding.ai"

try:
    from reply_safety import current_message
    from reviewed_contact_hold import active_hold
except ImportError:
    from scripts.reply_safety import current_message
    from scripts.reviewed_contact_hold import active_hold

NEGATIVE_PATTERNS = [
    r"\bnot interested\b",
    r"\bno thanks\b",
    r"\bnot a fit\b",
    r"\bremove me\b",
    r"\bunsubscribe\b",
    r"\bdo not contact\b",
    r"(?:^|[.!?\n]\s*|\bplease\s+)never\s+(?:contact|email|e-mail)\s+(?:me|us)\s+again\b",
]
INTEREST_PATTERNS = [
    r"\binterested\b",
    r"\bcollaborat(?:e|ion)\b",
    r"\bsponsor(?:ed|ship)?\b",
    r"\bcampaign brief\b",
    r"\bcreator access\b",
    r"\bwould love\b",
    r"\bsounds good\b",
    r"\bwant to try\b",
    r"\bmy rate\b",
    r"\brate card\b",
    r"\bprice\b",
    r"\bhappy to (?:help|discuss|talk)\b",
    r"\$\s?\d",
]
AUTOMATED_PATTERNS = [
    r"\bautomatic reply\b",
    r"\bautomated response\b",
    r"\bout of office\b",
    r"we(?:'|’)ve received your message",
    r"received your message and will get back",
    r"we usually respond in",
    r"\bguest posts?\b",
    r"\blink insertions?\b",
    r"congradulations, you are on the list",
]
CAMPAIGN_SUBJECT_PATTERNS = [
    r"\bvideocoding\b.*\b(?:collaboration|partnership|creator|trial)\b",
    r"paid partnership",
    r"paid promotion",
    r"paid collaboration",
    r"still interested in videocoding",
    r"creator partnership",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--backfill-sent-context", action="store_true")
    return parser.parse_args()


def iso_utc(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_inbox_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS inbox_events (
          id TEXT PRIMARY KEY,
          internet_message_id TEXT,
          conversation_id TEXT,
          received_at TEXT NOT NULL,
          sender_name TEXT NOT NULL DEFAULT '',
          sender_email TEXT NOT NULL DEFAULT '',
          subject TEXT NOT NULL DEFAULT '',
          body_preview TEXT NOT NULL DEFAULT '',
          body_text TEXT,
          event_type TEXT NOT NULL,
          contact_id INTEGER,
          matched_email TEXT,
          web_link TEXT,
          processed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS inbox_events_received_idx
          ON inbox_events(received_at DESC);
        CREATE INDEX IF NOT EXISTS inbox_events_contact_idx
          ON inbox_events(contact_id, event_type);
        CREATE INDEX IF NOT EXISTS inbox_events_contact_received_idx
          ON inbox_events(contact_id,received_at DESC,id DESC);
        CREATE INDEX IF NOT EXISTS inbox_events_conversation_received_idx
          ON inbox_events(conversation_id,received_at DESC,id DESC);
        CREATE TABLE IF NOT EXISTS outreach_conversations (
          conversation_id TEXT PRIMARY KEY,
          contact_id INTEGER NOT NULL,
          recipient_email TEXT NOT NULL,
          subject TEXT NOT NULL DEFAULT '',
          sent_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS outreach_conversations_contact_idx
          ON outreach_conversations(contact_id);
        CREATE TABLE IF NOT EXISTS outreach_sent_messages (
          id TEXT PRIMARY KEY,
          conversation_id TEXT,
          contact_id INTEGER NOT NULL,
          recipient_email TEXT NOT NULL,
          subject TEXT NOT NULL DEFAULT '',
          body_text TEXT NOT NULL DEFAULT '',
          sent_at TEXT NOT NULL,
          synced_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS outreach_sent_messages_contact_idx
          ON outreach_sent_messages(contact_id,sent_at DESC);
        CREATE INDEX IF NOT EXISTS outreach_sent_messages_conversation_sent_idx
          ON outreach_sent_messages(conversation_id,sent_at DESC);
        CREATE INDEX IF NOT EXISTS contacts_email_normalized_idx
          ON contacts(lower(email));
        CREATE INDEX IF NOT EXISTS contacts_channel_name_normalized_idx
          ON contacts(lower(trim(channel_name)));
        """
    )
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(inbox_events)")}
    if "body_text" not in columns:
        db.execute("ALTER TABLE inbox_events ADD COLUMN body_text TEXT")
    db.commit()


def get_setting(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM outreach_settings WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else None


def set_setting(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """INSERT INTO outreach_settings(key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')""",
        (key, value),
    )


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def overlap_since(value: str | None) -> str:
    previous = parse_timestamp(value)
    start = max(CAMPAIGN_START, (previous - timedelta(minutes=10)) if previous else CAMPAIGN_START)
    return iso_utc(start)


def graph_json(access_token: str, url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.body-content-type="text"',
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"Microsoft Graph HTTP {exc.code}: {detail}") from exc


def fetch_messages(
    access_token: str,
    folder: str,
    time_field: str,
    since: str,
    select: list[str],
    *,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "$top": "100",
            "$orderby": f"{time_field} desc",
            "$filter": f"{time_field} ge {since}",
            "$select": ",".join(select),
        }
    )
    url: str | None = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages?{query}"
    messages: list[dict[str, Any]] = []
    pages = 0
    while url and pages < max_pages:
        payload = graph_json(access_token, url)
        messages.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")
        pages += 1
    if url:
        # Never advance the sync checkpoint past a partially fetched mailbox.
        raise RuntimeError(f"Incomplete {folder} pagination after {pages} pages; sync checkpoint retained")
    return messages


def fetch_inbox(access_token: str, since: str) -> list[dict[str, Any]]:
    return fetch_messages(
        access_token,
        "inbox",
        "receivedDateTime",
        since,
        ["id", "internetMessageId", "conversationId", "receivedDateTime", "subject", "bodyPreview", "body", "from", "webLink"],
    )


def fetch_sent(access_token: str, since: str, *, max_pages: int = 10) -> list[dict[str, Any]]:
    return fetch_messages(
        access_token,
        "sentitems",
        "sentDateTime",
        since,
        ["id", "internetMessageId", "conversationId", "sentDateTime", "subject", "bodyPreview", "body", "toRecipients", "webLink"],
        max_pages=max_pages,
    )


def sender(message: dict[str, Any]) -> tuple[str, str]:
    address = (message.get("from") or {}).get("emailAddress") or {}
    return str(address.get("name") or "").strip(), str(address.get("address") or "").strip().lower()


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|tr|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", text).strip()


def message_text(message: dict[str, Any]) -> str:
    body = (message.get("body") or {}).get("content") or message.get("bodyPreview") or ""
    if str((message.get("body") or {}).get("contentType") or "").lower() == "text":
        # Graph's requested plain-text body may contain <email@example.com>.
        # Treating that as HTML discards forwarded sender/recipient evidence.
        text = str(body).replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"[ \t]+", " ", text).strip()
    return clean_text(str(body))


def newest_reply(text: str) -> str:
    markers = [
        r"(?:^|\n)\s*On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[\s\S]{0,260}?\bwrote:\s*",
        r"(?:^|\n)\s*From:\s*[^\n]{1,260}",
        r"(?:^|\n)\s*-{2,}\s*Original Message\s*-{2,}",
    ]
    indexes = [match.start() for pattern in markers if (match := re.search(pattern, text, flags=re.I)) and match.start() > 0]
    return current_message(text[: min(indexes) if indexes else len(text)].strip())


def compact_preview(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    value = re.sub(r"^(?:hi|hello|hey)\s+[^,]{0,60},?\s*", "", value, flags=re.I)
    return value[:700]


def pattern_match(patterns: list[str], value: str) -> bool:
    return any(re.search(pattern, value, flags=re.I) for pattern in patterns)


def is_campaign_subject(subject: str) -> bool:
    return pattern_match(CAMPAIGN_SUBJECT_PATTERNS, subject)


def is_bounce(message: dict[str, Any], text: str) -> bool:
    name, address = sender(message)
    subject = str(message.get("subject") or "")
    # Localized NDRs can use English wording outside the legacy phrases.
    # Require a system sender and SMTP diagnostic, not a creator's quote.
    localized_ndr = (
        re.match(r"^(?:postmaster|mailer-daemon)@", address, re.I)
        and re.match(r"^\s*未送达\s*[:：]", subject)
        and re.search(r"\b[45]\d{2}[ -]+[45]\.\d{1,3}\.\d{1,3}\b", text)
    )
    return bool(
        localized_ndr
        or re.search(r"undeliverable|delivery status notification|delivery has failed", subject, flags=re.I)
        or re.search(r"couldn(?:'|’)t be delivered|address not found|delivery failed|recipient address rejected|rejected your message|message was rejected", text, flags=re.I)
        or (re.search(r"microsoft outlook", name, flags=re.I) and re.search(r"delivered|delivery", text, flags=re.I))
    )


def failed_address(text: str) -> str | None:
    # Prefer the reported envelope recipient over addresses in explanatory
    # prose. Preserve local-part punctuation so suppression hits the exact
    # attempted address, including an apostrophe imported from a public source.
    addresses = re.findall(
        r"^[ \t]*(?:Recipient Address|(?:Final|Original)-Recipient)[ \t]*:[ \t]*"
        r"(?:rfc822[ \t]*;[ \t]*)?<?([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,})>?[ \t\r]*$",
        text, flags=re.I | re.M,
    )
    addresses += re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, flags=re.I)
    for address in addresses:
        email = address.lower().rstrip(".,;:")
        if email == SELF_EMAIL or "microsoft" in email or email.endswith(".onmicrosoft.com") or email.endswith("@videocoding.ai"):
            continue
        return email
    return None


def contact_by_email(db: sqlite3.Connection, email: str | None) -> sqlite3.Row | None:
    if not email:
        return None
    return db.execute(
        "SELECT id,email,channel_name,channel_url,first_sent_at FROM contacts WHERE lower(email)=? LIMIT 1",
        (email.lower(),),
    ).fetchone()


def contact_by_subject(db: sqlite3.Connection, subject: str) -> sqlite3.Row | None:
    match = re.search(r"paid (?:partnership|promotion):\s*(.+?)\s*[×x]\s*videocoding(?:\s+ai)?", subject, flags=re.I)
    if not match:
        return None
    channel = re.sub(r"^(?:re|fw|fwd):\s*", "", match.group(1), flags=re.I).strip()
    return db.execute(
        "SELECT id,email,channel_name,channel_url,first_sent_at FROM contacts WHERE lower(trim(channel_name))=lower(?) LIMIT 1",
        (channel,),
    ).fetchone()


def contact_by_conversation(db: sqlite3.Connection, conversation_id: str | None) -> sqlite3.Row | None:
    if not conversation_id:
        return None
    return db.execute(
        """SELECT c.id,c.email,c.channel_name,c.channel_url,c.first_sent_at
           FROM outreach_conversations oc JOIN contacts c ON c.id=oc.contact_id
           WHERE oc.conversation_id=? LIMIT 1""",
        (conversation_id,),
    ).fetchone()


def reviewed_sent_contact(db: sqlite3.Connection, message: dict[str, Any], recipient: str):
    """Recover a reviewed reply with a creator-supplied, non-campaign subject.

    Subject alone is not authorization to import arbitrary mailbox traffic.
    Require a unique accepted reply with the exact thread, recipient, body
    and nearby submission time, including a representative's thread identity.
    """
    required = {'approved_reply_batch_log', 'outreach_work_items', 'inbox_events'}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.issubset(tables) or not message.get('conversationId'):
        return None
    sent_at = str(message.get('sentDateTime') or '')
    body = ' '.join(newest_reply(message_text(message)).split())
    if not body:
        return None
    candidates = db.execute("""SELECT c.id,c.email,c.channel_name,c.channel_url,c.first_sent_at,w.draft_body
        FROM approved_reply_batch_log a
        JOIN outreach_work_items w ON w.id=a.work_item_id
        JOIN inbox_events e ON e.id=w.inbox_event_id AND e.contact_id=w.contact_id
        JOIN contacts c ON c.id=w.contact_id
        WHERE a.status='sent' AND e.conversation_id=? AND lower(e.sender_email)=?
          AND abs(julianday(a.sent_at)-julianday(?))*86400<=180""",
        (message['conversationId'], recipient, sent_at)).fetchall()
    matches = [r for r in candidates if ' '.join(str(r['draft_body'] or '').split()) == body]
    return matches[0] if len(matches) == 1 else None


def sync_sent_message(db: sqlite3.Connection, message: dict[str, Any]) -> bool:
    subject = str(message.get("subject") or "")
    sent_at = str(message.get("sentDateTime") or "")
    conversation_id = str(message.get("conversationId") or "")
    recipients = []
    for recipient in message.get("toRecipients") or []:
        address = (recipient.get("emailAddress") or {}).get("address")
        if address:
            recipients.append(str(address).strip().lower())
    if not sent_at or len(recipients) != 1:
        return False
    if not is_campaign_subject(subject):
        contact = reviewed_sent_contact(db, message, recipients[0])
        if not contact:
            return False
    else:
        contact = contact_by_email(db, recipients[0])
        if not contact:
            # The original invitation may still map this thread to the creator's
            # primary address. An accepted reply to their representative supplies
            # stronger, message-specific evidence than that historical mapping.
            contact = reviewed_sent_contact(db, message, recipients[0])
    if not contact and conversation_id:
        # A verified representative can reply from a different address than
        # the creator's primary contact. Keep this mapping conversation-local:
        # the same agency mailbox may represent several unrelated creators.
        contact = db.execute(
            """SELECT c.id,c.email,c.channel_name,c.channel_url,c.first_sent_at
               FROM outreach_conversations oc JOIN contacts c ON c.id=oc.contact_id
               WHERE oc.conversation_id=? AND lower(oc.recipient_email)=?""",
            (conversation_id, recipients[0]),
        ).fetchone()
    if not contact:
        return False
    message_id = str(message.get("id") or "")
    sent_body = newest_reply(message_text(message))[:12000]
    if message_id:
        db.execute(
            """INSERT INTO outreach_sent_messages(
                 id,conversation_id,contact_id,recipient_email,subject,body_text,sent_at,synced_at
               ) VALUES(?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                 conversation_id=excluded.conversation_id,contact_id=excluded.contact_id,
                 recipient_email=excluded.recipient_email,subject=excluded.subject,
                 body_text=excluded.body_text,sent_at=excluded.sent_at,synced_at=datetime('now')""",
            (message_id, conversation_id or None, int(contact["id"]), recipients[0], subject, sent_body, sent_at),
        )
    if conversation_id:
        db.execute(
            """INSERT INTO outreach_conversations(conversation_id,contact_id,recipient_email,subject,sent_at,updated_at)
               VALUES(?,?,?,?,?,datetime('now'))
               ON CONFLICT(conversation_id) DO UPDATE SET
                 contact_id=excluded.contact_id,recipient_email=excluded.recipient_email,
                 subject=excluded.subject,sent_at=excluded.sent_at,updated_at=datetime('now')""",
            (conversation_id, int(contact["id"]), recipients[0], subject, sent_at),
        )
    db.execute(
        """UPDATE contacts SET
             outreach_status=CASE WHEN outreach_status IN ('not_contacted','personalized','drafted') THEN 'sent' ELSE outreach_status END,
             first_sent_at=coalesce(first_sent_at,?),
             followup1_due=CASE WHEN first_sent_at IS NULL AND outreach_status IN ('not_contacted','personalized','drafted','sent')
               AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=contacts.id)
               AND ?=0 THEN coalesce(followup1_due,datetime(?,'+24 hours')) ELSE followup1_due END,
             followup2_due=CASE WHEN first_sent_at IS NULL AND outreach_status IN ('not_contacted','personalized','drafted','sent')
               AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=contacts.id)
               AND ?=0 THEN coalesce(followup2_due,date(?,'+4 days')) ELSE followup2_due END,
             notes=CASE WHEN notes LIKE '%Microsoft 365 sync: sent%' THEN notes
               ELSE notes || CASE WHEN notes='' THEN '' ELSE char(10) END || 'Microsoft 365 sync: sent imported.' END,
             updated_at=datetime('now') WHERE id=?""",
        (sent_at, int(bool(active_hold(db, contact['id']))), sent_at,
         int(bool(active_hold(db, contact['id']))), sent_at, int(contact["id"])),
    )
    return True


def reconcile_reply_queue(db: sqlite3.Connection) -> dict[str, int]:
    """Resolve stale queue rows using synchronized conversation evidence.

    The queue keeps only the latest inbound message in a conversation when no
    later outbound message exists. This is deliberately independent of AI
    classification so a dashboard refresh cannot resurrect obsolete work.
    """
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='outreach_work_items'"
    ).fetchone() is None:
        return {"sent_resolved": 0, "older_superseded": 0, "dnc_ignored": 0}

    sent = db.execute(
        """UPDATE outreach_work_items AS w SET status='sent',resolved_at=coalesce(resolved_at,datetime('now')),
             resolved_by='inbox_sync',decision_reason='已同步到该会话中更晚的出站邮件；无需再次回复。',
             updated_at=datetime('now')
           WHERE w.status IN ('pending','approved') AND EXISTS(
             SELECT 1 FROM inbox_events current
             JOIN outreach_sent_messages outbound
               ON outbound.conversation_id=current.conversation_id
             WHERE current.id=w.inbox_event_id
               AND coalesce(current.conversation_id,'')<>''
               AND datetime(outbound.sent_at)>datetime(current.received_at)
           )"""
    )
    superseded = db.execute(
        """UPDATE outreach_work_items AS w SET status='superseded',resolved_at=coalesce(resolved_at,datetime('now')),
             resolved_by='inbox_sync',decision_reason='同一会话已有更新的来信；旧草稿已失效。',
             updated_at=datetime('now')
           WHERE w.status IN ('pending','approved') AND EXISTS(
             SELECT 1 FROM inbox_events current JOIN inbox_events newer
               ON newer.conversation_id=current.conversation_id
             WHERE current.id=w.inbox_event_id
               AND coalesce(current.conversation_id,'')<>''
               AND (datetime(newer.received_at)>datetime(current.received_at)
                 OR (datetime(newer.received_at)=datetime(current.received_at) AND newer.id>current.id))
           )"""
    )
    ignored = db.execute(
        """UPDATE outreach_work_items AS w SET status='ignored',resolved_at=coalesce(resolved_at,datetime('now')),
             resolved_by='inbox_sync',decision_reason='联系人已拒绝、退订或被抑制；禁止继续回复。',
             updated_at=datetime('now')
           WHERE w.status IN ('pending','approved') AND EXISTS(
             SELECT 1 FROM contacts c WHERE c.id=w.contact_id AND coalesce(c.do_not_contact,0)=1
           )"""
    )
    db.commit()
    return {
        "sent_resolved": max(0, sent.rowcount),
        "older_superseded": max(0, superseded.rowcount),
        "dnc_ignored": max(0, ignored.rowcount),
    }


def classification(text: str) -> tuple[str, str, str, str, int]:
    if pattern_match(AUTOMATED_PATTERNS, text):
        return "automated", "sent", "no_reply", "自动回复；无需人工处理。", 0
    if pattern_match(NEGATIVE_PATTERNS, text):
        return "opted_out", "opted_out", "not_interested", "无需回复；已加入停止联系名单。", 1
    if pattern_match(INTEREST_PATTERNS, text):
        return "interested", "interested", "interested", "回复并提供测试权限、合作 brief，确认形式与报价。", 0
    return "reply", "replied", "replied", "阅读完整回复并确认下一步。", 0


def delivery_report_kind(text: str) -> str:
    # A full mailbox or recipient policy rejection is not evidence that the
    # address does not exist. Sender restrictions are handled before this.
    # Final expiration can include older 4xx retry diagnostics in the same NDR.
    if re.search(r'\b5\d{2}[ -]+5\.\d+\.\d+\s+message\s+expired\b', text, re.I):
        return 'delivery_failed'
    if re.search(r'\b4\d\d[ -]4\.\d+\.\d+\b|\bdelivery (?:is )?delayed\b', text, re.I):
        return 'delivery_delayed'
    if re.search(r'\b5\.1\.(?:1|3|10)\b|recipient (?:not found|does not exist)|user unknown|no such user|'
                 r'address (?:couldn.t be found|was not found)|wasn.t found at|email account[^\n]{0,100}does not exist', text, re.I):
        return 'bounce'
    return 'delivery_failed'


def sync_inbox_message(db: sqlite3.Connection, message: dict[str, Any]) -> tuple[bool, str]:
    message_id = str(message.get("id") or "")
    if not message_id or db.execute("SELECT 1 FROM inbox_events WHERE id=?", (message_id,)).fetchone():
        return False, "duplicate"

    from_name, from_email = sender(message)
    if from_email == SELF_EMAIL:
        return False, "ignored"
    text = message_text(message)
    reply_text = newest_reply(text)
    bounce = is_bounce(message, text)
    matched_email = failed_address(text) if bounce else from_email
    conversation_id = str(message.get("conversationId") or "")
    subject = str(message.get("subject") or "")
    contact = contact_by_email(db, matched_email)
    if not contact and not bounce:
        contact = contact_by_conversation(db, conversation_id) or contact_by_subject(db, subject)
    if not (contact or bounce or is_campaign_subject(subject)):
        return False, "ignored"

    summary = compact_preview(reply_text or str(message.get("bodyPreview") or ""))
    event_type = "unmatched"
    if bounce and is_sender_restriction(from_email, text):
        event_type = "sender_restricted"
        summary = RESTRICTION_SUMMARY
        record_restriction(db, str(message.get("receivedDateTime") or iso_utc()))
        # The recipient's address and opt-out state cannot be inferred from this NDR.
    elif bounce:
        event_type = delivery_report_kind(text)
        summary = f"投递失败：{matched_email}" if matched_email else "收到退信，但未能自动识别失败地址。"
        if contact and event_type == 'bounce':
            db.execute(
                """UPDATE contacts SET outreach_status=CASE WHEN outreach_status IN ('opted_out','closed')
                     THEN outreach_status ELSE 'bounced' END,validation_status='invalid',reply_status='no_reply',
                   do_not_contact=1,next_action='邮箱退信，停止发送并寻找新的公开联系方式。',
                   notes=CASE WHEN notes LIKE '%Microsoft 365 sync: bounce%' THEN notes
                     ELSE notes || CASE WHEN notes='' THEN '' ELSE char(10) END || 'Microsoft 365 sync: bounce detected.' END,
                   updated_at=datetime('now') WHERE id=?""",
                (int(contact["id"]),),
            )
        elif contact:
            db.execute("""UPDATE contacts SET do_not_contact=1,
                next_action='服务商投递失败或延迟；暂停跟进并核验，不把地址判为不存在，不自动重发。',
                updated_at=datetime('now') WHERE id=?""", (int(contact['id']),))
    else:
        event_type, outreach_status, reply_status, next_action, do_not_contact = classification(reply_text)
        headers={str(h.get('name','')).lower():str(h.get('value','')).lower()
                 for h in message.get('internetMessageHeaders',[]) if isinstance(h,dict)}
        if headers.get('auto-submitted','no') not in ('','no'):
            event_type,outreach_status,reply_status,next_action,do_not_contact = 'automated','sent','no_reply','自动回复；无需人工处理。',0
        if contact:
            received_at = str(message.get("receivedDateTime") or iso_utc())
            reply_from = f"{from_name} <{from_email}>" if from_name else from_email
            reviewed_hold = active_hold(db, contact['id'])
            if reviewed_hold and not do_not_contact:
                next_action = reviewed_hold
            if do_not_contact:
                db.execute("""UPDATE contacts SET do_not_contact=1,outreach_status='opted_out',
                  next_action='无需回复；已加入停止联系名单。',updated_at=datetime('now') WHERE id=?""",
                  (int(contact['id']),))
            db.execute(
                """UPDATE contacts SET outreach_status=?,first_sent_at=coalesce(first_sent_at,?),reply_status=?,
                   reply_received_at=?,reply_from=?,reply_summary=?,next_action=?,do_not_contact=max(coalesce(do_not_contact,0),?),
                   notes=CASE WHEN notes LIKE '%Microsoft 365 sync: reply%' THEN notes
                     ELSE notes || CASE WHEN notes='' THEN '' ELSE char(10) END || 'Microsoft 365 sync: reply imported.' END,
                   updated_at=datetime('now') WHERE id=? AND coalesce(do_not_contact,0)=0
                   AND (reply_received_at IS NULL OR datetime(reply_received_at)<=datetime(?))""",
                (
                    outreach_status,
                    received_at,
                    reply_status,
                    received_at,
                    reply_from,
                    summary or "已收到回复。",
                    next_action,
                    do_not_contact,
                    int(contact["id"]),
                    received_at,
                ),
            )

    cursor = db.execute(
        """INSERT OR IGNORE INTO inbox_events(
             id,internet_message_id,conversation_id,received_at,sender_name,sender_email,
             subject,body_preview,body_text,event_type,contact_id,matched_email,web_link,processed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (
            message_id,
            message.get("internetMessageId"),
            conversation_id or None,
            str(message.get("receivedDateTime") or iso_utc()),
            from_name,
            from_email,
            subject,
            summary,
            reply_text[:12000],
            event_type,
            int(contact["id"]) if contact else None,
            matched_email,
            message.get("webLink"),
        ),
    )
    return cursor.rowcount > 0, event_type


def run(db: sqlite3.Connection, *, backfill_sent_context: bool = False) -> dict[str, int]:
    ensure_schema(db)
    ensure_inbox_schema(db)
    access_token = refresh_access_token(db)

    conversation_count = int(db.execute("SELECT count(*) FROM outreach_conversations").fetchone()[0])
    sent_since = iso_utc(CAMPAIGN_START) if backfill_sent_context or conversation_count == 0 else overlap_since(get_setting(db, "microsoft_last_sent_sync_at"))
    inbox_since = overlap_since(get_setting(db, "microsoft_last_sync_at"))
    sent_messages = fetch_sent(access_token, sent_since, max_pages=250 if backfill_sent_context else 10)
    inbox_messages = fetch_inbox(access_token, inbox_since)

    result = {"scanned": len(inbox_messages), "imported": 0, "replies": 0, "bounces": 0, "ignored": 0, "sent_scanned": len(sent_messages), "sent_matched": 0}
    for index, message in enumerate(
        sorted(sent_messages, key=lambda item: str(item.get("sentDateTime") or "")), start=1
    ):
        if sync_sent_message(db, message):
            result["sent_matched"] += 1
        # A full mailbox backfill can contain tens of thousands of messages.
        # Commit bounded chunks so reply and sending workers are not locked out
        # for the entire backfill.
        if index % 100 == 0:
            db.commit()
    db.commit()

    for message in sorted(inbox_messages, key=lambda item: str(item.get("receivedDateTime") or "")):
        imported, event_type = sync_inbox_message(db, message)
        if imported:
            result["imported"] += 1
            if event_type == "bounce":
                result["bounces"] += 1
            if event_type in {"reply", "interested", "opted_out"}:
                result["replies"] += 1
        elif event_type == "ignored":
            result["ignored"] += 1
        db.commit()

    queue_reconciliation = reconcile_reply_queue(db)
    result.update({f"queue_{key}": value for key, value in queue_reconciliation.items()})

    try:
        from scripts.creator_feedback import collect as collect_feedback
    except ModuleNotFoundError:
        from creator_feedback import collect as collect_feedback
    result['feedback_candidates_added'] = collect_feedback(db, inbox_since)

    completed_at = iso_utc()
    set_setting(db, "microsoft_last_sync_at", completed_at)
    set_setting(db, "microsoft_last_sent_sync_at", completed_at)
    set_setting(db, "microsoft_last_sync_result", json.dumps(result, separators=(",", ":")))
    set_setting(db, "microsoft_connection_error", "")
    set_setting(db, "inbox_background_sync_status", "active")
    set_setting(db, "inbox_background_sync_last_run_at", completed_at)
    db.commit()
    return result


def main() -> int:
    args = parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        db = connect(args.db)
        exit_code = 0
        try:
            result = run(db, backfill_sent_context=args.backfill_sent_context)
            print(json.dumps({"completed_at": iso_utc(), **result}, ensure_ascii=False), flush=True)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            try:
                ensure_schema(db)
                set_setting(db, "microsoft_connection_error", message[:2000])
                set_setting(db, "inbox_background_sync_status", "error")
                db.commit()
            except Exception:
                pass
            print(message, file=sys.stderr, flush=True)
            exit_code = 1
        # Each provider progresses independently: a Graph failure must not hide
        # Gmail replies, delivery reports or the original sent context.
        try:
            if __package__:
                from .gmail_history_sync import run as sync_gmail
            else:
                from gmail_history_sync import run as sync_gmail
            gmail = sync_gmail(db)
            reconciliation = reconcile_reply_queue(db)
            print(json.dumps({'gmail': gmail, 'queue': reconciliation}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print('Gmail sync: '+type(exc).__name__+': '+str(exc)[:500], file=sys.stderr, flush=True)
            exit_code = 1
        finally:
            db.close()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
