#!/usr/bin/env python3
"""Send only context-safe, fixed-template replies for resolved creator workflows."""

from __future__ import annotations

import argparse
import fcntl
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from reply_context import context_hold_reason, VERSION
    from outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token, microsoft_outbound_block_reason
    from reply_safety import (
        CAMPAIGN_QUESTION_RE,
        CONDITIONAL_DEAL_RE,
        PRODUCT_ISSUE_RE,
        current_message,
        pre_send_hold_reason,
        template_allows_auto_send,
        template_automation_mode,
    )
    from send_coupon_replies import COUPON, send_reply
except ModuleNotFoundError:
    from scripts.reply_context import context_hold_reason, VERSION
    from scripts.outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token, microsoft_outbound_block_reason
    from scripts.reply_safety import (
        CAMPAIGN_QUESTION_RE,
        CONDITIONAL_DEAL_RE,
        PRODUCT_ISSUE_RE,
        current_message,
        pre_send_hold_reason,
        template_allows_auto_send,
        template_automation_mode,
    )
    from scripts.send_coupon_replies import COUPON, send_reply


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / ".wrangler" / "safe-workflow-reply-agent.lock"
SAFE_TEMPLATES = {"test_planned_ack", "rate_received", "marketing_listed", "ask_rate"}
HUMAN_REVIEW_BLOCKED_STAGE = "human_review_blocked"
DELIVERY_RECONCILIATION_STAGE = "delivery_reconciliation"

RATE_AND_COUPON_BODY = """Hi,

Thanks for sharing your rates. I’ve recorded them for review. I’m not confirming a campaign or budget yet, but here’s the code so you can test VideoCoding first:

FREETEST100

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter FREETEST100
4. Make sure the total shows $0.00 before confirming

Once you’ve tested it on one of your own videos, let me know how it went. We can then review the fit, promotion format, requirements, timeline, and your rate together.

Best,
Alhazar"""

TEST_ACCESS_RE = re.compile(
    r"(?:please|kindly|could you|can you|feel free to)?\s*(?:send|share|provide).{0,80}(?:code|coupon|access)"
    r"|(?:i(?:'d| would)?|we(?:'d| would)?)\s+(?:love|like|want)\s+to\s+(?:test|try)"
    r"|(?:happy|interested|ready)\s+to\s+(?:test|try)"
    r"|test\s+(?:the\s+)?(?:platform|tool|product)\s+first"
    r"|one[- ]month\s+(?:access|code)|free\s+month\s+(?:access|code)",
    re.I | re.S,
)
def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply routing changes without sending")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--daily-limit", type=int, default=100)
    parser.add_argument("--min-interval-seconds", type=float, default=60)
    parser.add_argument("--max-interval-seconds", type=float, default=180)
    return parser.parse_args()


def ensure_agent_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS safe_workflow_reply_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          work_item_id INTEGER NOT NULL UNIQUE,
          contact_id INTEGER,
          template_key TEXT NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS safe_workflow_reply_log_status_idx
          ON safe_workflow_reply_log(status,sent_at);
        """
    )
    db.execute(
        """INSERT INTO outreach_templates(template_key,name,stage,subject,body,risk_level,enabled,version,updated_at)
           VALUES('coupon_test_rate_received','已报价并愿意测试 · 发送 Coupon','coupon_ready',
             'Re: {{original_subject}}',?,'medium',1,1,datetime('now'))
           ON CONFLICT(template_key) DO NOTHING""",
        (RATE_AND_COUPON_BODY,),
    )
    db.commit()


def recover_stale_sending(db: sqlite3.Connection, stale_minutes: int = 15) -> int:
    """Fail closed when a previous process stopped after starting a send.

    Retrying an unknown delivery can duplicate a real email. Keep the work item
    visible for a Sent Items check, but make it ineligible for automation.
    """
    modifier = f"-{max(1, stale_minutes)} minutes"
    rows = db.execute(
        """SELECT work_item_id FROM safe_workflow_reply_log
           WHERE status='sending' AND datetime(updated_at)<=datetime('now',?)""",
        (modifier,),
    ).fetchall()
    for row in rows:
        work_item_id = int(row["work_item_id"])
        db.execute(
            """UPDATE safe_workflow_reply_log SET status='delivery_unknown',
                 error='Delivery outcome is unknown; verify Sent Items before any retry',
                 updated_at=datetime('now') WHERE work_item_id=? AND status='sending'""",
            (work_item_id,),
        )
        db.execute(
            """UPDATE outreach_work_items SET kind='human_review',workflow_stage=?,risk_level='high',
                 decision_reason='发送进程中断，投递结果未知；必须先核对 Sent Items，禁止自动重发。',
                 proposed_action_json='[{"type":"verify_sent_items"}]',updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (DELIVERY_RECONCILIATION_STAGE, work_item_id),
        )
    if rows:
        db.commit()
    return len(rows)


def render(template: sqlite3.Row, channel_name: str) -> tuple[str, str]:
    values = {
        "channel_name": channel_name or "there",
        "coupon_code": COUPON,
    }
    def replace(value: str) -> str:
        for key, replacement in values.items():
            value = value.replace("{{" + key + "}}", replacement)
        return value.replace("{{original_subject}}", "VideoCoding collaboration")
    return replace(str(template["subject"])), replace(str(template["body"]))


def reconcile_context(db: sqlite3.Connection) -> dict[str, int]:
    rows = db.execute(
        """SELECT w.id,w.contact_id,w.kind,w.template_key,w.confidence,e.body_text,e.body_preview,
                  c.channel_name,c.initial_template_key,
                  EXISTS(SELECT 1 FROM coupon_reply_log prior
                         WHERE prior.contact_id=w.contact_id AND prior.status='sent') AS prior_coupon
           FROM outreach_work_items w JOIN inbox_events e ON e.id=w.inbox_event_id
           JOIN contacts c ON c.id=w.contact_id
           WHERE w.status='pending' AND NOT EXISTS(
             SELECT 1 FROM inbox_events newer WHERE newer.contact_id=w.contact_id
               AND datetime(newer.received_at)>datetime(e.received_at)
           )"""
    ).fetchall()
    templates = {
        str(row["template_key"]): row
        for row in db.execute(
            """SELECT template_key,subject,body FROM outreach_templates
               WHERE template_key IN ('coupon_test','coupon_test_t2v_knowledge_v1','coupon_test_rate_received') AND enabled=1"""
        ).fetchall()
    }
    converted_coupon = forced_human = 0
    has_context_audits = any(column[1] == 'extracted_json' for column in db.execute('PRAGMA table_info(outreach_work_items)'))
    for row in rows:
        if has_context_audits:
            stored = db.execute('SELECT extracted_json FROM outreach_work_items WHERE id=?', (int(row['id']),)).fetchone()
            try:
                audit = (json.loads(stored[0] or '{}').get('context_audit') or {})
            except (ValueError, AttributeError):
                audit = {}
            if audit.get('version') == VERSION:
                # The context router owns these decisions; do not replace them
                # with a current-message-only guess. Send guards remain active.
                continue
        message = current_message(str(row["body_text"] or row["body_preview"] or ""))
        if (row["kind"] == "price_received" and not int(row["prior_coupon"] or 0)
                and TEST_ACCESS_RE.search(message)
                and not pre_send_hold_reason("coupon_test_rate_received", message)):
            key = "coupon_test_rate_received"
            template = templates.get(key)
            if template:
                subject, body = render(template, str(row["channel_name"] or ""))
                db.execute(
                    """UPDATE outreach_work_items SET kind='coupon_reply',workflow_stage='coupon_ready',
                         risk_level='medium',template_key=?,subject=?,draft_body=?,
                         decision_reason='对方同时提供报价并明确要求测试权限；报价原文已保留，先发送固定 Coupon，不代表接受报价。',
                         proposed_action_json='[{"type":"record_rate"},{"type":"send_coupon","code":"FREETEST100"}]',
                         updated_at=datetime('now') WHERE id=? AND status='pending'""",
                    (key, subject, body, int(row["id"])),
                )
                db.execute(
                    """UPDATE contacts SET rate_info=coalesce(nullif(rate_info,''),?),
                         next_action='发送测试权限；报价已保留，等待真实测试。',updated_at=datetime('now') WHERE id=?""",
                    (message[:2000], int(row["contact_id"])),
                )
                converted_coupon += 1
                continue
        reason = pre_send_hold_reason(str(row["template_key"] or ""), message)
        if reason:
            cursor = db.execute(
                """UPDATE outreach_work_items SET kind='human_review',workflow_stage=?,risk_level='high',
                     template_key=NULL,subject='',draft_body='',decision_reason=?,proposed_action_json='[{"type":"human_review"}]',
                     updated_at=datetime('now') WHERE id=? AND status='pending'
                       AND NOT (kind='human_review' AND workflow_stage=? AND template_key IS NULL
                         AND coalesce(decision_reason,'')=?)""",
                (HUMAN_REVIEW_BLOCKED_STAGE, reason, int(row["id"]), HUMAN_REVIEW_BLOCKED_STAGE, reason),
            )
            if cursor.rowcount:
                forced_human += 1
    db.commit()
    return {"converted_coupon": converted_coupon, "forced_human": forced_human}


def eligible(db: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    candidates = db.execute(
        """SELECT w.id,w.contact_id,w.template_key,w.draft_body,e.id AS message_id,e.received_at,c.channel_name,
                  p.usage_verification_status,e.body_text,e.body_preview
           FROM outreach_work_items w JOIN inbox_events e ON e.id=w.inbox_event_id
           JOIN contacts c ON c.id=w.contact_id LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
           LEFT JOIN safe_workflow_reply_log sent_log ON sent_log.work_item_id=w.id
           WHERE w.status='pending' AND w.confidence>=0.90 AND w.template_key IN (?,?,?,?)
             AND sent_log.id IS NULL AND c.do_not_contact=0
             AND NOT EXISTS(SELECT 1 FROM inbox_events newer WHERE newer.contact_id=w.contact_id
                            AND datetime(newer.received_at)>datetime(e.received_at))
             AND (w.template_key<>'ask_rate' OR p.usage_verification_status='completed')
             AND (w.template_key<>'marketing_listed' OR p.usage_verification_status='completed')
           ORDER BY CASE w.template_key WHEN 'marketing_listed' THEN 0 WHEN 'ask_rate' THEN 1
                        WHEN 'rate_received' THEN 2 ELSE 3 END,e.received_at DESC LIMIT ?""",
        (*sorted(SAFE_TEMPLATES), max(1, limit) * 10),
    ).fetchall()
    safe: list[sqlite3.Row] = []
    for row in candidates:
        if context_hold_reason(db, int(row["id"]), expected_template=str(row["template_key"] or ""), expected_draft=str(row["draft_body"] or "")):
            continue
        if not template_allows_auto_send(db, str(row["template_key"] or "")):
            continue
        message = current_message(str(row["body_text"] or row["body_preview"] or ""))
        if pre_send_hold_reason(str(row["template_key"] or ""), message):
            continue
        safe.append(row)
        if len(safe) >= max(1, limit):
            break
    return safe


def sent_today(db: sqlite3.Connection) -> int:
    return int(db.execute(
        "SELECT COUNT(*) FROM safe_workflow_reply_log WHERE status='sent' AND date(sent_at)=date('now')"
    ).fetchone()[0])


def still_latest(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
    if context_hold_reason(db, int(row["id"]), expected_template=str(row["template_key"] or ""), expected_draft=str(row["draft_body"] or "")):
        return False
    live = db.execute(
        """SELECT w.template_key,e.body_text,e.body_preview FROM outreach_work_items w JOIN contacts c ON c.id=w.contact_id
           JOIN inbox_events e ON e.id=w.inbox_event_id
           WHERE w.id=? AND w.status='pending' AND c.do_not_contact=0
             AND NOT EXISTS(SELECT 1 FROM inbox_events newer WHERE newer.contact_id=w.contact_id
                            AND datetime(newer.received_at)>datetime(e.received_at))""",
        (int(row["id"]),),
    ).fetchone()
    if live is None or not template_allows_auto_send(db, str(live["template_key"] or "")):
        return False
    message = current_message(str(live["body_text"] or live["body_preview"] or ""))
    return pre_send_hold_reason(str(live["template_key"] or ""), message) is None


def send_safe(db: sqlite3.Connection, args: argparse.Namespace) -> tuple[int, int]:
    if args.send and microsoft_outbound_block_reason(db):
        return 0, 0
    remaining = max(0, args.daily_limit - sent_today(db))
    rows = eligible(db, min(max(1, args.limit), remaining)) if remaining else []
    if not args.send:
        return 0, 0
    token = ""
    sent = failed = 0
    for index, row in enumerate(rows):
        if not still_latest(db, row):
            continue
        now = iso_utc()
        db.execute(
            """INSERT INTO safe_workflow_reply_log(work_item_id,contact_id,template_key,status,queued_at,updated_at)
               VALUES(?,?,?,'sending',?,?)""",
            (int(row["id"]), row["contact_id"], row["template_key"], now, now),
        )
        db.commit()
        if not token:
            token = refresh_access_token(db)
        ok, detail = send_reply(token, str(row["message_id"]), str(row["draft_body"]))
        db.execute(
            """UPDATE safe_workflow_reply_log SET status=?,sent_at=?,error=?,updated_at=? WHERE work_item_id=?""",
            ("sent" if ok else "failed", iso_utc() if ok else None, None if ok else detail[:1000], iso_utc(), int(row["id"])),
        )
        if ok:
            db.execute(
                """UPDATE outreach_work_items SET status='sent',resolved_at=datetime('now'),
                     resolved_by='safe_workflow_reply_agent',updated_at=datetime('now') WHERE id=? AND status='pending'""",
                (int(row["id"]),),
            )
            if row["template_key"] == "ask_rate":
                db.execute(
                    "UPDATE creator_profiles SET workflow_stage='price_needed',updated_at=datetime('now') WHERE contact_id=?",
                    (row["contact_id"],),
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
        ensure_agent_schema(db)
        delivery_unknown = recover_stale_sending(db)
        if args.send and (reason := microsoft_outbound_block_reason(db)):
            return {"sent": 0, "status": "sender_restricted", "reason": reason, "delivery_unknown": delivery_unknown}
        reconciled = reconcile_context(db) if args.send or args.apply else {"converted_coupon": 0, "forced_human": 0}
        ready = len(eligible(db, max(1, args.limit)))
        sent, failed = send_safe(db, args)
        return {**reconciled, "delivery_unknown": delivery_unknown, "ready": ready, "sent": sent, "failed": failed, "send_enabled": args.send}
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
