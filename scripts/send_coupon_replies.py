#!/usr/bin/env python3
"""Reply to qualified creator responses with the FREETEST100 test code."""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from outreach_scheduler import (
        DEFAULT_DB,
        T2V_KNOWLEDGE_TEMPLATE_KEY,
        connect,
        ensure_schema,
        refresh_access_token,
        microsoft_outbound_block_reason,
        is_graph_delivery,
    )
    from reply_context import context_hold_reason
    from reply_safety import automation_rule_mode, current_message, pre_send_hold_reason, template_automation_mode
except ModuleNotFoundError:
    from scripts.outreach_scheduler import (
        DEFAULT_DB,
        T2V_KNOWLEDGE_TEMPLATE_KEY,
        connect,
        ensure_schema,
        refresh_access_token,
        microsoft_outbound_block_reason,
        is_graph_delivery,
    )
    from scripts.reply_context import context_hold_reason
    from scripts.reply_safety import automation_rule_mode, current_message, pre_send_hold_reason, template_automation_mode


DEFAULT_LOCK = Path(__file__).resolve().parents[1] / ".wrangler" / "coupon-replies.lock"
COUPON = "FREETEST100"
SITE_URL = "https://videocoding.ai"

REDIRECTS = {
    "redacted-a69df6bfc5@example.com": "redacted-ad55f5ddba@example.com",
    "redacted-8386375f33@example.com": "redacted-47ca2eb338@example.com",
}

SKIP_SENDERS = {
    "redacted-606cbb3f85@example.com",
    "redacted-3c374d2fef@example.com",
    "redacted-8845d6d77d@example.com",
    "redacted-d711c7551f@example.com",
    "redacted-ab9d45e703@example.com",
}

SKIP_PATTERNS = [
    r"undeliverable|recipient not found|delivery (?:failed|has failed)",
    r"we(?:'|’)ve received your message and will get back",
    r"received your message and will get back",
    r"guest posts?|link insertions?",
    r"list it first sponsored editorial",
    r"congradulations, you are on the list",
]

TEST_PATTERNS = [
    r"\btry\b",
    r"\btest(?:ing|ed)?\b",
    r"\bcredits?\b",
    r"\baccess\b",
    r"created an account",
    r"signed up",
    r"check it out",
    r"\binterested\b",
    r"sounds (?:good|interesting)",
    r"(?:happy|glad) to (?:try|test)",
    r"would love to (?:try|test)",
    r"send (?:me )?(?:the )?(?:code|coupon)",
    r"get started",
]

RATE_PATTERNS = [
    r"[$€£]\s?\d[\d,.]*",
    r"\b(?:usd|eur|gbp)\s?\d[\d,.]*",
    r"\b\d[\d,.]*\s?(?:usd|eur|gbp)\b",
    r"\b\d[\d,.]*\s?\$",
    r"\b(?:my|our|standard|current)\s+(?:collaboration\s+)?(?:rate|rates|pricing)\s*(?:is|are|:|=|starts?\s+at)\b",
    r"\b(?:i|we)\s+(?:normally\s+|usually\s+)?(?:charge|quote)\b",
    r"\brate\s+card\b",
]

HARD_HOLD_PATTERNS = [
    r"\bonce\s+(?:you(?:'ve)?\s+)?subscrib(?:e|ed)\b",
    r"\b(?:upfront|deposit|paypal|payment\s+(?:terms?|confirmation))\b",
    r"\b(?:sign|signed|signing)\s+(?:the\s+)?(?:deal|agreement|contract)\b",
    r"\b(?:click\s+here|fill\s+(?:in|out)\s+(?:the\s+)?form|book\s+(?:a\s+)?call|calendar)\b",
    r"\b(?:won't|doesn't|does not|will not)\s+(?:really\s+)?load\b|\b(?:bug|error|not\s+working|stuck|failed|failure)\b",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="0 sends every eligible reply")
    parser.add_argument("--min-reply-age-hours", type=float, default=0.0)
    parser.add_argument("--min-interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-interval-seconds", type=float, default=180.0)
    return parser.parse_args()


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_reply_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS coupon_reply_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          inbox_event_id TEXT NOT NULL UNIQUE,
          contact_id INTEGER,
          recipient_email TEXT NOT NULL,
          conversation_id TEXT,
          variant TEXT NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT
        );
        CREATE INDEX IF NOT EXISTS coupon_reply_log_status_idx
          ON coupon_reply_log(status, sent_at);
        """
    )
    db.execute(
        """UPDATE outreach_work_items SET
             workflow_stage='testing',status='sent',resolved_at=coalesce(resolved_at,datetime('now')),
             resolved_by=coalesce(resolved_by,'coupon_automation_reconciled'),updated_at=datetime('now')
           WHERE status='pending' AND kind='coupon_reply' AND EXISTS(
             SELECT 1 FROM coupon_reply_log sent_log
             WHERE sent_log.inbox_event_id=outreach_work_items.inbox_event_id
               AND sent_log.status='sent'
           )"""
    )
    db.execute(
        """INSERT INTO creator_profiles(contact_id,coupon_code,coupon_sent_at,updated_at)
           SELECT contact_id,?,max(sent_at),datetime('now')
           FROM coupon_reply_log
           WHERE status='sent' AND contact_id IS NOT NULL
           GROUP BY contact_id
           ON CONFLICT(contact_id) DO UPDATE SET
             coupon_code=excluded.coupon_code,
             coupon_sent_at=CASE
               WHEN creator_profiles.coupon_sent_at IS NULL
                 OR excluded.coupon_sent_at>creator_profiles.coupon_sent_at
               THEN excluded.coupon_sent_at
               ELSE creator_profiles.coupon_sent_at
             END,
             updated_at=datetime('now')""",
        (COUPON,),
    )
    db.commit()


def recover_clear_interest(db: sqlite3.Connection) -> int:
    """Recover only unambiguous, fully contextualized test requests stranded by old routing."""
    rows = db.execute(
        """SELECT w.id,w.extracted_json,e.body_text,e.body_preview,c.initial_template_key,
                  (SELECT COUNT(*) FROM outreach_sent_messages captured
                    WHERE captured.contact_id=w.contact_id AND datetime(captured.sent_at)<=datetime(e.received_at)
                      AND trim(captured.body_text)<>'') AS captured_count,
                  ((SELECT COUNT(*) FROM outreach_send_log initial_sent
                      WHERE initial_sent.contact_id=w.contact_id AND initial_sent.status='sent'
                        AND datetime(initial_sent.sent_at)<=datetime(e.received_at)) +
                   (SELECT COUNT(*) FROM outreach_work_items reply_sent
                      WHERE reply_sent.contact_id=w.contact_id AND reply_sent.status='sent'
                        AND coalesce(reply_sent.resolved_by,'') NOT IN ('operations_queue_reconciler','inbox_sync')
                        AND datetime(coalesce(reply_sent.resolved_at,reply_sent.updated_at))<=datetime(e.received_at)) +
                   (SELECT COUNT(*) FROM creator_followups followup_sent
                      WHERE followup_sent.contact_id=w.contact_id AND followup_sent.status='sent'
                        AND datetime(followup_sent.sent_at)<=datetime(e.received_at))) AS expected_count
           FROM outreach_work_items w
           JOIN inbox_events e ON e.id=w.inbox_event_id
           JOIN contacts c ON c.id=w.contact_id
           LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
           WHERE w.status='pending' AND w.kind='human_review' AND w.confidence>=0.85
             AND coalesce(c.do_not_contact,0)=0
             AND p.coupon_sent_at IS NULL
             AND coalesce(p.membership_status,'none') NOT IN ('active','trialing')
             AND coalesce(p.usage_verification_status,'not_checked') NOT IN ('started','completed')
             AND coalesce(p.test_status,'not_started') NOT IN ('claimed','completed')
             AND NOT EXISTS(SELECT 1 FROM outreach_sent_messages sent
               WHERE sent.contact_id=w.contact_id AND sent.body_text LIKE '%FREETEST100%')"""
    ).fetchall()
    recovered = 0
    for row in rows:
        if context_hold_reason(db, int(row["id"])):
            continue
        try:
            decision = json.loads(str(row["extracted_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        intents = {str(value) for value in decision.get("secondary_intents") or []}
        if decision.get("intent") not in {"wants_test", "activation_help"}:
            continue
        if not intents or not intents.issubset({"wants_test", "activation_help", "plans_to_test"}):
            continue
        if (not decision.get("context_complete") or decision.get("template_gap")
                or decision.get("questions") or decision.get("missing_context") or decision.get("contradictions")):
            continue
        if int(row["captured_count"] or 0) < int(row["expected_count"] or 0):
            continue
        if not any(item.get("source") == "current_message" and item.get("intent") in {"wants_test", "activation_help"}
                   for item in decision.get("evidence") or [] if isinstance(item, dict)):
            continue
        message = current_message(str(row["body_text"] or row["body_preview"] or ""))
        template_key = "coupon_test_t2v_knowledge_v1" if row["initial_template_key"] == "first_contact_t2v_knowledge_v1" else "coupon_test"
        if pre_send_hold_reason(template_key, message) or has_pattern(HARD_HOLD_PATTERNS, message):
            continue
        template = db.execute(
            """SELECT subject,body FROM outreach_templates
               WHERE template_key=? AND enabled=1 AND approval_status='approved'""",
            (template_key,),
        ).fetchone()
        if not template:
            continue
        body = str(template["body"] or "").replace("{{coupon_code}}", COUPON)
        db.execute(
            """UPDATE outreach_work_items SET kind='coupon_reply',workflow_stage='coupon_ready',
                 risk_level='medium',template_key=?,subject=?,draft_body=?,
                 proposed_action_json='[{"type":"send_coupon","code":"FREETEST100"}]',
                 decision_reason=decision_reason || '；确定性恢复：完整线程中的当前来信明确接受测试邀请，且没有其他未解决意图或问题。',
                 updated_at=datetime('now') WHERE id=? AND status='pending' AND kind='human_review'""",
            (template_key, str(template["subject"] or ""), body, int(row["id"])),
        )
        recovered += 1
    db.commit()
    return recovered


def coupon_account_lookup_email(row) -> str:
    # This only checks for existing access before sending a coupon. A sender
    # address is never stored as a confirmed registration or used for a grant.
    return str(row["registered_email"] or row["registered_contact_email"] or row["sender_email"] or "").strip()


def latest_replies(db: sqlite3.Connection, min_reply_age_hours: float = 0.0) -> list[sqlite3.Row]:
    age_modifier = f"-{max(0.0, min_reply_age_hours):g} hours"
    return db.execute(
        """
        WITH ranked AS (
          SELECT e.*, c.channel_name, c.email AS contact_email, c.initial_template_key,
                 w.id AS work_item_id,w.kind AS work_kind,w.status AS work_status,
                 w.risk_level AS work_risk,w.confidence AS work_confidence,
                 w.template_key AS work_template_key,w.draft_body AS work_draft_body,
                 w.extracted_json AS work_extracted_json,
                 c.email AS registered_contact_email,
                 p.registered_email,p.membership_status,p.test_status,
                 p.usage_verification_status,p.coupon_sent_at,
                 (SELECT COUNT(*) FROM outreach_sent_messages captured_sent
                    WHERE captured_sent.contact_id=e.contact_id
                      AND datetime(captured_sent.sent_at)<=datetime(e.received_at)
                      AND trim(captured_sent.body_text)<>'') AS captured_sent_context_count,
                 ((SELECT COUNT(*) FROM outreach_send_log initial_sent
                     WHERE initial_sent.contact_id=e.contact_id AND initial_sent.status='sent'
                       AND datetime(initial_sent.sent_at)<=datetime(e.received_at)) +
                  (SELECT COUNT(*) FROM outreach_work_items reply_sent
                     WHERE reply_sent.contact_id=e.contact_id AND reply_sent.status='sent'
                        AND coalesce(reply_sent.resolved_by,'') NOT IN ('operations_queue_reconciler','inbox_sync')
                       AND datetime(coalesce(reply_sent.resolved_at,reply_sent.updated_at))<=datetime(e.received_at)) +
                  (SELECT COUNT(*) FROM creator_followups followup_sent
                     WHERE followup_sent.contact_id=e.contact_id AND followup_sent.status='sent'
                       AND datetime(followup_sent.sent_at)<=datetime(e.received_at))) AS expected_sent_context_count,
                 row_number() OVER (
                   PARTITION BY coalesce(cast(e.contact_id AS TEXT), lower(e.sender_email))
                   ORDER BY e.received_at DESC
                 ) AS rn
          FROM inbox_events e
          LEFT JOIN contacts c ON c.id=e.contact_id
          LEFT JOIN creator_profiles p ON p.contact_id=e.contact_id
          LEFT JOIN outreach_work_items w ON w.inbox_event_id=e.id
          WHERE e.event_type IN ('interested','reply')
            AND datetime(e.received_at) <= datetime('now', ?)
        )
        SELECT r.*
        FROM ranked r
        LEFT JOIN coupon_reply_log l
          ON l.inbox_event_id=r.id AND l.status='sent'
        WHERE r.rn=1 AND l.id IS NULL
          AND r.work_status='pending'
          AND r.work_kind='coupon_reply'
          AND r.work_risk IN ('medium','high')
          AND r.work_confidence>=0.85
          AND r.work_template_key IN ('coupon_test','coupon_test_t2v_knowledge_v1','coupon_test_rate_received','coupon_test_campaign_details')
          AND NOT EXISTS (
            SELECT 1 FROM coupon_reply_log prior
            WHERE prior.status='sent'
              AND (
                (r.contact_id IS NOT NULL AND prior.contact_id=r.contact_id)
                OR (
                  r.contact_id IS NULL
                  AND lower(prior.recipient_email)=lower(r.sender_email)
                )
              )
          )
        ORDER BY r.received_at ASC
        """,
        (age_modifier,),
    ).fetchall()


SAFE_INTENTS_BY_TEMPLATE = {
    "coupon_test": {"wants_test", "activation_help", "plans_to_test"},
    "coupon_test_t2v_knowledge_v1": {"wants_test", "activation_help", "plans_to_test"},
    "coupon_test_rate_received": {"wants_test", "activation_help", "plans_to_test", "price_received"},
    "coupon_test_campaign_details": {"wants_test", "activation_help", "plans_to_test", "asks_campaign_details"},
}


def ai_context_hold_reason(row: sqlite3.Row) -> str | None:
    """Fail closed unless the AI decision proves the approved copy covers the whole thread."""
    if int(row["captured_sent_context_count"] or 0) < int(row["expected_sent_context_count"] or 0):
        return "context requires human review: sent-history coverage is incomplete"
    try:
        decision = json.loads(str(row["work_extracted_json"] or "{}"))
    except json.JSONDecodeError:
        return "context requires human review: AI context record is invalid"
    if decision.get("needs_human"):
        return "automation policy holds matched draft for human approval"
    if not decision.get("context_complete"):
        return "context requires human review: AI marked the conversation incomplete"
    if decision.get("template_gap"):
        return "context requires human review: approved template does not cover the whole request"
    if decision.get("missing_context") or decision.get("contradictions"):
        return "context requires human review: unresolved context or contradiction"
    key = str(row["work_template_key"] or "")
    intents = {str(value) for value in decision.get("secondary_intents") or []}
    allowed = SAFE_INTENTS_BY_TEMPLATE.get(key, set())
    if not intents or not intents.issubset(allowed):
        return "context requires human review: live intents exceed the approved coupon template"
    evidence = decision.get("evidence") or []
    if not any(item.get("source") == "current_message" and item.get("intent") in {"wants_test", "activation_help"}
               for item in evidence if isinstance(item, dict)):
        return "context requires human review: no current-message evidence requesting test access"
    if decision.get("questions") and key != "coupon_test_campaign_details":
        return "context requires human review: unanswered question is not covered by this template"
    return None


def has_pattern(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def current_reply_text(row: sqlite3.Row) -> str:
    body_text = row["body_text"] if "body_text" in row.keys() else None
    return current_message(str(body_text or row["body_preview"] or ""))


def hard_hold_reason(row: sqlite3.Row) -> str | None:
    text = current_reply_text(row)
    reason = ai_context_hold_reason(row)
    if reason:
        return reason
    reason = pre_send_hold_reason(str(row["work_template_key"] or ""), text)
    if reason:
        return f"context requires human review: {reason}"
    if has_pattern(HARD_HOLD_PATTERNS, text):
        return "context requires human review: payment, contract, external workflow, or product issue"
    return None


def classify(row: sqlite3.Row) -> tuple[str, str | None]:
    sender_email = str(row["sender_email"] or "").lower()
    sender_name = str(row["sender_name"] or "")
    subject = str(row["subject"] or "")
    preview = str(row["body_preview"] or "")
    combined = "\n".join([sender_name, sender_email, subject, preview])

    if "onmicrosoft.com" in sender_email or sender_name.lower() == "microsoft outlook":
        return "skip", "delivery notice"
    if sender_email in SKIP_SENDERS or has_pattern(SKIP_PATTERNS, combined):
        return "skip", "automated or non-creator reply"
    if has_pattern([
        r"\bnot interested\b",
        r"\bno thanks\b",
        r"\bnot a fit\b",
        r"\bremove me\b",
        r"\bunsubscribe\b",
        r"\bdo not contact\b",
        r"\bdeclin(?:e|ed|ing)\b",
    ], combined):
        return "skip", "negative reply"
    if sender_email == "redacted-aa4cc0eaf8@example.com":
        return "exclusivity", None
    if sender_email in REDIRECTS:
        return "redirect", REDIRECTS[sender_email]
    if re.search(r"still interested in videocoding", subject, flags=re.I):
        return "waitlist", None
    if has_pattern([
        r"credit card", r"payment method", r"without (?:a )?card",
        r"don.?t want (?:to add )?(?:a )?card",
    ], preview):
        return "skip", "cardless membership request requires human review"
    if has_pattern([
        r"\b(?:i|we)(?:'ve| have)?\s+(?:already\s+|just\s+)?tested\b",
        r"\btesting\s+went\b", r"\bcompleted\s+(?:the\s+)?test\b",
    ], preview):
        return "skip", "creator already claims a completed test"

    wants_test = has_pattern(TEST_PATTERNS, preview)
    mentions_rate = has_pattern(RATE_PATTERNS, preview)
    if mentions_rate and not wants_test:
        return "rate_first", None
    if mentions_rate and any(token in preview.lower() for token in ["calendar", "availability", "strategy call"]):
        return "rate_first", None
    if wants_test or mentions_rate:
        return "standard", None
    return "skip", "no explicit creator-test interest"


def standard_body() -> str:
    return f"""Hi,

Sure — here’s the code for one month of VideoCoding Pro:

{COUPON}

To activate it:

1. Sign in or create an account at {SITE_URL}
2. Click Upgrade to Pro
3. Click Add promotion code and enter {COUPON}
4. Make sure the total shows $0.00 before confirming

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

When you get a chance, try it with a talking-head video you would normally publish. If anything doesn’t work, just reply here and I’ll help.

Once you’ve tried it, let me know what you think. If it’s a good fit, we can talk about the promotion and your rate.

Best,
Alhazar"""


def rate_first_body() -> str:
    return f"""Hi,

Thanks for sharing your rates. I’ve recorded them for review. I’m not confirming a campaign or budget yet, but here’s the code so you can test VideoCoding first:

{COUPON}

To activate it:

1. Sign in or create an account at {SITE_URL}
2. Click Upgrade to Pro
3. Click Add promotion code and enter {COUPON}
4. Make sure the total shows $0.00 before confirming

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

Once you’ve tested it on one of your own videos, let me know how it went. We can then review the fit, promotion format, requirements, timeline, and your rate together.

Best,
Alhazar"""


def t2v_knowledge_body() -> str:
    return f"""Hi,

Sure — here’s the code for one month of VideoCoding Pro:

{COUPON}

To activate it:

1. Sign in or create an account at {SITE_URL}
2. Click Upgrade to Pro
3. Click Add promotion code and enter {COUPON}
4. Make sure the total shows $0.00 before confirming

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

For a useful test, enter one real topic you would normally cover and start with a short prompt. VideoCoding can turn it into a complete video up to 10 minutes long, including the research, script, narration, scenes, motion, music, and final edit.

Once you’ve tried it, let me know what you think. If it’s a good fit, we can talk about the promotion and your rate.

Best,
Alhazar"""


def waitlist_body() -> str:
    return f"""Hi,

Thanks for waiting. Here’s your VideoCoding test code:

{COUPON}

To activate it:

1. Sign in or create an account at {SITE_URL}
2. Click Upgrade to Pro
3. Click Add promotion code and enter {COUPON}
4. Confirm that Total due today shows $0.00, then complete the subscription

The code gives you 100% off for one month.

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

Once you’ve tried it, reply and let me know how it went.

Best,
Alhazar Anwar"""


def exclusivity_body() -> str:
    return """Hi,

Thanks for flagging the exclusivity point. VideoCoding is fully generative and 100% AI-directed. It does not use editing templates—the system decides and generates the visual treatment from the source video.

Based on the restriction you described, it sounds like VideoCoding falls within the covered category until August 31. I won’t ask you to test it during the exclusivity period, and I’ll follow up in September with creator test access.

Best,
Alhazar Anwar"""


def redirected_body(sender_email: str) -> str:
    intro = "Your team asked me to contact you at this address."
    if sender_email == "redacted-a69df6bfc5@example.com":
        intro = "Thanks for directing me to this address."
    return f"""Hi,

{intro} Here’s the VideoCoding creator test code:

{COUPON}

To activate it:

1. Sign in or create an account at {SITE_URL}
2. Click Upgrade to Pro
3. Click Add promotion code and enter {COUPON}
4. Confirm that Total due today shows $0.00, then complete the subscription

The code gives you 100% off for one month.

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

For the best test, use a real talking-head video you would normally publish. Once you’ve tried it, reply and let me know how it went. If it feels like a good fit, we can discuss the promotion format and your rate.

Best,
Alhazar Anwar"""


def body_for(variant: str, sender_email: str, draft_body: str = "") -> str:
    if draft_body and COUPON in draft_body:
        return draft_body
    if variant == "t2v_knowledge":
        return t2v_knowledge_body()
    if variant == "rate_first":
        return rate_first_body()
    if variant == "waitlist":
        return waitlist_body()
    if variant == "exclusivity":
        return exclusivity_body()
    if variant == "redirect":
        return redirected_body(sender_email)
    return standard_body()


def graph_request(
    access_token: str,
    url: str,
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    if is_graph_delivery(url, method) and (reason := microsoft_outbound_block_reason()):
        return False, f"HTTP 423 sender_restricted: {reason}", {"error": {"code": "sender_restricted"}}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8", "replace")
            data = json.loads(raw) if raw.strip() else {}
            return response.status in {200, 201, 202, 204}, f"HTTP {response.status}", data
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1500]
        return False, f"HTTP {exc.code}: {detail}", {}
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", {}


def coupon_body_to_html(body: str) -> str:
    blocks: list[str] = []
    list_items: list[str] = []

    def inline(value: str) -> str:
        escaped = html.escape(value)
        return re.sub(
            r"https://[^\s<]+",
            lambda match: f'<a href="{match.group(0)}" style="color:#175cd3">{match.group(0)}</a>',
            escaped,
        )

    def flush_list() -> None:
        if not list_items:
            return
        blocks.append(f'<ol style="margin:8px 0 18px;padding-left:24px">{"".join(list_items)}</ol>')
        list_items.clear()

    for raw_line in body.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        numbered = re.match(r"^\d+\.\s+(.+)$", line)
        if numbered:
            list_items.append(f'<li style="margin:0 0 9px"><strong>{inline(numbered.group(1))}</strong></li>')
            continue
        flush_list()
        if not line:
            continue
        content = inline(line)
        heading = re.match(r"^(?:Promo Code:|How to activate:|To activate it:|Recommended Test:)", line, flags=re.I)
        blocks.append(f'<p style="margin:0 0 14px;line-height:1.55">{f"<strong>{content}</strong>" if heading else content}</p>')
    flush_list()
    return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#17212b">{"".join(blocks)}</div>'


def send_reply(access_token: str, message_id: str, body: str) -> tuple[bool, str]:
    if message_id.startswith('gmail:'):
        return False, 'Gmail message requires its original Gmail reply transport; Graph was not called'
    encoded = urllib.parse.quote(message_id, safe="")
    ok, detail, _ = graph_request(
        access_token,
        f"https://graph.microsoft.com/v1.0/me/messages/{encoded}/reply",
        payload={
            "message": {
                "body": {"contentType": "HTML", "content": coupon_body_to_html(body)},
            },
        },
    )
    return ok, detail


def send_new(access_token: str, recipient: str, body: str) -> tuple[bool, str]:
    payload = {
        "message": {
            "subject": "Your VideoCoding Pro Test Access + Instructions",
            "body": {"contentType": "HTML", "content": coupon_body_to_html(body)},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        },
        "saveToSentItems": True,
    }
    ok, detail, _ = graph_request(access_token, "https://graph.microsoft.com/v1.0/me/sendMail", payload=payload)
    return ok, detail


def record_queued(db: sqlite3.Connection, row: sqlite3.Row, recipient: str, variant: str) -> None:
    db.execute(
        """INSERT INTO coupon_reply_log(
             inbox_event_id,contact_id,recipient_email,conversation_id,variant,status,queued_at
           ) VALUES(?,?,?,?,?,'sending',?)
           ON CONFLICT(inbox_event_id) DO UPDATE SET
             recipient_email=excluded.recipient_email,variant=excluded.variant,
             status='sending',queued_at=excluded.queued_at,error=NULL""",
        (row["id"], row["contact_id"], recipient, row["conversation_id"], variant, iso_utc()),
    )
    db.commit()


def record_skipped(db: sqlite3.Connection, row: sqlite3.Row, reason: str) -> None:
    db.execute(
        """INSERT INTO coupon_reply_log(
             inbox_event_id,contact_id,recipient_email,conversation_id,variant,status,queued_at,error
           ) VALUES(?,?,?,?,?,'skipped',?,?)
           ON CONFLICT(inbox_event_id) DO UPDATE SET
             variant='skip',status='skipped',queued_at=excluded.queued_at,error=excluded.error""",
        (
            row["id"], row["contact_id"], row["sender_email"], row["conversation_id"],
            "skip", iso_utc(), reason,
        ),
    )
    if reason.startswith("context requires human review") and row["work_item_id"]:
        db.execute(
            """UPDATE outreach_work_items SET kind='human_review',workflow_stage='replied',risk_level='high',
                 template_key=NULL,subject='',draft_body='',decision_reason=?,
                 proposed_action_json='[{"type":"human_review"}]',updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (reason, int(row["work_item_id"])),
        )
    db.commit()


def record_result(db: sqlite3.Connection, row: sqlite3.Row, ok: bool, detail: str, variant: str) -> None:
    db.execute(
        """UPDATE coupon_reply_log SET status=?,sent_at=?,error=? WHERE inbox_event_id=?""",
        ("sent" if ok else "failed", iso_utc() if ok else None, None if ok else detail, row["id"]),
    )
    if ok and row["contact_id"]:
        if variant == "exclusivity":
            next_action = "2026-09-01 后跟进测试权限（对方排他期结束）。"
            note = "Replied: fully generative, 100% AI-directed workflow disclosed; follow up after exclusivity."
        else:
            next_action = f"等待创作者使用 {COUPON} 测试并反馈。"
            note = f"Coupon reply sent: {COUPON}."
        db.execute(
            """UPDATE contacts SET next_action=?,
               notes=notes || CASE WHEN notes='' THEN '' ELSE char(10) END || ?,
               updated_at=datetime('now') WHERE id=?""",
            (next_action, note, int(row["contact_id"])),
        )
        db.execute(
            """INSERT INTO creator_profiles(
                 contact_id,workflow_stage,coupon_code,coupon_sent_at,test_status,updated_at
               ) VALUES(?,'testing',?,datetime('now'),'wants_test',datetime('now'))
               ON CONFLICT(contact_id) DO UPDATE SET
                 workflow_stage='testing',coupon_code=excluded.coupon_code,
                 coupon_sent_at=excluded.coupon_sent_at,test_status='wants_test',
                 updated_at=datetime('now')""",
            (int(row["contact_id"]), COUPON),
        )
    if ok and row["work_item_id"]:
        db.execute(
            """UPDATE outreach_work_items SET
                 workflow_stage='testing',status='sent',resolved_at=datetime('now'),
                 resolved_by='coupon_automation',updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (int(row["work_item_id"]),),
        )
    db.commit()


def plan(db: sqlite3.Connection, min_reply_age_hours: float = 0.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in latest_replies(db, min_reply_age_hours):
        hold_reason = context_hold_reason(db, int(row["work_item_id"]), expected_template=str(row["work_template_key"] or ""), expected_draft=str(row["work_draft_body"] or "")) or hard_hold_reason(row)
        if hold_reason:
            skipped.append({
                "row": row,
                "channel": row["channel_name"],
                "from": row["sender_email"],
                "recipient": row["sender_email"],
                "subject": row["subject"],
                "variant": "skip",
                "reason": hold_reason,
            })
            continue
        template_key = str(row["work_template_key"] or "")
        template_mode = template_automation_mode(db, template_key)
        if template_mode != "auto":
            skipped.append({
                "row": row,
                "channel": row["channel_name"],
                "from": row["sender_email"],
                "recipient": row["sender_email"],
                "subject": row["subject"],
                "variant": "skip",
                "reason": f"automation policy holds {template_key or 'missing template'} in {template_mode} mode",
            })
            continue
        variant = (
            "t2v_knowledge"
            if template_key == "coupon_test_t2v_knowledge_v1"
            else "rate_first"
            if template_key == "coupon_test_rate_received"
            else "campaign_details"
            if template_key == "coupon_test_campaign_details"
            else "standard"
        )
        item = {
            "row": row,
            "channel": row["channel_name"],
            "from": row["sender_email"],
            "recipient": row["sender_email"],
            "subject": row["subject"],
            "variant": variant,
        }
        eligible.append(item)
    return eligible, skipped


def run(args: argparse.Namespace) -> int:
    db = connect(args.db)
    ensure_schema(db)
    ensure_reply_schema(db)
    if args.send and (reason := microsoft_outbound_block_reason(db)):
        print(json.dumps({"sent": 0, "status": "sender_restricted", "reason": reason}))
        return 0
    recovered = recover_clear_interest(db) if args.send else 0
    eligible, skipped = plan(db, args.min_reply_age_hours)
    if args.limit > 0:
        eligible = eligible[: args.limit]

    counts: dict[str, int] = {}
    for item in eligible:
        counts[item["variant"]] = counts.get(item["variant"], 0) + 1
    print(json.dumps({"eligible": len(eligible), "skipped": len(skipped), "recovered": recovered, "variants": counts}, ensure_ascii=False), flush=True)
    for item in eligible:
        print(json.dumps({key: value for key, value in item.items() if key != "row"}, ensure_ascii=False), flush=True)
    for item in skipped:
        print(json.dumps({key: value for key, value in item.items() if key != "row"}, ensure_ascii=False), flush=True)

    if not args.send:
        return 0

    mode_row = db.execute(
        "SELECT value FROM outreach_settings WHERE key='coupon_automation_mode'"
    ).fetchone()
    automation_mode = str(mode_row[0]) if mode_row else "review"
    rule_mode = automation_rule_mode(db, "coupon_reply")
    if automation_mode != "auto" or rule_mode != "auto":
        print(json.dumps({
            "completed_at": iso_utc(),
            "mode": automation_mode,
            "rule_mode": rule_mode,
            "eligible_for_review": len(eligible),
            "sent": 0,
            "message": "Coupon replies are held because executable policy is not auto.",
        }, ensure_ascii=False), flush=True)
        return 0

    for item in skipped:
        record_skipped(db, item["row"], str(item.get("reason") or "skipped"))
    if not eligible:
        print(json.dumps({"completed_at": iso_utc(), "sent": 0, "failed": 0, "skipped": len(skipped)}, ensure_ascii=False), flush=True)
        return 0

    # Live provider and product checks close the gap between the last sync and
    # the actual send.  A stale local database must never cause a duplicate
    # coupon or a reply after the creator sent a newer message.
    try:
        from creator_data_agent import creator_lookup, keychain_token
        from send_reviewed_offer_trial import mailbox_check
        from send_reviewed_simple_trial import check_addresses
    except ModuleNotFoundError:
        from scripts.creator_data_agent import creator_lookup, keychain_token
        from scripts.send_reviewed_offer_trial import mailbox_check
        from scripts.send_reviewed_simple_trial import check_addresses

    access_token = refresh_access_token(db)
    product_token = keychain_token()
    token_refreshed_at = time.monotonic()
    sent = 0
    failed = 0
    minimum = max(0.0, args.min_interval_seconds)
    maximum = max(minimum, args.max_interval_seconds)
    for index, item in enumerate(eligible):
        if time.monotonic() - token_refreshed_at >= 45 * 60:
            access_token = refresh_access_token(db)
            token_refreshed_at = time.monotonic()
        row = item["row"]
        variant = item["variant"]
        recipient = str(item["recipient"])
        hold_reason = context_hold_reason(db, int(row["work_item_id"]), expected_template=str(row["work_template_key"] or ""), expected_draft=str(row["work_draft_body"] or "")) or hard_hold_reason(row)
        if hold_reason:
            record_skipped(db, row, hold_reason)
            continue
        try:
            mailbox_check(access_token, row, True)
            check_addresses(access_token, row, True)
            lookup_email = coupon_account_lookup_email(row)
            if not lookup_email:
                raise ValueError("product account lookup email is unavailable")
            status, payload = creator_lookup("https://videocoding.ai", product_token, lookup_email)
            if status != 200 or not payload.get("success"):
                raise ValueError("product account lookup unavailable")
            creator = payload.get("creator") or {}
            subscription = (creator.get("subscription") or {}).get("status")
            total_tasks = int((creator.get("activity") or {}).get("totalTasks") or 0)
            if payload.get("found") and (subscription in {"active", "trialing"} or total_tasks > 0):
                raise ValueError("product backend already shows access or testing activity")
        except Exception as exc:
            print(
                f"held: {item['channel'] or item['from']} [{variant}] live pre-send guard: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue
        # Re-run local state after the network checks so another synchronized
        # worker cannot slip a reply or coupon into the verification window.
        hold_reason = context_hold_reason(db, int(row["work_item_id"]), expected_template=str(row["work_template_key"] or ""), expected_draft=str(row["work_draft_body"] or "")) or hard_hold_reason(row)
        changed = db.execute(
            """SELECT 1
               FROM contacts c LEFT JOIN creator_profiles p ON p.contact_id=c.id
               WHERE c.id=? AND (
                 coalesce(c.do_not_contact,0)=1 OR p.coupon_sent_at IS NOT NULL
                 OR p.membership_status IN ('active','trialing')
                 OR p.usage_verification_status IN ('started','completed')
                 OR p.test_status IN ('claimed','completed')
                 OR EXISTS(SELECT 1 FROM outreach_sent_messages s
                   WHERE s.contact_id=c.id AND (datetime(s.sent_at)>datetime(?) OR s.body_text LIKE '%FREETEST100%'))
                 OR EXISTS(SELECT 1 FROM coupon_reply_log l
                   WHERE l.contact_id=c.id AND l.status IN ('sending','sent','delivery_unknown'))
               ) LIMIT 1""",
            (row["contact_id"], row["received_at"]),
        ).fetchone()
        if hold_reason or changed:
            print(
                f"held: {item['channel'] or item['from']} [{variant}] local state changed during live checks",
                file=sys.stderr,
                flush=True,
            )
            continue
        body = body_for(variant, str(item["from"]), str(row["work_draft_body"] or ""))
        record_queued(db, row, recipient, variant)
        if variant == "redirect":
            ok, detail = send_new(access_token, recipient, body)
        else:
            ok, detail = send_reply(access_token, str(row["id"]), body)
        if not ok and "HTTP 401" in detail:
            access_token = refresh_access_token(db)
            token_refreshed_at = time.monotonic()
            if variant == "redirect":
                ok, detail = send_new(access_token, recipient, body)
            else:
                ok, detail = send_reply(access_token, str(row["id"]), body)
        record_result(db, row, ok, detail, variant)
        if ok:
            sent += 1
            print(f"sent {sent}/{len(eligible)}: {item['channel'] or item['from']} [{variant}]", flush=True)
        else:
            failed += 1
            print(f"failed: {item['channel'] or item['from']} [{variant}] {detail}", file=sys.stderr, flush=True)
        if index < len(eligible) - 1 and maximum > 0:
            delay = random.uniform(minimum, maximum)
            print(f"waiting {delay:.0f}s before next reply", flush=True)
            time.sleep(delay)
    print(json.dumps({"completed_at": iso_utc(), "sent": sent, "failed": failed, "skipped": len(skipped)}, ensure_ascii=False), flush=True)
    return 0 if failed == 0 else 1


def main() -> int:
    args = parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another coupon reply process is already running", file=sys.stderr)
            return 1
        return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
