#!/usr/bin/env python3
"""Merge the isolated lead-discovery spool into the outreach database.

Collectors write only to the spool.  This process is the single writer that
copies qualified contacts and discovery telemetry into the Miniflare/D1 SQLite
database in one short transaction, keeping the dashboard and reply workers
responsive.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "lead-discovery.sqlite"
DEFAULT_TARGET = (
    PROJECT_ROOT
    / ".wrangler/state/v3/d1/miniflare-D1DatabaseObject"
    / "faaf2b0445ab934c3aac48ddf0cdfade8f9bac050be98993748742cdd2cb05fb.sqlite"
)
DEFAULT_LOCK = PROJECT_ROOT / ".wrangler" / "lead-discovery-merger.lock"
SOURCE_NAMES = (
    "YouTube 24小时公开邮箱发现",
    "Apple Podcasts公开创作者来源",
)
DISCOVERY_CONTACT_WHERE = (
    "(source_file LIKE 'continuous_lead_discovery:%' "
    "OR source_file LIKE 'public_podcast_discovery:%')"
)
CURSORS = (
    ('contacts','updated_at','id','last_contact_updated_at','last_contact_id',DISCOVERY_CONTACT_WHERE),
    ('lead_discovery_channels','last_seen_at','channel_key','last_channel_seen_at','last_channel_key',''),
    ('lead_discovery_emails','last_seen_at','email','last_email_seen_at','last_email_key',''),
    ('lead_discovery_jobs','updated_at','id','last_job_updated_at','last_job_id',''),
    ('lead_provenance','discovered_at','id','last_provenance_at','last_provenance_id',''),
    ('creator_activity_cache','checked_at','channel_url','last_activity_checked_at','last_activity_url',''),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{path.resolve()}?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=120)
    else:
        db = sqlite3.connect(path, timeout=120)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=120000")
    if not readonly:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
    return db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--initialize", action="store_true")
    return parser.parse_args()


def ensure_merge_schema(db: sqlite3.Connection) -> None:
    db.execute('''CREATE TABLE IF NOT EXISTS creator_activity_cache(
      channel_url TEXT PRIMARY KEY,channel_id TEXT,status TEXT NOT NULL,
      published_at TEXT,video_url TEXT,checked_at TEXT NOT NULL,detail TEXT)''')
    for table, names in {
        'lead_discovery_jobs': ('last_new_channels','last_candidates','low_yield_streak'),
        'lead_discovery_runs': ('new_channels','duplicate_channels','eligible_contacts','active_channels',
                                'inactive_channels','unknown_activity','metrics_version'),
    }.items():
        columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if columns:
            for name in names:
                if name not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
    columns = {r[1] for r in db.execute('PRAGMA table_info(lead_discovery_runs)')}
    if columns:
        for name in ('selection_mode','selection_reason'):
            if name not in columns:
                db.execute(f'ALTER TABLE lead_discovery_runs ADD COLUMN {name} TEXT')
    db.execute(
        """CREATE TABLE IF NOT EXISTS lead_discovery_merge_state (
             id INTEGER PRIMARY KEY CHECK(id=1),
             last_run_id INTEGER NOT NULL DEFAULT 0,
             last_contact_updated_at TEXT NOT NULL DEFAULT '',
             last_channel_seen_at TEXT NOT NULL DEFAULT '',
             last_email_seen_at TEXT NOT NULL DEFAULT '',
             last_job_updated_at TEXT NOT NULL DEFAULT '',
             last_job_id INTEGER NOT NULL DEFAULT 0,
             last_provenance_at TEXT NOT NULL DEFAULT '',
             last_merged_at TEXT,
             last_error TEXT,
             updated_at TEXT NOT NULL
           )"""
    )
    state_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(lead_discovery_merge_state)").fetchall()
    }
    if "last_job_id" not in state_columns:
        db.execute(
            "ALTER TABLE lead_discovery_merge_state ADD COLUMN last_job_id INTEGER NOT NULL DEFAULT 0"
        )
    for _,_,key,time_field,key_field,_ in CURSORS:
        for name,definition in ((time_field,"TEXT NOT NULL DEFAULT ''"),
                                (key_field,"INTEGER NOT NULL DEFAULT 0" if key=='id' else "TEXT NOT NULL DEFAULT ''")):
            if name not in state_columns and name!='last_job_id':
                db.execute(f'ALTER TABLE lead_discovery_merge_state ADD COLUMN {name} {definition}')
    now = utc_now()
    db.execute(
        """INSERT OR IGNORE INTO lead_discovery_merge_state(id,updated_at)
           VALUES(1,?)""",
        (now,),
    )
    multisource_migration = db.execute(
        "SELECT value FROM outreach_settings WHERE key='lead_discovery_multisource_merge_v1'"
    ).fetchone()
    if not multisource_migration:
        # Reset only the idempotent contact/email/provenance cursors once so
        # public sources added after the original YouTube spool are not skipped
        # merely because their first timestamps predate the YouTube watermark.
        db.execute(
            """UPDATE lead_discovery_merge_state SET
                 last_contact_updated_at='',last_email_seen_at='',last_provenance_at='',updated_at=?
               WHERE id=1""",
            (now,),
        )
        db.execute(
            """INSERT INTO outreach_settings(key,value,updated_at)
               VALUES('lead_discovery_multisource_merge_v1','complete',?)""",
            (now,),
        )
    db.execute(
        """INSERT INTO outreach_settings(key,value,updated_at)
           VALUES('lead_discovery_storage_mode','isolated_spool',?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
        (now,),
    )


def scalar(db: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = db.execute(sql, params).fetchone()
    return row[0] if row else None


def maxima(source: sqlite3.Connection) -> dict[str, Any]:
    values={'last_run_id':int(scalar(source,'SELECT coalesce(max(id),0) FROM lead_discovery_runs') or 0)}
    for table,column,key,time_field,key_field,where in CURSORS:
        row=source.execute(f'SELECT {column},{key} FROM {table} '
          + (f'WHERE {where} ' if where else '')
          + f'ORDER BY julianday({column}) DESC,{key} DESC LIMIT 1').fetchone()
        values[time_field]=row[0] if row else ''
        values[key_field]=row[1] if row else (0 if key=='id' else '')
    return values


def save_watermarks(target, values, now):
    allowed={'last_run_id'} | {field for spec in CURSORS for field in spec[3:5]}
    if not set(values)<=allowed:
        raise ValueError('Unknown merge cursor')
    target.execute('UPDATE lead_discovery_merge_state SET '+','.join(f'{key}=?' for key in values)
                   + ',last_merged_at=?,last_error=NULL,updated_at=? WHERE id=1',
                   (*values.values(),now,now))


def initialize(source: sqlite3.Connection, target: sqlite3.Connection) -> dict[str, Any]:
    watermarks = maxima(source)
    now = utc_now()
    target.execute("BEGIN IMMEDIATE")
    try:
        ensure_merge_schema(target)
        save_watermarks(target,watermarks,now)
        target.commit()
    except Exception:
        target.rollback()
        raise
    return {"status": "initialized", **watermarks, "at": now}


def rows_since(
    db: sqlite3.Connection,
    table: str,
    timestamp_column: str,
    watermark: str,
    order_tail: str,
    limit: int,
    extra_where: str = "",
    after_key: Any = None,
) -> list[sqlite3.Row]:
    after_key = (0 if order_tail=='id' else '') if after_key is None else after_key
    where = f"(julianday({timestamp_column})>coalesce(julianday(?),0) OR (julianday({timestamp_column})=julianday(?) AND {order_tail}>?))"
    if extra_where:
        where += f" AND ({extra_where})"
    return db.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY julianday({timestamp_column}),{order_tail} LIMIT ?",
        (watermark, watermark, after_key, limit),
    ).fetchall()


def job_rows_since(
    db: sqlite3.Connection,
    timestamp: str,
    job_id: int,
    limit: int,
) -> list[sqlite3.Row]:
    return rows_since(db,'lead_discovery_jobs','updated_at',timestamp,'id',limit,after_key=job_id)


def merge_activity(target, rows):
    count=0
    for raw in rows:
        row=dict(raw)
        count+=target.execute('''INSERT INTO creator_activity_cache
          (channel_url,channel_id,status,published_at,video_url,checked_at,detail) VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(channel_url) DO UPDATE SET channel_id=excluded.channel_id,status=excluded.status,
          published_at=excluded.published_at,video_url=excluded.video_url,checked_at=excluded.checked_at,detail=excluded.detail
          WHERE julianday(excluded.checked_at)>julianday(creator_activity_cache.checked_at)''',
          tuple(row.get(k) for k in ('channel_url','channel_id','status','published_at','video_url','checked_at','detail'))).rowcount
    return count


def job_rows_by_ids(db: sqlite3.Connection, job_ids: list[int]) -> list[sqlite3.Row]:
    if not job_ids:
        return []
    placeholders = ",".join("?" for _ in job_ids)
    return db.execute(
        f"SELECT * FROM lead_discovery_jobs WHERE id IN ({placeholders}) ORDER BY id",
        tuple(job_ids),
    ).fetchall()


def table_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")]


def upsert_rows(
    db: sqlite3.Connection,
    table: str,
    rows: Iterable[dict[str, Any] | sqlite3.Row],
    conflict_columns: tuple[str, ...],
    *,
    excluded_columns: tuple[str, ...] = (),
) -> int:
    materialized = [dict(row) for row in rows]
    if not materialized:
        return 0
    available = set(table_columns(db, table))
    columns = [
        name
        for name in materialized[0]
        if name in available and name not in excluded_columns
    ]
    updates = [name for name in columns if name not in conflict_columns]
    placeholders = ",".join("?" for _ in columns)
    conflict = ",".join(conflict_columns)
    update_sql = ",".join(f"{name}=excluded.{name}" for name in updates)
    sql = f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})"
    sql += f" ON CONFLICT({conflict}) DO UPDATE SET {update_sql}"
    db.executemany(sql, [tuple(row.get(name) for name in columns) for row in materialized])
    return len(materialized)


NEW_CONTACT_COLUMNS = (
    "email",
    "channel_name",
    "channel_url",
    "platform",
    "category",
    "ai_match",
    "curated_ai",
    "ai_confidence",
    "validation_status",
    "verification_proof",
    "email_domain",
    "subscribers",
    "country",
    "language",
    "topic",
    "priority",
    "fit_score",
    "source_file",
    "source_count",
    "source_rows",
    "outreach_status",
    "reply_status",
    "deal_status",
    "do_not_contact",
    "notes",
    "updated_at",
)


def merge_contacts(target: sqlite3.Connection, rows: list[sqlite3.Row]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    insert_sql = (
        f"INSERT OR IGNORE INTO contacts({','.join(NEW_CONTACT_COLUMNS)}) "
        f"VALUES({','.join('?' for _ in NEW_CONTACT_COLUMNS)})"
    )
    for raw in rows:
        row = dict(raw)
        cursor = target.execute(insert_sql, tuple(row.get(name) for name in NEW_CONTACT_COLUMNS))
        if cursor.rowcount:
            inserted += 1
            continue
        # Never allow discovery to alter a contacted creator's conversation or
        # scheduling state.  It may only refresh qualification data for an
        # existing, still-unsent discovery contact.
        cursor = target.execute(
            """UPDATE contacts SET
                 channel_name=?,channel_url=?,category=?,ai_match=?,curated_ai=?,ai_confidence=?,
                 validation_status=?,verification_proof=?,subscribers=?,country=?,language=?,topic=?,
                 priority=?,fit_score=?,source_count=?,source_rows=?,updated_at=?
               WHERE lower(email)=lower(?)
                 AND (source_file LIKE 'continuous_lead_discovery:%'
                      OR source_file LIKE 'public_podcast_discovery:%')
                 AND outreach_status='not_contacted' AND first_sent_at IS NULL""",
            (
                row.get("channel_name"),
                row.get("channel_url"),
                row.get("category"),
                row.get("ai_match"),
                row.get("curated_ai"),
                row.get("ai_confidence"),
                row.get("validation_status"),
                row.get("verification_proof"),
                row.get("subscribers"),
                row.get("country"),
                row.get("language"),
                row.get("topic"),
                row.get("priority"),
                row.get("fit_score"),
                row.get("source_count"),
                row.get("source_rows"),
                row.get("updated_at"),
                row.get("email"),
            ),
        )
        updated += max(0, cursor.rowcount)
    return inserted, updated


def merge_once(source: sqlite3.Connection, target: sqlite3.Connection, limit: int) -> dict[str, Any]:
    # Read one stable source snapshot, then write to the outreach database in
    # small idempotent transactions.  The former single 5,000-row transaction
    # could block inbox and reply agents for several minutes.
    target.execute("BEGIN IMMEDIATE")
    try:
        ensure_merge_schema(target)
        state_row = target.execute("SELECT * FROM lead_discovery_merge_state WHERE id=1").fetchone()
        assert state_row is not None
        state = dict(state_row)
        stale_target_job_ids = [
            int(row["id"])
            for row in target.execute(
                """SELECT id FROM lead_discovery_jobs
                   WHERE status='running' AND (lease_until IS NULL OR datetime(lease_until)<datetime('now'))
                   ORDER BY id LIMIT ?""",
                (limit,),
            ).fetchall()
        ]
        unfinished_run_ids=[int(row[0]) for row in target.execute(
            "SELECT id FROM lead_discovery_runs WHERE status='running' ORDER BY id LIMIT ?",(limit,))]
        target.commit()

        source.execute("BEGIN")
        pages={};newest={}
        for table,column,key,time_field,key_field,where in CURSORS:
            rows=rows_since(source,table,column,state[time_field],key,limit,where,state[key_field])
            pages[table]=rows
            newest[time_field]=rows[-1][column] if rows else state[time_field]
            newest[key_field]=rows[-1][key] if rows else state[key_field]
        contact_rows=pages['contacts'];channel_rows=pages['lead_discovery_channels']
        email_rows=pages['lead_discovery_emails'];provenance_rows=pages['lead_provenance']
        activity_rows=pages['creator_activity_cache']
        incremental_job_rows=pages['lead_discovery_jobs']
        reconciled_job_rows = job_rows_by_ids(source, stale_target_job_ids)
        job_rows_by_id = {int(row["id"]): row for row in incremental_job_rows}
        job_rows_by_id.update({int(row["id"]): row for row in reconciled_job_rows})
        job_rows = list(job_rows_by_id.values())
        run_floor = int(state["last_run_id"] or 0)
        run_rows = source.execute(
            "SELECT * FROM lead_discovery_runs WHERE id>? ORDER BY id LIMIT ?",
            (run_floor, limit),
        ).fetchall()
        newest['last_run_id']=max((row['id'] for row in run_rows),default=run_floor)
        # A slow run may finish more than twenty newer runs later. Reconcile all
        # known unfinished runs instead of silently losing their final metrics.
        if unfinished_run_ids:
            marks=','.join('?' for _ in unfinished_run_ids)
            recovered=source.execute(f'SELECT * FROM lead_discovery_runs WHERE id IN ({marks})',unfinished_run_ids).fetchall()
            run_rows=list({row['id']:row for row in [*run_rows,*recovered]}.values())
        state_rows = source.execute("SELECT * FROM lead_discovery_state").fetchall()
        source_summaries = source.execute(
            "SELECT * FROM lead_sources WHERE name IN (?,?)", SOURCE_NAMES
        ).fetchall()

        def chunks(rows: list[sqlite3.Row], size: int = 100) -> Iterable[list[sqlite3.Row]]:
            for start in range(0, len(rows), size):
                yield rows[start:start + size]

        inserted = updated = 0
        for batch in chunks(contact_rows):
            target.execute("BEGIN IMMEDIATE")
            batch_inserted, batch_updated = merge_contacts(target, batch)
            target.commit()
            inserted += batch_inserted
            updated += batch_updated

        channels_count = emails_count = jobs_count = runs_count = states_count = provenance_count = 0
        for batch in chunks(channel_rows):
            target.execute("BEGIN IMMEDIATE")
            channels_count += upsert_rows(target, "lead_discovery_channels", batch, ("channel_key",))
            target.commit()

        activity_count=0
        for batch in chunks(activity_rows):
            target.execute('BEGIN IMMEDIATE')
            activity_count+=merge_activity(target,batch)
            target.commit()

        for batch in chunks(email_rows):
            target.execute("BEGIN IMMEDIATE")
            mapped: list[dict[str, Any]] = []
            for raw in batch:
                item = dict(raw)
                contact = target.execute(
                    "SELECT id FROM contacts WHERE lower(email)=lower(?)", (item["email"],)
                ).fetchone()
                item["contact_id"] = int(contact[0]) if contact else None
                mapped.append(item)
            emails_count += upsert_rows(target, "lead_discovery_emails", mapped, ("email",))
            target.commit()

        for table, rows, conflicts in (
            ("lead_discovery_jobs", job_rows, ("id",)),
            ("lead_discovery_runs", run_rows, ("id",)),
            ("lead_discovery_state", state_rows, ("worker_key",)),
        ):
            for batch in chunks(rows):
                target.execute("BEGIN IMMEDIATE")
                count = upsert_rows(target, table, batch, conflicts)
                target.commit()
                if table == "lead_discovery_jobs": jobs_count += count
                elif table == "lead_discovery_runs": runs_count += count
                else: states_count += count

        for batch in chunks(provenance_rows):
            target.execute("BEGIN IMMEDIATE")
            mapped = []
            for raw in batch:
                item = dict(raw)
                contact = target.execute(
                    "SELECT id FROM contacts WHERE lower(email)=lower(?)", (item["email"],)
                ).fetchone()
                item["contact_id"] = int(contact[0]) if contact else None
                mapped.append(item)
            provenance_count += upsert_rows(
                target, "lead_provenance", mapped, ("email", "source_url"), excluded_columns=("id",)
            )
            target.commit()

        if source_summaries:
            target.execute("BEGIN IMMEDIATE")
            upsert_rows(target, "lead_sources", source_summaries, ("name",), excluded_columns=("id",))
            target.commit()

        now = utc_now()
        target.execute("BEGIN IMMEDIATE")
        ensure_merge_schema(target)
        save_watermarks(target,newest,now)
        target.commit()
        source.commit()
    except Exception as exc:
        try:
            source.rollback()
        except Exception:
            pass
        target.rollback()
        try:
            target.execute("BEGIN IMMEDIATE")
            ensure_merge_schema(target)
            target.execute(
                "UPDATE lead_discovery_merge_state SET last_error=?,updated_at=? WHERE id=1",
                (str(exc)[:1000], utc_now()),
            )
            target.commit()
        except Exception:
            target.rollback()
        raise
    return {
        "status": "merged",
        "contacts_inserted": inserted,
        "contacts_refreshed": updated,
        "channels": channels_count,
        "emails": emails_count,
        "jobs": jobs_count,
        "runs": runs_count,
        "states": states_count,
        "provenance": provenance_count,
        "activity_evidence": activity_count,
        "at": now,
    }


def main() -> int:
    args = parse_args()
    if not args.source.exists():
        raise SystemExit(f"discovery source does not exist: {args.source}")
    if not args.target.exists():
        raise SystemExit(f"outreach target does not exist: {args.target}")
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}))
            return 0
        source = connect(args.source, readonly=True)
        target = connect(args.target)
        try:
            result = initialize(source, target) if args.initialize else merge_once(source, target, max(100, args.limit))
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        finally:
            source.close()
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
