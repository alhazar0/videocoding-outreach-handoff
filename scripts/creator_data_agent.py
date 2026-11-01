#!/usr/bin/env python3
"""Synchronize real product usage and execute approved cardless trial access.

The worker never writes generated email copy. It only uses enabled, human-owned
templates from outreach_templates and calls the product's least-privilege exact
email API. Membership grants are idempotent and the success reply is sent only
after the product backend confirms active access.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token
    from reply_safety import (
        RATE_SIGNAL_RE,
        automation_rule_mode,
        current_message,
        pre_send_hold_reason,
        safety_flags,
    )
    from send_coupon_replies import send_reply
except ModuleNotFoundError:
    from scripts.outreach_scheduler import DEFAULT_DB, connect, ensure_schema, refresh_access_token
    from scripts.reply_safety import (
        RATE_SIGNAL_RE,
        automation_rule_mode,
        current_message,
        pre_send_hold_reason,
        safety_flags,
    )
    from scripts.send_coupon_replies import send_reply


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / ".wrangler" / "creator-data-agent.lock"
KEYCHAIN_SERVICE = "ai.videocoding.product-outreach-token"
KEYCHAIN_ACCOUNT = os.environ.get("USER", "jfg")


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--origin", default=os.environ.get("PRODUCT_OUTREACH_API_ORIGIN", ""))
    parser.add_argument("--usage-limit", type=int, default=100)
    parser.add_argument("--membership-limit", type=int, default=20)
    parser.add_argument("--sync-minutes", type=int, default=15)
    parser.add_argument("--send", action="store_true")
    return parser.parse_args()


def ensure_agent_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS creator_data_agent_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT NOT NULL,
          usage_checked INTEGER NOT NULL DEFAULT 0,
          usage_updated INTEGER NOT NULL DEFAULT 0,
          coupons_redeemed INTEGER NOT NULL DEFAULT 0,
          memberships_granted INTEGER NOT NULL DEFAULT 0,
          template_replies_sent INTEGER NOT NULL DEFAULT 0,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          error TEXT
        );
        CREATE TABLE IF NOT EXISTS promotion_creator_list (
          contact_id INTEGER PRIMARY KEY,
          creator_email TEXT NOT NULL,
          registered_email TEXT,
          channel_name TEXT NOT NULL,
          channel_url TEXT,
          subscribers INTEGER,
          category TEXT,
          country TEXT,
          language TEXT,
          test_status TEXT NOT NULL,
          completed_videos INTEGER NOT NULL DEFAULT 0,
          usage_verified_at TEXT,
          interest_status TEXT NOT NULL DEFAULT 'ready_to_promote',
          interest_evidence TEXT NOT NULL DEFAULT '',
          price_dedicated REAL,
          price_integration REAL,
          price_shorts REAL,
          currency TEXT,
          turnaround TEXT,
          rate_info TEXT,
          quote_source_text TEXT,
          quote_received_at TEXT,
          promotion_formats TEXT,
          list_status TEXT NOT NULL DEFAULT 'waiting',
          added_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS promotion_creator_list_status_idx
          ON promotion_creator_list(list_status,updated_at DESC);
        """
    )
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(creator_profiles)")}
    additions = (
        ("coupon_redemption_status", "TEXT NOT NULL DEFAULT 'not_checked'"),
        ("coupon_redeemed_code", "TEXT"),
        ("coupon_redemption_verified_at", "TEXT"),
        ("usage_failed_tasks", "INTEGER NOT NULL DEFAULT 0"),
        ("usage_canceled_tasks", "INTEGER NOT NULL DEFAULT 0"),
        ("usage_last_seen_at", "TEXT"),
        ("product_sync_error", "TEXT"),
    )
    for name, definition in additions:
        if name not in columns:
            db.execute(f"ALTER TABLE creator_profiles ADD COLUMN {name} {definition}")
    db.execute(
        """INSERT INTO outreach_settings(key,value,updated_at)
           VALUES('creator_data_agent_enabled','1',datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value='1',updated_at=datetime('now')"""
    )
    db.execute(
        """UPDATE creator_data_agent_runs SET status='interrupted',completed_at=datetime('now'),
             error=coalesce(error,'Recovered after worker interruption') WHERE status='running'"""
    )
    db.commit()


def keychain_token() -> str:
    if __package__:
        from .handoff_guard import require_live_opt_in
    else:
        from handoff_guard import require_live_opt_in
    require_live_opt_in()
    explicit = os.environ.get("PRODUCT_OUTREACH_AUTOMATION_TOKEN", "").strip()
    if explicit:
        return explicit
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def api_json(origin: str, token: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    url = origin.rstrip("/") + path
    command = [
        "/usr/bin/curl", "-sS", "--max-time", "25", "--retry", "2", "--retry-delay", "1",
        "-A", "VideoCodingCreatorDataAgent/1.0 (+https://videocoding.ai)",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Accept: application/json",
        "-w", "\n%{http_code}",
    ]
    input_text = None
    if payload is not None:
        command.extend(["-H", "Content-Type: application/json", "--data-binary", "@-"])
        input_text = json.dumps(payload)
    command.append(url)
    completed = subprocess.run(command, input=input_text, text=True, capture_output=True, timeout=35, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[:500] or f"curl_exit_{completed.returncode}")
    response_text, separator, status_text = completed.stdout.rpartition("\n")
    if not separator or not status_text.isdigit():
        raise RuntimeError("product_api_invalid_http_response")
    status = int(status_text)
    try:
        detail = json.loads(response_text) if response_text.strip() else {}
    except json.JSONDecodeError:
        detail = {"error": f"HTTP_{status}"}
    return status, detail


def creator_lookup(origin: str, token: str, email: str) -> tuple[int, dict[str, Any]]:
    query = urllib.parse.urlencode({"email": email})
    return api_json(origin, token, f"/api/internal/outreach/creator?{query}")


def usage_status(creator: dict[str, Any]) -> str:
    activity = creator.get("activity") or {}
    if int(activity.get("completedTasks") or 0) > 0:
        return "completed"
    if int(activity.get("totalTasks") or 0) > 0:
        return "started"
    return "account_only"


def usage_candidates(db: sqlite3.Connection, limit: int, sync_minutes: int) -> list[sqlite3.Row]:
    modifier = f"-{max(1, sync_minutes)} minutes"
    return db.execute(
        """SELECT p.contact_id,p.registered_email,c.email AS contact_email
           FROM creator_profiles p
           JOIN contacts c ON c.id=p.contact_id
           WHERE (p.coupon_sent_at IS NOT NULL OR p.test_status<>'not_started'
                  OR p.membership_status<>'none' OR p.workflow_stage IN ('testing','test_claimed','membership_pending'))
             AND (p.usage_verified_at IS NULL OR datetime(p.usage_verified_at)<=datetime('now',?))
           ORDER BY CASE WHEN p.usage_verified_at IS NULL THEN 0 ELSE 1 END,p.usage_verified_at ASC
           LIMIT ?""",
        (modifier, max(1, limit)),
    ).fetchall()


def apply_usage(db: sqlite3.Connection, contact_id: int, email: str, payload: dict[str, Any]) -> tuple[bool, bool]:
    now = iso_utc()
    if not payload.get("found"):
        db.execute(
            """UPDATE creator_profiles SET account_status='not_found',usage_verification_status='account_not_found',
                 membership_status=CASE WHEN membership_status IN ('active','trialing') THEN 'unverified' ELSE membership_status END,
                 usage_verified_at=?,usage_source='product_outreach_api',product_sync_error=NULL,updated_at=?
               WHERE contact_id=?""",
            (now, now, contact_id),
        )
        return True, False
    creator = payload.get("creator") or {}
    activity = creator.get("activity") or {}
    coupon = creator.get("coupon") or {}
    subscription = creator.get("subscription") or {}
    status = usage_status(creator)
    coupon_status = str(coupon.get("status") or "not_checked")
    if coupon_status == 'not_redeemed' and not coupon.get('checkedAt'):
        coupon_status = 'not_checked'
    completed = int(activity.get("completedTasks") or 0)
    total = int(activity.get("totalTasks") or 0)
    test_status = "completed" if completed > 0 else "testing" if total > 0 else "wants_test"
    db.execute(
        """UPDATE creator_profiles SET
             account_status=CASE WHEN ? THEN 'verified' ELSE 'unverified' END,
             product_user_id=?,usage_verification_status=?,usage_total_tasks=?,usage_active_tasks=?,
             usage_completed_tasks=?,usage_failed_tasks=?,usage_canceled_tasks=?,usage_last_login_at=?,
             usage_last_task_at=?,usage_last_seen_at=?,usage_verified_at=?,usage_source='product_outreach_api',
             coupon_redemption_status=?,coupon_redeemed_code=?,coupon_redemption_verified_at=?,
             membership_status=coalesce(?,membership_status),
             membership_expires_at=CASE WHEN ? THEN ? ELSE membership_expires_at END,test_status=?,
             test_started_at=CASE WHEN ?>0 THEN coalesce(test_started_at,?) ELSE test_started_at END,
             test_completed_at=CASE WHEN ?>0 THEN coalesce(test_completed_at,?) ELSE test_completed_at END,
             product_sync_error=NULL,updated_at=? WHERE contact_id=?""",
        (
            int(bool(creator.get("emailVerified"))), str(creator.get("id") or ""), status, total,
            int(activity.get("activeTasks") or 0), completed, int(activity.get("failedTasks") or 0),
            int(activity.get("canceledTasks") or 0), creator.get("lastLoginAt"), activity.get("lastTaskAt"),
            activity.get("lastSeenAt"), now, coupon_status, coupon.get("code"), now,
            subscription.get("status") or None, int('currentPeriodEnd' in subscription), subscription.get("currentPeriodEnd"), test_status,
            total, now, completed, now, now, contact_id,
        ),
    )
    if not db.execute("SELECT registered_email FROM creator_profiles WHERE contact_id=?", (contact_id,)).fetchone()[0]:
        # The lookup was exact, but this is still product evidence rather than
        # an AI inference. Keep the confirmed account email for future syncs.
        db.execute("UPDATE creator_profiles SET registered_email=? WHERE contact_id=?", (email, contact_id))
    return True, coupon_status == "redeemed"


def sync_usage(db: sqlite3.Connection, origin: str, token: str, limit: int, sync_minutes: int) -> tuple[int, int, int]:
    checked = updated = redeemed = 0
    results: list[tuple[int, str, dict[str, Any] | None, str | None]] = []
    for row in usage_candidates(db, limit, sync_minutes):
        email = str(row["registered_email"] or row["contact_email"] or "").strip().lower()
        if not email:
            continue
        checked += 1
        try:
            status, payload = creator_lookup(origin, token, email)
            if status != 200 or not payload.get("success"):
                raise RuntimeError(str(payload.get("code") or payload.get("error") or f"HTTP_{status}"))
            results.append((int(row["contact_id"]), email, payload, None))
        except Exception as exc:
            results.append((int(row["contact_id"]), email, None, str(exc)[:500]))
    if not results:
        return checked, updated, redeemed
    db.execute("BEGIN IMMEDIATE")
    for contact_id, email, payload, error in results:
        if payload is not None:
            changed, used_coupon = apply_usage(db, contact_id, email, payload)
            updated += int(changed)
            redeemed += int(used_coupon)
        else:
            db.execute(
                "UPDATE creator_profiles SET product_sync_error=?,usage_verified_at=?,updated_at=? WHERE contact_id=?",
                (error, iso_utc(), iso_utc(), contact_id),
            )
    db.commit()
    return checked, updated, redeemed


def reconcile_verified_tests(db: sqlite3.Connection) -> int:
    """Advance verified tests to a review draft without sending email."""
    rows = db.execute(
        """SELECT w.id,w.contact_id,w.kind,w.inbox_event_id,w.subject,e.body_text,e.body_preview,
                  c.rate_info,
                  EXISTS(SELECT 1 FROM outreach_work_items priced
                         WHERE priced.contact_id=w.contact_id
                           AND priced.kind IN ('price_received','price_and_test_claim')) AS has_price_item
           FROM outreach_work_items w
           JOIN creator_profiles p ON p.contact_id=w.contact_id
           JOIN contacts c ON c.id=w.contact_id
           LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
           WHERE w.status='pending' AND p.usage_verification_status='completed'
             AND w.workflow_stage NOT IN (
               'marketing_review','price_needed','human_review_blocked','delivery_reconciliation'
             )
             AND w.id=(SELECT MAX(recent.id) FROM outreach_work_items recent
                       WHERE recent.contact_id=w.contact_id AND recent.status='pending')
             AND w.kind IN ('test_claim','price_and_test_claim')"""
    ).fetchall()
    routed = 0
    for row in rows:
        body = current_message(str(row["body_text"] or row["body_preview"] or ""))
        flags = set(safety_flags(body))
        has_price = bool(
            row["has_price_item"]
            or str(row["rate_info"] or "").strip()
            or RATE_SIGNAL_RE.search(body)
        )
        blocking_flags = flags.intersection({"rejection", "product_issue", "conditional_deal", "campaign_question"})
        if blocking_flags:
            template_key = None
            kind = "human_review"
            stage = "marketing_review"
            risk = "high"
            reason = pre_send_hold_reason("ask_rate", body) or "当前来信包含需要人工处理的高优先级信号。"
            marketing_status = "needs_review"
            next_action = "人工处理拒绝、产品反馈或合作条件；不得自动询价或推进合作。"
        elif has_price:
            template_key = "marketing_listed"
            kind = "price_received"
            stage = "marketing_review"
            risk = "high"
            reason = "产品后台已确认至少一个成功视频，且当前或历史会话已有报价；进入营销名单人工确认。"
            marketing_status = "listed"
            next_action = "人工确认营销名单回复；不得自动承诺预算或合作。"
        else:
            template_key = "ask_rate"
            kind = "test_verified"
            stage = "price_needed"
            risk = "medium"
            reason = "产品后台已确认至少一个成功视频；使用人工设定的询价模板，等待审核。"
            marketing_status = "price_needed"
            next_action = "审核询价草稿；只有规则明确允许时才可发送。"
        subject = draft_body = ""
        if template_key:
            template = db.execute(
                "SELECT subject,body FROM outreach_templates WHERE template_key=? AND enabled=1",
                (template_key,),
            ).fetchone()
            if not template:
                template_key = None
                kind = "human_review"
                stage = "marketing_review"
                risk = "high"
                reason = "真实测试已确认，但所需人工模板不存在或未启用；转人工。"
                marketing_status = "needs_review"
            else:
                original_subject = str(row["subject"] or "VideoCoding collaboration")
                subject = str(template["subject"] or "").replace("{{original_subject}}", original_subject)
                draft_body = str(template["body"] or "")
        db.execute(
            """UPDATE outreach_work_items SET kind=?,workflow_stage=?,risk_level=?,template_key=?,
                 subject=?,draft_body=?,decision_reason=?,updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (kind, stage, risk, template_key, subject, draft_body, reason, int(row["id"])),
        )
        db.execute(
            """UPDATE outreach_work_items SET status='superseded',resolved_at=datetime('now'),
                 resolved_by='creator_data_agent',updated_at=datetime('now')
               WHERE contact_id=? AND status='pending' AND id<>?
                 AND kind IN ('test_claim','price_and_test_claim','test_planned','human_review')""",
            (int(row["contact_id"]), int(row["id"])),
        )
        db.execute(
            """UPDATE creator_profiles SET workflow_stage=?,test_status='completed',marketing_status=?,
                 updated_at=datetime('now') WHERE contact_id=?""",
            (stage, marketing_status, int(row["contact_id"])),
        )
        db.execute(
            "UPDATE contacts SET next_action=?,updated_at=datetime('now') WHERE id=?",
            (next_action, int(row["contact_id"])),
        )
        audit(db, "verified_test_routed", int(row["id"]), {
            "contact_id": int(row["contact_id"]), "template": template_key, "stage": stage,
            "safety_flags": sorted(flags),
        })
        routed += 1
    if routed:
        db.commit()
    return routed


def hold_progressed_membership_requests(db: sqlite3.Connection) -> int:
    """Account progress is not proof that the creator's latest question was answered.

    Keep these requests visible for contextual review, without permitting another
    automatic grant. Age alone must never resolve an unanswered request.
    """
    rows = db.execute(
        """SELECT w.id FROM outreach_work_items w
           LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
           WHERE w.kind='membership_request' AND w.status='pending'
             AND (coalesce(p.membership_status,'none') IN ('active','trialing')
                  OR coalesce(p.usage_verification_status,'') IN ('started','completed')
                  OR coalesce(p.test_status,'') IN ('claimed','completed'))"""
    ).fetchall()
    for row in rows:
        db.execute(
            """UPDATE outreach_work_items SET kind='human_review',workflow_stage='human_review_blocked',
                 risk_level='high',template_key=NULL,draft_body='',resolved_at=NULL,resolved_by=NULL,
                 decision_reason='账号状态已推进，但不能证明最新诉求已解决；保留待审核，禁止重复开通或自动回复。',
                 updated_at=datetime('now') WHERE id=? AND status='pending'""",
            (int(row["id"]),),
        )
        audit(db, "progressed_membership_request_held", int(row["id"]), {})
    if rows:
        db.commit()
    return len(rows)


PROMOTION_INTEREST_RE = re.compile(
    r"(?:happy|ready|interested|love|would like)\s+(?:to\s+)?(?:move forward|proceed|collaborate|promote)|"
    r"paid collaboration|sponsored (?:video|integration)|campaign (?:brief|next steps)",
    re.I,
)


def money_near(text: str, labels: tuple[str, ...]) -> tuple[float | None, str | None]:
    label_group = r"(?<![A-Za-z])(?:" + "|".join(re.escape(label) for label in labels) + r")(?![A-Za-z])"
    currency_token = r"(?<![A-Za-z])(?:US\$|CA\$|AU\$|NZ\$|HK\$|SG\$|USD|GBP|EUR|INR|CAD|AUD|NZD|HKD|SGD|£|€|₹|\$)"
    patterns = (
        rf"(?P<label>{label_group})[^\n\r;]{{0,100}}?(?P<currency>{currency_token})\s*(?P<amount>[0-9][0-9,.]*)",
        rf"(?P<currency>{currency_token})\s*(?P<amount>[0-9][0-9,.]*)[^\n\r;]{{0,100}}?(?P<label>{label_group})",
    )
    currency_map = {"$": None, "£": "GBP", "€": "EUR", "₹": "INR",
                    "US$": "USD", "CA$": "CAD", "AU$": "AUD",
                    "NZ$": "NZD", "HK$": "HKD", "SG$": "SGD"}
    matches = [m for pattern in patterns for m in re.finditer(pattern, text, flags=re.I)]
    # Prefer the amount next to the format, rather than an unrelated later
    # package price that happens to supply an explicit currency.
    matches.sort(key=lambda m: (max(m.start("currency") - m.end("label"),
                                   m.start("label") - m.end("amount")),
                                m.start("currency")))
    for match in matches:
        # A format mentioned in a bundle is not a unit quote for that format.
        # Keep it in the source text for review instead of refilling a scalar
        # price that an operator deliberately cleared after reading the offer.
        span_start = min(match.start('label'), match.start('currency'))
        span_end = max(match.end('label'), match.end('amount'))
        span = text[span_start:span_end]
        tail = text[match.end('amount'):match.end('amount') + 100]
        if (re.search(r'\b(package|bundle|combined|combination)\b', span, re.I)
                or re.search(r'\b(?:two|three|four|five|[2-9])\s+(?:\w+[ -]){0,2}(?:shorts?|videos?)\b', span, re.I)
                or re.match(r'\s*(?:USD|CAD|AUD|EUR|GBP)?\s*(?:for\s+)?(?:the\s+)?(?:complete\s+|entire\s+)?(?:package|bundle)\b', tail, re.I)):
            continue
        try:
            amount = float(match.group("amount").replace(",", ""))
        except ValueError:
            continue
        raw_currency = match.group("currency").upper()
        if raw_currency == "$":
            # A dollar sign alone does not identify the currency. Accept an
            # explicit code immediately after this amount, not elsewhere in
            # the conversation or from a different quoted format.
            suffix = re.match(r"\s*(?:\(\s*)?(USD|CAD|AUD|NZD|HKD|SGD)\b",
                              text[match.end("amount"):], flags=re.I)
            return amount, suffix.group(1).upper() if suffix else None
        return amount, currency_map.get(raw_currency, raw_currency)
    return None, None


def refresh_promotion_creator_list(db: sqlite3.Connection) -> int:
    try:
        from reviewed_contact_hold import active_hold
    except ImportError:
        from scripts.reviewed_contact_hold import active_hold
    candidates = db.execute(
        """SELECT p.*,c.email AS creator_email,c.channel_name,c.channel_url,c.subscribers,c.category,
                  c.country,c.language,c.rate_info,c.reply_status,c.do_not_contact
           FROM creator_profiles p JOIN contacts c ON c.id=p.contact_id
           WHERE p.usage_verification_status='completed' AND p.usage_completed_tasks>0
             AND c.do_not_contact=0 AND c.reply_status<>'not_interested'
             AND (p.marketing_status IN ('listed','review_list')
                  OR EXISTS(SELECT 1 FROM outreach_work_items w WHERE w.contact_id=p.contact_id
                            AND w.kind IN ('price_received','price_and_test_claim'))
                  OR EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=p.contact_id AND (
                       lower(coalesce(e.body_text,e.body_preview,'')) LIKE '%move forward%'
                    OR lower(coalesce(e.body_text,e.body_preview,'')) LIKE '%paid collaboration%'
                    OR lower(coalesce(e.body_text,e.body_preview,'')) LIKE '%happy to promote%'
                    OR lower(coalesce(e.body_text,e.body_preview,'')) LIKE '%ready to promote%')))
           ORDER BY p.updated_at DESC"""
    ).fetchall()
    now = iso_utc()
    refreshed = 0
    for row in candidates:
        hold = active_hold(db, row['contact_id'])
        if hold:
            db.execute("""UPDATE promotion_creator_list SET list_status='held',
                interest_status='needs_review',interest_evidence=?,updated_at=? WHERE contact_id=?""",
                (hold, now, int(row['contact_id'])))
            continue
        evidence_rows = db.execute(
            """SELECT body_text,body_preview,received_at FROM inbox_events
               WHERE contact_id=? ORDER BY received_at DESC LIMIT 20""",
            (int(row["contact_id"]),),
        ).fetchall()
        quote_text = ""
        quote_at = None
        interest_text = ""
        for evidence in evidence_rows:
            text = str(evidence["body_text"] or evidence["body_preview"] or "").strip()
            if not interest_text and PROMOTION_INTEREST_RE.search(text):
                interest_text = text
            if not quote_text and RATE_SIGNAL_RE.search(text):
                quote_text = text
                quote_at = evidence["received_at"]
        dedicated, dedicated_currency = money_near(quote_text, ("dedicated", "dedicated video", "sponsored video"))
        integration, integration_currency = money_near(
            quote_text, ("integration", "mention", "natural mention", "sponsored mention")
        )
        shorts, shorts_currency = money_near(quote_text, ("short", "shorts", "youtube short"))
        dedicated = row["price_dedicated"] if row["price_dedicated"] is not None else dedicated
        integration = row["price_integration"] if row["price_integration"] is not None else integration
        shorts = row["price_shorts"] if row["price_shorts"] is not None else shorts
        currency = row["currency"] or dedicated_currency or integration_currency or shorts_currency or None
        formats = []
        if dedicated is not None or re.search(r"dedicated|sponsored video", quote_text, re.I):
            formats.append("dedicated_video")
        if integration is not None or re.search(r"integration|mention", quote_text, re.I):
            formats.append("integration")
        if shorts is not None or re.search(r"\bshorts?\b", quote_text, re.I):
            formats.append("shorts")
        interest_evidence = (interest_text or quote_text or "真实测试完成，且会话已进入营销评估。")[:5000]
        db.execute(
            """INSERT INTO promotion_creator_list(
                 contact_id,creator_email,registered_email,channel_name,channel_url,subscribers,category,country,language,
                 test_status,completed_videos,usage_verified_at,interest_status,interest_evidence,
                 price_dedicated,price_integration,price_shorts,currency,turnaround,rate_info,quote_source_text,
                 quote_received_at,promotion_formats,list_status,added_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,'completed',?,?,'ready_to_promote',?,?,?,?,?,?,?,?,?,?,'waiting',?,?)
               ON CONFLICT(contact_id) DO UPDATE SET
                 creator_email=excluded.creator_email,registered_email=excluded.registered_email,
                 channel_name=excluded.channel_name,channel_url=excluded.channel_url,subscribers=excluded.subscribers,
                 category=excluded.category,country=excluded.country,language=excluded.language,
                 test_status='completed',completed_videos=excluded.completed_videos,
                 usage_verified_at=excluded.usage_verified_at,interest_status='ready_to_promote',
                 interest_evidence=excluded.interest_evidence,
                 price_dedicated=coalesce(excluded.price_dedicated,promotion_creator_list.price_dedicated),
                 price_integration=coalesce(excluded.price_integration,promotion_creator_list.price_integration),
                 price_shorts=coalesce(excluded.price_shorts,promotion_creator_list.price_shorts),
                 currency=coalesce(excluded.currency,promotion_creator_list.currency),
                 turnaround=coalesce(excluded.turnaround,promotion_creator_list.turnaround),
                 rate_info=coalesce(nullif(excluded.rate_info,''),promotion_creator_list.rate_info),
                 quote_source_text=coalesce(nullif(excluded.quote_source_text,''),promotion_creator_list.quote_source_text),
                 quote_received_at=coalesce(excluded.quote_received_at,promotion_creator_list.quote_received_at),
                 promotion_formats=coalesce(nullif(excluded.promotion_formats,''),promotion_creator_list.promotion_formats),
                 list_status=CASE WHEN promotion_creator_list.list_status IN ('approved','launched')
                                  THEN promotion_creator_list.list_status ELSE 'waiting' END,
                 updated_at=excluded.updated_at""",
            (
                int(row["contact_id"]), str(row["creator_email"]), row["registered_email"],
                str(row["channel_name"]), row["channel_url"], row["subscribers"], row["category"],
                row["country"], row["language"], int(row["usage_completed_tasks"] or 0),
                row["usage_verified_at"], interest_evidence, dedicated, integration, shorts, currency,
                row["turnaround"], row["rate_info"], quote_text[:10000], quote_at,
                ",".join(formats), now, now,
            ),
        )
        # Preserve structured prices for campaign launch even when the original
        # reply was captured before the operator entered the rate form.
        db.execute(
            """UPDATE creator_profiles SET price_dedicated=coalesce(price_dedicated,?),
                 price_integration=coalesce(price_integration,?),price_shorts=coalesce(price_shorts,?),
                 currency=coalesce(currency,?),marketing_status='listed',updated_at=datetime('now')
               WHERE contact_id=?""",
            (dedicated, integration, shorts, currency, int(row["contact_id"])),
        )
        refreshed += 1
    if refreshed:
        db.commit()
    return refreshed


def render_template(body: str, registered_email: str) -> str:
    return body.replace("{{registered_email}}", registered_email)


def confirmed_month_grant(subscription: dict[str, Any]) -> bool:
    """A generic active subscription is not proof of a new free month."""
    if subscription.get("status") not in {"active", "trialing"} or subscription.get("manualGrant") is not True:
        return False
    try:
        expires = datetime.fromisoformat(str(subscription.get("currentPeriodEnd") or "").replace("Z", "+00:00"))
        if expires.tzinfo is None:
            return False
        days_remaining = (expires - datetime.now(timezone.utc)).total_seconds() / 86400
    except (ValueError, TypeError):
        return False
    return 29 <= days_remaining <= 31


def membership_candidates(db: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    rows = db.execute(
        """SELECT w.id,w.inbox_event_id,w.contact_id,w.template_key,w.confidence,w.risk_level,
                  coalesce(nullif(json_extract(w.extracted_json,'$.registered_email'),''),nullif(p.registered_email,'')) AS registered_email,
                  p.membership_status,p.test_status,p.workflow_stage,p.usage_verification_status,w.extracted_json,t.body
           FROM outreach_work_items w
           JOIN creator_profiles p ON p.contact_id=w.contact_id
           JOIN inbox_events e ON e.id=w.inbox_event_id
           LEFT JOIN outreach_templates t ON t.template_key=w.template_key AND t.enabled=1
           WHERE w.kind='membership_request' AND w.status='pending' AND w.confidence>=0.90
             AND w.risk_level='high' AND w.template_key IN ('ask_registered_email','account_not_found','membership_activated')
             AND NOT EXISTS(
               SELECT 1 FROM inbox_events newer
               WHERE newer.contact_id=w.contact_id
                 AND (datetime(newer.received_at)>datetime(e.received_at)
                   OR (datetime(newer.received_at)=datetime(e.received_at) AND newer.id>e.id))
             )
             AND coalesce(p.membership_status,'none') NOT IN ('active','trialing')
             AND coalesce(p.usage_verification_status,'not_checked') NOT IN ('started','completed')
             AND coalesce(p.test_status,'not_started') NOT IN ('claimed','completed')
             AND coalesce(p.workflow_stage,'') NOT IN ('test_claimed','price_received','price_received_unverified','marketing_review')
           ORDER BY w.created_at ASC LIMIT ?""",
        (max(1, limit) * 2,),
    ).fetchall()
    human_only_terms = (
        "budget", "deliverable", "timeline", "campaign", "rate", "usage terms",
        "language", "english", "disclosure", "promotion involve", "specific angle", "use case",
    )
    safe: list[sqlite3.Row] = []
    for row in rows:
        try:
            extracted = json.loads(str(row["extracted_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        questions = " ".join(str(question).lower() for question in extracted.get("questions", []) if question)
        if any(term in questions for term in human_only_terms):
            continue
        safe.append(row)
        if len(safe) >= max(1, limit):
            break
    return safe


def audit(db: sqlite3.Connection, action: str, entity_id: int, detail: dict[str, Any]) -> None:
    db.execute(
        """INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
           VALUES(?,'work_item',?,'creator_data_agent',?,datetime('now'))""",
        (action, str(entity_id), json.dumps(detail, ensure_ascii=False, default=str)),
    )


def send_fixed_reply(db: sqlite3.Connection, access_token: str, row: sqlite3.Row, template_key: str, email: str) -> bool:
    event = db.execute(
        "SELECT body_text,body_preview FROM inbox_events WHERE id=?",
        (str(row["inbox_event_id"]),),
    ).fetchone()
    message = current_message(str((event["body_text"] or event["body_preview"] or "") if event else ""))
    hold_reason = pre_send_hold_reason(template_key, message)
    if hold_reason:
        db.execute(
            """UPDATE outreach_work_items SET kind='human_review',workflow_stage='human_review_blocked',risk_level='high',
                 template_key=NULL,draft_body='',decision_reason=?,updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (hold_reason, int(row["id"])),
        )
        audit(db, "membership_template_held_by_safety", int(row["id"]), {
            "template": template_key,
            "reason": hold_reason,
        })
        db.commit()
        return False
    template = db.execute(
        "SELECT body FROM outreach_templates WHERE template_key=? AND enabled=1 AND approval_status='approved'",
        (template_key,),
    ).fetchone()
    if not template:
        return False
    body = render_template(str(template[0]), email)
    ok, detail = send_reply(access_token, str(row["inbox_event_id"]), body)
    if not ok:
        audit(db, "membership_template_send_failed", int(row["id"]), {"template": template_key, "error": detail[:500]})
        db.commit()
        return False
    db.execute(
        """UPDATE outreach_work_items SET status='sent',template_key=?,draft_body=?,resolved_at=datetime('now'),
             resolved_by='creator_data_agent',updated_at=datetime('now') WHERE id=? AND status='pending'""",
        (template_key, body, int(row["id"])),
    )
    audit(db, "membership_template_sent", int(row["id"]), {"template": template_key})
    db.commit()
    return True


def process_memberships(db: sqlite3.Connection, origin: str, token: str, limit: int, send: bool) -> tuple[int, int]:
    if not send or automation_rule_mode(db, "membership_grant") != "auto":
        return 0, 0
    # Check approval before any entitlement mutation, not merely before email.
    approved = db.execute(
        """SELECT COUNT(*) FROM outreach_templates WHERE enabled=1 AND approval_status='approved'
           AND template_key IN ('ask_registered_email','account_not_found','membership_activated')"""
    ).fetchone()[0]
    if approved != 3:
        return 0, 0
    rows = membership_candidates(db, limit)
    if not rows:
        return 0, 0
    access_token = refresh_access_token(db)
    granted = replies = 0
    for row in rows:
        registered_email = str(row["registered_email"] or "").strip().lower()
        if not registered_email:
            if send_fixed_reply(db, access_token, row, "ask_registered_email", ""):
                replies += 1
                time.sleep(2)
            continue
        db.execute(
            """UPDATE creator_profiles SET registered_email=coalesce(nullif(registered_email,''),?),
                 workflow_stage='membership_pending',updated_at=datetime('now') WHERE contact_id=?""",
            (registered_email, int(row["contact_id"])),
        )
        db.commit()
        status, payload = creator_lookup(origin, token, registered_email)
        if status != 200 or not payload.get("success"):
            audit(db, "membership_lookup_failed", int(row["id"]), {"status": status, "error": payload.get("code") or payload.get("error")})
            db.commit()
            continue
        if not payload.get("found"):
            if send_fixed_reply(db, access_token, row, "account_not_found", registered_email):
                replies += 1
                time.sleep(2)
            continue
        creator = payload.get("creator") or {}
        if not creator.get("emailVerified"):
            audit(db, "membership_held_unverified_email", int(row["id"]), {"registered_email": registered_email})
            db.commit()
            continue
        subscription = creator.get("subscription") or {}
        active = str(subscription.get("status") or "") in {"active", "trialing"}
        if active:
            audit(db, "membership_existing_access_needs_review", int(row["id"]), {})
            db.commit()
            continue
        if not active:
            idem = hashlib.sha256(f"creator-data-agent:{row['id']}:{registered_email}".encode("utf-8")).hexdigest()
            grant_status, grant_payload = api_json(
                origin,
                token,
                "/api/internal/outreach/membership-grants",
                {
                    "email": registered_email,
                    "reason": "Creator requested a cardless VideoCoding product test",
                    "idempotencyKey": idem,
                },
            )
            if grant_status != 200 or not grant_payload.get("success"):
                audit(db, "membership_grant_held", int(row["id"]), {"status": grant_status, "code": grant_payload.get("code") or grant_payload.get("error")})
                db.commit()
                continue
            subscription = grant_payload.get("subscription") or {}
            granted += 1
        if not confirmed_month_grant(subscription):
            audit(db, "membership_grant_confirmation_incomplete", int(row["id"]), {
                "reason": "Backend must confirm manual active access and a one-month expiry before activated copy can be sent."
            })
            db.commit()
            continue
        expires = subscription.get("currentPeriodEnd")
        db.execute(
            """UPDATE creator_profiles SET workflow_stage='testing',account_status='verified',membership_status='active',
                 membership_expires_at=coalesce(?,membership_expires_at),test_status='wants_test',
                 test_started_at=coalesce(test_started_at,datetime('now')),updated_at=datetime('now') WHERE contact_id=?""",
            (expires, int(row["contact_id"])),
        )
        audit(db, "membership_backend_confirmed", int(row["id"]), {"registered_email": registered_email, "expires_at": expires})
        db.commit()
        if send_fixed_reply(db, access_token, row, "membership_activated", registered_email):
            replies += 1
        time.sleep(1)
    return granted, replies


def run(args: argparse.Namespace) -> int:
    db = connect(args.db)
    ensure_schema(db)
    ensure_agent_schema(db)
    origin = args.origin.strip().rstrip("/")
    token = keychain_token()
    configured = bool(origin and len(token) >= 32)
    db.execute(
        """INSERT INTO outreach_settings(key,value,updated_at) VALUES('product_data_agent_configured',?,datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
        ("1" if configured else "0",),
    )
    started = iso_utc()
    cursor = db.execute("INSERT INTO creator_data_agent_runs(status,started_at) VALUES('running',?)", (started,))
    run_id = int(cursor.lastrowid)
    db.commit()
    if not configured:
        db.execute(
            "UPDATE creator_data_agent_runs SET status='waiting_for_configuration',completed_at=? WHERE id=?",
            (iso_utc(), run_id),
        )
        db.commit()
        print(json.dumps({"configured": False, "usage_checked": 0, "memberships_granted": 0}, ensure_ascii=False))
        return 0
    try:
        checked, updated, redeemed = sync_usage(db, origin, token, args.usage_limit, args.sync_minutes)
        held_memberships = hold_progressed_membership_requests(db)
        verified_tests_routed = reconcile_verified_tests(db)
        promotion_creators_refreshed = refresh_promotion_creator_list(db)
        granted, replies = process_memberships(db, origin, token, args.membership_limit, args.send)
        completed = iso_utc()
        db.execute(
            """UPDATE creator_data_agent_runs SET status='completed',usage_checked=?,usage_updated=?,coupons_redeemed=?,
                 memberships_granted=?,template_replies_sent=?,completed_at=? WHERE id=?""",
            (checked, updated, redeemed, granted, replies, completed, run_id),
        )
        db.commit()
        print(json.dumps({
            "configured": True,
            "usage_checked": checked,
            "usage_updated": updated,
            "coupons_redeemed": redeemed,
            "verified_tests_routed": verified_tests_routed,
            "progressed_memberships_held": held_memberships,
            "promotion_creators_refreshed": promotion_creators_refreshed,
            "memberships_granted": granted,
            "template_replies_sent": replies,
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        db.execute(
            "UPDATE creator_data_agent_runs SET status='failed',completed_at=?,error=? WHERE id=?",
            (iso_utc(), str(exc)[:1000], run_id),
        )
        db.commit()
        raise


def main() -> int:
    args = parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            return run(args)
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
