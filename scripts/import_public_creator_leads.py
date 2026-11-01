#!/usr/bin/env python3
"""Import public creator business contacts into the outreach master database.

This importer is deliberately conservative: it keeps provenance, never deletes or
overwrites campaign state, and leaves domains without mail-routing evidence out of
the send-ready queue.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from outreach_scheduler import DEFAULT_DB, connect, ensure_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = (
    Path("/Users/OPERATOR/Documents/Playground/outputs/youtube_english_expanded/english_talking_head_channels.json"),
    Path("/Users/OPERATOR/Documents/Playground/outputs/youtube_fresh_talking_head/fresh_youtube_channels.json"),
)
DEFAULT_DOMAIN_CHECKS = Path("/Users/OPERATOR/Documents/Playground/outputs/youtube_english_expanded/email_domain_checks.json")
EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$", re.I)
BLOCKED_DOMAINS = {"example.com", "domain.com", "email.com", "sentry.io"}
AI_PATTERN = re.compile(r"(?:\bai\b|\bchatgpt\b|\bgpt\b|\bllm\b|machine learning|artificial intelligence)", re.I)


@dataclass(frozen=True)
class Candidate:
    email: str
    channel_name: str
    channel_url: str
    subscribers: int | None
    category: str
    topic: str
    priority: str
    fit_score: int
    source_type: str
    source_url: str
    source_file: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source", action="append", type=Path, dest="sources")
    parser.add_argument("--domain-checks", type=Path, default=DEFAULT_DOMAIN_CHECKS)
    parser.add_argument("--apply", action="store_true", help="Write new records; default is a dry run.")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalized_email(value: Any) -> str | None:
    email = str(value or "").strip().lower().strip(".,;:()[]{}<>")
    if not EMAIL_RE.fullmatch(email) or len(email) > 254:
        return None
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64 or domain in BLOCKED_DOMAINS or domain.split(".")[0].isdigit():
        return None
    return email


def category_name(values: Any) -> str:
    raw = " ".join(str(item) for item in (values if isinstance(values, list) else [values or ""]))
    if AI_PATTERN.search(raw) or any(needle in raw for needle in ("科技", "编程")):
        return "AI & Tech"
    mappings = (
        ("商业", "Business & Marketing"), ("营销", "Business & Marketing"),
        ("金融", "Finance & Economics"), ("经济", "Finance & Economics"),
        ("健康", "Health & Fitness"), ("健身", "Health & Fitness"),
        ("教育", "Education & Science"), ("科学", "Education & Science"),
        ("历史", "History & Society"), ("社会", "History & Society"),
        ("新闻", "News & Commentary"), ("评论", "News & Commentary"),
        ("创作者", "Creative & Media"), ("设计", "Creative & Media"),
        ("职业", "Career & Growth"), ("成长", "Career & Growth"),
    )
    return next((label for needle, label in mappings if needle.lower() in raw.lower()), "Needs Review")


def load_candidates(paths: tuple[Path, ...]) -> list[Candidate]:
    candidates: dict[tuple[str, str], Candidate] = {}
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for channel in payload.get("channels", []):
            channel_url = str(channel.get("channel_url") or "").strip()
            if not channel_url.startswith("https://www.youtube.com/"):
                continue
            categories = channel.get("categories") or []
            queries = channel.get("queries") or []
            common = {
                "channel_name": str(channel.get("channel_name") or channel.get("handle") or "Unknown creator").strip()[:240],
                "channel_url": channel_url[:1000],
                "subscribers": int(channel["subscriber_count"]) if isinstance(channel.get("subscriber_count"), (int, float)) else None,
                "category": category_name(categories),
                "topic": ", ".join(str(item) for item in (queries or categories))[:500],
                "priority": str(channel.get("priority") or "P3")[:20],
                "fit_score": max(0, min(100, int(channel.get("fit_score") or 0))),
                "source_file": str(path),
            }
            groups = (
                (channel.get("emails") or [], "youtube_public_description", channel_url),
                (channel.get("website_emails") or [], "creator_linked_official_website", ""),
            )
            website_sources = list(channel.get("website_email_sources") or [])
            for addresses, source_type, default_source_url in groups:
                for index, raw_email in enumerate(addresses):
                    email = normalized_email(raw_email)
                    if not email:
                        continue
                    source_url = default_source_url
                    if source_type == "creator_linked_official_website" and index < len(website_sources):
                        source_url = str(website_sources[index])
                    if not source_url.startswith(("http://", "https://")):
                        continue
                    candidates[(email, source_url)] = Candidate(email=email, source_type=source_type, source_url=source_url[:1500], **common)
    return list(candidates.values())


def load_domain_checks(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def has_mx(domain: str, checks: dict[str, dict[str, Any]], cache: dict[str, bool]) -> bool:
    if domain in cache:
        return cache[domain]
    existing = checks.get(domain) or {}
    if existing.get("mx"):
        cache[domain] = True
        return True
    try:
        result = subprocess.run(
            ["/usr/bin/dig", "+short", "MX", domain],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        value = bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        value = False
    cache[domain] = value
    return value


def ensure_import_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS lead_provenance (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          contact_id INTEGER,
          email TEXT NOT NULL,
          source_type TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_file TEXT NOT NULL,
          contact_basis TEXT NOT NULL,
          domain_validation TEXT NOT NULL,
          discovered_at TEXT NOT NULL,
          UNIQUE(email, source_url)
        );
        CREATE INDEX IF NOT EXISTS lead_provenance_contact_idx ON lead_provenance(contact_id);
        CREATE TABLE IF NOT EXISTS lead_sources (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          source_type TEXT NOT NULL,
          location TEXT NOT NULL,
          policy_status TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          discovered_count INTEGER NOT NULL DEFAULT 0,
          imported_count INTEGER NOT NULL DEFAULT 0,
          last_run_at TEXT,
          last_error TEXT,
          updated_at TEXT NOT NULL
        );
        """
    )


def contact_values(candidate: Candidate, mx_valid: bool) -> tuple[Any, ...]:
    ai_text = f"{candidate.category} {candidate.topic}".lower()
    ai_match = int(bool(AI_PATTERN.search(ai_text)))
    curated_ai = int(candidate.category == "AI & Tech" and candidate.fit_score >= 85 and mx_valid)
    return (
        candidate.email, candidate.channel_name, candidate.channel_url, "YouTube", candidate.category,
        ai_match, curated_ai, "conservative_public_import" if curated_ai else "candidate", "send_ready" if mx_valid else "needs_validation",
        "公开商务邮箱 + MX" if mx_valid else "公开商务邮箱；域名待验证",
        candidate.email.rsplit("@", 1)[1], candidate.subscribers, "Unknown", "English", candidate.topic,
        candidate.priority, candidate.fit_score, candidate.source_file, 1, 1,
        "not_contacted", "no_reply", "not_started", 0,
        "Imported from a public creator-controlled page; sending requires normal queue safeguards.", now_iso(),
    )


def run(db: sqlite3.Connection, candidates: list[Candidate], checks: dict[str, dict[str, Any]], apply: bool) -> dict[str, int | bool]:
    ensure_schema(db)
    ensure_import_schema(db)
    existing = {str(row[0]).lower() for row in db.execute("SELECT email FROM contacts")}
    unique_emails = {candidate.email for candidate in candidates}
    best_by_email: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best_by_email.get(candidate.email)
        if current is None or candidate.fit_score > current.fit_score:
            best_by_email[candidate.email] = candidate
    mx_cache: dict[str, bool] = {}
    mx_status = {email: has_mx(email.rsplit("@", 1)[1], checks, mx_cache) for email in unique_emails}
    new_candidates = [candidate for candidate in candidates if candidate.email not in existing]
    inserted = 0
    provenance = 0
    if apply:
        for candidate in candidates:
            best = best_by_email[candidate.email]
            if candidate.email not in existing:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO contacts(
                      email,channel_name,channel_url,platform,category,ai_match,curated_ai,ai_confidence,
                      validation_status,verification_proof,email_domain,subscribers,country,language,topic,
                      priority,fit_score,source_file,source_count,source_rows,outreach_status,reply_status,
                      deal_status,do_not_contact,notes,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    contact_values(best, mx_status[candidate.email]),
                )
                if cursor.rowcount > 0:
                    inserted += 1
                    existing.add(candidate.email)
            contact = db.execute("SELECT id FROM contacts WHERE lower(email)=?", (candidate.email,)).fetchone()
            if not contact:
                continue
            if candidate == best:
                db.execute(
                    """UPDATE contacts SET channel_name=?,channel_url=?,category=?,ai_match=?,
                       curated_ai=CASE WHEN ?=1 THEN 1 ELSE curated_ai END,
                       ai_confidence=CASE WHEN ?=1 THEN 'conservative_public_import' ELSE ai_confidence END,
                       subscribers=coalesce(subscribers,?),
                       topic=?,priority=?,fit_score=max(fit_score,?),updated_at=datetime('now')
                       WHERE id=? AND notes LIKE 'Imported from a public creator-controlled page%'""",
                    (best.channel_name, best.channel_url, best.category,
                     int(bool(AI_PATTERN.search(f"{best.category} {best.topic}"))),
                     int(best.category == "AI & Tech" and best.fit_score >= 85 and mx_status[best.email]),
                     int(best.category == "AI & Tech" and best.fit_score >= 85 and mx_status[best.email]),
                     best.subscribers,
                     best.topic, best.priority, best.fit_score, int(contact[0])),
                )
            cursor = db.execute(
                """INSERT OR IGNORE INTO lead_provenance(contact_id,email,source_type,source_url,source_file,
                   contact_basis,domain_validation,discovered_at) VALUES(?,?,?,?,?,'public_business_contact',?,?)""",
                (int(contact[0]), candidate.email, candidate.source_type, candidate.source_url, candidate.source_file,
                 "mx" if mx_status[candidate.email] else "needs_validation", now_iso()),
            )
            provenance += max(0, cursor.rowcount)
        public_description = sum(1 for candidate in candidates if candidate.source_type == "youtube_public_description")
        website = sum(1 for candidate in candidates if candidate.source_type == "creator_linked_official_website")
        db.execute("""UPDATE lead_sources SET discovered_count=?,imported_count=?,last_run_at=datetime('now'),last_error=NULL,updated_at=datetime('now')
          WHERE name='YouTube 公开频道简介'""", (public_description, public_description))
        db.execute("""UPDATE lead_sources SET discovered_count=?,imported_count=?,last_run_at=datetime('now'),last_error=NULL,updated_at=datetime('now')
          WHERE name='YouTube 简介链接的官方网站'""", (website, website))
        db.commit()
    return {
        "apply": apply,
        "source_records": len(candidates),
        "unique_emails": len(unique_emails),
        "mx_ready": sum(1 for email in unique_emails if mx_status[email]),
        "needs_validation": sum(1 for email in unique_emails if not mx_status[email]),
        "new_candidates": len({candidate.email for candidate in new_candidates}),
        "inserted": inserted,
        "provenance_added": provenance,
    }


def main() -> int:
    args = parse_args()
    sources = tuple(args.sources or DEFAULT_SOURCES)
    candidates = load_candidates(sources)
    checks = load_domain_checks(args.domain_checks)
    db = connect(args.db)
    try:
        result = run(db, candidates, checks, args.apply)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
