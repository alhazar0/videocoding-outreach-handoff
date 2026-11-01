#!/usr/bin/env python3
"""Queue and send one human-authored follow-up after 24 hours of silence."""

from __future__ import annotations

import argparse
import fcntl
import json
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from outreach_scheduler import (
        DEFAULT_DB,
        GOOGLE_MAILBOX,
        MAILBOX,
        connect,
        ensure_schema,
        gmail_smtp_transport_available,
        google_app_password,
        refresh_access_token,
        microsoft_outbound_block_reason,
        send_gmail_smtp,
    )
    from reply_safety import template_automation_mode
    from reviewed_contact_hold import active_hold
    from send_coupon_replies import coupon_body_to_html, graph_request, send_reply
except ModuleNotFoundError:
    from scripts.outreach_scheduler import (
        DEFAULT_DB,
        GOOGLE_MAILBOX,
        MAILBOX,
        connect,
        ensure_schema,
        gmail_smtp_transport_available,
        google_app_password,
        refresh_access_token,
        microsoft_outbound_block_reason,
        send_gmail_smtp,
    )
    from scripts.reply_safety import template_automation_mode
    from scripts.reviewed_contact_hold import active_hold
    from scripts.send_coupon_replies import coupon_body_to_html, graph_request, send_reply


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / ".wrangler" / "creator-followup-agent.lock"

INITIAL_FOLLOWUP = """Hi {{channel_name}},

Just following up in case my last email got buried. We’d still be interested in exploring a paid collaboration with you.

If you’d like to test VideoCoding on one of your own videos, reply and I’ll send you a code for one free month.

Best,
Alhazar Anwar"""

RATE_FOLLOWUP = """Hi,

Just following up after your VideoCoding test. When you have a moment, could you share your rate for a dedicated YouTube video and, if available, a 60–90 second integration?

Best,
Alhazar Anwar"""

REGISTERED_EMAIL_FOLLOWUP = """Hi,

Just following up — when you have a moment, please send me the email address you used to register for VideoCoding. I’ll check the account before arranging the one-month creator access from our side.

Best,
Alhazar Anwar"""


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--daily-limit", type=int, default=250)
    parser.add_argument("--gmail-daily-limit", type=int, default=25)
    parser.add_argument("--min-interval-seconds", type=float, default=60)
    parser.add_argument("--max-interval-seconds", type=float, default=180)
    return parser.parse_args()


def ensure_followup_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS creator_followups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          contact_id INTEGER NOT NULL,
          source_kind TEXT NOT NULL,
          source_id TEXT NOT NULL,
          sequence INTEGER NOT NULL DEFAULT 1,
          template_key TEXT NOT NULL,
          sender_email TEXT NOT NULL,
          conversation_id TEXT,
          source_sent_at TEXT NOT NULL,
          due_at TEXT NOT NULL,
          subject TEXT NOT NULL,
          body TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT,
          updated_at TEXT NOT NULL,
          UNIQUE(source_kind,source_id,sequence)
        );
        CREATE INDEX IF NOT EXISTS creator_followups_due_idx
          ON creator_followups(status,due_at);
        """
    )
    now = iso_utc()
    for key, name, stage, subject, body in (
        (
            "initial_followup_24h",
            "首次合作邀请 · 24小时无回复跟进",
            "followup",
            "Re: A paid collaboration with VideoCoding",
            INITIAL_FOLLOWUP,
        ),
        (
            "rate_followup_24h",
            "真实测试完成 · 24小时未报价跟进",
            "price_needed",
            "Re: VideoCoding collaboration",
            RATE_FOLLOWUP,
        ),
        (
            "registered_email_followup_24h",
            "不绑卡申请 · 24小时未提供注册邮箱跟进",
            "membership_email_needed",
            "Re: VideoCoding creator access",
            REGISTERED_EMAIL_FOLLOWUP,
        ),
    ):
        db.execute(
            """INSERT INTO outreach_templates(template_key,name,stage,subject,body,risk_level,enabled,version,updated_at)
               VALUES(?,?,?,?,?,'medium',1,1,?)
               ON CONFLICT(template_key) DO NOTHING""",
            (key, name, stage, subject, body, now),
        )
    db.execute(
        """INSERT INTO outreach_settings(key,value,updated_at)
           VALUES('followup_after_hours','24',?)
           ON CONFLICT(key) DO UPDATE SET value='24',updated_at=excluded.updated_at""",
        (now,),
    )
    db.commit()


def render(template: str, channel_name: str) -> str:
    return template.replace("{{channel_name}}", channel_name or "there")


def queue_initial_followups(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """SELECT l.id,l.contact_id,l.sent_at,l.sender_email,c.channel_name
           FROM outreach_send_log l JOIN contacts c ON c.id=l.contact_id
           WHERE l.status='sent' AND l.sent_at IS NOT NULL
             AND l.id=(SELECT MAX(latest.id) FROM outreach_send_log latest
                       WHERE latest.contact_id=l.contact_id AND latest.status='sent')
             AND datetime(l.sent_at)<=datetime('now','-24 hours')
             AND c.do_not_contact=0 AND c.outreach_status NOT IN ('bounced','opted_out','closed')
             AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=l.contact_id
                            AND datetime(e.received_at)>datetime(l.sent_at))
             AND NOT EXISTS(SELECT 1 FROM creator_followups f
                            WHERE f.source_kind='initial' AND f.source_id=cast(l.id AS TEXT))
           ORDER BY l.sent_at ASC"""
    ).fetchall()
    template = db.execute(
        "SELECT subject,body FROM outreach_templates WHERE template_key='initial_followup_24h' AND enabled=1"
    ).fetchone()
    if not template:
        return 0
    now = iso_utc()
    for row in rows:
        db.execute(
            """INSERT OR IGNORE INTO creator_followups(
                 contact_id,source_kind,source_id,template_key,sender_email,source_sent_at,due_at,
                 subject,body,status,queued_at,updated_at
               ) VALUES(?,'initial',?,'initial_followup_24h',?,?,datetime(?,'+24 hours'),?,?,'pending',?,?)""",
            (
                int(row["contact_id"]), str(row["id"]), str(row["sender_email"] or MAILBOX),
                row["sent_at"], row["sent_at"], str(template["subject"]),
                render(str(template["body"]), str(row["channel_name"] or "")), now, now,
            ),
        )
    db.commit()
    return len(rows)


def queue_coupon_followups(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """SELECT r.id,r.inbox_event_id,r.contact_id,r.sent_at,r.conversation_id,c.channel_name
           FROM coupon_reply_log r JOIN contacts c ON c.id=r.contact_id
           LEFT JOIN creator_profiles p ON p.contact_id=r.contact_id
           WHERE r.status='sent' AND r.sent_at IS NOT NULL
             AND r.id=(SELECT MAX(latest.id) FROM coupon_reply_log latest
                       WHERE latest.contact_id=r.contact_id AND latest.status='sent')
             AND datetime(r.sent_at)<=datetime('now','-24 hours')
             AND c.do_not_contact=0 AND coalesce(p.usage_verification_status,'')<>'completed'
             AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=r.contact_id
                            AND datetime(e.received_at)>datetime(r.sent_at))
             AND NOT EXISTS(SELECT 1 FROM creator_followups f
                            WHERE f.source_kind='coupon' AND f.source_id=cast(r.id AS TEXT))
           ORDER BY r.sent_at ASC"""
    ).fetchall()
    template = db.execute(
        "SELECT subject,body FROM outreach_templates WHERE template_key='test_followup' AND enabled=1"
    ).fetchone()
    if not template:
        return 0
    now = iso_utc()
    for row in rows:
        db.execute(
            """INSERT OR IGNORE INTO creator_followups(
                 contact_id,source_kind,source_id,template_key,sender_email,conversation_id,source_sent_at,due_at,
                 subject,body,status,queued_at,updated_at
               ) VALUES(?,'coupon',?,'test_followup',?,?,?,datetime(?,'+24 hours'),?,?,'pending',?,?)""",
            (
                int(row["contact_id"]), str(row["id"]), MAILBOX, row["inbox_event_id"],
                row["sent_at"], row["sent_at"], str(template["subject"]),
                render(str(template["body"]), str(row["channel_name"] or "")), now, now,
            ),
        )
    db.commit()
    return len(rows)


def queue_rate_followups(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """SELECT w.id,w.contact_id,w.resolved_at,e.id AS inbox_event_id,e.conversation_id,c.channel_name
           FROM outreach_work_items w JOIN contacts c ON c.id=w.contact_id
           LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
           LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
           WHERE w.status='sent' AND w.template_key='ask_rate' AND w.resolved_at IS NOT NULL
             AND datetime(w.resolved_at)<=datetime('now','-24 hours') AND c.do_not_contact=0
             AND p.usage_verification_status='completed'
             AND coalesce(p.price_dedicated,p.price_integration,p.price_shorts) IS NULL
             AND NOT EXISTS(SELECT 1 FROM inbox_events newer WHERE newer.contact_id=w.contact_id
                            AND datetime(newer.received_at)>datetime(w.resolved_at))
             AND NOT EXISTS(SELECT 1 FROM creator_followups f
                            WHERE f.source_kind='rate' AND f.source_id=cast(w.id AS TEXT))"""
    ).fetchall()
    template = db.execute(
        "SELECT subject,body FROM outreach_templates WHERE template_key='rate_followup_24h' AND enabled=1"
    ).fetchone()
    if not template:
        return 0
    now = iso_utc()
    for row in rows:
        db.execute(
            """INSERT OR IGNORE INTO creator_followups(
                 contact_id,source_kind,source_id,template_key,sender_email,conversation_id,source_sent_at,due_at,
                 subject,body,status,queued_at,updated_at
               ) VALUES(?,'rate',?,'rate_followup_24h',?,?,?,datetime(?,'+24 hours'),?,?,'pending',?,?)""",
            (
                int(row["contact_id"]), str(row["id"]), MAILBOX, row["inbox_event_id"],
                row["resolved_at"], row["resolved_at"], str(template["subject"]),
                render(str(template["body"]), str(row["channel_name"] or "")), now, now,
            ),
        )
    db.commit()
    return len(rows)


def queue_workflow_followups(db: sqlite3.Connection) -> int:
    """Follow up after fixed workflow replies that do not have their own send log."""
    rows = db.execute(
        """SELECT w.id,w.inbox_event_id,w.contact_id,w.resolved_at,w.template_key,c.channel_name,
                  p.registered_email,p.usage_verification_status
           FROM outreach_work_items w JOIN contacts c ON c.id=w.contact_id
           LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
           WHERE w.status='sent' AND w.resolved_at IS NOT NULL
             AND w.template_key IN ('membership_activated','ask_registered_email')
             AND datetime(w.resolved_at)<=datetime('now','-24 hours')
             AND c.do_not_contact=0
             AND NOT EXISTS(SELECT 1 FROM inbox_events newer WHERE newer.contact_id=w.contact_id
                            AND datetime(newer.received_at)>datetime(w.resolved_at))
             AND NOT EXISTS(SELECT 1 FROM creator_followups f
                            WHERE f.source_kind='workflow' AND f.source_id=cast(w.id AS TEXT))
             AND ((w.template_key='membership_activated' AND coalesce(p.usage_verification_status,'')<>'completed')
               OR (w.template_key='ask_registered_email' AND coalesce(p.registered_email,'')=''))
           ORDER BY w.resolved_at ASC"""
    ).fetchall()
    templates = {
        str(row["template_key"]): row
        for row in db.execute(
            """SELECT template_key,subject,body FROM outreach_templates
               WHERE template_key IN ('test_followup','registered_email_followup_24h') AND enabled=1"""
        ).fetchall()
    }
    now = iso_utc()
    queued = 0
    for row in rows:
        followup_key = "test_followup" if row["template_key"] == "membership_activated" else "registered_email_followup_24h"
        template = templates.get(followup_key)
        if not template:
            continue
        cursor = db.execute(
            """INSERT OR IGNORE INTO creator_followups(
                 contact_id,source_kind,source_id,template_key,sender_email,conversation_id,source_sent_at,due_at,
                 subject,body,status,queued_at,updated_at
               ) VALUES(?,'workflow',?,?,?,?,?,datetime(?,'+24 hours'),?,?,'pending',?,?)""",
            (
                int(row["contact_id"]), str(row["id"]), followup_key, MAILBOX,
                row["inbox_event_id"], row["resolved_at"], row["resolved_at"],
                str(template["subject"]), render(str(template["body"]), str(row["channel_name"] or "")), now, now,
            ),
        )
        queued += max(0, cursor.rowcount)
    db.commit()
    return queued


def cancel_replied_or_ineligible(db: sqlite3.Connection) -> int:
    cursor = db.execute(
        """UPDATE creator_followups SET status='cancelled',error='Reply received or contact no longer eligible',
             updated_at=datetime('now')
           WHERE status='pending' AND (
             EXISTS(SELECT 1 FROM contacts c WHERE c.id=creator_followups.contact_id
                    AND (c.do_not_contact=1 OR c.outreach_status IN ('bounced','opted_out','closed')))
             OR EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=creator_followups.contact_id
                       AND datetime(e.received_at)>datetime(creator_followups.source_sent_at))
             OR (source_kind='coupon' AND EXISTS(SELECT 1 FROM creator_profiles p
                 WHERE p.contact_id=creator_followups.contact_id AND p.usage_verification_status='completed'))
           )"""
    )
    db.commit()
    return max(0, cursor.rowcount)


def hold_unsupported_senders(db: sqlite3.Connection) -> int:
    """Never change the sender identity just to make a follow-up automatic."""
    cursor = db.execute(
        """UPDATE creator_followups SET status='manual_required',
             error='Original sender is not connected to the automatic follow-up service',updated_at=datetime('now')
           WHERE status='pending' AND lower(sender_email) NOT IN (?,?)""",
        (MAILBOX.lower(), GOOGLE_MAILBOX.lower()),
    )
    db.commit()
    return max(0, cursor.rowcount)


def recover_stale_sending(db: sqlite3.Connection, stale_minutes: int = 15) -> int:
    """Hold interrupted sends for manual delivery verification; never retry blindly."""
    modifier = f"-{max(1, stale_minutes)} minutes"
    cursor = db.execute(
        """UPDATE creator_followups SET status='manual_required',
             error='Delivery outcome is unknown; verify Sent Items before any retry',updated_at=datetime('now')
           WHERE status='sending' AND datetime(updated_at)<=datetime('now',?)""",
        (modifier,),
    )
    db.commit()
    return max(0, cursor.rowcount)


def graph_send_new(token: str, recipient: str, subject: str, body: str) -> tuple[bool, str]:
    ok, detail, _ = graph_request(
        token,
        "https://graph.microsoft.com/v1.0/me/sendMail",
        payload={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": coupon_body_to_html(body)},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
                "internetMessageHeaders": [
                    {"name": "x-videocoding-campaign", "value": "creator-followup-24h"},
                ],
            },
            "saveToSentItems": True,
        },
    )
    return ok, detail


def still_eligible(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
    if active_hold(db, row['contact_id']):
        return False
    return db.execute(
        """SELECT 1 FROM contacts c WHERE c.id=? AND c.do_not_contact=0
             AND c.outreach_status NOT IN ('bounced','opted_out','closed')
             AND NOT EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=c.id
                            AND datetime(e.received_at)>datetime(?))""",
        (int(row["contact_id"]), row["source_sent_at"]),
    ).fetchone() is not None


def sent_today(db: sqlite3.Connection, sender: str) -> int:
    return int(
        db.execute(
            "SELECT COUNT(*) FROM creator_followups WHERE status='sent' AND sender_email=? AND date(sent_at)=date('now')",
            (sender,),
        ).fetchone()[0]
    )


def send_due(db: sqlite3.Connection, args: argparse.Namespace) -> tuple[int, int]:
    limit = max(1, args.limit)
    rows: list[sqlite3.Row] = []
    for sender, cap in ((MAILBOX.lower(), args.daily_limit), (GOOGLE_MAILBOX.lower(), args.gmail_daily_limit)):
        if sender == MAILBOX.lower() and microsoft_outbound_block_reason(db):
            continue
        remaining = max(0, cap - sent_today(db, sender))
        if not remaining:
            continue
        rows.extend(
            db.execute(
                """SELECT f.*,c.email,c.channel_name FROM creator_followups f JOIN contacts c ON c.id=f.contact_id
                   WHERE f.status='pending' AND datetime(f.due_at)<=datetime('now') AND lower(f.sender_email)=?
                   ORDER BY CASE f.source_kind WHEN 'coupon' THEN 0 WHEN 'workflow' THEN 0 WHEN 'rate' THEN 0 ELSE 1 END,
                     datetime(f.due_at) DESC,f.id DESC LIMIT ?""",
                (sender, min(limit, remaining)),
            ).fetchall()
        )
    def priority(row: sqlite3.Row) -> tuple[int, float]:
        warm = 0 if row["source_kind"] in ("coupon", "workflow", "rate") else 1
        value = str(row["due_at"] or "").replace("Z", "+00:00")
        try:
            timestamp = datetime.fromisoformat(value).timestamp()
        except ValueError:
            timestamp = 0
        return warm, -timestamp
    rows = sorted(rows, key=priority)[:limit]
    if not args.send:
        return 0, 0
    if any(str(row["sender_email"] or "").lower() == GOOGLE_MAILBOX.lower() for row in rows):
        transport_ok, transport_detail = gmail_smtp_transport_available()
        db.execute(
            """INSERT INTO outreach_settings(key,value,updated_at) VALUES('gmail_smtp_transport_status',?,datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            ("available" if transport_ok else transport_detail,),
        )
        db.commit()
        if not transport_ok:
            rows = [
                row for row in rows
                if str(row["sender_email"] or "").lower() != GOOGLE_MAILBOX.lower()
            ]
    graph_token = ""
    gmail_password = ""
    sent = failed = 0
    for index, row in enumerate(rows):
        sender = str(row["sender_email"] or MAILBOX).lower()
        if sender == MAILBOX.lower() and microsoft_outbound_block_reason(db):
            continue
        cap = args.gmail_daily_limit if sender == GOOGLE_MAILBOX.lower() else args.daily_limit
        if sent_today(db, sender) >= cap:
            continue
        policy_mode = template_automation_mode(db, str(row["template_key"] or ""))
        if policy_mode != "auto":
            db.execute(
                """UPDATE creator_followups SET status='manual_required',error=?,updated_at=?
                   WHERE id=? AND status='pending'""",
                (f"Automation rule mode is {policy_mode}; follow-up requires operator review", iso_utc(), int(row["id"])),
            )
            db.commit()
            continue
        if not still_eligible(db, row):
            db.execute(
                "UPDATE creator_followups SET status='cancelled',error='Reply received before send',updated_at=? WHERE id=?",
                (iso_utc(), int(row["id"])),
            )
            db.commit()
            continue
        db.execute(
            "UPDATE creator_followups SET status='sending',error=NULL,updated_at=? WHERE id=? AND status='pending'",
            (iso_utc(), int(row["id"])),
        )
        db.commit()
        if row["conversation_id"] and sender != GOOGLE_MAILBOX.lower():
            if not graph_token:
                graph_token = refresh_access_token(db)
            ok, detail = send_reply(graph_token, str(row["conversation_id"]), str(row["body"]))
        elif sender == GOOGLE_MAILBOX.lower():
            if not gmail_password:
                gmail_password = google_app_password()
            ok, detail = send_gmail_smtp(
                gmail_password,
                row,
                "followup-24h-v1",
                str(row["subject"]),
                str(row["body"]),
                experiment_id="creator-followup-24h-2026-08",
                variant=str(row["template_key"]),
            )
        else:
            if not graph_token:
                graph_token = refresh_access_token(db)
            ok, detail = graph_send_new(graph_token, str(row["email"]), str(row["subject"]), str(row["body"]))
        db.execute(
            """UPDATE creator_followups SET status=?,sent_at=?,error=?,updated_at=? WHERE id=?""",
            ("sent" if ok else "failed", iso_utc() if ok else None, None if ok else detail[:1000], iso_utc(), int(row["id"])),
        )
        if ok:
            db.execute(
                """UPDATE contacts SET outreach_status=CASE WHEN outreach_status='sent' THEN 'followup1' ELSE outreach_status END,
                     followup1_due=NULL,next_action='已进行24小时跟进；等待回复。',updated_at=datetime('now') WHERE id=?""",
                (int(row["contact_id"]),),
            )
            sent += 1
        else:
            failed += 1
        db.commit()
        if index < len(rows) - 1:
            time.sleep(random.uniform(max(5, args.min_interval_seconds), max(args.min_interval_seconds, args.max_interval_seconds)))
    return sent, failed


def run(args: argparse.Namespace) -> dict[str, Any]:
    db = connect(args.db)
    try:
        ensure_schema(db)
        ensure_followup_schema(db)
        delivery_unknown = recover_stale_sending(db)
        queued_initial = queue_initial_followups(db)
        queued_coupon = queue_coupon_followups(db)
        queued_rate = queue_rate_followups(db)
        queued_workflow = queue_workflow_followups(db)
        cancelled = cancel_replied_or_ineligible(db)
        manual_required = hold_unsupported_senders(db)
        pending = int(db.execute("SELECT COUNT(*) FROM creator_followups WHERE status='pending'").fetchone()[0])
        sent, failed = send_due(db, args)
        return {
            "queued_initial": queued_initial,
            "queued_coupon": queued_coupon,
            "queued_rate": queued_rate,
            "queued_workflow": queued_workflow,
            "cancelled": cancelled,
            "manual_required": manual_required,
            "delivery_unknown": delivery_unknown,
            "pending": pending,
            "sent": sent,
            "failed": failed,
            "send_enabled": args.send,
        }
    finally:
        db.close()


def main() -> int:
    args = parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            print(json.dumps(run(args), ensure_ascii=False), flush=True)
            return 0
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
