#!/usr/bin/env python3
"""Resume-safe public YouTube creator discovery for the VideoCoding lead library.

The worker searches public YouTube metadata, scans creator-controlled channel
descriptions for explicitly published contact emails, validates mail routing,
and imports new contacts with exact provenance. It never signs in, reveals
hidden About-page addresses, solves CAPTCHAs, or bypasses platform controls.
"""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import random
import re
import socket
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

try:
    from outreach_scheduler import DEFAULT_DB, connect, ensure_schema
    from discovery_topics import AI_TOPIC_ROOTS, AI_NATIVE_ROOTS, AI_ADDITIONAL_TITLE_PATTERN, CREATOR_TOPIC_ROOTS, CREATOR_TITLE_PATTERN
    from discovery_topics import KNOWLEDGE_TOPIC_ROOTS, KNOWLEDGE_TITLE_PATTERN
    from creator_activity import fetch_activity, recent, ensure_activity_schema, save_activity
    from discovery_learning import FOCUS_SQL, ensure_learning_schema, learned_family, recover_expired_leases
except ModuleNotFoundError:  # Imported as scripts.continuous_lead_discovery in tests.
    from scripts.outreach_scheduler import DEFAULT_DB, connect, ensure_schema
    from scripts.discovery_topics import AI_TOPIC_ROOTS, AI_NATIVE_ROOTS, AI_ADDITIONAL_TITLE_PATTERN, CREATOR_TOPIC_ROOTS, CREATOR_TITLE_PATTERN
    from scripts.discovery_topics import KNOWLEDGE_TOPIC_ROOTS, KNOWLEDGE_TITLE_PATTERN
    from scripts.creator_activity import fetch_activity, recent, ensure_activity_schema, save_activity
    from scripts.discovery_learning import FOCUS_SQL, ensure_learning_schema, learned_family, recover_expired_leases


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = PROJECT_ROOT / ".wrangler" / "continuous-lead-discovery.lock"
WORKER_KEY = "youtube_public_discovery"
SOURCE_NAME = "YouTube 24小时公开邮箱发现"
TARGET_NEW_CONTACTS = 1_000_000
DAILY_TARGET_NEW_CONTACTS = 3_000
SEED_INVENTORY_VERSION = "2026-10-08-active-broad-discovery-v6"
DISCOVERY_SCHEMA_LOCK = PROJECT_ROOT / ".wrangler" / "lead-discovery-schema.lock"

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24})(?![A-Za-z0-9._%+-])",
    re.I,
)
AI_SIGNAL_RE = re.compile(
    r"(?:\bai\b|artificial intelligence|chatgpt|\bgpt[- ]?\d*\b|\bllm\b|claude|gemini|midjourney|"
    r"machine learning|deep learning|generative ai|ai agent|vibe cod(?:e|ing)|"
    + AI_ADDITIONAL_TITLE_PATTERN + r")",
    re.I,
)
CREATOR_SIGNAL_RE = re.compile(CREATOR_TITLE_PATTERN, re.I)
KNOWLEDGE_SIGNAL_RE = re.compile(KNOWLEDGE_TITLE_PATTERN, re.I)
TIER_ONE_PATTERNS = {
    "codex": re.compile(r"(?:openai\s+)?codex(?:\s+cli|\s+coding|\s+agent)?", re.I),
    "claude code": re.compile(r"claude\s+code", re.I),
    "hyperframes": re.compile(r"hyperframes?", re.I),
    "remotion": re.compile(r"\bremotion\b", re.I),
    "gemini": re.compile(r"(?:google\s+gemini|gemini\s+(?:ai|cli|code|coding|agent|tutorial))", re.I),
}
CONTACT_CONTEXT_RE = re.compile(
    r"business|contact|email|e-mail|collab|collaboration|sponsor|sponsorship|partnership|booking|"
    r"inquir(?:y|ies)|advertis|brand|media|press|合作|商务|联系|邮箱|광고|문의|連絡|仕事|提携",
    re.I,
)
ROLE_LOCAL_PARTS = (
    "business", "contact", "hello", "team", "work", "press", "media", "sponsor",
    "partner", "booking", "advertising", "ads", "collab", "inquiry", "pr",
)
BLOCKED_DOMAINS = {
    "example.com", "domain.com", "email.com", "youtube.com", "ytimg.com", "google.com",
    "sentry.io", "sentry.wixpress.com", "w3.org", "schema.org",
}
BLOCKED_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "support", "privacy", "legal", "abuse"}
SKIPPED_WEBSITE_DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "facebook.com", "tiktok.com", "patreon.com", "amazon.", "amzn.", "discord.", "spotify.com",
    "apple.com", "google.com", "substack.com", "beehiiv.com", "linktr.ee", "beacons.ai",
)
PUBLIC_CRAWLER_UA = "VideoCodingPublicCreatorDiscovery/1.0 (+https://videocoding.ai)"
PUBLIC_SSL_CONTEXT = ssl.create_default_context()
AI_LONG_TAIL_INTENTS = ("tutorial", "review", "workflow", "case study")
AI_LONG_TAIL_AUDIENCES = (
    "for content creators",
    "for YouTubers",
    "for developers",
    "for marketers",
    "for small business",
    "for students",
    "for teachers",
    "for designers",
    "for freelancers",
    "for startups",
)
AI_NATIVE_INTENTS = {
    "Hindi": ("ट्यूटोरियल", "रिव्यू", "कैसे इस्तेमाल करें", "नए फीचर", "टिप्स"),
    "Spanish": ("tutorial", "reseña", "cómo usar", "novedades", "consejos"),
    "Portuguese": ("tutorial", "análise", "como usar", "novidades", "dicas"),
    "French": ("tutoriel", "avis", "comment utiliser", "nouveautés", "conseils"),
    "German": ("Anleitung", "Test", "erklärt", "Neuigkeiten", "Tipps"),
    "Indonesian": ("tutorial", "ulasan", "cara menggunakan", "fitur baru", "tips"),
    "Arabic": ("شرح", "مراجعة", "كيفية الاستخدام", "ميزات جديدة", "نصائح"),
    "Japanese": ("使い方", "レビュー", "解説", "新機能", "コツ"),
    "Korean": ("사용법", "리뷰", "튜토리얼", "새 기능", "팁"),
    "Chinese": ("教程", "评测", "怎么用", "新功能", "技巧"),
    "Russian": ("обзор", "как пользоваться", "инструкция", "новые функции", "советы"),
    "Turkish": ("inceleme", "nasıl kullanılır", "eğitim", "yeni özellikler", "ipuçları"),
    "Italian": ("tutorial", "recensione", "come usare", "novità", "consigli"),
    "Dutch": ("handleiding", "review", "uitleg", "nieuwe functies", "tips"),
    "Polish": ("poradnik", "recenzja", "jak używać", "nowe funkcje", "wskazówki"),
    "Vietnamese": ("hướng dẫn", "đánh giá", "cách sử dụng", "tính năng mới", "mẹo"),
    "Thai": ("สอนใช้งาน", "รีวิว", "วิธีใช้", "ฟีเจอร์ใหม่", "เคล็ดลับ"),
    "Bengali": ("টিউটোরিয়াল", "রিভিউ", "কিভাবে ব্যবহার করবেন", "নতুন ফিচার", "টিপস"),
    "Urdu": ("ٹیوٹوریل", "ریویو", "استعمال کرنے کا طریقہ", "نئے فیچرز", "ٹپس"),
    "Malay": ("tutorial", "ulasan", "cara guna", "ciri baharu", "tip"),
    "Filipino": ("tutorial", "review", "paano gamitin", "bagong feature", "mga tip"),
    "Romanian": ("tutorial", "recenzie", "cum se folosește", "funcții noi", "sfaturi"),
    "Persian": ("آموزش", "بررسی", "نحوه استفاده", "ویژگی جدید", "ترفند"),
    "Swahili": ("mafunzo", "mapitio", "jinsi ya kutumia", "vipengele vipya", "vidokezo"),
}
AI_PRODUCT_ROOTS = (
    ("ai_product_perplexity", "Perplexity AI"),
    ("ai_product_deepseek", "DeepSeek AI"),
    ("ai_product_notebooklm", "NotebookLM"),
    ("ai_product_cursor", "Cursor AI coding"),
    ("ai_product_github_copilot", "GitHub Copilot"),
    ("ai_product_n8n", "n8n AI automation"),
    ("ai_product_sora", "OpenAI Sora"),
    ("ai_product_runway", "Runway AI video"),
    ("ai_product_midjourney", "Midjourney AI"),
    ("ai_product_grok", "Grok AI"),
    ("ai_product_manus", "Manus AI"),
    ("ai_product_gamma", "Gamma AI"),
    ("ai_product_napkin", "Napkin AI"),
    ("ai_product_canva", "Canva AI"),
    ("ai_product_descript", "Descript AI"),
    ("ai_product_opus_clip", "Opus Clip AI"),
    ("ai_product_heygen", "HeyGen AI"),
    ("ai_product_synthesia", "Synthesia AI"),
    ("ai_product_kling", "Kling AI video"),
    ("ai_product_elevenlabs", "ElevenLabs AI"),
    ("ai_product_firefly", "Adobe Firefly AI"),
    ("ai_product_lovable", "Lovable AI coding"),
    ("ai_product_bolt", "Bolt.new AI coding"),
    ("ai_product_replit", "Replit Agent AI"),
    ("ai_product_windsurf", "Windsurf AI coding"),
    ("ai_product_openrouter", "OpenRouter AI"),
    ("ai_product_veo", "Google Veo AI video"),
    ("ai_product_pika", "Pika AI video"),
    ("ai_product_luma", "Luma AI video"),
    ("ai_product_higgsfield", "Higgsfield AI video"),
    ("ai_product_capcut", "CapCut AI video editing"),
    ("ai_product_veed", "VEED AI video"),
    ("ai_product_invideo", "InVideo AI"),
    ("ai_product_pictory", "Pictory AI video"),
    ("ai_product_filmora", "Filmora AI video editing"),
    ("ai_product_davinci", "DaVinci Resolve AI editing"),
)
TIER_TWO_PRODUCT_PATTERNS = {
    "ai_product_perplexity": re.compile(r"\bperplexity(?:\s+ai)?\b", re.I),
    "ai_product_deepseek": re.compile(r"\bdeepseek\b", re.I),
    "ai_product_notebooklm": re.compile(r"\bnotebook\s*lm\b", re.I),
    "ai_product_cursor": re.compile(r"\bcursor(?:\s+ai)?\b", re.I),
    "ai_product_github_copilot": re.compile(r"\b(?:github\s+)?copilot\b", re.I),
    "ai_product_n8n": re.compile(r"\bn8n\b", re.I),
    "ai_product_sora": re.compile(r"\b(?:openai\s+)?sora(?:\s+ai)?\b", re.I),
    "ai_product_runway": re.compile(r"\brunway(?:\s+(?:ai|gen[- ]?\d|video))?\b", re.I),
    "ai_product_midjourney": re.compile(r"\bmidjourney\b", re.I),
    "ai_product_grok": re.compile(r"\bgrok(?:\s+ai)?\b", re.I),
    "ai_product_manus": re.compile(r"\bmanus\s+ai\b", re.I),
    "ai_product_gamma": re.compile(r"\bgamma(?:\.app|\s+ai)\b", re.I),
    "ai_product_napkin": re.compile(r"\bnapkin\s+ai\b", re.I),
    "ai_product_canva": re.compile(r"\bcanva\s+ai\b", re.I),
    "ai_product_descript": re.compile(r"\bdescript(?:\s+ai)?\b", re.I),
    "ai_product_opus_clip": re.compile(r"\bopus\s+clip\b", re.I),
    "ai_product_heygen": re.compile(r"\bheygen\b", re.I),
    "ai_product_synthesia": re.compile(r"\bsynthesia(?:\s+ai)?\b", re.I),
    "ai_product_kling": re.compile(r"\bkling(?:\s+ai)?\b", re.I),
    "ai_product_elevenlabs": re.compile(r"\belevenlabs\b", re.I),
    "ai_product_firefly": re.compile(r"\b(?:adobe\s+)?firefly\b", re.I),
    "ai_product_lovable": re.compile(r"\blovable(?:\.dev|\s+ai)?\b", re.I),
    "ai_product_bolt": re.compile(r"\bbolt(?:\.new|\s+ai)\b", re.I),
    "ai_product_replit": re.compile(r"\breplit\s+(?:agent|ai)\b", re.I),
    "ai_product_windsurf": re.compile(r"\bwindsurf(?:\s+ai)?\b", re.I),
    "ai_product_openrouter": re.compile(r"\bopenrouter\b", re.I),
    "ai_product_veo": re.compile(r"\b(?:google\s+)?veo(?:\s+ai)?\b", re.I),
    "ai_product_pika": re.compile(r"\bpika\s+ai\b", re.I),
    "ai_product_luma": re.compile(r"\bluma\s+(?:ai|dream\s+machine)\b", re.I),
    "ai_product_higgsfield": re.compile(r"\bhiggsfield(?:\s+ai)?\b", re.I),
    "ai_product_capcut": re.compile(r"\bcapcut\s+ai\b", re.I),
    "ai_product_veed": re.compile(r"\bveed(?:\.io)?\s+ai\b", re.I),
    "ai_product_invideo": re.compile(r"\binvideo\s+ai\b", re.I),
    "ai_product_pictory": re.compile(r"\bpictory(?:\s+ai)?\b", re.I),
    "ai_product_filmora": re.compile(r"\bfilmora\s+ai\b", re.I),
    "ai_product_davinci": re.compile(r"\bdavinci\s+resolve\s+ai\b", re.I),
}


@dataclass(frozen=True)
class DiscoveryJobSeed:
    tier: int
    tier_key: str
    query: str
    language: str
    priority: int
    search_mode: str


@dataclass(frozen=True)
class ChannelCandidate:
    key: str
    channel_id: str
    name: str
    url: str
    video_title: str
    video_url: str


@dataclass(frozen=True)
class EmailEvidence:
    email: str
    source_url: str
    source_type: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--expanded-seed", action="store_true")
    parser.add_argument("--results-per-query", type=int, default=50)
    parser.add_argument("--channel-limit", type=int, default=50)
    parser.add_argument("--channel-workers", type=int, default=6)
    parser.add_argument("--min-sleep-seconds", type=float, default=90.0)
    parser.add_argument("--max-sleep-seconds", type=float, default=150.0)
    parser.add_argument("--startup-delay-seconds", type=float, default=0.0)
    parser.add_argument("--worker-key", default=WORKER_KEY)
    parser.add_argument("--focus", choices=("all", "ai", "creator", "knowledge"), default="all",
                        help="Reserve this worker for AI or video/content-creator discovery.")
    parser.add_argument("--lease-seconds", type=int, default=1800)
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def seed_queries(expanded: bool = False) -> list[DiscoveryJobSeed]:
    seeds: list[DiscoveryJobSeed] = []
    seen: set[tuple[str, str]] = set()

    def add(tier: int, tier_key: str, query: str, language: str, priority: int, search_mode: str) -> None:
        normalized = " ".join(query.split())
        key = (search_mode, normalized.casefold())
        if key in seen:
            return
        seen.add(key)
        seeds.append(DiscoveryJobSeed(tier, tier_key, normalized, language, priority, search_mode))

    def add_both(tier: int, tier_key: str, query: str, language: str, priority: int) -> None:
        add(tier, tier_key, query, language, priority, "recent_public_videos")
        add(tier, tier_key, query, language, priority - 1, "relevance_public_videos")

    current_year = utc_now().year
    recent_years = range(max(2023, current_year - 3), current_year + 1)
    global_language_suffixes = [
        "Hindi", "Spanish", "Portuguese", "French", "German", "Indonesian",
        "Arabic", "Japanese", "Korean", "Chinese", "Russian", "Turkish",
        "Italian", "Dutch", "Polish", "Vietnamese", "Thai", "Bengali", "Urdu",
        "Malay", "Filipino", "Romanian", "Persian", "Swahili",
    ]

    tier_one = {
        "codex": ["OpenAI Codex", "Codex CLI", "Codex coding agent", "Codex tutorial", "vibe coding with Codex"],
        "claude_code": ["Claude Code", "Anthropic Claude Code", "Claude Code tutorial", "Claude Code agent", "vibe coding Claude Code"],
        "hyperframes": ["Hyperframes AI", "Hyperframe AI", "Hyperframes tutorial"],
        "remotion": ["Remotion React video", "Remotion tutorial", "Remotion AI video", "Remotion motion graphics"],
        "gemini": ["Google Gemini AI", "Gemini CLI", "Gemini coding assistant", "Gemini AI tutorial", "Gemini coding agent"],
    }
    for group, queries in tier_one.items():
        for query in queries:
            for suffix in ["", "tutorial", "review", "demo", "workflow", "explained", "news", "tips", "comparison", "update"]:
                add_both(1, group, f"{query} {suffix}", "Product-name / global", 320 if not suffix else 310)
            for year in recent_years:
                add_both(1, group, f"{query} {year}", "Product-name / global", 305 - (current_year - year))
            for language in global_language_suffixes:
                add_both(1, group, f"{query} {language}", language, 295)

    tier_two = [
        ("ai_tools", "AI tools tutorial", "English"),
        ("ai_agents", "AI agents workflow", "English"),
        ("generative_ai", "generative AI explained", "English"),
        ("ai_automation", "AI automation tutorial", "English"),
        ("llm", "LLM tutorial", "English"),
        ("chatgpt", "ChatGPT tutorial", "English"),
        ("ai_coding", "AI coding tools", "English"),
        ("ai_video", "AI video tools", "English"),
        ("ai_news", "AI news analysis", "English"),
        ("ai_business", "AI for business", "English"),
        ("ai_startups", "AI startup analysis", "English"),
        ("ai_marketing", "AI marketing tools", "English"),
        ("ai_design", "AI design tools", "English"),
        ("ai_productivity", "AI productivity workflow", "English"),
        ("ai_research", "AI research tools", "English"),
        ("ai_saas", "AI SaaS review", "English"),
        ("ai_creator", "AI tools for creators", "English"),
        ("ai_editing", "AI video editing", "English"),
        ("ai_audio", "AI audio tools", "English"),
        ("ai_image", "AI image generation", "English"),
        ("ai_prompting", "prompt engineering tutorial", "English"),
        ("ai_dev", "AI developer tools", "English"),
        ("machine_learning", "machine learning explained", "English"),
        ("deep_learning", "deep learning tutorial", "English"),
        ("ai_models", "AI model comparison", "English"),
        ("ai_workflow", "generative AI workflow", "English"),
        ("ai_apps", "best AI apps", "English"),
        ("ai_weekly", "weekly AI news", "English"),
        ("ai_consulting", "AI consulting advice", "English"),
        ("ai_education", "learn artificial intelligence", "English"),
        ("ai_zh", "AI 工具 教程", "Chinese"),
        ("ai_zh", "人工智能 教程", "Chinese"),
        ("ai_es", "herramientas de inteligencia artificial", "Spanish"),
        ("ai_es", "tutorial de IA", "Spanish"),
        ("ai_pt", "ferramentas de inteligência artificial", "Portuguese"),
        ("ai_fr", "outils intelligence artificielle tutoriel", "French"),
        ("ai_de", "KI Tools Tutorial", "German"),
        ("ai_ja", "AI ツール 解説", "Japanese"),
        ("ai_ko", "AI 도구 튜토리얼", "Korean"),
        ("ai_hi", "AI tools Hindi tutorial", "Hindi"),
        ("ai_id", "tutorial alat AI", "Indonesian"),
        ("ai_ar", "شرح أدوات الذكاء الاصطناعي", "Arabic"),
        ("ai_ru", "инструменты ИИ обучение", "Russian"),
        ("ai_tr", "yapay zeka araçları eğitim", "Turkish"),
        ("ai_it", "strumenti intelligenza artificiale tutorial", "Italian"),
        ("ai_nl", "AI hulpmiddelen uitleg", "Dutch"),
        ("ai_pl", "narzędzia AI poradnik", "Polish"),
        ("ai_vi", "công cụ AI hướng dẫn", "Vietnamese"),
        ("ai_th", "เครื่องมือ AI สอนใช้งาน", "Thai"),
        ("ai_bn", "AI টুল টিউটোরিয়াল", "Bengali"),
        ("ai_ur", "AI ٹولز ٹیوٹوریل", "Urdu"),
    ]
    for group, query, language in tier_two:
        add_both(2, group, query, language, 220)
        for suffix in ["tutorial", "review", "explained", "workflow", "news", "tools", "for beginners"]:
            add_both(2, group, f"{query} {suffix}", language, 210)
        for year in range(max(2024, current_year - 2), current_year + 1):
            add_both(2, group, f"{query} {year}", language, 205 - (current_year - year))
        if language == "English":
            for region in ["India", "United States", "United Kingdom", "Canada", "Australia", "Nigeria", "Kenya", "Pakistan", "Philippines", "Singapore", "South Africa", "New Zealand"]:
                add_both(2, group, f"{query} {region}", f"English · {region}", 195)

    # The short generic query list above is good for an initial pass, but it is
    # finite and heavily overlaps once its first 50 results have been scanned.
    # Keep an AI-first long-tail inventory that targets creators by both content
    # intent and audience. Each job still refreshes on its normal tier schedule,
    # so newly published videos replenish the queue without inventing duplicate
    # date-stamped queries every day.
    for group, query, language in tier_two:
        if language != "English":
            continue
        stem = re.sub(r"\s+(?:tutorial|review|explained|workflow|analysis|advice)$", "", query, flags=re.I)
        for intent in AI_LONG_TAIL_INTENTS:
            for audience in AI_LONG_TAIL_AUDIENCES:
                add_both(2, group, f"{stem} {intent} {audience}", "English · audience intent", 200)

    product_languages = (
        "Hindi", "Spanish", "Portuguese", "French", "German", "Indonesian",
        "Arabic", "Japanese", "Korean", "Chinese", "Russian", "Turkish",
        "Italian", "Dutch", "Polish", "Vietnamese", "Thai", "Bengali", "Urdu",
        "Malay", "Filipino", "Romanian", "Persian", "Swahili",
    )
    product_regions = (
        "India", "United States", "United Kingdom", "Canada", "Australia", "Nigeria",
        "Kenya", "Pakistan", "Philippines", "Singapore", "South Africa", "New Zealand",
        "Ghana", "Bangladesh", "Sri Lanka", "Malaysia", "Indonesia", "Thailand", "Vietnam",
        "Japan", "South Korea", "Taiwan", "Hong Kong", "Germany", "France", "Spain", "Italy",
        "Netherlands", "Poland", "Romania", "United Arab Emirates", "Saudi Arabia", "Egypt",
        "Brazil", "Mexico", "Argentina", "Colombia", "Chile", "Peru",
        "Morocco", "Algeria", "Jordan", "Qatar", "Kuwait", "Nepal", "Cambodia",
        "Myanmar", "Romania", "Ukraine", "Greece", "Israel", "Tanzania", "Uganda",
    )
    for group, root in AI_PRODUCT_ROOTS:
        add_both(2, group, root, "Product-name / global", 250)
        for intent in ("tutorial", "review", "workflow", "comparison", "how to use"):
            add_both(2, group, f"{root} {intent}", "Product-name / global", 245)
        add_both(2, group, f"{root} {current_year}", "Product-name / global", 242)
        for language in product_languages:
            add_both(2, group, f"{root} {language}", language, 240)
            # Product names usually remain in English, while creator titles use
            # local-language intent words. These recent-only jobs reach a much
            # less overlapping result set than generic English relevance jobs.
            for intent in AI_NATIVE_INTENTS.get(language, ()):
                add(2, group, f"{root} {intent}", language, 244, "recent_public_videos")
                add(2, group, f"{root} {intent} {current_year}", language, 243, "recent_public_videos")
        for region in product_regions:
            add_both(2, group, f"{root} tutorial {region}", f"English · {region}", 235)

    tier_three = [
        ("talking_head_tech", "technology commentary explained", "English"),
        ("talking_head_business", "business advice talking head", "English"),
        ("talking_head_creator", "creator economy commentary", "English"),
        ("talking_head_marketing", "marketing strategy explained", "English"),
        ("talking_head_productivity", "productivity advice face camera", "English"),
        ("talking_head_education", "educational commentary face camera", "English"),
        ("talking_head_science", "science explained presenter", "English"),
        ("talking_head_finance", "personal finance explained camera", "English"),
        ("talking_head_career", "career advice talking head", "English"),
        ("talking_head_video", "YouTube talking head creator", "English"),
        ("talking_head_startup", "startup analysis presenter", "English"),
        ("talking_head_saas", "SaaS review face camera", "English"),
        ("talking_head_design", "design advice face camera", "English"),
        ("talking_head_dev", "software developer commentary", "English"),
        ("talking_head_news", "technology news presenter", "English"),
        ("talking_head_sales", "sales advice talking head", "English"),
        ("talking_head_leadership", "leadership advice presenter", "English"),
        ("talking_head_study", "study advice face camera", "English"),
        ("talking_head_language", "language learning presenter", "English"),
        ("talking_head_photo", "photography advice presenter", "English"),
        ("talking_head_film", "filmmaking commentary face camera", "English"),
        ("talking_head_podcast", "video podcast solo host", "English"),
        ("talking_head_zh", "口播 视频 科技", "Chinese"),
        ("talking_head_zh", "口播 博主 教程", "Chinese"),
        ("talking_head_es", "comentario frente a cámara", "Spanish"),
        ("talking_head_pt", "vídeo falando para câmera", "Portuguese"),
        ("talking_head_fr", "vidéo face caméra explication", "French"),
        ("talking_head_de", "Erklärvideo vor der Kamera", "German"),
        ("talking_head_ja", "顔出し 解説 動画", "Japanese"),
        ("talking_head_ko", "얼굴 해설 영상", "Korean"),
        ("talking_head_hi", "talking head video Hindi", "Hindi"),
        ("talking_head_id", "video berbicara depan kamera", "Indonesian"),
        ("talking_head_ms", "video bercakap depan kamera", "Malay"),
        ("talking_head_fil", "paliwanag sa harap ng camera", "Filipino"),
        ("talking_head_ro", "explicație video în fața camerei", "Romanian"),
        ("talking_head_fa", "ویدیو آموزشی جلوی دوربین", "Persian"),
        ("talking_head_sw", "video ya maelezo mbele ya kamera", "Swahili"),
    ]
    for group, query, language in tier_three:
        add_both(3, group, query, language, 120)
        for suffix in ["tutorial", "explained", "review", "tips", "channel", "creator"]:
            add_both(3, group, f"{query} {suffix}", language, 115)
        for year in range(max(2024, current_year - 2), current_year + 1):
            add_both(3, group, f"{query} {year}", language, 110 - (current_year - year))
        if language == "English":
            for region in ["India", "United States", "United Kingdom", "Canada", "Australia", "Nigeria", "Kenya", "Pakistan", "Philippines", "Singapore", "South Africa", "New Zealand"]:
                add_both(3, group, f"{query} {region}", f"English · {region}", 105)
        if expanded:
            global_regions = [
                "United States", "United Kingdom", "India", "Canada", "Australia", "New Zealand",
                "Nigeria", "Kenya", "Ghana", "South Africa", "Pakistan", "Bangladesh", "Sri Lanka",
                "Philippines", "Singapore", "Malaysia", "Indonesia", "Thailand", "Vietnam",
                "Japan", "South Korea", "Taiwan", "Hong Kong", "Germany", "France", "Spain",
                "Portugal", "Italy", "Netherlands", "Sweden", "Norway", "Denmark", "Poland",
                "Romania", "Turkey", "United Arab Emirates", "Saudi Arabia", "Egypt", "Brazil",
                "Mexico", "Argentina", "Colombia", "Chile", "Peru",
                "Morocco", "Algeria", "Jordan", "Qatar", "Kuwait", "Nepal", "Cambodia",
                "Myanmar", "Romania", "Ukraine", "Greece", "Israel", "Tanzania", "Uganda",
            ]
            for region in global_regions:
                add_both(3, group, f"{query} {region}", f"{language} · {region}", 104)
                for intent in ("tutorial", "review", "tips"):
                    add_both(3, group, f"{query} {intent} {region}", f"{language} · {region}", 102)
    # Add missing AI subject areas and native-language queries. Keep a bounded,
    # deduplicated inventory instead of adding new copies of the same query daily.
    for group, root in AI_TOPIC_ROOTS:
        add_both(2, group, root, "English", 255)
        for intent in AI_LONG_TAIL_INTENTS:
            add_both(2, group, f"{root} {intent}", "English", 252)
        for language in global_language_suffixes:
            add(2, group, f"{root} {language}", language, 248, "recent_public_videos")
    for language, root in AI_NATIVE_ROOTS.items():
        add_both(2, "ai_native", root, language, 258)
        for intent in AI_NATIVE_INTENTS.get(language, ()):
            add_both(2, "ai_native", f"{root} {intent}", language, 256)
            add(2, "ai_native", f"{root} {intent} {current_year}", language, 254, "recent_public_videos")
    for group, root in (*CREATOR_TOPIC_ROOTS, *KNOWLEDGE_TOPIC_ROOTS):
        add_both(3, group, root, "Creator tools / global", 180)
        stem = re.sub(r'\s+(?:tutorial|review|workflow|tips|explained)$', '', root, flags=re.I)
        for intent in ("tutorial", "review", "workflow", "tips"):
            add_both(3, group, f"{stem} {intent}", "Creator tools / global", 175)
        if group not in {"creator_zh", "creator_ja", "creator_ko", "creator_es", "creator_pt", "creator_fr", "creator_de"}:
            for language in global_language_suffixes:
                add(3, group, f"{root} {language}", language, 170, "recent_public_videos")
    return seeds


def ensure_discovery_schema(
    db: sqlite3.Connection,
    worker_key: str = WORKER_KEY,
    seed_jobs: bool = True,
    expanded_seed: bool = False,
) -> None:
    ensure_schema(db)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS lead_discovery_jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tier INTEGER NOT NULL,
          tier_key TEXT NOT NULL,
          query TEXT NOT NULL,
          search_mode TEXT NOT NULL DEFAULT 'recent_public_videos',
          language TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued',
          priority INTEGER NOT NULL,
          run_count INTEGER NOT NULL DEFAULT 0,
          videos_scanned INTEGER NOT NULL DEFAULT 0,
          channels_found INTEGER NOT NULL DEFAULT 0,
          public_emails_found INTEGER NOT NULL DEFAULT 0,
          contacts_imported INTEGER NOT NULL DEFAULT 0,
          last_started_at TEXT,
          last_completed_at TEXT,
          next_run_at TEXT NOT NULL,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(query, search_mode)
        );
        CREATE INDEX IF NOT EXISTS lead_discovery_jobs_queue_idx
          ON lead_discovery_jobs(tier, next_run_at, priority DESC);
        CREATE TABLE IF NOT EXISTS lead_discovery_channels (
          channel_key TEXT PRIMARY KEY,
          channel_id TEXT,
          channel_name TEXT NOT NULL,
          channel_url TEXT NOT NULL,
          best_tier INTEGER NOT NULL,
          matched_query TEXT NOT NULL,
          matched_video_title TEXT,
          matched_video_url TEXT,
          language TEXT NOT NULL,
          scan_status TEXT NOT NULL DEFAULT 'discovered',
          email_count INTEGER NOT NULL DEFAULT 0,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          last_contact_scan_at TEXT,
          last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS lead_discovery_emails (
          email TEXT PRIMARY KEY,
          channel_key TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_type TEXT NOT NULL,
          validation_status TEXT NOT NULL,
          contact_id INTEGER,
          was_new_contact INTEGER NOT NULL DEFAULT 0,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lead_discovery_state (
          worker_key TEXT PRIMARY KEY,
          enabled INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'starting',
          current_job_id INTEGER,
          active_tier INTEGER,
          current_query TEXT,
          heartbeat_at TEXT,
          last_started_at TEXT,
          last_completed_at TEXT,
          last_error TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lead_discovery_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          videos_scanned INTEGER NOT NULL DEFAULT 0,
          channels_found INTEGER NOT NULL DEFAULT 0,
          public_emails_found INTEGER NOT NULL DEFAULT 0,
          contacts_imported INTEGER NOT NULL DEFAULT 0,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          error TEXT
        );
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
    email_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(lead_discovery_emails)")}
    if "was_new_contact" not in email_columns:
        db.execute("ALTER TABLE lead_discovery_emails ADD COLUMN was_new_contact INTEGER NOT NULL DEFAULT 0")
    job_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(lead_discovery_jobs)")}
    for name, definition in (
        ("claimed_by", "TEXT"),
        ("claim_token", "TEXT"),
        ("lease_until", "TEXT"),
        ("heartbeat_at", "TEXT"),
        ("consecutive_errors", "INTEGER NOT NULL DEFAULT 0"),
        ("last_new_channels", "INTEGER NOT NULL DEFAULT 0"),
        ("last_candidates", "INTEGER NOT NULL DEFAULT 0"),
        ("low_yield_streak", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in job_columns:
            db.execute(f"ALTER TABLE lead_discovery_jobs ADD COLUMN {name} {definition}")
    run_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(lead_discovery_runs)")}
    for name in ("worker_key", "claim_token"):
        if name not in run_columns:
            db.execute(f"ALTER TABLE lead_discovery_runs ADD COLUMN {name} TEXT")
    if "channels_scanned" not in run_columns:
        db.execute("ALTER TABLE lead_discovery_runs ADD COLUMN channels_scanned INTEGER NOT NULL DEFAULT 0")
    for name in ('new_channels', 'duplicate_channels'):
        if name not in run_columns:
            db.execute(f'ALTER TABLE lead_discovery_runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
    channel_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(lead_discovery_channels)")}
    for name in ("scan_claimed_by", "scan_lease_until"):
        if name not in channel_columns:
            db.execute(f"ALTER TABLE lead_discovery_channels ADD COLUMN {name} TEXT")
    db.execute('CREATE INDEX IF NOT EXISTS discovery_family_started_idx ON lead_discovery_jobs(tier_key,language,last_started_at)')
    db.execute('CREATE INDEX IF NOT EXISTS discovery_topic_started_idx ON lead_discovery_jobs(tier_key,last_started_at)')
    db.execute('CREATE INDEX IF NOT EXISTS discovery_channel_url_idx ON lead_discovery_channels(channel_url)')
    ensure_learning_schema(db)
    ensure_activity_schema(db)
    now = iso_utc()
    db.execute(
        """INSERT OR IGNORE INTO lead_discovery_state(
             worker_key,enabled,status,heartbeat_at,updated_at
           ) VALUES(?,1,'starting',?,?)""",
        (worker_key, now, now),
    )
    if seed_jobs:
        # Only the supervisor/seeder performs global migrations and inventory
        # maintenance. Running these write-heavy statements in every worker
        # causes avoidable contention with the local D1 dashboard.
        db.execute(
            """UPDATE lead_discovery_emails SET was_new_contact=1
               WHERE was_new_contact<>1 AND contact_id IN (
                 SELECT id FROM contacts WHERE source_file LIKE 'continuous_lead_discovery:%'
               )"""
        )
        db.execute(
            """INSERT OR IGNORE INTO lead_sources(
                 name,source_type,location,policy_status,enabled,discovered_count,imported_count,updated_at
               ) VALUES(?, 'youtube_public_continuous', 'scripts/continuous_lead_discovery.py',
                 'public_business_contact',1,0,0,?)""",
            (SOURCE_NAME, now),
        )
        db.execute(
            """INSERT INTO outreach_settings(key,value,updated_at)
               VALUES('lead_discovery_target_new_contacts',?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (str(TARGET_NEW_CONTACTS), now),
        )
        db.execute(
            """INSERT INTO outreach_settings(key,value,updated_at)
               VALUES('lead_discovery_daily_target_new_contacts',?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (str(DAILY_TARGET_NEW_CONTACTS), now),
        )
        seed_version = db.execute(
            "SELECT value FROM outreach_settings WHERE key='lead_discovery_seed_version'"
        ).fetchone()
        if not seed_version or str(seed_version["value"]) != SEED_INVENTORY_VERSION:
            for seed in seed_queries(expanded=expanded_seed):
                db.execute(
                    """INSERT OR IGNORE INTO lead_discovery_jobs(
                         tier,tier_key,query,search_mode,language,status,priority,next_run_at,created_at,updated_at
                       ) VALUES(?,?,?,?,?,'queued',?,?,?,?)""",
                    (seed.tier, seed.tier_key, seed.query, seed.search_mode, seed.language, seed.priority, now, now, now),
                )
            db.execute(
                """INSERT INTO outreach_settings(key,value,updated_at) VALUES('lead_discovery_seed_version',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (SEED_INVENTORY_VERSION, now),
            )
        # Tier 1 product-name matches and Tier 2 explicit AI-title matches both
        # have inspectable public video evidence and can enter the send queue.
        db.execute(
            """UPDATE contacts SET curated_ai=1,
                 notes=notes || CASE WHEN notes='' THEN '' ELSE char(10) END ||
                   '自动资格：公开匹配视频标题属于优先产品词或明确 AI 主题。',
                 updated_at=datetime('now')
               WHERE validation_status='send_ready' AND outreach_status='not_contacted'
                 AND source_file LIKE 'continuous_lead_discovery:%'
                 AND id IN (
                   SELECT c.id FROM contacts c
                   JOIN lead_discovery_jobs j
                     ON c.source_file=('continuous_lead_discovery:' || j.id)
                   WHERE j.tier<=2
                 )
                 AND curated_ai=0"""
        )
        # MX is not evidence of audience size; unknown subscriber counts fail closed.
        db.execute(
            """UPDATE contacts SET validation_status='needs_subscriber_verification',
                 notes=notes || CASE WHEN notes='' THEN '' ELSE char(10) END ||
                   '发送资格待核验：公开订阅数尚未确认达到 1,000。',updated_at=datetime('now')
               WHERE source_file LIKE 'continuous_lead_discovery:%'
                 AND validation_status='send_ready' AND COALESCE(subscribers,0)<1000
                 AND outreach_status='not_contacted' AND first_sent_at IS NULL"""
        )
        recover_expired_leases(db, now)
    db.execute(
        """UPDATE lead_discovery_state SET status='starting',current_job_id=NULL,active_tier=NULL,
             current_query=NULL,heartbeat_at=?,updated_at=? WHERE worker_key=?""",
        (now, now, worker_key),
    )
    db.commit()


def normalize_description(value: str) -> str:
    text = value or ""
    substitutions = (
        (r"\s*(?:\[at\]|\(at\)|\{at\})\s*", "@"),
        (r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", "."),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def public_contact_emails(description: str) -> list[str]:
    text = normalize_description(description)
    candidates: list[tuple[str, bool]] = []
    for match in EMAIL_RE.finditer(text):
        email = match.group(1).lower().strip(".,;:()[]{}<>")
        local, _, domain = email.partition("@")
        if (
            not domain
            or domain in BLOCKED_DOMAINS
            or local in BLOCKED_LOCAL_PARTS
            or "/" in local
            or "\\" in local
            or domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
            or len(email) > 254
        ):
            continue
        context = text[max(0, match.start() - 140): min(len(text), match.end() + 140)]
        has_contact_context = bool(CONTACT_CONTEXT_RE.search(context))
        has_role_address = any(role in local for role in ROLE_LOCAL_PARTS)
        candidates.append((email, has_contact_context or has_role_address))
    unique = list(dict.fromkeys(email for email, is_contact in candidates if is_contact))
    if not unique and len(candidates) == 1 and CONTACT_CONTEXT_RE.search(text):
        unique = [candidates[0][0]]
    return unique


def public_website_urls(description: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s<>()\[\]{}\"']+", description or "", re.I):
        url = match.group(0).rstrip(".,;:!?)]}")
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if not host or any(blocked in host for blocked in SKIPPED_WEBSITE_DOMAINS):
            continue
        if url not in urls:
            urls.append(url)
    return urls[:2]


def is_public_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().strip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_global:
                return False
        except ValueError:
            return False
    return True


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        absolute = urllib.parse.urljoin(request.full_url, new_url)
        if not is_public_http_url(absolute):
            raise urllib.error.URLError("redirect target is not a public HTTP address")
        return super().redirect_request(request, file_pointer, code, message, headers, absolute)


def fetch_public_html(url: str) -> tuple[str, str]:
    try:
        if not is_public_http_url(url):
            return "", url
        opener = urllib.request.build_opener(
            SafeRedirectHandler(),
            urllib.request.HTTPSHandler(context=PUBLIC_SSL_CONTEXT),
        )
        request = urllib.request.Request(
            url,
            headers={"User-Agent": PUBLIC_CRAWLER_UA, "Accept": "text/html,application/xhtml+xml"},
        )
        with opener.open(request, timeout=12) as response:
            content_type = str(response.headers.get("content-type") or "").lower()
            if "text/html" not in content_type:
                return "", response.geturl()
            return response.read(600_000).decode("utf-8", errors="ignore"), response.geturl()
    except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException):
        return "", url


def public_contact_page_urls(page: str, base_url: str) -> list[str]:
    base_host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    urls: list[str] = []
    for match in re.finditer(r"href\s*=\s*[\"']([^\"']+)[\"']", page or "", re.I):
        absolute = urllib.parse.urljoin(base_url, match.group(1))
        parsed = urllib.parse.urlparse(absolute)
        if (parsed.hostname or "").lower() != base_host:
            continue
        path = (parsed.path or "").lower()
        if not re.search(r"(?:^|/)(?:contact|about|work-with|collab|partnership|advertis|sponsor)", path):
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
        if clean not in urls and is_public_http_url(clean):
            urls.append(clean)
    return urls[:1]


def valid_match_for_tier(tier: int, tier_key: str, title: str) -> bool:
    if tier == 1:
        group = tier_key.replace("_", " ")
        pattern = TIER_ONE_PATTERNS.get(group) or TIER_ONE_PATTERNS.get(tier_key)
        return bool(pattern and pattern.search(title))
    if tier == 2:
        product_pattern = TIER_TWO_PRODUCT_PATTERNS.get(tier_key)
        if product_pattern:
            return bool(product_pattern.search(title))
        return bool(AI_SIGNAL_RE.search(title))
    if tier_key.startswith("creator_"):
        return bool(CREATOR_SIGNAL_RE.search(title))
    if tier_key.startswith("knowledge_"):
        return bool(KNOWLEDGE_SIGNAL_RE.search(title))
    return True


def has_mx(email: str, cache: dict[str, bool]) -> bool:
    domain = email.rsplit("@", 1)[1].lower()
    if domain in cache:
        return cache[domain]
    try:
        result = subprocess.run(
            ["/usr/bin/dig", "+short", "MX", domain],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        valid = bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        valid = False
    cache[domain] = valid
    return valid


def discover_channels(job: sqlite3.Row, results_per_query: int) -> tuple[int, list[ChannelCandidate]]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": results_per_query,
        "socket_timeout": 25,
        "retries": 2,
        "ignoreerrors": True,
        "noplaylist": False,
    }
    search_prefix = "ytsearchdate" if str(job["search_mode"]) == "recent_public_videos" else "ytsearch"
    with YoutubeDL(options) as ydl:
        payload = ydl.extract_info(f"{search_prefix}{results_per_query}:{job['query']}", download=False) or {}
    entries = [entry for entry in (payload.get("entries") or []) if entry]
    channels: dict[str, ChannelCandidate] = {}
    for entry in entries:
        title = str(entry.get("title") or "").strip()
        if not valid_match_for_tier(int(job["tier"]), str(job["tier_key"]), title):
            continue
        channel_id = str(entry.get("channel_id") or "").strip()
        channel_url = str(entry.get("uploader_url") or entry.get("channel_url") or "").strip()
        if not channel_url and channel_id:
            channel_url = f"https://www.youtube.com/channel/{channel_id}"
        if not channel_url.startswith("https://www.youtube.com/"):
            continue
        key = channel_id or channel_url.lower().rstrip("/")
        video_id = str(entry.get("id") or "").strip()
        video_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        if video_id and not video_url.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        channels.setdefault(
            key,
            ChannelCandidate(
                key=key,
                channel_id=channel_id,
                name=str(entry.get("channel") or entry.get("uploader") or "Unknown creator")[:240],
                url=channel_url[:1000],
                video_title=title[:500],
                video_url=video_url[:1000],
            ),
        )
    return len(entries), list(channels.values())


def scan_channel(candidate: ChannelCandidate) -> tuple[str, int | None, list[EmailEvidence], dict]:
    activity = fetch_activity(candidate.url, candidate.channel_id, days=30)
    if not recent(activity):
        return candidate.name, None, [], activity
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": 1,
        "socket_timeout": 25,
        "retries": 1,
        "ignoreerrors": True,
        "noplaylist": False,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(candidate.url.rstrip("/") + "/videos", download=False) or {}
    description = str(info.get("description") or "")
    channel_name = str(info.get("channel") or info.get("uploader") or candidate.name)[:240]
    raw_subscribers = info.get("channel_follower_count") or info.get("subscriber_count")
    try:
        subscribers = int(raw_subscribers) if raw_subscribers is not None else None
    except (TypeError, ValueError):
        subscribers = None
    if subscribers is not None and subscribers < 0:
        subscribers = None
    evidence = [
        EmailEvidence(email=email, source_url=candidate.url, source_type="youtube_public_description")
        for email in public_contact_emails(description)
    ]
    if not evidence:
        for website_url in public_website_urls(description)[:1]:
            page, final_url = fetch_public_html(website_url)
            if not page:
                continue
            evidence.extend(
                EmailEvidence(email=email, source_url=final_url, source_type="creator_linked_official_website")
                for email in public_contact_emails(page)
            )
            if not evidence:
                for contact_url in public_contact_page_urls(page, final_url):
                    contact_page, contact_final_url = fetch_public_html(contact_url)
                    evidence.extend(
                        EmailEvidence(
                            email=email,
                            source_url=contact_final_url,
                            source_type="creator_linked_official_website",
                        )
                        for email in public_contact_emails(contact_page)
                    )
            break
    unique: dict[str, EmailEvidence] = {}
    for item in evidence:
        unique.setdefault(item.email, item)
    ranked = sorted(
        unique.values(),
        key=lambda item: (
            0 if any(role in item.email.split("@", 1)[0] for role in ROLE_LOCAL_PARTS) else 1,
            0 if item.source_type == "youtube_public_description" else 1,
            len(item.email),
        ),
    )
    return channel_name, subscribers, ranked[:1], activity


def upsert_channel(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    candidate: ChannelCandidate,
    worker_key: str,
    lease_seconds: int,
    claim_scan: bool = True,
) -> bool:
    now = iso_utc()
    db.execute(
        """INSERT INTO lead_discovery_channels(
             channel_key,channel_id,channel_name,channel_url,best_tier,matched_query,matched_video_title,
             matched_video_url,language,scan_status,email_count,first_seen_at,last_seen_at
           ) VALUES(?,?,?,?,?,?,?,?,?,'discovered',0,?,?)
           ON CONFLICT(channel_key) DO UPDATE SET
             channel_id=coalesce(excluded.channel_id,lead_discovery_channels.channel_id),
             channel_name=excluded.channel_name,channel_url=excluded.channel_url,
             best_tier=min(lead_discovery_channels.best_tier,excluded.best_tier),
             matched_query=CASE WHEN excluded.best_tier<=lead_discovery_channels.best_tier THEN excluded.matched_query ELSE lead_discovery_channels.matched_query END,
             matched_video_title=CASE WHEN excluded.best_tier<=lead_discovery_channels.best_tier THEN excluded.matched_video_title ELSE lead_discovery_channels.matched_video_title END,
             matched_video_url=CASE WHEN excluded.best_tier<=lead_discovery_channels.best_tier THEN excluded.matched_video_url ELSE lead_discovery_channels.matched_video_url END,
             language=CASE WHEN excluded.best_tier<=lead_discovery_channels.best_tier THEN excluded.language ELSE lead_discovery_channels.language END,
             last_seen_at=excluded.last_seen_at""",
        (
            candidate.key, candidate.channel_id or None, candidate.name, candidate.url, int(job["tier"]),
            str(job["query"]), candidate.video_title, candidate.video_url or None, str(job["language"]), now, now,
        ),
    )
    if not claim_scan:
        return False
    cutoff = iso_utc(utc_now() - timedelta(days=90))
    retry_cutoff = iso_utc(utc_now() - timedelta(hours=4))
    inactive_cutoff = iso_utc(utc_now() - timedelta(days=7))
    lease_until = iso_utc(utc_now() + timedelta(seconds=max(300, lease_seconds)))
    cursor = db.execute(
        """UPDATE lead_discovery_channels SET scan_status='scanning',scan_claimed_by=?,scan_lease_until=?
           WHERE channel_key=?
             AND (last_contact_scan_at IS NULL OR last_contact_scan_at<?
                  OR (scan_status IN ('scan_error','activity_unknown') AND last_contact_scan_at<?)
                  OR (scan_status='inactive' AND last_contact_scan_at<?))
             AND (scan_lease_until IS NULL OR scan_lease_until<? OR scan_claimed_by=?)""",
        (worker_key, lease_until, candidate.key, cutoff, retry_cutoff, inactive_cutoff, now, worker_key),
    )
    return cursor.rowcount > 0


def import_public_email(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    candidate: ChannelCandidate,
    channel_name: str,
    subscribers: int | None,
    evidence: EmailEvidence,
    mx_valid: bool,
) -> bool:
    email = evidence.email
    now = iso_utc()
    validation = "mx" if mx_valid else "needs_validation"
    contact = db.execute("SELECT id FROM contacts WHERE lower(email)=?", (email,)).fetchone()
    inserted = False
    if mx_valid and not contact:
        tier = int(job["tier"])
        category = "AI & Tech" if tier <= 2 else "Creative & Media"
        ai_match = int(tier <= 2)
        curated_ai = int(tier <= 2)
        confidence = "matched_priority_product_video" if tier == 1 else "matched_ai_video_title" if tier == 2 else "talking_head_candidate"
        priority = f"P{tier}"
        fit_score = {1: 98, 2: 90, 3: 74}[tier]
        audience_status = (
            "send_ready" if subscribers is not None and subscribers >= 1_000
            else "below_minimum_audience" if subscribers is not None
            else "needs_subscriber_verification"
        )
        cursor = db.execute(
            """INSERT OR IGNORE INTO contacts(
                 email,channel_name,channel_url,platform,category,ai_match,curated_ai,ai_confidence,
                 validation_status,verification_proof,email_domain,subscribers,country,language,topic,
                 priority,fit_score,source_file,source_count,source_rows,outreach_status,reply_status,
                 deal_status,do_not_contact,notes,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                email, channel_name, candidate.url, "YouTube", category, ai_match, curated_ai, confidence,
                audience_status,
                "公开创作者频道简介 + MX" if evidence.source_type == "youtube_public_description" else "频道链接的公开官方网站 + MX",
                email.rsplit("@", 1)[1], subscribers, "Unknown",
                str(job["language"]), f"{job['query']} · {candidate.video_title}"[:500], priority,
                fit_score, f"continuous_lead_discovery:{job['id']}", 1, 1, "not_contacted", "no_reply",
                "not_started", 0,
                "Public creator-controlled contact; exact matched video and source are retained in discovery tables.", now,
            ),
        )
        inserted = cursor.rowcount > 0
        contact = db.execute("SELECT id FROM contacts WHERE lower(email)=?", (email,)).fetchone()
    elif mx_valid and contact and subscribers is not None:
        audience_status = "send_ready" if subscribers >= 1_000 else "below_minimum_audience"
        db.execute(
            """UPDATE contacts SET subscribers=?,validation_status=?,updated_at=datetime('now')
               WHERE id=? AND source_file LIKE 'continuous_lead_discovery:%'""",
            (subscribers, audience_status, int(contact["id"])),
        )
    contact_id = int(contact["id"]) if contact else None
    db.execute(
        """INSERT INTO lead_discovery_emails(
             email,channel_key,source_url,source_type,validation_status,contact_id,was_new_contact,first_seen_at,last_seen_at
           ) VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(email) DO UPDATE SET channel_key=excluded.channel_key,source_url=excluded.source_url,
             validation_status=CASE WHEN excluded.validation_status='mx' THEN 'mx' ELSE lead_discovery_emails.validation_status END,
             contact_id=coalesce(excluded.contact_id,lead_discovery_emails.contact_id),
             was_new_contact=max(lead_discovery_emails.was_new_contact,excluded.was_new_contact),
             last_seen_at=excluded.last_seen_at""",
        (email, candidate.key, evidence.source_url, evidence.source_type, validation, contact_id, int(inserted), now, now),
    )
    if contact_id:
        db.execute(
            """INSERT OR IGNORE INTO lead_provenance(
                 contact_id,email,source_type,source_url,source_file,contact_basis,domain_validation,discovered_at
               ) VALUES(?,?,?,?,?,'public_business_contact',?,?)""",
            (contact_id, email, evidence.source_type, evidence.source_url, f"continuous_lead_discovery:{job['id']}", validation, now),
        )
    return inserted


def refresh_source_totals(db: sqlite3.Connection, error: str | None = None) -> None:
    counts = db.execute(
        """SELECT COUNT(*) AS discovered,
                  SUM(CASE WHEN contact_id IS NOT NULL THEN 1 ELSE 0 END) AS imported
           FROM lead_discovery_emails"""
    ).fetchone()
    db.execute(
        """UPDATE lead_sources SET discovered_count=?,imported_count=?,last_run_at=?,last_error=?,updated_at=?
           WHERE name=?""",
        (
            int(counts["discovered"] or 0), int(counts["imported"] or 0), iso_utc(),
            error[:1000] if error else None, iso_utc(), SOURCE_NAME,
        ),
    )


def renew_job_heartbeat(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    worker_key: str,
    lease_seconds: int,
) -> None:
    now = iso_utc()
    lease_until = iso_utc(utc_now() + timedelta(seconds=max(300, lease_seconds)))
    cursor = db.execute(
        """UPDATE lead_discovery_jobs SET heartbeat_at=?,lease_until=?,updated_at=?
           WHERE id=? AND claimed_by=? AND claim_token=? AND status='running'""",
        (now, lease_until, now, int(job["id"]), worker_key, str(job["claim_token"])),
    )
    if cursor.rowcount != 1:
        db.rollback()
        raise RuntimeError('job lease was lost; stop this worker result')
    db.execute('''UPDATE lead_discovery_channels SET scan_lease_until=?
      WHERE scan_status='scanning' AND scan_claimed_by=?''', (lease_until, worker_key))
    db.execute(
        """UPDATE lead_discovery_state SET heartbeat_at=?,updated_at=?
           WHERE worker_key=?""",
        (now, now, worker_key),
    )
    db.commit()


def run_job(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    results_per_query: int,
    channel_limit: int,
    channel_workers: int,
    worker_key: str = WORKER_KEY,
    lease_seconds: int = 1800,
) -> dict[str, int]:
    videos_scanned, candidates = discover_channels(job, results_per_query)
    renew_job_heartbeat(db, job, worker_key, lease_seconds)
    mx_cache: dict[str, bool] = {}
    public_emails_found = 0
    contacts_imported = 0
    eligible_contacts = active_channels = inactive_channels = unknown_activity = 0
    scan_candidates: list[ChannelCandidate] = []
    existing = set()
    if candidates:
        marks = ','.join('?' for _ in candidates)
        existing = {r[0] for r in db.execute(f'SELECT channel_key FROM lead_discovery_channels WHERE channel_key IN ({marks})', [c.key for c in candidates])}
    candidates.sort(key=lambda c: c.key in existing)
    for candidate in candidates:
        should_scan = upsert_channel(db, job, candidate, worker_key, lease_seconds,
                                     claim_scan=len(scan_candidates)<channel_limit)
        if should_scan and len(scan_candidates) < channel_limit:
            scan_candidates.append(candidate)
    db.commit()
    renew_job_heartbeat(db, job, worker_key, lease_seconds)
    with ThreadPoolExecutor(max_workers=max(1, channel_workers)) as pool:
        futures = {pool.submit(scan_channel, candidate): candidate for candidate in scan_candidates}
        for future in as_completed(futures):
            candidate = futures[future]
            renew_job_heartbeat(db, job, worker_key, lease_seconds)
            try:
                channel_name, subscribers, evidence_items, activity = future.result()
            except (DownloadError, OSError, RuntimeError, ValueError) as exc:
                unknown_activity += 1
                db.execute(
                    """UPDATE lead_discovery_channels SET scan_status='scan_error',last_contact_scan_at=?,last_error=?
                         ,scan_claimed_by=NULL,scan_lease_until=NULL
                       WHERE channel_key=? AND scan_claimed_by=?""",
                    (iso_utc(), str(exc)[:500], candidate.key, worker_key),
                )
                db.commit()
                renew_job_heartbeat(db, job, worker_key, lease_seconds)
                continue
            save_activity(db, candidate.url, activity)
            if not recent(activity):
                if activity['status'] == 'inactive':
                    inactive_channels += 1
                else:
                    unknown_activity += 1
                db.execute("""UPDATE lead_discovery_channels SET scan_status=?,last_contact_scan_at=?,last_error=?,
                  scan_claimed_by=NULL,scan_lease_until=NULL WHERE channel_key=? AND scan_claimed_by=?""",
                  ('inactive' if activity['status']=='inactive' else 'activity_unknown',iso_utc(),activity.get('detail'),candidate.key,worker_key))
                db.commit()
                renew_job_heartbeat(db, job, worker_key, lease_seconds)
                continue
            active_channels += 1
            found_here = 0
            for evidence in evidence_items:
                found_here += 1
                public_emails_found += 1
                if import_public_email(db, job, candidate, channel_name, subscribers, evidence, has_mx(evidence.email, mx_cache)):
                    contacts_imported += 1
                    if subscribers is not None and subscribers >= 1000:
                        eligible_contacts += 1
            db.execute(
                """UPDATE lead_discovery_channels SET channel_name=?,scan_status=?,email_count=?,
                     last_contact_scan_at=?,last_error=NULL,scan_claimed_by=NULL,scan_lease_until=NULL
                   WHERE channel_key=? AND scan_claimed_by=?""",
                (channel_name, "email_found" if found_here else "no_public_contact", found_here, iso_utc(), candidate.key, worker_key),
            )
            db.commit()
            renew_job_heartbeat(db, job, worker_key, lease_seconds)
    return {
        "videos_scanned": videos_scanned,
        "channels_found": len(candidates),
        "channels_scanned": len(scan_candidates),
        "public_emails_found": public_emails_found,
        "contacts_imported": contacts_imported,
        "new_channels": len(candidates) - len(existing),
        "duplicate_channels": len(existing),
        "eligible_contacts": eligible_contacts,
        "active_channels": active_channels,
        "inactive_channels": inactive_channels,
        "unknown_activity": unknown_activity,
    }


def discovery_cooldown(job, result):
    """Reward fresh audiences and progressively cool exhausted search shards."""
    total = result['channels_found']
    low_yield = total == 0 or result['new_channels'] / total < 0.1
    streak = int(job['low_yield_streak']) + 1 if low_yield else 0
    if low_yield:
        days = (7, 14, 30, 60)[min(streak-1, 3)]
    else:
        days = 2 if result.get('eligible_contacts', result['contacts_imported']) > 0 else 7
    if job['search_mode'] == 'relevance_public_videos':
        days = max(days, 30)
    return streak, days


def claim_next_job(
    db: sqlite3.Connection,
    worker_key: str,
    lease_seconds: int,
    focus: str = "all",
) -> tuple[sqlite3.Row, int, str] | None:
    if focus not in {"all", "ai", "creator", "knowledge"}:
        raise ValueError(f"Unknown discovery focus: {focus}")
    now = iso_utc()
    lease_until = iso_utc(utc_now() + timedelta(seconds=max(300, lease_seconds)))
    claim_token = uuid.uuid4().hex
    # Most workers stay AI-first. Two workers may pick due talking-head jobs
    # ahead of tier-2 refreshes; these jobs have recently produced more new
    # public contacts, but still cannot bypass downstream fit checks.
    prefer_talking_head = worker_key in {"collector-07", "collector-08"}
    try:
        db.execute("BEGIN IMMEDIATE")
        recover_expired_leases(db, now)
        family, selection_mode, selection_reason = learned_family(db, focus, worker_key, now)
        job = db.execute(
            f"""SELECT j.* FROM lead_discovery_jobs j
               WHERE next_run_at<=? AND status IN ('queued','waiting','error')
                 AND ({FOCUS_SQL.get(focus, '1=1')})
                 AND (? IS NULL OR (tier_key=? AND language=? AND low_yield_streak<2))
               ORDER BY
                 CASE WHEN run_count=0 THEN 0 ELSE 1 END,
                 low_yield_streak ASC,
                 CASE
                   WHEN run_count=0 AND tier=1 THEN 0
                   WHEN search_mode='recent_public_videos' AND tier=? THEN 1
                   WHEN search_mode='recent_public_videos' AND tier=? THEN 2
                   WHEN search_mode='recent_public_videos' AND tier=1 THEN 3
                   ELSE 4
                 END,
                 CASE status WHEN 'queued' THEN 0 WHEN 'error' THEN 1 ELSE 2 END,
                 CASE WHEN run_count=0 THEN 0 ELSE 1 END,
                 COALESCE((SELECT max(topic.last_started_at) FROM lead_discovery_jobs topic
                   WHERE topic.tier_key=j.tier_key),'') ASC,
                 COALESCE((SELECT max(f.last_started_at) FROM lead_discovery_jobs f
                   WHERE f.tier_key=j.tier_key AND f.language=j.language),'') ASC,
                 priority DESC,last_completed_at ASC,id ASC
               LIMIT 1""",
            (now, family[0] if family else None,
             family[0] if family else None, family[1] if family else None,
             3 if prefer_talking_head else 2, 2 if prefer_talking_head else 3),
        ).fetchone()
        if not job:
            db.commit()
            return None
        cursor = db.execute(
            """UPDATE lead_discovery_jobs SET status='running',run_count=run_count+1,last_started_at=?,
                 last_error=NULL,claimed_by=?,claim_token=?,lease_until=?,heartbeat_at=?,updated_at=?
               WHERE id=? AND status IN ('queued','waiting','error') AND next_run_at<=?""",
            (now, worker_key, claim_token, lease_until, now, now, int(job["id"]), now),
        )
        if cursor.rowcount != 1:
            db.rollback()
            return None
        run_cursor = db.execute(
            """INSERT INTO lead_discovery_runs(job_id,status,started_at,worker_key,claim_token,selection_mode,selection_reason)
               VALUES(?,'running',?,?,?,?,?)""",
            (int(job["id"]), now, worker_key, claim_token, selection_mode, selection_reason),
        )
        db.commit()
        claimed = db.execute(
            "SELECT * FROM lead_discovery_jobs WHERE id=? AND claimed_by=? AND claim_token=?",
            (int(job["id"]), worker_key, claim_token),
        ).fetchone()
        if not claimed:
            return None
        return claimed, int(run_cursor.lastrowid), claim_token
    except sqlite3.Error:
        db.rollback()
        raise


def update_state(db: sqlite3.Connection, status: str, worker_key: str = WORKER_KEY, **values: Any) -> None:
    allowed = {
        "current_job_id", "active_tier", "current_query", "heartbeat_at", "last_started_at",
        "last_completed_at", "last_error",
    }
    assignments = ["status=?", "updated_at=?"]
    params: list[Any] = [status, iso_utc()]
    for key, value in values.items():
        if key not in allowed:
            continue
        assignments.append(f"{key}=?")
        params.append(value)
    params.append(worker_key)
    db.execute(f"UPDATE lead_discovery_state SET {','.join(assignments)} WHERE worker_key=?", params)
    db.commit()


def execute_once(
    db: sqlite3.Connection,
    results_per_query: int,
    channel_limit: int,
    channel_workers: int,
    worker_key: str = WORKER_KEY,
    lease_seconds: int = 1800,
    focus: str = "all",
) -> dict[str, Any]:
    state = db.execute("SELECT enabled FROM lead_discovery_state WHERE worker_key=?", (worker_key,)).fetchone()
    if state and not int(state["enabled"]):
        update_state(db, "paused", worker_key=worker_key, heartbeat_at=iso_utc(), current_job_id=None, active_tier=None, current_query=None)
        return {"status": "paused"}
    claim = claim_next_job(db, worker_key, lease_seconds, focus)
    if not claim:
        update_state(db, "idle", worker_key=worker_key, heartbeat_at=iso_utc(), current_job_id=None, active_tier=None, current_query=None, last_error=None)
        return {"status": "idle", "focus": focus}
    job, run_id, claim_token = claim
    started = str(job["last_started_at"])
    update_state(
        db, "running", worker_key=worker_key, current_job_id=int(job["id"]), active_tier=int(job["tier"]),
        current_query=str(job["query"]), heartbeat_at=started, last_started_at=started, last_error=None,
    )
    try:
        result = run_job(db, job, results_per_query, channel_limit, channel_workers, worker_key, lease_seconds)
        completed = iso_utc()
        low_yield_streak, refresh_days = discovery_cooldown(job, result)
        next_run = iso_utc(utc_now() + timedelta(days=refresh_days))
        completion = db.execute(
            """UPDATE lead_discovery_jobs SET status='waiting',videos_scanned=videos_scanned+?,
                 channels_found=channels_found+?,public_emails_found=public_emails_found+?,
                 contacts_imported=contacts_imported+?,last_completed_at=?,next_run_at=?,last_error=NULL,
                 claimed_by=NULL,claim_token=NULL,lease_until=NULL,heartbeat_at=NULL,consecutive_errors=0,updated_at=?,
                 last_new_channels=?,last_candidates=?,low_yield_streak=?
               WHERE id=? AND claimed_by=? AND claim_token=?""",
            (
                result["videos_scanned"], result["channels_found"], result["public_emails_found"],
                result["contacts_imported"], completed, next_run, completed,
                result['new_channels'],result['channels_found'],low_yield_streak,int(job["id"]), worker_key, claim_token,
            ),
        )
        if completion.rowcount != 1:
            raise RuntimeError("job lease was lost before completion")
        db.execute(
            """UPDATE lead_discovery_runs SET status='completed',videos_scanned=?,channels_found=?,channels_scanned=?,
                 public_emails_found=?,contacts_imported=?,completed_at=?,new_channels=?,duplicate_channels=?,
                 eligible_contacts=?,active_channels=?,inactive_channels=?,unknown_activity=?,metrics_version=1 WHERE id=?""",
            (
                result["videos_scanned"], result["channels_found"], result["channels_scanned"], result["public_emails_found"],
                result["contacts_imported"], completed, result['new_channels'],result['duplicate_channels'],
                result['eligible_contacts'],result['active_channels'],result['inactive_channels'],result['unknown_activity'],run_id,
            ),
        )
        refresh_source_totals(db)
        db.commit()
        update_state(
            db, "sleeping", worker_key=worker_key, current_job_id=None, active_tier=None, current_query=None,
            heartbeat_at=completed, last_completed_at=completed, last_error=None,
        )
        return {"status": "completed", "worker_key": worker_key, "job_id": int(job["id"]), "tier": int(job["tier"]), "query": str(job["query"]), **result}
    except (DownloadError, OSError, RuntimeError, sqlite3.Error) as exc:
        message = str(exc)[:1000]
        failed = iso_utc()
        retry = iso_utc(utc_now() + timedelta(hours=min(24,2**min(int(job['consecutive_errors']),5))))
        db.execute(
            """UPDATE lead_discovery_jobs SET status='error',last_error=?,next_run_at=?,
                 claimed_by=NULL,claim_token=NULL,lease_until=NULL,heartbeat_at=NULL,
                 consecutive_errors=consecutive_errors+1,updated_at=?
               WHERE id=? AND claimed_by=? AND claim_token=?""",
            (message, retry, failed, int(job["id"]), worker_key, claim_token),
        )
        db.execute(
            """UPDATE lead_discovery_runs SET status='error',completed_at=?,error=? WHERE id=? AND status='running'""",
            (failed, message, run_id),
        )
        refresh_source_totals(db, message)
        db.commit()
        update_state(
            db, "degraded", worker_key=worker_key, current_job_id=None, active_tier=None, current_query=None,
            heartbeat_at=failed, last_completed_at=failed, last_error=message,
        )
        return {"status": "error", "worker_key": worker_key, "job_id": int(job["id"]), "tier": int(job["tier"]), "query": str(job["query"]), "error": message}


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def main() -> int:
    args = parse_args()
    worker_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(args.worker_key)).strip("-") or WORKER_KEY
    if args.lock == DEFAULT_LOCK and worker_key != WORKER_KEY:
        args.lock = DEFAULT_LOCK.with_name(f"continuous-lead-discovery.{worker_key}.lock")
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit({"status": "already_running"})
            return 0
        if args.startup_delay_seconds > 0:
            time.sleep(args.startup_delay_seconds)
        DISCOVERY_SCHEMA_LOCK.parent.mkdir(parents=True, exist_ok=True)
        with DISCOVERY_SCHEMA_LOCK.open("a+") as schema_lock:
            fcntl.flock(schema_lock, fcntl.LOCK_EX)
            db = connect(args.db)
            db.execute("PRAGMA busy_timeout=120000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            ensure_discovery_schema(
                db,
                worker_key,
                seed_jobs=not args.skip_seed,
                expanded_seed=args.expanded_seed,
            )
        try:
            if args.seed_only:
                count = db.execute("SELECT COUNT(*) AS count FROM lead_discovery_jobs").fetchone()["count"]
                update_state(db, "stopped", worker_key=worker_key, heartbeat_at=iso_utc())
                emit({"status": "seeded", "jobs": int(count)})
                return 0
            if not args.continuous:
                emit(execute_once(
                    db, max(5, args.results_per_query), max(1, args.channel_limit),
                    max(1, args.channel_workers), worker_key, max(300, args.lease_seconds), args.focus,
                ))
                update_state(db, "stopped", worker_key=worker_key, heartbeat_at=iso_utc())
                return 0
            while True:
                result = execute_once(
                    db,
                    max(5, args.results_per_query),
                    max(1, args.channel_limit),
                    max(1, args.channel_workers),
                    worker_key,
                    max(300, args.lease_seconds),
                    args.focus,
                )
                emit(result)
                if result["status"] == "paused":
                    sleep_for = 60.0
                elif result["status"] == "idle":
                    sleep_for = min(300.0, max(60.0, args.min_sleep_seconds))
                else:
                    low = max(5.0, args.min_sleep_seconds)
                    high = max(low, args.max_sleep_seconds)
                    sleep_for = random.uniform(low, high)
                time.sleep(sleep_for)
        except KeyboardInterrupt:
            update_state(db, "stopped", worker_key=worker_key, heartbeat_at=iso_utc(), current_job_id=None, active_tier=None, current_query=None)
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
