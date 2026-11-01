#!/usr/bin/env python3
"""Preflight the 08:00 ET creator send and alert when the first batch is missing."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from datetime import datetime, time as clock_time, timezone
from pathlib import Path

if __package__:
    from .outreach_scheduler import CAMPAIGN_ZONE, DEFAULT_DB, GOOGLE_MAILBOX, MAILBOX, SEND_WINDOW_HOURS, connect, microsoft_outbound_block_reason
    from .creator_activity import activity_days, ensure_activity_schema
else:
    from outreach_scheduler import CAMPAIGN_ZONE, DEFAULT_DB, GOOGLE_MAILBOX, MAILBOX, SEND_WINDOW_HOURS, connect, microsoft_outbound_block_reason
    from creator_activity import activity_days, ensure_activity_schema


SENDER_LABELS = ("ai.videocoding.outreach", "ai.videocoding.gmail-outreach")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--force", choices=("preflight", "zero-send", "progress"))
    parser.add_argument("--no-notification", action="store_true")
    return parser.parse_args()


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def service_loaded(label: str) -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def notify(message: str) -> None:
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        ["/usr/bin/osascript", "-e", f'display notification "{safe}" with title "VideoCoding 外联告警"'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def selected_phase(now_et: datetime, forced: str | None) -> str | None:
    if forced:
        return forced
    current = now_et.time().replace(tzinfo=None)
    if clock_time(7, 55) <= current < clock_time(8, 0):
        return "preflight"
    if clock_time(8, 5) <= current < clock_time(8, 15):
        return "zero-send"
    if clock_time(8, 15) <= current < clock_time(8+SEND_WINDOW_HOURS, 15):
        return "progress"
    return None


def preflight(db: sqlite3.Connection, day: str) -> dict[str, object]:
    issues: list[str] = []
    rule = db.execute(
        "SELECT mode,enabled FROM automation_rules WHERE rule_key='first_contact'"
    ).fetchone()
    if not rule or not int(rule["enabled"] or 0) or str(rule["mode"]) != "auto":
        issues.append("首次邀请规则未处于 auto")
    template = db.execute(
        """SELECT
             SUM(CASE WHEN enabled=1 AND approval_status='approved' AND length(trim(body))>0 THEN 1 ELSE 0 END) approved,
             SUM(CASE WHEN enabled=1 AND (approval_status<>'approved' OR length(trim(body))=0) THEN 1 ELSE 0 END) unsafe
           FROM outreach_templates WHERE stage='first_contact'"""
    ).fetchone()
    if int(template["approved"] or 0) < 1 or int(template["unsafe"] or 0) > 0:
        issues.append("首次邀请模板未全部通过批准检查")
    accounts = {
        str(row["sender_email"]): str(row["status"])
        for row in db.execute(
            "SELECT sender_email,status FROM outreach_sender_accounts WHERE sender_email IN (?,?)",
            (MAILBOX, GOOGLE_MAILBOX),
        )
    }
    for sender in (MAILBOX, GOOGLE_MAILBOX):
        if accounts.get(sender) not in {"active", "active_warmup"}:
            issues.append(f"发件通道未启用：{sender}")
    queue = db.execute(
        """SELECT COUNT(*) count FROM contacts
           WHERE planned_send_date=? AND validation_status='send_ready'
             AND outreach_status='not_contacted' AND first_sent_at IS NULL AND do_not_contact=0""",
        (day,),
    ).fetchone()
    if int(queue["count"] or 0) < 1:
        issues.append("当天发送队列为空")
    supply=progress_check(db,day,datetime.combine(datetime.fromisoformat(day).date(),clock_time(8),CAMPAIGN_ZONE))
    issues.extend(supply['issues'])
    missing_services = [label for label in SENDER_LABELS if not service_loaded(label)]
    if missing_services:
        issues.append("发送任务未加载：" + ", ".join(missing_services))
    return {
        "phase": "preflight",
        "day_et": day,
        "status": "healthy" if not issues else "alert",
        "issues": issues,
        "planned": int(queue["count"] or 0),
        "checked_at": iso_utc(),
    }


def zero_send_check(db: sqlite3.Connection, day: str) -> dict[str, object]:
    row = db.execute(
        """SELECT COUNT(*) count FROM outreach_send_log
           WHERE campaign_day_et=? AND status='sent'""",
        (day,),
    ).fetchone()
    sent = int(row["count"] or 0)
    return {
        "phase": "zero-send",
        "day_et": day,
        "status": "healthy" if sent > 0 else "alert",
        "issues": [] if sent > 0 else ["纽约时间08:05仍无首封成功发送"],
        "sent": sent,
        "checked_at": iso_utc(),
    }


def progress_check(db: sqlite3.Connection, day: str, now_et: datetime) -> dict[str, object]:
    """A few early sends are not success: check per-channel pace and supply."""
    targets={MAILBOX:700,GOOGLE_MAILBOX:50}
    elapsed=max(0.0,min(float(SEND_WINDOW_HOURS),now_et.hour+now_et.minute/60-8))
    channels=[];issues=[]
    if reason := microsoft_outbound_block_reason(db):
        issues.append(f'{MAILBOX}：微软发件账号受限，停止发送，需管理员处理；{reason}')
    days=activity_days(db)
    activity_sql='';activity_params=()
    if days:
        ensure_activity_schema(db)
        activity_sql=''' AND EXISTS(SELECT 1 FROM creator_activity_cache a
          WHERE a.channel_url=c.channel_url AND a.status='recent'
            AND julianday(a.checked_at) BETWEEN julianday('now')-2 AND julianday('now')
            AND julianday(a.published_at) BETWEEN julianday('now')-? AND julianday('now'))'''
        activity_params=(days,)
    for sender,target in targets.items():
        sent=db.execute("SELECT count(*) FROM outreach_send_log WHERE campaign_day_et=? AND sender_email=? AND status='sent'",(day,sender)).fetchone()[0]
        pending_sql="""SELECT count(*) FROM contacts c
          WHERE planned_send_date=? AND planned_sender_email=? AND validation_status='send_ready'
          AND subscribers>=1000 AND outreach_status='not_contacted' AND first_sent_at IS NULL AND do_not_contact=0
          AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=c.id AND l.campaign_kind='creator_initial')"""
        scheduled=db.execute(pending_sql,(day,sender)).fetchone()[0]
        pending=db.execute(pending_sql+activity_sql,(day,sender,*activity_params)).fetchone()[0]
        expected=int(target*elapsed/SEND_WINDOW_HOURS)
        if sent+pending<target:
            issues.append(f'{sender}：今日名单不足，还缺 {target-sent-pending} 个任务')
        if scheduled>pending:
            issues.append(f'{sender}：排队 {scheduled} 个，其中 {scheduled-pending} 个未满足近 {days} 天活跃核验，不能计入可发库存')
        if sent < expected-max(3,int(target*.05)):
            issues.append(f'{sender}：发送进度落后，已发 {sent}，此时应约 {expected}')
        channels.append({'sender':sender,'target':target,'sent':sent,'pending':pending,
                         'scheduled':scheduled,'activity_held':scheduled-pending,'expected_by_now':expected})
    return {'phase':'progress','day_et':day,'status':'alert' if issues else 'healthy','issues':issues,
            'sent':sum(c['sent'] for c in channels),'target':750,'channels':channels,
            'alert_slot':str(now_et.hour),'checked_at':iso_utc()}


def persist(db: sqlite3.Connection, result: dict[str, object]) -> bool:
    phase = str(result["phase"])
    day = str(result["day_et"])
    value = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    entity_id = f"{day}:{phase}" + (f":{result['alert_slot']}:{result['status']}" if phase=='progress' else '')
    existing = db.execute(
        """SELECT 1 FROM outreach_audit_log
           WHERE action='outreach_health_check' AND entity_type='campaign' AND entity_id=? LIMIT 1""",
        (entity_id,),
    ).fetchone()
    db.execute(
        """INSERT INTO outreach_settings(key,value,updated_at) VALUES(?,?,datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
        (f"outreach_health_{phase}", value),
    )
    if not existing:
        db.execute(
            """INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
               VALUES('outreach_health_check','campaign',?,'system',?,datetime('now'))""",
            (entity_id, value),
        )
    db.commit()
    return not bool(existing)


def main() -> int:
    args = parse_args()
    now_et = datetime.now(timezone.utc).astimezone(CAMPAIGN_ZONE)
    phase = selected_phase(now_et, args.force)
    if not phase:
        print(json.dumps({"status": "idle", "now_et": now_et.isoformat()}, separators=(",", ":")))
        return 0
    db = connect(Path(args.db))
    try:
        day=now_et.date().isoformat()
        result = (preflight(db,day) if phase=='preflight' else progress_check(db,day,now_et) if phase=='progress' else zero_send_check(db,day))
        is_new = persist(db, result)
    finally:
        db.close()
    if result["status"] == "alert" and is_new and not args.no_notification:
        notify("；".join(str(item) for item in result["issues"]))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 1 if result["status"] == "alert" else 0


if __name__ == "__main__":
    raise SystemExit(main())
