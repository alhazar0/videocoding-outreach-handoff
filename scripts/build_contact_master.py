from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path


SOURCE_ROOT = Path("/Users/OPERATOR/Desktop/1月24日 (3)")
INVENTORY = Path("/Users/OPERATOR/Documents/Playground/email_segments/deduped_email_inventory.csv")
CURATED_BASE = SOURCE_ROOT / "agent4" / "videocoding_final_5000_prioritized.csv"
PROJECT_ROOT = Path("/Users/OPERATOR/Documents/Playground/outputs/videocoding_outreach_manager")
DATA_DIR = PROJECT_ROOT / "data"
PUBLIC_DATA_DIR = PROJECT_ROOT / "public" / "data"
DAILY_SEND_MIN = 750
DAILY_SEND_TARGET = 750
DAILY_SEND_HARD_CAP = 750

SKIP_DIRS = {
    "site-packages",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    "managed_components",
    "__pycache__",
}

EMAIL_RE = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.I,
)
BLOCKED_EMAIL_DOMAINS = {"sentry.io", "sentry.wixpress.com"}
ASSET_DOMAIN_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")

AI_RE = re.compile(
    r"(artificial intelligence|\bai\b|generative ai|chatgpt|claude|gemini|"
    r"\bllm\b|large language model|machine learning|deep learning|data science|"
    r"coding|programming|developer|web development|software engineering|"
    r"devops|cybersecurity|cloud computing|tech review|technology|no[- ]code|"
    r"automation|robotics|computer science)",
    re.I,
)

CURATED_AI_RE = re.compile(
    r"(artificial intelligence|\bai\b|chatgpt|llm|machine learning|data science|"
    r"coding|programming|developer|web development|devops|cybersecurity|software|"
    r"technology|tech review|mobile development|cs fundamentals)",
    re.I,
)

CATEGORY_RULES = [
    ("AI & Tech", AI_RE),
    ("Business & Marketing", re.compile(
        r"(business|startup|entrepreneur|marketing|sales|ecommerce|e-commerce|"
        r"creator economy|advertising|branding|saas)", re.I)),
    ("Finance & Economics", re.compile(
        r"(finance|invest|stock|crypto|econom|trading|money|wealth|market analysis)", re.I)),
    ("Education & Science", re.compile(
        r"(education|science|physics|chemistry|biology|math|space|astronomy|"
        r"language learning|study skills|tutorial|lesson|academic|research)", re.I)),
    ("Health & Fitness", re.compile(
        r"(health|medical|medicine|fitness|nutrition|wellness|psychology|therapy)", re.I)),
    ("History & Society", re.compile(
        r"(history|historical|humanities|society|culture|geopolitic|geography)", re.I)),
    ("News & Commentary", re.compile(
        r"(news|current affairs|commentary|politics|analysis|opinion|review channel)", re.I)),
    ("Career & Growth", re.compile(
        r"(career|productivity|self improvement|self-improvement|motivation|"
        r"leadership|coaching|personal development)", re.I)),
    ("Professional Services", re.compile(
        r"(real estate|realtor|legal|lawyer|law firm|accounting|consulting|"
        r"consultant|insurance)", re.I)),
    ("Podcast & Interviews", re.compile(
        r"(podcast|interview|talk show|thought leadership)", re.I)),
    ("Creative & Media", re.compile(
        r"(filmmaking|video editing|camera gear|motion graphics|design|animation|"
        r"photography|creator tips)", re.I)),
    ("Lifestyle & Reviews", re.compile(
        r"(beauty|fashion|lifestyle|book review|travel tips|parenting)", re.I)),
]

SEMANTIC_FIELDS = (
    "channel_name",
    "industry_major",
    "industry_main",
    "industry_sub",
    "industry_bucket",
    "topic",
    "recent_topic",
    "query_seed",
    "latest_video_title",
    "niche",
    "search_query",
)

STRONG_SEMANTIC_FIELDS = (
    "channel_name",
    "topic",
    "recent_topic",
    "query_seed",
    "latest_video_title",
    "niche",
    "search_query",
)

YES = {"yes", "true", "1", "valid", "pass", "passed"}


def norm(value: object) -> str:
    return html.unescape(str(value or "").strip())


def lower(value: object) -> str:
    return norm(value).lower()


def is_parser_artifact(email: str) -> bool:
    local, separator, domain = lower(email).partition("@")
    return (
        separator != "@"
        or not local
        or not domain
        or domain in BLOCKED_EMAIL_DOMAINS
        or domain.endswith(ASSET_DOMAIN_SUFFIXES)
        or "/" in local
        or "\\" in local
    )


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def parse_subscribers(value: object) -> int | None:
    text = lower(value).replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmb])?", text)
    if not match:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2), 1)
    return round(float(match.group(1)) * multiplier)


def source_priority(path: str) -> int:
    text = path.lower()
    if "final_master_dedup.csv" in text:
        return 100
    if "phase_progressive_verified_10000.csv" in text:
        return 95
    if "validated_emails" in text or "mx_verified" in text or "verified" in text:
        return 90
    if "creator_email_ready" in text:
        return 82
    if "master" in text:
        return 72
    return 50


def candidate_score(row: dict[str, str], source: str) -> int:
    useful = sum(1 for key in SEMANTIC_FIELDS if norm(row.get(key)))
    useful += sum(
        1
        for key in ("channel_url", "profile_url", "country", "language", "language_bucket",
                    "subscriber_count", "subscriber_count_raw", "subscriber_count_num")
        if norm(row.get(key))
    )
    return source_priority(source) + useful


def working_dates(start: date):
    current = start
    while True:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)

    with INVENTORY.open(encoding="utf-8-sig", errors="ignore", newline="") as handle:
        inventory_rows = list(csv.DictReader(handle))

    curated_ai_emails: set[str] = set()
    with CURATED_BASE.open(encoding="utf-8-sig", errors="ignore", newline="") as handle:
        for row in csv.DictReader(handle):
            email = lower(row.get("email"))
            curated_text = " | ".join(
                norm(row.get(key))
                for key in (
                    "channel_name",
                    "industry_bucket",
                    "recent_topic",
                    "latest_video_title",
                    "industry_major",
                    "industry_sub",
                )
            )
            if email and not is_parser_artifact(email) and CURATED_AI_RE.search(curated_text):
                curated_ai_emails.add(email)

    records: dict[str, dict] = {}
    for row in inventory_rows:
        email = lower(row.get("email"))
        if not email or is_parser_artifact(email):
            continue
        records[email] = {
            "email": email,
            "best_row": row,
            "best_source": norm(row.get("source_file")),
            "best_score": candidate_score(row, norm(row.get("source_file"))),
            "semantic": {norm(row.get(key)) for key in SEMANTIC_FIELDS if norm(row.get(key))},
            "strong_semantic": {
                norm(row.get(key)) for key in STRONG_SEMANTIC_FIELDS if norm(row.get(key))
            },
            "sources": {norm(row.get("source_file"))} if norm(row.get("source_file")) else set(),
            "source_rows": 1,
            "verified": lower(row.get("email_verified_status")) == "verified",
            "mx_yes": False,
            "format_yes": False,
        }

    source_files = 0
    source_rows = 0
    for path in SOURCE_ROOT.rglob("*.csv"):
        if should_skip(path):
            continue
        try:
            with path.open(encoding="utf-8-sig", errors="ignore", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    continue
                normalized_headers = {lower(field): field for field in reader.fieldnames}
                if "email" not in normalized_headers:
                    continue
                email_key = normalized_headers["email"]
                source_files += 1
                path_text = str(path)
                for row in reader:
                    email = lower(row.get(email_key))
                    if not email or "@" not in email or email not in records:
                        continue
                    source_rows += 1
                    record = records[email]
                    record["source_rows"] += 1
                    record["sources"].add(path_text)
                    record["verified"] = record["verified"] or lower(row.get("email_verified_status")) == "verified"
                    record["mx_yes"] = record["mx_yes"] or lower(row.get("email_mx_valid")) in YES
                    record["format_yes"] = record["format_yes"] or lower(row.get("email_format_valid")) in YES
                    for key in SEMANTIC_FIELDS:
                        value = norm(row.get(key))
                        if value and len(record["semantic"]) < 80:
                            record["semantic"].add(value)
                    for key in STRONG_SEMANTIC_FIELDS:
                        value = norm(row.get(key))
                        if value and len(record["strong_semantic"]) < 80:
                            record["strong_semantic"].add(value)
                    score = candidate_score(row, path_text)
                    if score > record["best_score"]:
                        record["best_score"] = score
                        record["best_row"] = row
                        record["best_source"] = path_text
        except Exception:
            continue

    contacts: list[dict] = []
    for record in records.values():
        row = record["best_row"]
        email = record["email"]
        syntax_valid = bool(EMAIL_RE.match(email))
        send_ready = syntax_valid and (record["verified"] or record["mx_yes"])
        if not syntax_valid:
            validation_status = "invalid"
            proof = "invalid"
        elif send_ready:
            validation_status = "send_ready"
            if record["verified"] and record["mx_yes"]:
                proof = "verified + MX"
            elif record["verified"]:
                proof = "verified"
            else:
                proof = "MX"
        else:
            validation_status = "needs_validation"
            proof = "syntax only"

        platform = norm(row.get("platform"))
        channel_url = norm(row.get("channel_url") or row.get("profile_url"))
        channel_name = norm(row.get("channel_name"))
        creator_like = (
            platform.lower() in {"youtube", "tiktok", "instagram", "x", "twitter"}
            or "youtube.com" in channel_url.lower()
            or bool(channel_name and channel_url)
        )

        semantic_text = " | ".join(sorted(record["semantic"]))
        category = "Needs Review"
        for name, pattern in CATEGORY_RULES:
            if pattern.search(semantic_text):
                category = name
                break

        curated_ai = email in curated_ai_emails and creator_like
        if curated_ai:
            category = "AI & Tech"
        ai_match = category == "AI & Tech" and creator_like
        ai_confidence = (
            "Confirmed"
            if curated_ai
            else "Candidate"
            if ai_match
            else "None"
        )

        subscribers = (
            parse_subscribers(row.get("subscriber_count_num"))
            or parse_subscribers(row.get("subscriber_count_raw"))
            or parse_subscribers(row.get("subscriber_count"))
        )
        score = 30
        if send_ready:
            score += 25
        if creator_like:
            score += 15
        if curated_ai:
            score += 20
        elif ai_match:
            score += 5
        if subscribers and 10_000 <= subscribers <= 500_000:
            score += 5
        score = min(100, score)
        priority = "P1" if score >= 90 else "P2" if score >= 80 else "P3" if score >= 65 else "Review"

        contacts.append({
            "email": email,
            "channel_name": channel_name or email.split("@", 1)[0],
            "channel_url": channel_url,
            "platform": platform or ("YouTube" if "youtube.com" in channel_url.lower() else "Unknown"),
            "category": category,
            "ai_match": ai_match,
            "curated_ai": curated_ai,
            "ai_confidence": ai_confidence,
            "validation_status": validation_status,
            "verification_proof": proof,
            "email_domain": email.split("@", 1)[1] if "@" in email else "",
            "subscribers": subscribers,
            "country": norm(row.get("country")) or "Unknown",
            "language": norm(row.get("language") or row.get("language_bucket")) or "Unknown",
            "topic": norm(
                row.get("recent_topic")
                or row.get("query_seed")
                or row.get("industry_bucket")
                or row.get("latest_video_title")
            ),
            "priority": priority,
            "fit_score": score,
            "source_file": record["best_source"],
            "source_count": len(record["sources"]),
            "source_rows": record["source_rows"],
            "planned_send_date": None,
            "planned_sender_email": None,
            "outreach_status": "not_contacted",
            "first_sent_at": None,
            "followup1_due": None,
            "followup2_due": None,
            "reply_status": "no_reply",
            "deal_status": "not_started",
            "do_not_contact": False,
            "notes": "",
        })

    ai_queue = sorted(
        (
            contact for contact in contacts
            if contact["ai_match"]
            and contact["curated_ai"]
            and contact["validation_status"] == "send_ready"
        ),
        key=lambda contact: (
            -contact["fit_score"],
            -(contact["subscribers"] or 0),
            contact["channel_name"].lower(),
        ),
    )

    send_dates = working_dates(date(2026, 7, 29))
    assigned_on_day = 0
    current_send_date = next(send_dates)
    for contact in ai_queue:
        if assigned_on_day >= DAILY_SEND_TARGET:
            assigned_on_day = 0
            current_send_date = next(send_dates)
        contact["planned_send_date"] = current_send_date.isoformat()
        slot = assigned_on_day
        contact["planned_sender_email"] = (
            "team@videocoding.ai" if slot < 700 else "creator@outreach.videocoding.ai"
        )
        assigned_on_day += 1

    contacts.sort(
        key=lambda contact: (
            0 if contact["curated_ai"] and contact["validation_status"] == "send_ready" else 1,
            contact["planned_send_date"] or "9999-12-31",
            -contact["fit_score"],
            contact["email"],
        )
    )
    for index, contact in enumerate(contacts, start=1):
        contact["id"] = index

    category_counts = Counter(contact["category"] for contact in contacts)
    validation_counts = Counter(contact["validation_status"] for contact in contacts)
    report = {
        "generated_at": "2026-07-28",
        "source_root": str(SOURCE_ROOT),
        "source_files_with_email_column": source_files,
        "source_row_occurrences_reviewed": source_rows,
        "total_unique_emails": len(contacts),
        "syntax_valid": sum(contact["validation_status"] != "invalid" for contact in contacts),
        "send_ready": validation_counts["send_ready"],
        "needs_validation": validation_counts["needs_validation"],
        "invalid": validation_counts["invalid"],
        "ai_creator_send_queue": len(ai_queue),
        "daily_send_min": DAILY_SEND_MIN,
        "daily_send_target": DAILY_SEND_TARGET,
        "daily_send_hard_cap": DAILY_SEND_HARD_CAP,
        "ai_creator_review_queue": sum(
            contact["ai_match"]
            and not contact["curated_ai"]
            and contact["validation_status"] == "send_ready"
            for contact in contacts
        ),
        "ai_classification_note": (
            "Recomputed only from creator/channel/topic/industry metadata. "
            "Email addresses and source paths are excluded from AI matching."
        ),
        "category_counts": dict(category_counts.most_common()),
        "validation_counts": dict(validation_counts),
    }

    csv_fields = list(contacts[0].keys())
    with (DATA_DIR / "contact_master.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(contacts)

    (DATA_DIR / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (PUBLIC_DATA_DIR / "contacts_seed.json").write_text(
        json.dumps({"report": report, "contacts": contacts}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
