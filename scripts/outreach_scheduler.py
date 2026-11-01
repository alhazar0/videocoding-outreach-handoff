#!/usr/bin/env python3
"""Send the VideoCoding creator campaign through approved mailboxes.

Microsoft Graph is the 700/day human-reply channel for the current campaign. A separate Google
Workspace mailbox can be enabled as a deliberately slow warm-up channel after
SPF, DKIM, DMARC, and its local Keychain credential are ready. Both channels
write to the same audit log and route replies to team@videocoding.ai.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import random
import re
import socket
import smtplib
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time as clock_time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__:
    from .reply_safety import automation_rule_mode, template_automation_mode
    from .mail_delivery import block_reason, is_graph_delivery
else:
    from reply_safety import automation_rule_mode, template_automation_mode
    from mail_delivery import block_reason, is_graph_delivery


MICROSOFT_CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "00000000-0000-0000-0000-000000000001")
MICROSOFT_TENANT_ID = os.environ.get("MICROSOFT_TENANT_ID", "00000000-0000-0000-0000-000000000002")
MICROSOFT_SCOPES = "openid offline_access User.Read Mail.Read Mail.Send"
MAILBOX = "team@videocoding.ai"
GOOGLE_MAILBOX = "creator@outreach.videocoding.ai"
GOOGLE_PROVIDER = "google_workspace_smtp"
GOOGLE_KEYCHAIN_SERVICE = "videocoding-outreach-gmail-smtp"
SUBJECT = "A paid collaboration with VideoCoding"
GENERATIVE_TEMPLATE = "creator-initial-human-v3"
DAILY_TARGET = 700
DAILY_HARD_CAP = 700
GOOGLE_DAILY_TARGET = 50
GOOGLE_DAILY_HARD_CAP = 50
TOTAL_DAILY_TARGET = DAILY_TARGET + GOOGLE_DAILY_TARGET
CAMPAIGN_ZONE = ZoneInfo("America/New_York")
DEFAULT_MIN_INTERVAL_SECONDS = 45.0
DEFAULT_MAX_INTERVAL_SECONDS = 60.0
GOOGLE_MIN_INTERVAL_SECONDS = 120.0
GOOGLE_MAX_INTERVAL_SECONDS = 120.0
SEND_WINDOW_HOURS = 13
CONSECUTIVE_FAILURE_LIMIT = 3
TARGET_DAILY_CAPACITY = 1_000
WINNING_COPY_EXPERIMENT_ID = "first-contact-winner-rollout-2026-08"
WINNING_COPY_VARIANT = "first-contact-v1"
QUEUE_POLICY_VERSION = "approved-evidence-700-plus-50-v12-single-planner"
SEGMENTED_COPY_START_DAY = "2026-08-27"
T2V_KNOWLEDGE_TEMPLATE_KEY = "first_contact_t2v_knowledge_v1"
T2V_KNOWLEDGE_EXPERIMENT_ID = "segmented-first-contact-2026-08"
T2V_KNOWLEDGE_VARIANT = "t2v-knowledge-v1"
T2V_KNOWLEDGE_BODY = """Hi {{channel_name}},

I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you.

With just a short prompt, VideoCoding can generate a complete video up to 10 minutes long—from research and scripting to narration, scenes, motion, music, and the final edit.

You can see some examples here:
https://videocoding.ai/?mode=t2v

I think it could be a useful fit for the kind of knowledge videos you create. If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0.

After you try it with one real topic, we can discuss the promotion format and your rate.

Best,
Alhazar Anwar
VideoCoding"""

COUNTRY_TIMEZONES = {
    "australia": "Australia/Sydney",
    "canada": "America/Toronto",
    "china": "Asia/Shanghai",
    "france": "Europe/Paris",
    "germany": "Europe/Berlin",
    "india": "Asia/Kolkata",
    "indonesia": "Asia/Jakarta",
    "japan": "Asia/Tokyo",
    "kenya": "Africa/Nairobi",
    "mexico": "America/Mexico_City",
    "new zealand": "Pacific/Auckland",
    "nigeria": "Africa/Lagos",
    "pakistan": "Asia/Karachi",
    "philippines": "Asia/Manila",
    "singapore": "Asia/Singapore",
    "south africa": "Africa/Johannesburg",
    "spain": "Europe/Madrid",
    "turkey": "Europe/Istanbul",
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "united states": "America/New_York",
    "usa": "America/New_York",
}

# Unknown recipients are deliberately labeled as test cohorts, not inferred
# timezones. Each campaign day is spread across three UTC windows while still
# counting against one America/New_York operating day.
UNKNOWN_COHORTS = (
    ("europe_africa_test", 8, 0),
    ("americas_test", 14, 0),
    ("asia_pacific_test", 1, 1),
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.environ.get("VIDEOCODING_DB", str(PROJECT_ROOT / ".wrangler/state/v3/d1/miniflare-D1DatabaseObject/faaf2b0445ab934c3aac48ddf0cdfade8f9bac050be98993748742cdd2cb05fb.sqlite")))
DEFAULT_LOCK = PROJECT_ROOT / ".wrangler/outreach-scheduler.lock"
QUEUE_PLANNER_LOCK = PROJECT_ROOT / ".wrangler/outreach-queue-planner.lock"

# These checks remove the obvious false positives in the imported source set.
# Explicit AI/LLM names are ranked first, followed by AI-adjacent engineering
# channels whose topics match the product's creator audience.
NAME_INCLUDE = (
    "ai", "artificial intelligence", "chatgpt", "gpt", "llm", "machine learning",
    "coding", "code", "developer", "devops", "data science", "automation", "saas",
)
NAME_EXCLUDE = (
    "auto service", "carwow", "carros", "money", "finance", "english with",
    "gaming", "roblox", "music", "islam", "railway", "food tech", "repair",
    "1thek", "voice of telugu",
)
TRUSTED_TOPICS = {
    "ai coding", "data science", "web development", "devops", "cybersecurity",
    "cs fundamentals", "mobile development", "machine learning",
}
SUSPICIOUS_DOMAINS = {"gimal.com", "gamil.com"}
SUSPICIOUS_LEADING_N_ROLES = {
    "nadmin", "nadvertising", "nbusiness", "ncollab", "ncontact", "nfound",
    "nhello", "ninfo", "njobs", "nmail", "nmarketing", "npartners",
    "npress", "nquery", "nsales", "nsupport", "nteam",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=DAILY_TARGET)
    parser.add_argument(
        "--provider",
        choices=("microsoft_graph", GOOGLE_PROVIDER),
        default="microsoft_graph",
    )
    parser.add_argument("--min-interval-seconds", type=float, default=DEFAULT_MIN_INTERVAL_SECONDS)
    parser.add_argument("--max-interval-seconds", type=float, default=DEFAULT_MAX_INTERVAL_SECONDS)
    parser.add_argument("--interval-seconds", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--contact-ids", default="")
    parser.add_argument("--replan-only", action="store_true")
    return parser.parse_args()


def sender_config(provider: str) -> dict[str, object]:
    if provider == GOOGLE_PROVIDER:
        return {
            "provider": GOOGLE_PROVIDER,
            "sender_email": GOOGLE_MAILBOX,
            "daily_target": GOOGLE_DAILY_TARGET,
            "daily_hard_cap": GOOGLE_DAILY_HARD_CAP,
            "min_interval_seconds": GOOGLE_MIN_INTERVAL_SECONDS,
            "max_interval_seconds": GOOGLE_MAX_INTERVAL_SECONDS,
            "template_key": "first_contact",
            "experiment_id": WINNING_COPY_EXPERIMENT_ID,
            "variant": WINNING_COPY_VARIANT,
        }
    return {
        "provider": "microsoft_graph",
        "sender_email": MAILBOX,
        "daily_target": DAILY_TARGET,
        "daily_hard_cap": DAILY_HARD_CAP,
        "min_interval_seconds": DEFAULT_MIN_INTERVAL_SECONDS,
        "max_interval_seconds": DEFAULT_MAX_INTERVAL_SECONDS,
        "template_key": "first_contact",
        "experiment_id": WINNING_COPY_EXPERIMENT_ID,
        "variant": WINNING_COPY_VARIANT,
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def eastern_day(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(CAMPAIGN_ZONE).date().isoformat()


def next_eastern_run(value: datetime | None = None) -> str:
    now = (value or utc_now()).astimezone(CAMPAIGN_ZONE)
    target = datetime.combine(now.date(), clock_time(8, 0), CAMPAIGN_ZONE)
    if now >= target:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def campaign_send_window_open(day: str, value: datetime | None = None) -> bool:
    now = (value or utc_now()).astimezone(CAMPAIGN_ZONE)
    return now.date().isoformat() == day and 8 <= now.hour < 8 + SEND_WINDOW_HOURS


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise RuntimeError(f"Dashboard database not found: {path}")
    connection = sqlite3.connect(path, timeout=120)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 120000")
    return connection


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS outreach_send_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          contact_id INTEGER NOT NULL,
          campaign_kind TEXT NOT NULL DEFAULT 'creator_initial',
          campaign_day_et TEXT NOT NULL,
          status TEXT NOT NULL,
          queued_at TEXT NOT NULL,
          sent_at TEXT,
          error TEXT,
          template_version TEXT,
          UNIQUE(contact_id, campaign_kind)
        );
        CREATE INDEX IF NOT EXISTS outreach_send_log_day_idx
          ON outreach_send_log(campaign_day_et, status);
        CREATE TABLE IF NOT EXISTS outreach_sender_accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sender_email TEXT NOT NULL UNIQUE,
          provider TEXT NOT NULL,
          status TEXT NOT NULL,
          daily_target INTEGER NOT NULL,
          daily_hard_cap INTEGER NOT NULL,
          min_interval_seconds INTEGER NOT NULL,
          reply_to_email TEXT NOT NULL,
          sending_domain TEXT NOT NULL,
          configuration_note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outreach_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(outreach_send_log)").fetchall()
    }
    if "template_version" not in columns:
        db.execute("ALTER TABLE outreach_send_log ADD COLUMN template_version TEXT")
    send_log_additions = {
        "sender_email": "TEXT",
        "provider": "TEXT",
        "experiment_id": "TEXT",
        "variant": "TEXT",
        "recipient_timezone": "TEXT",
        "timezone_source": "TEXT",
        "send_window": "TEXT",
        "scheduled_for": "TEXT",
    }
    columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(outreach_send_log)").fetchall()}
    for name, column_type in send_log_additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE outreach_send_log ADD COLUMN {name} {column_type}")

    contact_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(contacts)").fetchall()}
    contact_additions = {
        "planned_sender_email": "TEXT",
        "initial_template_key": "TEXT",
        "send_timezone": "TEXT",
        "timezone_source": "TEXT NOT NULL DEFAULT 'unknown'",
        "timezone_confidence": "TEXT NOT NULL DEFAULT 'unknown'",
        "send_window": "TEXT",
        "next_eligible_send_at": "TEXT",
    }
    for name, column_type in contact_additions.items():
        if name not in contact_columns:
            db.execute(f"ALTER TABLE contacts ADD COLUMN {name} {column_type}")
    db.execute(
        """CREATE INDEX IF NOT EXISTS contacts_sender_queue_idx
           ON contacts(planned_send_date,planned_sender_email,next_eligible_send_at)"""
    )

    now = iso_utc()
    db.execute(
        """INSERT INTO outreach_sender_accounts(
             sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
             reply_to_email,sending_domain,configuration_note,created_at,updated_at
           ) VALUES(?, 'microsoft_graph', 'active', ?, ?, ?, ?, 'videocoding.ai',
             'Current human reply mailbox; campaign target raised to 700/day with 45-60 second jitter by operator on 2026-08-17.', ?, ?)
           ON CONFLICT(sender_email) DO UPDATE SET
             provider=excluded.provider,daily_target=excluded.daily_target,
             daily_hard_cap=excluded.daily_hard_cap,min_interval_seconds=excluded.min_interval_seconds,
             reply_to_email=excluded.reply_to_email,sending_domain=excluded.sending_domain,
             configuration_note=excluded.configuration_note,updated_at=excluded.updated_at""",
        (MAILBOX, DAILY_TARGET, DAILY_HARD_CAP, int(DEFAULT_MIN_INTERVAL_SECONDS), MAILBOX, now, now),
    )
    db.execute(
        """INSERT INTO outreach_sender_accounts(
             sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
             reply_to_email,sending_domain,configuration_note,created_at,updated_at
           ) VALUES(?, ?, 'configuration_required', ?, ?, ?, ?, 'outreach.videocoding.ai',
             'Workspace created. Complete SPF, DKIM, DMARC and Keychain SMTP credential before enabling the 50/day channel.', ?, ?)
           ON CONFLICT(sender_email) DO UPDATE SET
             provider=excluded.provider,daily_target=excluded.daily_target,
             daily_hard_cap=excluded.daily_hard_cap,min_interval_seconds=excluded.min_interval_seconds,
             reply_to_email=excluded.reply_to_email,sending_domain=excluded.sending_domain,
             configuration_note=CASE
               WHEN outreach_sender_accounts.status IN ('active','active_warmup')
               THEN outreach_sender_accounts.configuration_note
               ELSE excluded.configuration_note END,
             updated_at=excluded.updated_at""",
        (
            GOOGLE_MAILBOX,
            GOOGLE_PROVIDER,
            GOOGLE_DAILY_TARGET,
            GOOGLE_DAILY_HARD_CAP,
            int(GOOGLE_MIN_INTERVAL_SECONDS),
            MAILBOX,
            now,
            now,
        ),
    )
    db.execute(
        """INSERT INTO outreach_sender_accounts(
             sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
             reply_to_email,sending_domain,configuration_note,created_at,updated_at
           ) VALUES('pending@outreach.videocoding.ai','dedicated_external_sender','configuration_required',
             800,800,1,?,'outreach.videocoding.ai',
             'Provision a dedicated external email service plus SPF, DKIM, DMARC and suppression handling before activation.',?,?)
           ON CONFLICT(sender_email) DO NOTHING""",
        (MAILBOX, now, now),
    )
    try:
        db.execute(
            """INSERT OR IGNORE INTO outreach_templates(
                 template_key,name,stage,subject,body,risk_level,enabled,version,updated_at
               ) VALUES(
                 'first_contact_legacy_light','首次邀请 · Legacy-light','first_contact',?,?,'medium',1,1,datetime('now')
               )""",
            (
                SUBJECT,
                """Hi {{channel_name}},

I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you.

VideoCoding turns a talking-head cut into a finished video with B-roll, motion graphics, captions, music, and sound design.

You can see it here:
https://videocoding.ai

If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0. After you try it on one real video, we can discuss the promotion format and your rate.

Best,
Alhazar Anwar
VideoCoding""",
            ),
        )
        db.execute(
            """INSERT INTO outreach_templates(
                 template_key,name,stage,subject,body,risk_level,enabled,version,updated_at
               ) VALUES(?, '首次邀请 · T2V 知识类 v1', 'first_contact', ?, ?, 'medium', 1, 1, datetime('now'))
               ON CONFLICT(template_key) DO UPDATE SET
                 name=excluded.name,stage=excluded.stage,subject=excluded.subject,body=excluded.body,
                 risk_level=excluded.risk_level,enabled=1,updated_at=excluded.updated_at""",
            (T2V_KNOWLEDGE_TEMPLATE_KEY, SUBJECT, T2V_KNOWLEDGE_BODY),
        )
    except sqlite3.OperationalError:
        # The operational UI creates outreach_templates during first startup.
        pass
    if get_setting(db, "legacy_leading_n_quarantine_v1") != "complete":
        db.execute(
            """UPDATE contacts SET validation_status='needs_validation',
                 planned_send_date=NULL,next_eligible_send_at=NULL,
                 notes=notes || CASE WHEN notes='' THEN '' ELSE char(10) END ||
                   '隔离：旧数据邮箱本地部分以异常 n 开头；该模式历史退信率过高，需重新核验。',
                 updated_at=datetime('now')
               WHERE source_file NOT LIKE 'continuous_lead_discovery:%'
                 AND lower(substr(email,1,1))='n'
                 AND outreach_status='not_contacted'
                 AND first_sent_at IS NULL
                 AND validation_status='send_ready'"""
        )
        db.execute(
            """INSERT INTO outreach_settings(key,value,updated_at)
               VALUES('legacy_leading_n_quarantine_v1','complete',datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at"""
        )
    db.commit()


def recover_stale_initial_sends(db: sqlite3.Connection, stale_minutes: int = 15) -> int:
    """Mark interrupted initial sends as unknown instead of risking a duplicate retry."""
    modifier = f"-{max(1, stale_minutes)} minutes"
    cursor = db.execute(
        """UPDATE outreach_send_log SET status='delivery_unknown',
             error='Delivery outcome is unknown; verify provider Sent Items before any retry'
           WHERE status='sending' AND datetime(queued_at)<=datetime('now',?)""",
        (modifier,),
    )
    db.commit()
    return max(0, cursor.rowcount)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def stable_cohort(email: str) -> tuple[str, int, int]:
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).digest()
    return UNKNOWN_COHORTS[digest[0] % len(UNKNOWN_COHORTS)]


def timezone_metadata(contact: sqlite3.Row) -> tuple[str | None, str, str, str]:
    country = str(contact["country"] or "").strip().lower()
    timezone_name = COUNTRY_TIMEZONES.get(country)
    if timezone_name:
        return timezone_name, "public_country", "high", "local_10am"
    cohort, _, _ = stable_cohort(str(contact["email"]))
    return None, "deterministic_global_cohort", "unknown", cohort


def scheduled_for_campaign_day(contact: sqlite3.Row, campaign_day: str) -> tuple[str, str | None, str, str, str]:
    # This campaign intentionally starts both sender queues at 08:00 Eastern.
    # Country/timezone metadata is still retained on the contact for later
    # experiments, but it does not alter this explicitly approved send time.
    campaign_date = datetime.fromisoformat(f"{campaign_day}T00:00:00+00:00").date()
    scheduled = datetime.combine(campaign_date, clock_time(8, 0), CAMPAIGN_ZONE)
    return (
        iso_utc(scheduled.astimezone(timezone.utc)),
        "America/New_York",
        "campaign_policy",
        "high",
        "america_new_york_8am",
    )


def next_due_run(db: sqlite3.Connection, sender_email: str | None = None) -> str:
    sender_filter = " AND planned_sender_email = ?" if sender_email else ""
    parameters = (sender_email,) if sender_email else ()
    row = db.execute(
        f"""SELECT MIN(next_eligible_send_at) AS due_at FROM contacts
           WHERE validation_status='send_ready'
             AND outreach_status='not_contacted' AND first_sent_at IS NULL
             AND do_not_contact=0 AND next_eligible_send_at IS NOT NULL
             {sender_filter}""",
        parameters,
    ).fetchone()
    if row and row["due_at"]:
        return str(row["due_at"])
    return next_eastern_run()


def get_setting(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM outreach_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def set_setting(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """INSERT INTO outreach_settings(key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             updated_at = excluded.updated_at""",
        (key, value),
    )
    db.commit()


def microsoft_outbound_block_reason(db: sqlite3.Connection | None = None) -> str | None:
    if db is not None:
        return block_reason(db)
    # A final transport gate also covers one-off scripts that use Graph directly.
    try:
        guard_db = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True, timeout=10)
        try:
            return block_reason(guard_db)
        finally:
            guard_db.close()
    except sqlite3.Error as exc:
        return f"Microsoft outbound guard unavailable: {type(exc).__name__}"


def request_json(url: str, *, data: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    if is_graph_delivery(url):
        reason = microsoft_outbound_block_reason()
        if reason:
            return 423, {"error": {"code": "sender_restricted", "message": reason}}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": {"message": raw.decode("utf-8", errors="replace")[:500]}}
        return error.code, payload
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {
            "error": {
                "message": f"{type(error).__name__}: {error}"[:500],
                "deliveryOutcome": "unknown",
            }
        }


def refresh_access_token(db: sqlite3.Connection) -> str:
    if __package__:
        from .handoff_guard import require_live_opt_in
    else:
        from handoff_guard import require_live_opt_in
    require_live_opt_in()
    refresh_token = get_setting(db, "microsoft_refresh_token")
    if not refresh_token:
        raise RuntimeError("Microsoft 365 is not connected to the dashboard")
    form = urllib.parse.urlencode(
        {
            "client_id": MICROSOFT_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": MICROSOFT_SCOPES,
        }
    ).encode()
    status, payload = request_json(
        f"https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}/oauth2/v2.0/token",
        data=form,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    access_token = payload.get("access_token")
    if status != 200 or not access_token:
        message = payload.get("error_description") or payload.get("error") or "token refresh failed"
        raise RuntimeError(str(message)[:500])
    rotated = payload.get("refresh_token")
    if rotated and rotated != refresh_token:
        set_setting(db, "microsoft_refresh_token", rotated)
    return str(access_token)


def google_app_password() -> str:
    if __package__:
        from .handoff_guard import require_live_opt_in
    else:
        from handoff_guard import require_live_opt_in
    require_live_opt_in()
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            GOOGLE_MAILBOX,
            "-s",
            GOOGLE_KEYCHAIN_SERVICE,
            "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    password = result.stdout.strip()
    if result.returncode != 0 or not password:
        raise RuntimeError(
            "Google Workspace SMTP credential is not stored in macOS Keychain"
        )
    return password


def template_version(contact_id: int) -> str:
    """Use the creator outreach copy approved by the campaign owner."""
    _ = contact_id
    return GENERATIVE_TEMPLATE


def message_body(_channel_name: str, version: str) -> str:
    _ = version
    return """Hi,

I’m Alhazar from VideoCoding. I think it could be a useful fit for the kind of videos you create, and I’d love to explore a paid collaboration with you.

You upload the talking-head cut you want to keep, and VideoCoding takes care of the rest—deciding where visuals are needed, creating the B-roll and motion graphics, and handling the captions, music, and sound design.

The entire edit is AI-directed, with no templates or prompts. If you want more control, you can also download the project as code and continue editing it with a vibe-coding tool.

Here’s the site:
https://videocoding.ai

If you’d like to test it on one of your own videos, reply and I’ll send you a code for one free month. If you like the result, we can discuss the promotion and your rate.

Best,
Alhazar Anwar"""


def approved_initial_template(
    db: sqlite3.Connection,
    template_key: str = "first_contact",
) -> tuple[str, str, str]:
    """Read an explicitly approved operator-controlled template, or fail closed."""
    try:
        row = db.execute(
            """SELECT subject,body,version FROM outreach_templates
               WHERE template_key=? AND enabled=1 AND approval_status='approved' LIMIT 1""",
            (template_key,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if not row or not str(row["body"] or "").strip():
        raise RuntimeError(
            f"Assigned outreach template is missing, disabled, or not approved: {template_key}"
        )
    return (
        str(row["subject"] or SUBJECT),
        str(row["body"]),
        f"{template_key}-v{int(row['version'])}",
    )


def contact_template_metadata(
    contact: sqlite3.Row,
    sender: dict[str, object],
) -> tuple[str, str, str]:
    """Return the operator-assigned template and its experiment labels."""
    template_key = str(contact["initial_template_key"] or sender["template_key"])
    if template_key == 'first_contact_legacy_light':
        return template_key, 'approved-general-creator-rollout-20260908', template_key
    if template_key == T2V_KNOWLEDGE_TEMPLATE_KEY:
        return (
            template_key,
            T2V_KNOWLEDGE_EXPERIMENT_ID,
            T2V_KNOWLEDGE_VARIANT,
        )
    return template_key, str(sender["experiment_id"]), str(sender["variant"])


def initial_template_is_approved(db: sqlite3.Connection, template_key: str) -> bool:
    """Fail closed unless the selected first-contact copy was explicitly approved."""
    try:
        template = db.execute(
            """SELECT enabled,approval_status,length(trim(body)) AS body_length
               FROM outreach_templates WHERE template_key=? LIMIT 1""",
            (template_key,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(
        template
        and int(template["enabled"] or 0) == 1
        and str(template["approval_status"] or "") == "approved"
        and int(template["body_length"] or 0) > 0
    )


def partition_policy_allowed_contacts(
    db: sqlite3.Connection,
    contacts: list[sqlite3.Row],
    sender: dict[str, object],
) -> tuple[list[sqlite3.Row], list[dict[str, object]]]:
    """Keep only contacts whose assigned template is explicitly auto-enabled."""
    allowed: list[sqlite3.Row] = []
    held: list[dict[str, object]] = []
    for contact in contacts:
        template_key, _, _ = contact_template_metadata(contact, sender)
        mode = template_automation_mode(db, template_key)
        template_approved = initial_template_is_approved(db, template_key)
        if mode == "auto" and template_approved:
            allowed.append(contact)
        else:
            held.append(
                {
                    "id": int(contact["id"]),
                    "template_key": template_key,
                    "mode": mode,
                    "template_approved": template_approved,
                }
            )
    return allowed, held


def render_initial_body(template: str, contact: sqlite3.Row) -> str:
    return template.replace("{{channel_name}}", str(contact["channel_name"] or "")).replace("{{coupon_code}}", "FREETEST100")


def send_mail(access_token: str, contact: sqlite3.Row, version: str, subject: str, body: str) -> tuple[bool, str]:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": str(contact["email"])}}
            ],
            "internetMessageHeaders": [
                {"name": "x-videocoding-campaign", "value": "creator-initial-v3"},
                {"name": "x-videocoding-template", "value": version},
                {"name": "x-videocoding-contact-id", "value": str(contact["id"])},
            ],
        },
        "saveToSentItems": True,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    delay = 2.0
    for attempt in range(3):
        status, result = request_json(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            data=data,
            headers={
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
            },
        )
        if status == 202:
            return True, "accepted"
        error = result.get("error", {})
        message = error.get("message") if isinstance(error, dict) else str(error)
        if status == 0:
            return False, f"DELIVERY_UNKNOWN: {message or 'network request failed'}"[:500]
        if status not in {429, 500, 502, 503, 504}:
            return False, f"HTTP {status}: {message or 'send failed'}"[:500]
        if attempt < 2:
            time.sleep(delay)
            delay *= 2
    return False, f"HTTP {status}: {message or 'send failed'}"[:500]


def build_gmail_message(
    contact: sqlite3.Row | dict[str, object],
    version: str,
    subject: str,
    body: str,
    *,
    experiment_id: str,
    variant: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = f"Alhazar Anwar <{GOOGLE_MAILBOX}>"
    message["To"] = str(contact["email"])
    message["Reply-To"] = MAILBOX
    message["Subject"] = subject
    message["X-VideoCoding-Campaign"] = "creator-initial-v4"
    message["X-VideoCoding-Template"] = version
    message["X-VideoCoding-Contact-Id"] = str(contact["id"])
    message["X-VideoCoding-Experiment"] = experiment_id
    message["X-VideoCoding-Variant"] = variant
    message.set_content(body)
    return message


def send_gmail_smtp(
    app_password: str,
    contact: sqlite3.Row,
    version: str,
    subject: str,
    body: str,
    *,
    experiment_id: str,
    variant: str,
) -> tuple[bool, str]:
    message = build_gmail_message(
        contact,
        version,
        subject,
        body,
        experiment_id=experiment_id,
        variant=variant,
    )
    submission_started = False
    provider_accepted = False
    try:
        with gmail_smtp_connection(timeout=30) as smtp:
            smtp.login(GOOGLE_MAILBOX, app_password)
            submission_started = True
            smtp.send_message(message)
            provider_accepted = True
        return True, "accepted"
    except (OSError, smtplib.SMTPException) as error:
        # A failed QUIT cannot undo a successful DATA acknowledgement.
        if provider_accepted:
            return True, "accepted"
        detail = f"Gmail SMTP: {type(error).__name__}: {error}"
        # Explicit envelope/DATA rejection is definite; a transport failure
        # during send_message may have lost the final acceptance response.
        rejected = isinstance(error, (smtplib.SMTPSenderRefused,
                                      smtplib.SMTPRecipientsRefused,
                                      smtplib.SMTPDataError))
        if submission_started and not rejected:
            return False, f"DELIVERY_UNKNOWN: {detail}"[:500]
        return False, detail[:500]


class GmailTunnelSMTP(smtplib.SMTP_SSL):
    """Reach Gmail through a loopback SSH forward; authenticate Gmail's TLS name."""

    def _get_socket(self, host, port, timeout):
        tunnel_port = int(os.environ['VIDEOCODING_GMAIL_TUNNEL_PORT'])
        raw = socket.create_connection(('127.0.0.1', tunnel_port), timeout)
        try:
            return self.context.wrap_socket(raw, server_hostname=host)
        except BaseException:
            raw.close()
            raise


def gmail_smtp_connection(timeout: float = 8.0):
    # Fail over only while opening the connection, before authentication or any
    # message submission. A failed/uncertain send must never trigger a resend.
    context = ssl.create_default_context()
    if os.environ.get('VIDEOCODING_GMAIL_TUNNEL_PORT'):
        try:
            return GmailTunnelSMTP('smtp.gmail.com', 465, context=context, timeout=timeout)
        except (OSError, smtplib.SMTPException):
            pass
    return smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context, timeout=timeout)


def gmail_smtp_transport_available(timeout: float = 8.0) -> tuple[bool, str]:
    """Probe the same verified TLS transport used by actual delivery."""
    try:
        with gmail_smtp_connection(timeout=timeout) as smtp:
            code, _ = smtp.noop()
            return code == 250, f'Gmail SMTP NOOP {code}'
    except (OSError, smtplib.SMTPException, ValueError) as error:
        return False, f"Gmail SMTP transport unavailable: {type(error).__name__}: {error}"[:500]


def contact_score(row: sqlite3.Row) -> tuple[int, int, int]:
    name = str(row["channel_name"]).lower()
    topic = str(row["topic"]).lower()
    explicit = sum(1 for token in NAME_INCLUDE[:6] if name_contains(name, token))
    adjacent = sum(1 for token in NAME_INCLUDE[6:] if name_contains(name, token))
    topic_signal = 2 if topic in TRUSTED_TOPICS else int("ai " in topic or "ai_" in topic or "chatgpt" in topic or "llm" in topic)
    subscribers = int(row["subscribers"] or 0)
    if 100_000 <= subscribers < 250_000:
        audience_band = 4
    elif 50_000 <= subscribers < 100_000:
        audience_band = 3
    elif subscribers >= 250_000:
        audience_band = 2
    elif 10_000 <= subscribers < 50_000:
        audience_band = 1
    else:
        audience_band = 0
    return explicit * 10 + adjacent * 4 + topic_signal * 3, audience_band, subscribers


def name_contains(name: str, token: str) -> bool:
    if token in {"ai", "gpt", "llm", "code"}:
        return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", name) is not None
    return token in name


def qualified_contact(row: sqlite3.Row) -> bool:
    name = str(row["channel_name"]).lower()
    topic = str(row["topic"]).lower()
    email = str(row["email"]).strip().lower()
    local, _, domain = email.partition("@")
    if domain in SUSPICIOUS_DOMAINS or local in SUSPICIOUS_LEADING_N_ROLES:
        return False
    if int(row["subscribers"] or 0) < 1_000:
        return False
    if any(token in name for token in NAME_EXCLUDE):
        return False
    return (
        any(name_contains(name, token) for token in NAME_INCLUDE)
        or any(token in topic for token in NAME_INCLUDE)
        or topic in TRUSTED_TOPICS
    )


def candidate_contacts(
    db: sqlite3.Connection,
    *,
    explicit_ids: list[int],
    limit: int,
    sender_email: str,
) -> list[sqlite3.Row]:
    where = """
      validation_status = 'send_ready'
      AND COALESCE(subscribers,0) >= 1000
      AND outreach_status = 'not_contacted'
      AND first_sent_at IS NULL
      AND do_not_contact = 0
      AND planned_sender_email = ?
      AND (
        planned_send_date < ?
        OR initial_template_key IS NOT NULL
        OR COALESCE(curated_ai,0) = 1
        OR COALESCE(ai_match,0) = 1
        OR category = 'AI & Tech'
      )
      AND NOT EXISTS (
        SELECT 1 FROM outreach_send_log l
        WHERE l.contact_id = contacts.id AND l.campaign_kind = 'creator_initial'
      )
    """
    if explicit_ids:
        placeholders = ",".join("?" for _ in explicit_ids)
        rows = db.execute(
            f"SELECT * FROM contacts WHERE {where} AND id IN ({placeholders})",
            [sender_email, SEGMENTED_COPY_START_DAY, *explicit_ids],
        ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        return [by_id[item] for item in explicit_ids if item in by_id][:limit]
    rows = db.execute(
        f"""SELECT * FROM contacts WHERE {where}
              AND next_eligible_send_at IS NOT NULL
              AND datetime(next_eligible_send_at) <= datetime('now')
              AND datetime(next_eligible_send_at, '+{SEND_WINDOW_HOURS} hours') >= datetime('now')
            ORDER BY CASE WHEN COALESCE(curated_ai,0)=1 OR COALESCE(ai_match,0)=1 OR category='AI & Tech' THEN 0 ELSE 1 END,
              datetime(next_eligible_send_at), fit_score DESC, subscribers DESC""",
        (sender_email, SEGMENTED_COPY_START_DAY),
    ).fetchall()
    return rows[:limit]


def sent_today(
    db: sqlite3.Connection,
    day: str,
    sender_email: str = MAILBOX,
) -> int:
    row = db.execute(
        """SELECT COUNT(*) count FROM outreach_send_log
           WHERE campaign_day_et = ? AND status = 'sent'
             AND COALESCE(sender_email, ?) = ?""",
        (day, MAILBOX, sender_email),
    ).fetchone()
    return int(row["count"])


def delivery_committed_today(
    db: sqlite3.Connection,
    day: str,
    sender_email: str = MAILBOX,
) -> int:
    """Count accepted and ambiguous attempts toward the hard cap to prevent over-send."""
    row = db.execute(
        """SELECT COUNT(*) count FROM outreach_send_log
           WHERE campaign_day_et = ? AND status IN ('sent','sending','delivery_unknown')
             AND COALESCE(sender_email, ?) = ?""",
        (day, MAILBOX, sender_email),
    ).fetchone()
    return int(row["count"])


def choose_sender(campaign_day, committed, planned):
    """Keep both approved channels supplied instead of starving the 50/day one."""
    caps = ((MAILBOX,DAILY_TARGET),(GOOGLE_MAILBOX,GOOGLE_DAILY_TARGET))
    available = [(sender,cap) for sender,cap in caps
                 if committed.get((campaign_day,sender),0)+planned.get((campaign_day,sender),0)<cap]
    return min(available,key=lambda item:(committed.get((campaign_day,item[0]),0)+planned.get((campaign_day,item[0]),0))/item[1])[0] if available else None


def plan_rows(db: sqlite3.Connection, rows: list[sqlite3.Row], *, reset: bool) -> int:
    db.execute('BEGIN IMMEDIATE')
    try:
        count = _plan_rows_locked(db, rows, reset=reset)
        db.commit()
        return count
    except Exception:
        db.rollback()
        raise


def _plan_rows_locked(db: sqlite3.Connection, rows: list[sqlite3.Row], *, reset: bool) -> int:
    # Candidate selection can predate another planner. Do not move its reservations.
    rows = [current for row in rows if (current := db.execute("""SELECT * FROM contacts c WHERE id=?
      AND outreach_status='not_contacted' AND first_sent_at IS NULL AND do_not_contact=0
      AND validation_status='send_ready' AND coalesce(subscribers,0)>=1000
      AND (? OR planned_send_date IS NULL OR next_eligible_send_at IS NULL)
      AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')""", (row['id'],int(reset))).fetchone()) is not None]
    if reset:
        db.execute(
            """UPDATE contacts SET planned_send_date=NULL,planned_sender_email=NULL,next_eligible_send_at=NULL,
                 send_timezone=NULL,timezone_source='unknown',timezone_confidence='unknown',send_window=NULL
               WHERE outreach_status='not_contacted' AND first_sent_at IS NULL"""
        )
    now_et = utc_now().astimezone(CAMPAIGN_ZONE)
    today = now_et.date() + timedelta(days=int(now_et.hour >= 8 + SEND_WINDOW_HOURS))
    sent_counts = {
        (str(row["campaign_day_et"]), str(row["sender_email"])): int(row["count"])
        for row in db.execute(
            """SELECT campaign_day_et,COALESCE(sender_email,?) sender_email,COUNT(*) count
               FROM outreach_send_log WHERE status IN ('sent','sending','delivery_unknown')
               GROUP BY campaign_day_et,COALESCE(sender_email,?)""",
            (MAILBOX, MAILBOX),
        ).fetchall()
    }
    planned_counts = {
        (str(row["planned_send_date"]), str(row["planned_sender_email"])): int(row["count"])
        for row in db.execute(
            """SELECT planned_send_date,planned_sender_email,COUNT(*) count FROM contacts
               WHERE validation_status='send_ready'
                 AND outreach_status='not_contacted' AND first_sent_at IS NULL
                 AND do_not_contact=0 AND planned_send_date IS NOT NULL
                 AND planned_sender_email IS NOT NULL
                 AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=contacts.id AND l.campaign_kind='creator_initial')
               GROUP BY planned_send_date,planned_sender_email"""
        ).fetchall()
    }
    updates: list[tuple[str, str, str | None, str, str, str, str, int]] = []
    day_offset = 0
    for row in rows:
        while True:
            campaign_day = (today + timedelta(days=day_offset)).isoformat()
            selected_sender = choose_sender(campaign_day, sent_counts, planned_counts)
            if selected_sender:
                break
            day_offset += 1
        scheduled_at, timezone_name, source, confidence, window = scheduled_for_campaign_day(row, campaign_day)
        updates.append((campaign_day, selected_sender, scheduled_at, timezone_name, source, confidence, window, int(row["id"])))
        key = (campaign_day, selected_sender)
        planned_counts[key] = planned_counts.get(key, 0) + 1
    db.executemany(
        """UPDATE contacts SET planned_send_date=?,planned_sender_email=?,next_eligible_send_at=?,send_timezone=?,
             timezone_source=?,timezone_confidence=?,send_window=?,updated_at=datetime('now') WHERE id=?""",
        updates,
    )
    db.commit()
    return len(rows)


def replan_queue(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """SELECT * FROM contacts
           WHERE validation_status = 'send_ready'
             AND COALESCE(subscribers,0) >= 1000
             AND outreach_status = 'not_contacted'
             AND first_sent_at IS NULL
             AND do_not_contact = 0
             AND (
               planned_send_date < ?
               OR initial_template_key IS NOT NULL
               OR COALESCE(curated_ai,0) = 1
               OR COALESCE(ai_match,0) = 1
               OR category = 'AI & Tech'
             )
             AND NOT EXISTS (
               SELECT 1 FROM outreach_send_log l
               WHERE l.contact_id = contacts.id AND l.campaign_kind = 'creator_initial'
             )""",
        (SEGMENTED_COPY_START_DAY,),
    ).fetchall()
    rows.sort(
        key=lambda row: (
            int(bool(row["curated_ai"] or row["ai_match"] or row["category"] == "AI & Tech")),
            int(row["fit_score"] or 0),
            int(row["subscribers"] or 0),
        ),
        reverse=True,
    )
    planned = plan_rows(db, rows, reset=True)
    set_setting(db, "queue_policy", QUEUE_POLICY_VERSION)
    return planned


def maybe_upgrade_queue_policy(db: sqlite3.Connection) -> int:
    """Rebuild the queue once, only when neither sender is mid-batch."""
    if get_setting(db, "queue_policy") == QUEUE_POLICY_VERSION:
        return 0
    QUEUE_PLANNER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_PLANNER_LOCK.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if get_setting(db, "queue_policy") == QUEUE_POLICY_VERSION:
            return 0
        running = db.execute(
            """SELECT COUNT(*) AS count FROM outreach_settings
               WHERE key IN ('scheduler_status','gmail_scheduler_status')
                 AND value='running'"""
        ).fetchone()
        if running and int(running["count"] or 0) > 0:
            return 0
        return replan_queue(db)


def release_unassigned_segment_queue(db: sqlite3.Connection) -> int:
    """Keep new non-AI segments out of the send queue until copy is assigned."""
    cursor = db.execute(
        """UPDATE contacts SET
             planned_send_date=NULL,
             planned_sender_email=NULL,
             next_eligible_send_at=NULL,
             send_timezone=NULL,
             timezone_source='unknown',
             timezone_confidence='unknown',
             send_window=NULL,
             updated_at=datetime('now')
           WHERE validation_status='send_ready'
             AND outreach_status='not_contacted'
             AND first_sent_at IS NULL
             AND do_not_contact=0
             AND planned_send_date >= ?
             AND initial_template_key IS NULL
             AND COALESCE(curated_ai,0)=0
             AND COALESCE(ai_match,0)=0
             AND category <> 'AI & Tech'
             AND NOT EXISTS (
               SELECT 1 FROM outreach_send_log l
               WHERE l.contact_id=contacts.id AND l.campaign_kind='creator_initial'
             )""",
        (SEGMENTED_COPY_START_DAY,),
    )
    db.commit()
    return max(0, int(cursor.rowcount))


def append_unplanned_contacts(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """SELECT * FROM contacts
           WHERE validation_status='send_ready'
             AND COALESCE(subscribers,0) >= 1000
             AND outreach_status='not_contacted' AND first_sent_at IS NULL
             AND do_not_contact=0
             AND (
               initial_template_key IS NOT NULL
               OR COALESCE(curated_ai,0)=1
               OR COALESCE(ai_match,0)=1
               OR category='AI & Tech'
             )
             AND (planned_send_date IS NULL OR next_eligible_send_at IS NULL)
             AND NOT EXISTS (
               SELECT 1 FROM outreach_send_log l
               WHERE l.contact_id=contacts.id AND l.campaign_kind='creator_initial'
             )"""
    ).fetchall()
    rows.sort(
        key=lambda row: (
            int(bool(row["curated_ai"] or row["ai_match"] or row["category"] == "AI & Tech")),
            int(row["fit_score"] or 0),
            int(row["subscribers"] or 0),
        ),
        reverse=True,
    )
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in rows)
    db.execute(
        f"""UPDATE contacts SET planned_send_date=NULL,planned_sender_email=NULL,next_eligible_send_at=NULL
             WHERE id IN ({placeholders})""",
        [int(row["id"]) for row in rows],
    )
    db.commit()
    return plan_rows(db, rows, reset=False)


def reschedule_missed_windows(db: sqlite3.Connection) -> int:
    """Reallocate missed/overfull reservations across both capped mailboxes.

    Moving each missed day forward independently accumulates many 50-message
    Gmail batches on one day while the 700-message mailbox runs out of work.
    Reuse the capacity planner under one write transaction instead.
    """
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = db.execute("""SELECT c.* FROM contacts c
          WHERE validation_status='send_ready' AND coalesce(subscribers,0)>=1000
            AND outreach_status='not_contacted' AND first_sent_at IS NULL
            AND do_not_contact=0 AND next_eligible_send_at IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM outreach_send_log l
              WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')
          ORDER BY datetime(next_eligible_send_at),id""").fetchall()
        counts = {}
        expired = False
        cutoff = utc_now() - timedelta(hours=SEND_WINDOW_HOURS)
        for row in rows:
            key = (str(row['planned_send_date']), str(row['planned_sender_email']))
            counts[key] = counts.get(key, 0) + 1
            expired = expired or parse_iso(str(row['next_eligible_send_at'])) < cutoff
        caps = {MAILBOX: DAILY_HARD_CAP, GOOGLE_MAILBOX: GOOGLE_DAILY_HARD_CAP}
        overfull = any(
            count + delivery_committed_today(db, day, sender) > caps.get(sender, 0)
            for (day, sender), count in counts.items()
        )
        if not expired and not overfull:
            db.commit()
            return 0
        db.executemany("""UPDATE contacts SET planned_send_date=NULL,
          planned_sender_email=NULL,next_eligible_send_at=NULL WHERE id=?""",
          [(row['id'],) for row in rows])
        return _plan_rows_locked(db, rows, reset=False)
    except BaseException:
        db.rollback()
        raise


def empty_queue_status(db: sqlite3.Connection, sender_email: str) -> str:
    """Do not disguise an unassigned inventory as a future sending window."""
    if __package__:
        from .creator_activity import activity_days, ensure_activity_schema
    else:
        from creator_activity import activity_days, ensure_activity_schema
    days = activity_days(db)
    activity_sql = ''
    activity_params = ()
    if days:
        ensure_activity_schema(db)
        activity_sql = ''' AND EXISTS(SELECT 1 FROM creator_activity_cache a
            WHERE a.channel_url=c.channel_url AND a.status='recent'
            AND julianday(a.checked_at) BETWEEN julianday('now')-2 AND julianday('now')
            AND julianday(a.published_at) BETWEEN julianday('now')-? AND julianday('now'))'''
        activity_params = (days,)
    future = db.execute("""SELECT 1 FROM contacts c WHERE c.planned_sender_email=?
        AND c.validation_status='send_ready' AND coalesce(c.subscribers,0)>=1000
        AND c.outreach_status='not_contacted' AND c.first_sent_at IS NULL AND c.do_not_contact=0
        AND datetime(c.next_eligible_send_at)>datetime('now')
        AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')"""
        + activity_sql + ' LIMIT 1', (sender_email, *activity_params)).fetchone()
    if future:
        return 'waiting_for_send_window'
    if days and db.execute("""SELECT 1 FROM contacts c WHERE c.planned_sender_email=?
        AND c.validation_status='send_ready' AND coalesce(c.subscribers,0)>=1000
        AND c.outreach_status='not_contacted' AND c.first_sent_at IS NULL AND c.do_not_contact=0
        AND c.initial_template_key IS NOT NULL
        AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')"""
        + activity_sql.replace('AND EXISTS', 'AND NOT EXISTS', 1) + ' LIMIT 1',
        (sender_email, *activity_params)).fetchone():
        return 'waiting_for_recent_activity'
    unassigned = db.execute("""SELECT 1 FROM contacts c WHERE c.validation_status='send_ready'
        AND coalesce(c.subscribers,0)>=1000 AND c.outreach_status='not_contacted'
        AND c.first_sent_at IS NULL AND c.do_not_contact=0 AND c.initial_template_key IS NULL
        AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial') LIMIT 1""").fetchone()
    return 'waiting_for_template_assignment' if unassigned else 'queue_empty'


def replace_activity_held_slots(db: sqlite3.Connection, sender_email: str) -> int:
    """Swap blocked reservations with verified future work on the SAME sender."""
    if __package__:
        from .creator_activity import activity_days, ensure_activity_schema
    else:
        from creator_activity import activity_days, ensure_activity_schema
    days = activity_days(db)
    now = utc_now().astimezone(CAMPAIGN_ZONE)
    if not days or not 8 <= now.hour < 8 + SEND_WINDOW_HOURS:
        return 0
    if sender_email == MAILBOX and microsoft_outbound_block_reason(db):
        return 0
    account = db.execute('SELECT status FROM outreach_sender_accounts WHERE sender_email=?',(sender_email,)).fetchone()
    if not account or account[0] not in ('active','active_warmup'):
        return 0
    ensure_activity_schema(db)
    day = now.date().isoformat()
    cap = DAILY_HARD_CAP if sender_email == MAILBOX else GOOGLE_DAILY_HARD_CAP
    reservations = """FROM contacts c WHERE c.planned_sender_email=?
      AND c.validation_status='send_ready' AND c.outreach_status='not_contacted'
      AND c.first_sent_at IS NULL AND c.do_not_contact=0 AND coalesce(c.subscribers,0)>=1000
      AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')"""
    # An inactive legacy reservation can lack an assigned template and still
    # occupy a daily slot. Defer it without assigning copy or approving it;
    # only a future contact with approved copy may replace that reservation.
    base = reservations + """ AND EXISTS(SELECT 1 FROM outreach_templates t
      WHERE t.template_key=c.initial_template_key AND t.enabled=1 AND t.approval_status='approved')"""
    evidence = """EXISTS(SELECT 1 FROM creator_activity_cache a WHERE a.channel_url=c.channel_url
      AND a.status='recent' AND julianday(a.checked_at) BETWEEN julianday('now')-2 AND julianday('now')
      AND julianday(a.published_at) BETWEEN julianday('now')-? AND julianday('now'))"""
    db.execute('BEGIN IMMEDIATE')
    try:
        held = db.execute('SELECT c.* '+reservations+' AND c.planned_send_date=? AND NOT '+evidence+' ORDER BY c.id',
                          (sender_email,day,days)).fetchall()
        ready = db.execute('SELECT count(*) '+base+' AND c.planned_send_date=? AND '+evidence,
                           (sender_email,day,days)).fetchone()[0]
        slots = min(len(held),max(0,cap-delivery_committed_today(db,day,sender_email)-ready))
        future = db.execute('SELECT c.* '+base+' AND c.planned_send_date>? AND '+evidence+
                            ' ORDER BY c.planned_send_date,c.id LIMIT ?', (sender_email,day,days,slots)).fetchall()
        changes=[]
        for blocked,replacement in zip(held,future):
            future_day=replacement['planned_send_date']
            for row,target_day in ((blocked,future_day),(replacement,day)):
                at,tz,source,confidence,window=scheduled_for_campaign_day(row,target_day)
                db.execute("""UPDATE contacts SET planned_send_date=?,next_eligible_send_at=?,send_timezone=?,
                  timezone_source=?,timezone_confidence=?,send_window=?,updated_at=datetime('now') WHERE id=?""",
                  (target_day,at,tz,source,confidence,window,row['id']))
            changes.append({'held':blocked['id'],'replacement':replacement['id'],'future_day':future_day})
        if changes and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outreach_audit_log'").fetchone():
            db.execute("""INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
              VALUES('activity_reservations_replaced','sender',?,'outreach_scheduler',?,datetime('now'))""",
              (sender_email,json.dumps({'campaign_day':day,'swaps':changes})))
        db.commit()
        return len(changes)
    except BaseException:
        db.rollback()
        raise


def run(args: argparse.Namespace) -> int:
    now_et = utc_now().astimezone(CAMPAIGN_ZONE)
    day = now_et.date().isoformat()
    db = connect(args.db)
    ensure_schema(db)
    delivery_unknown = recover_stale_initial_sends(db)
    config = sender_config(args.provider)
    sender_email = str(config["sender_email"])
    daily_target = int(config["daily_target"])
    daily_hard_cap = int(config["daily_hard_cap"])
    setting_prefix = "gmail_scheduler" if args.provider == GOOGLE_PROVIDER else "scheduler"
    if args.replan_only:
        planned = replan_queue(db)
        print(json.dumps({"planned": planned, "first_date_et": now_et.date().isoformat()}))
        return 0
    if not args.dry_run and not campaign_send_window_open(day):
        result = {"day_et": day, "sent": 0, "status": "waiting_for_send_window"}
        set_setting(db, f"{setting_prefix}_status", result["status"])
        set_setting(db, f"{setting_prefix}_last_result", json.dumps(result))
        set_setting(db, f"{setting_prefix}_next_run_at", next_eastern_run())
        print(json.dumps(result), flush=True)
        return 0
    if args.provider == 'microsoft_graph' and (reason := microsoft_outbound_block_reason(db)):
        result = {"day_et": day, "sent": 0, "status": "sender_restricted", "reason": reason}
        if not args.dry_run:
            set_setting(db, "scheduler_status", "sender_restricted")
            set_setting(db, "scheduler_last_result", json.dumps(result))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    first_contact_mode = automation_rule_mode(db, "first_contact")
    if first_contact_mode != "auto":
        result = {
            "day_et": day,
            "sent": 0,
            "status": "policy_held",
            "rule_key": "first_contact",
            "mode": first_contact_mode,
        }
        if args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        set_setting(db, f"{setting_prefix}_last_result", json.dumps(result, separators=(",", ":")))
        set_setting(db, f"{setting_prefix}_status", "policy_held")
        set_setting(db, f"{setting_prefix}_next_run_at", next_eastern_run())
        return 0
    if not args.dry_run and args.provider == 'microsoft_graph':
        if __package__:
            from .initial_template_router import route_unassigned
        else:
            from initial_template_router import route_unassigned
        prepared = db.execute("""SELECT count(*) FROM contacts c WHERE validation_status='send_ready'
          AND subscribers>=1000 AND outreach_status='not_contacted' AND first_sent_at IS NULL
          AND do_not_contact=0 AND initial_template_key IS NOT NULL AND planned_send_date IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')""").fetchone()[0]
        if prepared < TOTAL_DAILY_TARGET:
            route_unassigned(db, apply=True)
    policy_replanned = maybe_upgrade_queue_policy(db)
    released_unassigned = release_unassigned_segment_queue(db)
    newly_planned = append_unplanned_contacts(db)
    reschedule_missed_windows(db)
    replace_activity_held_slots(db, sender_email)
    already_sent = sent_today(db, day, sender_email)
    already_committed = delivery_committed_today(db, day, sender_email)
    remaining_to_target = max(0, daily_target - already_committed)
    requested = max(0, min(args.limit, remaining_to_target, daily_hard_cap - already_committed))
    if requested == 0:
        set_setting(db, f"{setting_prefix}_last_result", json.dumps({"day_et": day, "total_sent_today": already_sent, "status": "daily_target_reached", "policy_replanned": policy_replanned, "newly_planned": newly_planned, "released_unassigned": released_unassigned}))
        set_setting(db, f"{setting_prefix}_status", "complete")
        set_setting(db, f"{setting_prefix}_next_run_at", next_due_run(db, sender_email))
        return 0

    explicit_ids = [int(value) for value in args.contact_ids.split(",") if value.strip()]
    selected_candidates = candidate_contacts(
        db,
        explicit_ids=explicit_ids,
        limit=requested,
        sender_email=sender_email,
    )
    candidates, policy_held = partition_policy_allowed_contacts(
        db,
        selected_candidates,
        config,
    )
    if args.dry_run:
        print(json.dumps({
            "day_et": day,
            "provider": args.provider,
            "sender_email": sender_email,
            "already_sent": already_sent,
            "selected": len(candidates),
            "policy_held": policy_held,
            "contacts": [
                {"id": int(row["id"]), "channel": row["channel_name"], "email": row["email"]}
                for row in candidates
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    provider_row = db.execute(
        "SELECT status FROM outreach_sender_accounts WHERE sender_email=?",
        (sender_email,),
    ).fetchone()
    if args.provider == GOOGLE_PROVIDER and (
        not provider_row or str(provider_row["status"]) not in {"active", "active_warmup"}
    ):
        set_setting(db, "gmail_scheduler_status", "configuration_required")
        set_setting(
            db,
            "gmail_scheduler_last_result",
            json.dumps({"day_et": day, "sent": 0, "status": "configuration_required"}),
        )
        return 0
    if not candidates:
        status = "policy_held" if policy_held else empty_queue_status(db, sender_email)
        set_setting(db, f"{setting_prefix}_last_result", json.dumps({"day_et": day, "sent": 0, "status": status, "policy_held": policy_held, "policy_replanned": policy_replanned, "newly_planned": newly_planned, "released_unassigned": released_unassigned}))
        set_setting(db, f"{setting_prefix}_status", status)
        set_setting(db, f"{setting_prefix}_next_run_at", next_due_run(db, sender_email))
        return 0

    if args.provider == GOOGLE_PROVIDER:
        transport_ok, transport_detail = gmail_smtp_transport_available()
        set_setting(db, 'gmail_smtp_transport_status', 'available' if transport_ok else 'unavailable')
        set_setting(db, 'gmail_smtp_transport_checked_at', iso_utc())
        if not transport_ok:
            result = {
                "day_et": day,
                "sent": 0,
                "status": "transport_unavailable",
                "detail": transport_detail,
                "selected": len(candidates),
            }
            set_setting(db, "gmail_scheduler_status", "transport_unavailable")
            set_setting(db, "gmail_scheduler_last_result", json.dumps(result, separators=(",", ":")))
            set_setting(db, "gmail_scheduler_next_run_at", next_due_run(db, sender_email))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0
        credential = google_app_password()
    else:
        credential = refresh_access_token(db)
    template_cache: dict[str, tuple[str, str, str]] = {}
    token_refreshed_at = time.monotonic()
    run_started = iso_utc()
    set_setting(db, f"{setting_prefix}_last_run_at", run_started)
    set_setting(db, f"{setting_prefix}_status", "running")
    set_setting(db, f"{setting_prefix}_next_run_at", next_due_run(db, sender_email))
    sent = 0
    failed = 0
    delivery_unknown_this_run = 0
    consecutive_failures = 0
    window_closed = False
    for index, contact in enumerate(candidates):
        if not campaign_send_window_open(day):
            window_closed = True
            break
        if delivery_committed_today(db, day, sender_email) >= daily_hard_cap:
            break
        current = db.execute("SELECT * FROM contacts WHERE id=? AND do_not_contact=0 AND first_sent_at IS NULL AND outreach_status='not_contacted' AND planned_sender_email=?", (contact['id'], sender_email)).fetchone()
        if not current:
            continue
        if __package__:
            from .initial_template_router import valid_recipient
        else:
            from initial_template_router import valid_recipient
        if not valid_recipient(current['email']):
            db.execute("UPDATE contacts SET validation_status='invalid',next_action='邮箱格式无效，需核实原始公开来源；不猜测修正。',updated_at=datetime('now') WHERE id=?", (contact['id'],))
            db.commit()
            continue
        contact = current
        if __package__:
            from .creator_activity import activity_days, check_activity, recent
        else:
            from creator_activity import activity_days, check_activity, recent
        required_days = activity_days(db)
        if required_days:
            activity = check_activity(db, contact['channel_url'], days=required_days)
            if not recent(activity, required_days):
                inactive = activity.get('status') == 'inactive'
                policy_held.append({'id':int(contact['id']),'reason':
                    'no_recent_upload' if inactive else 'recent_activity_unverified'})
                db.execute("""UPDATE contacts SET next_eligible_send_at=?,next_action=?,updated_at=datetime('now')
                  WHERE id=? AND outreach_status='not_contacted' AND first_sent_at IS NULL""",
                  (iso_utc(utc_now()+timedelta(hours=4)),
                   f'近 {required_days} 天未发布视频，等待新发布记录。' if inactive else f'待核验：最近 {required_days} 天发布记录。',
                   contact['id']))
                db.commit()
                continue
        if args.provider == "microsoft_graph" and time.monotonic() - token_refreshed_at >= 45 * 60:
            credential = refresh_access_token(db)
            token_refreshed_at = time.monotonic()
        queued_at = iso_utc()
        template_key, experiment_id, variant = contact_template_metadata(contact, config)
        if template_automation_mode(db,template_key)!='auto' or not initial_template_is_approved(db,template_key):
            policy_held.append({'id':int(contact['id']),'template_key':template_key,'reason':'approval_changed_before_send'})
            continue
        template_cache[template_key] = approved_initial_template(db, template_key)
        approved_subject, approved_body, version = template_cache[template_key]
        rendered_body = render_initial_body(approved_body, contact)
        try:
            db.execute('BEGIN IMMEDIATE')
            # Activity lookup can involve network I/O. Recheck suppression and
            # assignment in the same short transaction that reserves delivery.
            if not db.execute("""SELECT 1 FROM contacts WHERE id=? AND do_not_contact=0
              AND first_sent_at IS NULL AND outreach_status='not_contacted'
              AND planned_sender_email=? AND validation_status='send_ready'""",
              (contact['id'],sender_email)).fetchone():
                db.rollback()
                continue
            # Activity checks, token refreshes and database lock waits may cross
            # 21:00 ET or the campaign day. Do not reserve a submission after that.
            if not campaign_send_window_open(day):
                db.rollback()
                window_closed = True
                break
            db.execute(
                """INSERT INTO outreach_send_log(
                     contact_id, campaign_kind, campaign_day_et, status, queued_at, template_version,
                     sender_email,provider,experiment_id,variant,recipient_timezone,timezone_source,send_window,scheduled_for
                   ) VALUES (?, 'creator_initial', ?, 'sending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(contact["id"]), day, queued_at, version, sender_email,
                    str(config["provider"]), experiment_id, variant,
                    contact["send_timezone"], contact["timezone_source"], contact["send_window"],
                    contact["next_eligible_send_at"],
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            continue

        if args.provider == GOOGLE_PROVIDER:
            ok, detail = send_gmail_smtp(
                credential,
                contact,
                version,
                approved_subject,
                rendered_body,
                experiment_id=experiment_id,
                variant=variant,
            )
        else:
            ok, detail = send_mail(credential, contact, version, approved_subject, rendered_body)
            if not ok and "HTTP 401" in detail and campaign_send_window_open(day):
                credential = refresh_access_token(db)
                token_refreshed_at = time.monotonic()
                if campaign_send_window_open(day):
                    ok, detail = send_mail(credential, contact, version, approved_subject, rendered_body)
        if ok:
            sent_at = iso_utc()
            db.execute(
                "UPDATE outreach_send_log SET status = 'sent', sent_at = ?, error = NULL WHERE contact_id = ? AND campaign_kind = 'creator_initial'",
                (sent_at, int(contact["id"])),
            )
            db.execute(
                """UPDATE contacts SET
                     outreach_status = 'sent',
                     first_sent_at = COALESCE(first_sent_at, ?),
                     followup1_due = COALESCE(followup1_due, datetime(?, '+24 hours')),
                     followup2_due = COALESCE(followup2_due, date(?, '+4 days')),
                     notes = notes || CASE WHEN notes = '' THEN '' ELSE char(10) END || ?,
                     updated_at = datetime('now')
                   WHERE id = ?""",
                (
                    sent_at,
                    sent_at,
                    sent_at,
                    f"{args.provider} scheduler: accepted from {sender_email}.",
                    int(contact["id"]),
                ),
            )
            db.commit()
            sent += 1
            consecutive_failures = 0
            print(f"accepted {sent}/{len(candidates)}: {contact['channel_name']} [{version}]", flush=True)
        elif detail.startswith("DELIVERY_UNKNOWN:"):
            db.execute(
                "UPDATE outreach_send_log SET status = 'delivery_unknown', error = ? WHERE contact_id = ? AND campaign_kind = 'creator_initial'",
                (detail, int(contact["id"])),
            )
            db.commit()
            delivery_unknown_this_run += 1
            set_setting(db, f"{setting_prefix}_status", "paused_delivery_unknown")
            print(
                f"delivery unknown for {contact['channel_name']}; paused to prevent duplicate delivery: {detail}",
                file=sys.stderr,
                flush=True,
            )
            break
        else:
            db.execute(
                "UPDATE outreach_send_log SET status = 'failed', error = ? WHERE contact_id = ? AND campaign_kind = 'creator_initial'",
                (detail, int(contact["id"])),
            )
            db.commit()
            failed += 1
            consecutive_failures += 1
            print(f"failed {contact['channel_name']}: {detail}", file=sys.stderr, flush=True)
            if "InvalidAuthenticationToken" in detail or "Authorization" in detail or "HTTP 401" in detail:
                break
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                set_setting(db, f"{setting_prefix}_status", "paused_delivery_failures")
                print(
                    f"circuit breaker: paused after {consecutive_failures} consecutive delivery failures",
                    file=sys.stderr,
                    flush=True,
                )
                break
        if index < len(candidates) - 1:
            if args.interval_seconds is not None:
                min_interval = max_interval = max(0.0, args.interval_seconds)
            elif args.provider == GOOGLE_PROVIDER:
                min_interval = float(config["min_interval_seconds"])
                max_interval = float(config["max_interval_seconds"])
            else:
                min_interval = max(0.0, args.min_interval_seconds)
                max_interval = max(min_interval, args.max_interval_seconds)
            if max_interval > 0:
                delay = random.uniform(min_interval, max_interval)
                print(f"waiting {delay:.0f}s before next email", flush=True)
                time.sleep(delay)

    result = {
        "day_et": day,
        "started_at": run_started,
        "completed_at": iso_utc(),
        "selected": len(candidates),
        "sent": sent,
        "failed": failed,
        "delivery_unknown": delivery_unknown + delivery_unknown_this_run,
        "policy_held": policy_held,
        "sender_email": sender_email,
        "provider": args.provider,
        "total_sent_today": sent_today(db, day, sender_email),
        "status": (
            "paused_delivery_unknown"
            if delivery_unknown_this_run
            else "waiting_for_send_window" if window_closed
            else "complete" if sent == len(candidates) else "partial"
        ),
    }
    result["newly_planned"] = newly_planned + append_unplanned_contacts(db)
    result["released_unassigned"] = released_unassigned
    result["policy_replanned"] = policy_replanned
    remaining_row = db.execute(
        """SELECT COUNT(*) count FROM contacts WHERE validation_status='send_ready'
           AND outreach_status='not_contacted' AND first_sent_at IS NULL AND do_not_contact=0
           AND next_eligible_send_at IS NOT NULL"""
    ).fetchone()
    result["remaining_planned"] = int(remaining_row["count"])
    set_setting(db, f"{setting_prefix}_last_result", json.dumps(result, separators=(",", ":")))
    set_setting(db, f"{setting_prefix}_status", result["status"])
    set_setting(db, f"{setting_prefix}_next_run_at", next_eastern_run() if window_closed else next_due_run(db, sender_email))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if failed == 0 else 1


def should_continue_scheduled(args, result, started_day, now_et):
    return bool(args.scheduled and not args.dry_run and not args.replan_only and not args.contact_ids
        and now_et.date().isoformat()==started_day and 8<=now_et.hour<8+SEND_WINDOW_HOURS
        and result.get('status')=='complete' and int(result.get('sent',0))>0
        and not result.get('failed') and not result.get('delivery_unknown')
        and int(result.get('total_sent_today',0))<int(sender_config(args.provider)['daily_target']))


def main() -> int:
    args = parse_args()
    if args.provider == GOOGLE_PROVIDER and args.lock == DEFAULT_LOCK:
        args.lock = PROJECT_ROOT / ".wrangler/gmail-outreach-scheduler.lock"
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        started_day=eastern_day()
        while True:
            code=run(args)
            if code:
                return code
            check=connect(args.db)
            try:
                prefix='gmail_scheduler' if args.provider==GOOGLE_PROVIDER else 'scheduler'
                result=json.loads(get_setting(check,f'{prefix}_last_result') or '{}')
            finally:
                check.close()
            if not should_continue_scheduled(args,result,started_day,utc_now().astimezone(CAMPAIGN_ZONE)):
                return code
            # Preserve spacing across batch boundaries, without a 5–10 minute idle gap.
            pause=args.interval_seconds if args.interval_seconds is not None else GOOGLE_MIN_INTERVAL_SECONDS if args.provider==GOOGLE_PROVIDER else args.min_interval_seconds
            time.sleep(max(1.0,pause))


if __name__ == "__main__":
    raise SystemExit(main())
