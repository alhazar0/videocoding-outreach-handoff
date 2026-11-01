#!/usr/bin/env python3
"""Discover public creator business emails from Apple Podcasts and creator RSS feeds."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .continuous_lead_discovery import (
        PUBLIC_CRAWLER_UA,
        PUBLIC_SSL_CONTEXT,
        SafeRedirectHandler,
        has_mx,
        is_public_http_url,
        public_contact_emails,
    )
else:
    from continuous_lead_discovery import (
        PUBLIC_CRAWLER_UA,
        PUBLIC_SSL_CONTEXT,
        SafeRedirectHandler,
        has_mx,
        is_public_http_url,
        public_contact_emails,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "lead-discovery.sqlite"
DEFAULT_LOCK = PROJECT_ROOT / ".wrangler" / "public-podcast-discovery.lock"
SOURCE_NAME = "Apple Podcasts公开创作者来源"
SEARCHES = (
    ("AI creator podcast", "US", "English · United States"),
    ("generative AI podcast", "GB", "English · United Kingdom"),
    ("AI tools for creators", "AU", "English · Australia"),
    ("video editing podcast", "CA", "English · Canada"),
    ("podcast inteligencia artificial", "ES", "Spanish · Spain"),
    ("podcast inteligencia artificial", "MX", "Spanish · Mexico"),
    ("podcast inteligência artificial", "BR", "Portuguese · Brazil"),
    ("podcast intelligence artificielle", "FR", "French · France"),
    ("KI Podcast", "DE", "German · Germany"),
    ("podcast intelligenza artificiale", "IT", "Italian · Italy"),
    ("AI ポッドキャスト", "JP", "Japanese · Japan"),
    ("인공지능 팟캐스트", "KR", "Korean · South Korea"),
    ("人工智能 播客", "TW", "Chinese · Taiwan"),
    ("AI पॉडकास्ट", "IN", "Hindi · India"),
    ("podcast kecerdasan buatan", "ID", "Indonesian · Indonesia"),
    ("AI podcast", "NG", "English · Nigeria"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--workers", type=int, default=12)
    return parser.parse_args()


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_public_text(url: str, accepted: tuple[str, ...], limit: int = 800_000) -> tuple[str, str]:
    if not is_public_http_url(url):
        return "", url
    opener = urllib.request.build_opener(
        SafeRedirectHandler(),
        urllib.request.HTTPSHandler(context=PUBLIC_SSL_CONTEXT),
    )
    request = urllib.request.Request(url, headers={"User-Agent": PUBLIC_CRAWLER_UA, "Accept": "application/json,application/rss+xml,application/xml,text/xml,text/html"})
    try:
        with opener.open(request, timeout=15) as response:
            content_type = str(response.headers.get("content-type") or "").lower()
            if accepted and not any(item in content_type for item in accepted):
                return "", response.geturl()
            return response.read(limit).decode("utf-8", errors="ignore"), response.geturl()
    except (OSError, urllib.error.URLError, ValueError):
        return "", url


def search_podcasts(term: str, country: str, limit: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({
        "term": term,
        "country": country,
        "media": "podcast",
        "entity": "podcast",
        "limit": max(1, min(50, limit)),
    })
    payload, _ = fetch_public_text(
        f"https://itunes.apple.com/search?{query}",
        ("application/json", "text/javascript", "text/plain"),
    )
    if not payload:
        return []
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        return []
    return [item for item in result.get("results", []) if isinstance(item, dict)]


def official_site_from_feed(feed: str, feed_url: str) -> str | None:
    for pattern in (r"<link[^>]*href=[\"']([^\"']+)", r"<link>\s*(https?://[^<\s]+)\s*</link>"):
        for match in re.finditer(pattern, feed, re.I):
            candidate = urllib.parse.urljoin(feed_url, match.group(1).strip())
            host = (urllib.parse.urlparse(candidate).hostname or "").lower()
            if host and "itunes.apple.com" not in host and "podcasts.apple.com" not in host and is_public_http_url(candidate):
                return candidate
    return None


def scan_podcast(item: dict[str, Any], term: str, language: str) -> dict[str, Any] | None:
    feed_url = str(item.get("feedUrl") or "").strip()
    track_id = str(item.get("collectionId") or item.get("trackId") or "").strip()
    if not feed_url or not track_id:
        return None
    feed, final_feed_url = fetch_public_text(feed_url, ("xml", "rss", "text/plain"))
    if not feed:
        return {"emails": [], "track_id": track_id}
    evidence_url = final_feed_url
    source_type = "apple_podcast_public_rss"
    emails = public_contact_emails(feed)
    if not emails:
        website = official_site_from_feed(feed, final_feed_url)
        if website:
            page, final_page = fetch_public_text(website, ("text/html",))
            emails = public_contact_emails(page)
            if emails:
                evidence_url = final_page
                source_type = "podcast_creator_official_website"
    return {
        "emails": emails[:1],
        "track_id": track_id,
        "name": str(item.get("collectionName") or item.get("artistName") or "Podcast creator"),
        "evidence_url": evidence_url,
        "source_type": source_type,
        "query": term,
        "language": language,
    }


def ensure_source(db: sqlite3.Connection) -> None:
    now = iso_utc()
    db.execute(
        """INSERT OR IGNORE INTO lead_sources(
             name,source_type,location,policy_status,enabled,discovered_count,imported_count,updated_at
           ) VALUES(?, 'apple_podcast_public', 'https://itunes.apple.com/search + public RSS',
             'public_business_contact',1,0,0,?)""",
        (SOURCE_NAME, now),
    )
    db.commit()


def import_email(
    db: sqlite3.Connection,
    *,
    email: str,
    name: str,
    public_url: str,
    source_type: str,
    source_key: str,
    query: str,
    language: str,
    mx_valid: bool,
) -> bool:
    now = iso_utc()
    contact = db.execute("SELECT id FROM contacts WHERE lower(email)=?", (email,)).fetchone()
    inserted = False
    if mx_valid and not contact:
        cursor = db.execute(
            """INSERT OR IGNORE INTO contacts(
                 email,channel_name,channel_url,platform,category,ai_match,curated_ai,ai_confidence,
                 validation_status,verification_proof,email_domain,subscribers,country,language,topic,
                 priority,fit_score,source_file,source_count,source_rows,outreach_status,reply_status,
                 deal_status,do_not_contact,notes,updated_at
               ) VALUES(?,?,?,'Podcast','AI & Tech',1,1,'public_ai_podcast',
                 'needs_subscriber_verification',?, ?,NULL,'Unknown',?,?,
                 'P2',88,?,1,1,'not_contacted','no_reply','not_started',0,?,?)""",
            (
                email,
                name[:240],
                public_url[:1000],
                "公开Podcast RSS或创作者官网 + MX；受众规模待核验",
                email.rsplit("@", 1)[1],
                language,
                query[:500],
                f"public_podcast_discovery:{source_key}",
                "Public creator-controlled business contact; audience size must be verified before sending.",
                now,
            ),
        )
        inserted = cursor.rowcount > 0
        contact = db.execute("SELECT id FROM contacts WHERE lower(email)=?", (email,)).fetchone()
    contact_id = int(contact["id"]) if contact else None
    db.execute(
        """INSERT INTO lead_discovery_emails(
             email,channel_key,source_url,source_type,validation_status,contact_id,was_new_contact,first_seen_at,last_seen_at
           ) VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(email) DO UPDATE SET source_url=excluded.source_url,source_type=excluded.source_type,
             validation_status=CASE WHEN excluded.validation_status='mx' THEN 'mx' ELSE lead_discovery_emails.validation_status END,
             contact_id=coalesce(excluded.contact_id,lead_discovery_emails.contact_id),
             was_new_contact=max(lead_discovery_emails.was_new_contact,excluded.was_new_contact),last_seen_at=excluded.last_seen_at""",
        (email, source_key, public_url, source_type, "mx" if mx_valid else "needs_validation", contact_id, int(inserted), now, now),
    )
    if contact_id:
        db.execute(
            """INSERT OR IGNORE INTO lead_provenance(
                 contact_id,email,source_type,source_url,source_file,contact_basis,domain_validation,discovered_at
               ) VALUES(?,?,?,?,?,'public_business_contact',?,?)""",
            (contact_id, email, source_type, public_url, f"public_podcast_discovery:{source_key}", "mx" if mx_valid else "needs_validation", now),
        )
    db.commit()
    return inserted


def main() -> int:
    args = parse_args()
    DEFAULT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}))
            return 0
        db = sqlite3.connect(args.db, timeout=120)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=120000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        ensure_source(db)
        found = 0
        imported = 0
        scanned = 0
        mx_cache: dict[str, bool] = {}
        try:
            inventory: dict[str, tuple[dict[str, Any], str, str]] = {}
            with ThreadPoolExecutor(max_workers=min(8, len(SEARCHES))) as pool:
                searches = {
                    pool.submit(search_podcasts, term, country, args.limit): (term, language)
                    for term, country, language in SEARCHES
                }
                for future in as_completed(searches):
                    term, language = searches[future]
                    for item in future.result():
                        track_id = str(item.get("collectionId") or item.get("trackId") or "").strip()
                        feed_url = str(item.get("feedUrl") or "").strip()
                        if track_id and feed_url:
                            inventory.setdefault(track_id, (item, term, language))

            worker_count = max(1, min(24, args.workers))
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                scans = [
                    pool.submit(scan_podcast, item, term, language)
                    for item, term, language in inventory.values()
                ]
                for future in as_completed(scans):
                    result = future.result()
                    if not result:
                        continue
                    scanned += 1
                    for email in result["emails"]:
                        found += 1
                        key = "podcast:" + hashlib.sha256(result["track_id"].encode()).hexdigest()[:24]
                        if import_email(
                            db,
                            email=email,
                            name=result["name"],
                            public_url=result["evidence_url"],
                            source_type=result["source_type"],
                            source_key=key,
                            query=result["query"],
                            language=result["language"],
                            mx_valid=has_mx(email, mx_cache),
                        ):
                            imported += 1
                    if scanned % 50 == 0:
                        print(json.dumps({"status": "progress", "podcasts_scanned": scanned, "emails_found": found}), flush=True)
            now = iso_utc()
            totals = db.execute(
                """SELECT COUNT(*) discovered,SUM(contact_id IS NOT NULL) imported
                   FROM lead_discovery_emails WHERE source_type IN ('apple_podcast_public_rss','podcast_creator_official_website')"""
            ).fetchone()
            db.execute(
                """UPDATE lead_sources SET discovered_count=?,imported_count=?,last_run_at=?,last_error=NULL,updated_at=?
                   WHERE name=?""",
                (int(totals["discovered"] or 0), int(totals["imported"] or 0), now, now, SOURCE_NAME),
            )
            db.commit()
        finally:
            db.close()
    print(json.dumps({"status": "complete", "podcasts_scanned": scanned, "emails_found": found, "contacts_imported": imported}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
