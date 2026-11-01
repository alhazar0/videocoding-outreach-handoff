#!/usr/bin/env python3
"""Send an explicit, operator-approved batch of fully audited template replies.

This sender is intentionally unavailable to broad queue scans: every work item
must be named on the command line and must still pass the stored context audit,
approved-template, latest-message, sent-history, and duplicate-delivery checks.
"""

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
    from outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token, microsoft_outbound_block_reason
    from reply_safety import current_message, pre_send_hold_reason
    from send_coupon_replies import COUPON, send_reply
except ModuleNotFoundError:
    from scripts.outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token, microsoft_outbound_block_reason
    from scripts.reply_safety import current_message, pre_send_hold_reason
    from scripts.send_coupon_replies import COUPON, send_reply


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / ".wrangler" / "approved-reply-batch.lock"
COUPON_LOCK = ROOT / ".wrangler" / "coupon-replies.lock"
SAFE_WORKFLOW_LOCK = ROOT / ".wrangler" / "safe-workflow-reply-agent.lock"


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ids(value: str) -> list[int]:
    result: list[int] = []
    for part in value.split(","):
        try:
            item_id = int(part.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid work item id: {part}") from exc
        if item_id <= 0:
            raise argparse.ArgumentTypeError(f"invalid work item id: {part}")
        if item_id not in result:
            result.append(item_id)
    if not result:
        raise argparse.ArgumentTypeError("at least one work item id is required")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--ids", type=parse_ids, required=True)
    parser.add_argument("--older-than-hours", type=float, default=72.0)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--min-interval-seconds", type=float, default=15.0)
    parser.add_argument("--max-interval-seconds", type=float, default=30.0)
    return parser.parse_args()


def ensure_batch_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS approved_reply_batch_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          work_item_id INTEGER NOT NULL UNIQUE,
          inbox_event_id TEXT NOT NULL,
          contact_id INTEGER,
          template_key TEXT NOT NULL,
          template_version INTEGER NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS approved_reply_batch_log_status_idx
          ON approved_reply_batch_log(status,sent_at);
        """
    )
    db.commit()


def recover_stale_sending(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """SELECT work_item_id FROM approved_reply_batch_log
           WHERE status='sending' AND datetime(updated_at)<=datetime('now','-15 minutes')"""
    ).fetchall()
    for row in rows:
        item_id = int(row[0])
        db.execute(
            """UPDATE approved_reply_batch_log SET status='delivery_unknown',
                 error='Delivery outcome unknown; verify Sent Items before any retry',updated_at=datetime('now')
               WHERE work_item_id=? AND status='sending'""",
            (item_id,),
        )
        db.execute(
            """UPDATE outreach_work_items SET kind='human_review',workflow_stage='delivery_reconciliation',
                 risk_level='high',decision_reason='发送进程中断；必须先核对 Sent Items，禁止自动重发。',
                 updated_at=datetime('now') WHERE id=? AND status='pending'""",
            (item_id,),
        )
    if rows:
        db.commit()
    return len(rows)


def render(row: sqlite3.Row) -> tuple[str, str]:
    original_subject = str(row["original_subject"] or "")
    while True:
        lowered = original_subject.lower()
        prefix = next((item for item in ("re:", "fw:", "fwd:") if lowered.startswith(item)), None)
        if not prefix:
            break
        original_subject = original_subject[len(prefix):].lstrip()
    extracted = json.loads(str(row["extracted_json"] or "{}"))
    values = {
        "original_subject": original_subject,
        "coupon_code": COUPON,
        "registered_email": str(extracted.get("registered_email") or "the email you registered with"),
        "channel_name": str(row["channel_name"] or ""),
    }

    def replace(value: str) -> str:
        for key, replacement in values.items():
            value = value.replace("{{" + key + "}}", replacement)
        return value

    return replace(str(row["template_subject"] or "")), replace(str(row["template_body"] or ""))


def load_candidates(db: sqlite3.Connection, item_ids: list[int], older_than_hours: float) -> tuple[list[sqlite3.Row], dict[int, str]]:
    placeholders = ",".join("?" for _ in item_ids)
    age_modifier = f"-{max(0.0, older_than_hours):g} hours"
    rows = db.execute(
        f"""
        SELECT w.id,w.contact_id,w.inbox_event_id,w.kind,w.workflow_stage,w.status,w.risk_level,w.confidence,
          w.template_key,w.subject,w.draft_body,w.extracted_json,w.decision_reason,
          e.received_at,e.subject AS original_subject,e.body_text,e.body_preview,e.conversation_id,
          c.channel_name,c.do_not_contact,c.rate_info,
          p.coupon_sent_at,p.membership_status,p.test_status,p.usage_verification_status,
          p.price_dedicated,p.price_integration,p.price_shorts,
          t.subject AS template_subject,t.body AS template_body,t.version AS template_version,
          t.enabled AS template_enabled,t.approval_status,
          (SELECT COUNT(*) FROM outreach_sent_messages captured
             WHERE captured.contact_id=w.contact_id AND datetime(captured.sent_at)<=datetime(e.received_at)
               AND trim(captured.body_text)<>'') AS captured_sent_context_count,
          ((SELECT COUNT(*) FROM outreach_send_log initial_sent
              WHERE initial_sent.contact_id=w.contact_id AND initial_sent.status='sent'
                AND datetime(initial_sent.sent_at)<=datetime(e.received_at)) +
           (SELECT COUNT(*) FROM outreach_work_items reply_sent
              WHERE reply_sent.contact_id=w.contact_id AND reply_sent.status='sent'
                AND datetime(coalesce(reply_sent.resolved_at,reply_sent.updated_at))<=datetime(e.received_at)) +
           (SELECT COUNT(*) FROM creator_followups followup_sent
              WHERE followup_sent.contact_id=w.contact_id AND followup_sent.status='sent'
                AND datetime(followup_sent.sent_at)<=datetime(e.received_at))) AS expected_sent_context_count
        FROM outreach_work_items w
        JOIN inbox_events e ON e.id=w.inbox_event_id
        JOIN contacts c ON c.id=w.contact_id
        LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
        JOIN outreach_templates t ON t.template_key=w.template_key
        WHERE w.id IN ({placeholders})
        ORDER BY datetime(e.received_at),w.id
        """,
        item_ids,
    ).fetchall()
    found = {int(row["id"]): row for row in rows}
    held: dict[int, str] = {}
    safe: list[sqlite3.Row] = []
    for item_id in item_ids:
        row = found.get(item_id)
        if row is None:
            held[item_id] = "work item or approved template not found"
            continue
        if row["status"] != "pending":
            held[item_id] = f"work item is {row['status']}, not pending"
            continue
        if int(row["do_not_contact"] or 0):
            held[item_id] = "contact is do-not-contact"
            continue
        if not int(row["template_enabled"] or 0) or row["approval_status"] != "approved":
            held[item_id] = "template is not enabled and approved"
            continue
        if float(row["confidence"] or 0) < 0.90:
            held[item_id] = "context confidence is below 0.90"
            continue
        if int(row["captured_sent_context_count"] or 0) < int(row["expected_sent_context_count"] or 0):
            held[item_id] = "sent-history body coverage is incomplete"
            continue
        if db.execute(
            """SELECT 1 FROM inbox_events newer WHERE
                 coalesce(nullif(newer.conversation_id,''),'message:'||newer.id)=
                 coalesce(nullif(?,''),'message:'||?)
                 AND datetime(newer.received_at)>datetime(?) LIMIT 1""",
            (row["conversation_id"], row["inbox_event_id"], row["received_at"]),
        ).fetchone():
            held[item_id] = "a newer inbound message exists"
            continue
        if db.execute(
            """SELECT 1 FROM outreach_sent_messages sent WHERE sent.conversation_id=?
                 AND datetime(sent.sent_at)>datetime(?) LIMIT 1""",
            (row["conversation_id"], row["received_at"]),
        ).fetchone():
            held[item_id] = "the conversation already has a newer outbound reply"
            continue
        if db.execute(
            """SELECT 1 FROM approved_reply_batch_log WHERE work_item_id=?
                 AND status IN ('sending','sent','delivery_unknown') LIMIT 1""",
            (item_id,),
        ).fetchone():
            held[item_id] = "batch delivery log already exists"
            continue
        if db.execute(
            "SELECT 1 FROM coupon_reply_log WHERE inbox_event_id=? AND status IN ('sending','sent') LIMIT 1",
            (row["inbox_event_id"],),
        ).fetchone():
            held[item_id] = "coupon delivery log already exists"
            continue
        if db.execute(
            "SELECT 1 FROM safe_workflow_reply_log WHERE work_item_id=? AND status IN ('sending','sent','delivery_unknown') LIMIT 1",
            (item_id,),
        ).fetchone():
            held[item_id] = "safe-workflow delivery log already exists"
            continue
        try:
            extracted = json.loads(str(row["extracted_json"] or "{}"))
        except json.JSONDecodeError:
            held[item_id] = "context audit JSON is invalid"
            continue
        if not extracted.get("context_complete"):
            held[item_id] = "context audit is incomplete"
            continue
        if extracted.get("template_gap"):
            held[item_id] = "context audit reports a template gap"
            continue
        if extracted.get("questions") and row["template_key"] not in {"coupon_test_campaign_details", "campaign_details_after_coupon"}:
            held[item_id] = "the newest message has an unresolved question"
            continue
        if extracted.get("contradictions"):
            held[item_id] = "the conversation has a contradiction"
            continue
        if extracted.get("conversation_state") in {"ambiguous", "conflicts_with_prior"}:
            held[item_id] = "conversation state is ambiguous or conflicting"
            continue
        if extracted.get("template_key") != row["template_key"]:
            held[item_id] = "audited template no longer matches the work item"
            continue
        if row["template_key"] == "coupon_test_campaign_details":
            live_intents = set(extracted.get("secondary_intents") or [])
            if not live_intents or not live_intents.issubset({"asks_campaign_details", "wants_test", "activation_help"}):
                held[item_id] = "test-first campaign template has another live intent"
                continue
            if row["coupon_sent_at"] or str(row["membership_status"] or "") in {"active", "trialing"}:
                held[item_id] = "test access or membership is already active"
                continue
            if str(row["usage_verification_status"] or "") in {"started", "completed"} or str(row["test_status"] or "") in {"claimed", "completed"}:
                held[item_id] = "product testing has already started or completed"
                continue
            if row["rate_info"] or row["price_dedicated"] or row["price_integration"] or row["price_shorts"]:
                held[item_id] = "creator pricing is already present and requires a different template"
                continue
        if row["template_key"] == "campaign_details_after_coupon":
            live_intents = set(extracted.get("secondary_intents") or [])
            if live_intents != {"asks_campaign_details"}:
                held[item_id] = "post-coupon campaign template has another live intent"
                continue
            if not row["coupon_sent_at"]:
                held[item_id] = "test code was not previously sent"
                continue
            if str(row["membership_status"] or "") in {"active", "trialing"}:
                held[item_id] = "membership is already active"
                continue
            if str(row["usage_verification_status"] or "") in {"started", "completed"} or str(row["test_status"] or "") in {"claimed", "completed"}:
                held[item_id] = "product testing has already started or completed"
                continue
            if row["rate_info"] or row["price_dedicated"] or row["price_integration"] or row["price_shorts"]:
                held[item_id] = "creator pricing is already present and requires a different template"
                continue
        message = current_message(str(row["body_text"] or row["body_preview"] or ""))
        reason = pre_send_hold_reason(str(row["template_key"] or ""), message)
        if reason:
            held[item_id] = reason
            continue
        _, approved_body = render(row)
        if str(row["draft_body"] or "").strip() != approved_body.strip():
            held[item_id] = "draft body differs from the current approved template version"
            continue
        if db.execute("SELECT datetime(?)>datetime('now',?)", (row["received_at"], age_modifier)).fetchone()[0]:
            held[item_id] = f"reply is not older than {older_than_hours:g} hours"
            continue
        safe.append(row)
    return safe, held


def record_audit(db: sqlite3.Connection, action: str, item_id: int, detail: dict[str, Any]) -> None:
    db.execute(
        """INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
           VALUES(?,'work_item',?,'operator_approved_template_batch',?,datetime('now'))""",
        (action, str(item_id), json.dumps(detail, ensure_ascii=False)),
    )


def record_sending(db: sqlite3.Connection, row: sqlite3.Row, *, gmail=False) -> None:
    if gmail:
        from scripts.gmail_reviewed_reply import validate_route
        validate_route(db, row)
    elif reason := microsoft_outbound_block_reason(db):
        raise RuntimeError(f"sender_restricted: {reason}")
    now = iso_utc()
    db.execute(
        """INSERT INTO approved_reply_batch_log(
             work_item_id,inbox_event_id,contact_id,template_key,template_version,status,queued_at,updated_at
           ) VALUES(?,?,?,?,?,'sending',?,?)""",
        (int(row["id"]), row["inbox_event_id"], row["contact_id"], row["template_key"], int(row["template_version"]), now, now),
    )
    record_audit(db, "operator_approved_reply_sending", int(row["id"]), {
        "template_key": row["template_key"], "template_version": int(row["template_version"]),
    })
    db.commit()


def record_result(db: sqlite3.Connection, row: sqlite3.Row, ok: bool, detail: str) -> None:
    item_id = int(row["id"])
    now = iso_utc()
    db.execute(
        """UPDATE approved_reply_batch_log SET status=?,sent_at=?,error=?,updated_at=? WHERE work_item_id=?""",
        ("sent" if ok else "delivery_unknown", now if ok else None, None if ok else detail[:1000], now, item_id),
    )
    record_audit(db, "operator_approved_reply_sent" if ok else "operator_approved_reply_failed", item_id, {
        "template_key": row["template_key"], "template_version": int(row["template_version"]),
        "result": detail[:300],
    })
    if ok:
        db.execute(
            """UPDATE outreach_work_items SET status='sent',resolved_at=datetime('now'),
                 resolved_by='operator_approved_template_batch',updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (item_id,),
        )
        if row["template_key"] in {"coupon_test", "coupon_test_t2v_knowledge_v1", "coupon_test_rate_received", "coupon_test_campaign_details", "offer_trial_first_20260905", "budget_under_5000_trial_20260913", "reviewed_scope_with_code_20260913", "reviewed_rate_scope_with_code_20260921", "reviewed_arabic_code_20260913", "reviewed_affiliate_code_20260913"}:
            db.execute(
                """INSERT INTO coupon_reply_log(
                     inbox_event_id,contact_id,recipient_email,conversation_id,variant,status,queued_at,sent_at
                   ) SELECT ?,?,e.sender_email,?,'operator_approved','sent',?,?
                     FROM inbox_events e WHERE e.id=?
                   ON CONFLICT(inbox_event_id) DO UPDATE SET status='sent',sent_at=excluded.sent_at,error=NULL""",
                (row["inbox_event_id"], row["contact_id"], row["conversation_id"], now, now, row["inbox_event_id"]),
            )
            db.execute(
                """INSERT INTO creator_profiles(contact_id,workflow_stage,coupon_code,coupon_sent_at,test_status,updated_at)
                   VALUES(?,'testing',?,datetime('now'),'wants_test',datetime('now'))
                   ON CONFLICT(contact_id) DO UPDATE SET workflow_stage='testing',coupon_code=excluded.coupon_code,
                     coupon_sent_at=excluded.coupon_sent_at,
                     test_status=CASE WHEN creator_profiles.test_status IN ('claimed','completed')
                       THEN creator_profiles.test_status ELSE 'wants_test' END,updated_at=datetime('now')""",
                (row["contact_id"], COUPON),
            )
        elif row["template_key"] == "test_planned_ack":
            db.execute(
                """UPDATE creator_profiles SET workflow_stage='testing',
                     test_status=CASE WHEN test_status IN ('claimed','completed') THEN test_status ELSE 'wants_test' END,
                     updated_at=datetime('now') WHERE contact_id=?""",
                (row["contact_id"],),
            )
        db.execute(
            """UPDATE contacts SET next_action='等待创作者完成真实测试并反馈。',updated_at=datetime('now') WHERE id=?""",
            (row["contact_id"],),
        )
    db.commit()


def run(args: argparse.Namespace) -> int:
    db = connect(args.db)
    try:
        ensure_schema(db)
        ensure_batch_schema(db)
        delivery_unknown = recover_stale_sending(db)
        rows, held = load_candidates(db, args.ids, args.older_than_hours)
        print(json.dumps({
            "requested": len(args.ids), "ready": len(rows), "held": len(held),
            "delivery_unknown": delivery_unknown,
            "templates": {key: sum(row["template_key"] == key for row in rows) for key in sorted({row["template_key"] for row in rows})},
            "ready_ids": [int(row["id"]) for row in rows], "held_reasons": held,
            "send_enabled": args.send,
        }, ensure_ascii=False), flush=True)
        if not args.send or not rows:
            return 0
        token = refresh_access_token(db)
        token_refreshed_at = time.monotonic()
        sent = failed = 0
        minimum = max(0.0, args.min_interval_seconds)
        maximum = max(minimum, args.max_interval_seconds)
        for index, planned in enumerate(rows):
            live_rows, live_held = load_candidates(db, [int(planned["id"])], args.older_than_hours)
            if not live_rows:
                held[int(planned["id"])] = live_held.get(int(planned["id"]), "failed final pre-send check")
                continue
            row = live_rows[0]
            if time.monotonic() - token_refreshed_at >= 45 * 60:
                token = refresh_access_token(db)
                token_refreshed_at = time.monotonic()
            _, body = render(row)
            record_sending(db, row)
            ok, detail = send_reply(token, str(row["inbox_event_id"]), body)
            if not ok and "HTTP 401" in detail:
                token = refresh_access_token(db)
                token_refreshed_at = time.monotonic()
                ok, detail = send_reply(token, str(row["inbox_event_id"]), body)
            record_result(db, row, ok, detail)
            if ok:
                sent += 1
                print(json.dumps({"sent": sent, "total": len(rows), "id": int(row["id"]), "template": row["template_key"]}), flush=True)
            else:
                failed += 1
                print(json.dumps({"failed_id": int(row["id"]), "error": detail[:500]}), file=sys.stderr, flush=True)
            if index < len(rows) - 1 and maximum > 0:
                time.sleep(random.uniform(minimum, maximum))
        print(json.dumps({"completed_at": iso_utc(), "sent": sent, "failed": failed, "held": held}, ensure_ascii=False), flush=True)
        return 0 if failed == 0 else 1
    finally:
        db.close()


def main() -> int:
    args = parse_args()
    probe = connect(args.db)
    try:
        placeholders = ",".join("?" for _ in args.ids)
        has_coupon = bool(probe.execute(
            f"""SELECT 1 FROM outreach_work_items WHERE id IN ({placeholders})
                 AND template_key IN ('coupon_test','coupon_test_t2v_knowledge_v1','coupon_test_rate_received','coupon_test_campaign_details','campaign_details_after_coupon','offer_trial_first_20260905')
                 LIMIT 1""",
            args.ids,
        ).fetchone())
    finally:
        probe.close()
    lock_paths = [DEFAULT_LOCK, SAFE_WORKFLOW_LOCK]
    if has_coupon:
        lock_paths.append(COUPON_LOCK)
    handles = []
    try:
        for path in lock_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+")
            handles.append(handle)
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"reply sender is busy: {path.name}", file=sys.stderr)
                return 1
        return run(args)
    finally:
        for handle in reversed(handles):
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
