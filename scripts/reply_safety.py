#!/usr/bin/env python3
"""Shared, fail-closed safety policy for automated outreach replies."""

from __future__ import annotations

import re
import sqlite3


CAMPAIGN_QUESTION_RE = re.compile(
    r"(?:\?|\b(?:please\s+(?:share|send|confirm|let\s+me\s+know)|could\s+you|can\s+you|"
    r"what(?:'s|\s+is)|when\s+would|do\s+you|how\s+much)\b)"
    r"[^.!?\n]{0,180}\b(?:budget|rate\s+range|timeline|deliverables?|brief|requirements?|"
    r"payment|compensation|angle|use\s+case|demo|examples?|previous\s+creators?|disclosure)\b|"
    r"(?:\?|\b(?:please\s+(?:share|send)|could\s+you|can\s+you)\b)"
    r"[^.!?\n]{0,140}\b(?:campaign|collaboration|promotion)\s+(?:details?|next\s+steps?)\b|"
    r"(?:\?|\b(?:please\s+(?:share|send)|could\s+you|can\s+you)\b)"
    r"[^.!?\n]{0,100}\b(?:details?|next\s+steps?)\b[^.!?\n]{0,100}"
    r"\b(?:campaign|collaboration|promotion)\b",
    re.I | re.S,
)
PRODUCT_ISSUE_RE = re.compile(
    r"\b(?:bug|error|not\s+working|won't\s+(?:really\s+)?load|doesn't\s+(?:really\s+)?load|lag|slow|"
    r"slower|stuck|failed|failure|problem|issue|privacy|terms|data\s+use|generation\s+time|"
    r"render(?:ing)?\s+(?:time|speed|faster)|long\s+to\s+generate|technical\s+report|key\s+findings|"
    r"limitations?)\b",
    re.I,
)
PRODUCT_EXAMPLE_RE = re.compile(
    r"\b(?:examples?|demos?|samples?|proof)\b[^.!?\n]{0,120}\b(?:edit(?:s|ing)?|output|result|video|tool|product)\b|"
    r"\b(?:edit(?:s|ing)?|output|result|video|tool|product)\b[^.!?\n]{0,120}\b(?:examples?|demos?|samples?|proof)\b",
    re.I,
)
TARGET_CHANNEL_QUESTION_RE = re.compile(
    r"\b(?:are\s+you|is\s+(?:this|the)|do\s+you)\b[^.!?\n]{0,140}\b(?:collaboration|promotion|campaign)\b"
    r"[^.!?\n]{0,100}\b(?:channel|youtube|page|account)\b|"
    r"\b(?:which|what)\b[^.!?\n]{0,80}\b(?:channel|youtube\s+channel|page|account)\b",
    re.I,
)
PAID_TEST_RE = re.compile(
    r"\b(?:paid|compensated)\s+(?:product\s+)?(?:test|testing|trial|feedback)\b|"
    r"\b(?:payment|compensation)\s+for\s+(?:the\s+)?(?:product\s+)?(?:test|testing|trial|feedback)\b|"
    r"\b(?:test|testing|trial|feedback)\b[^.!?\n]{0,35}\b(?:must|needs?\s+to|has\s+to|should)\s+be\s+(?:paid|compensated)\b",
    re.I,
)
CONDITIONAL_DEAL_RE = re.compile(
    r"\b(?:once\s+(?:you(?:'ve)?\s+)?subscrib(?:e|ed)|click\s+here|fill\s+(?:in|out)\s+(?:the\s+)?form|"
    r"book\s+(?:a\s+)?call|calendar|(?:contact|message|reach)\s+(?:me|us)?\s*(?:on|via|to)\s+(?:wechat|telegram|whatsapp)|"
    r"(?:continue|move)\b[^.!?\n]{0,60}\b(?:on|via|to)\s+(?:wechat|telegram|whatsapp)|upfront|deposit|payment\s+(?:terms?|confirmation)|"
    r"paypal|sign(?:ed|ing)?\s+(?:the\s+)?(?:deal|agreement|contract)|cash\s+budget|sponsored\s+editorial|"
    r"guest\s+posts?|directory|first\s+step\s+is\s+to\s+list)\b",
    re.I,
)
REJECTION_RE = re.compile(
    r"\b(?:not\s+interested|no\s+thanks|not\s+a\s+fit|remove\s+me|unsubscribe|do\s+not\s+contact|"
    r"declin(?:e|ed|ing)|cannot\s+(?:showcase|promote|move\s+forward)|can't\s+(?:showcase|promote|move\s+forward)|"
    r"won't\s+(?:showcase|promote|move\s+forward)|will\s+not\s+(?:showcase|promote|move\s+forward)|too\s+basic)\b",
    re.I,
)
RATE_SIGNAL_RE = re.compile(
    r"(?:\$|£|€|₹|\b(?:USD|GBP|EUR|INR)\b)\s*[0-9][0-9,.]*|"
    r"[0-9][0-9,.]*\s*(?:\$|£|€|₹|\b(?:USD|GBP|EUR|INR)\b)|"
    r"\b(?:my|our|standard|current)\s+(?:collaboration\s+)?(?:rate|rates|pricing)\s*"
    r"(?:is|are|:|=|starts?\s+at)\b|\brate\s+card\b|\b(?:i|we)\s+(?:normally\s+|usually\s+)?(?:charge|quote)\b",
    re.I,
)


AUTOMATION_RULE_BY_TEMPLATE = {
    "first_contact": "first_contact",
    "first_contact_legacy_light": "first_contact",
    "first_contact_t2v_knowledge_v1": "first_contact",
    "initial_followup_24h": "first_contact",
    "rate_followup_24h": "price_request",
    "registered_email_followup_24h": "membership_grant",
    "test_followup": "semantic_template_router",
    "coupon_test": "coupon_reply",
    "coupon_test_t2v_knowledge_v1": "coupon_reply",
    # These templates acknowledge (but never accept) a rate or defer generic
    # campaign details until after the creator test.  Their executable action
    # is still the already-approved coupon reply, so they belong to the coupon
    # policy.  Keeping them under the price/manual or generic/draft rules left
    # correctly routed, fully covered test requests stranded in review.
    "coupon_test_rate_received": "coupon_reply",
    "coupon_test_campaign_details": "coupon_reply",
    "campaign_details_after_coupon": "semantic_template_router",
    "ask_registered_email": "membership_grant",
    "account_not_found": "membership_grant",
    "membership_activated": "membership_grant",
    "product_question_hold": "product_question",
    "test_claim_checking": "test_verification",
    "test_planned_ack": "semantic_template_router",
    "ask_rate": "price_request",
    "rate_received": "price_received",
    "marketing_listed": "marketing_list",
}


def current_message(value: str) -> str:
    """Remove common quoted-thread content before making a new send decision."""
    text = value.replace("\r\n", "\n")
    text = re.split(
        r"\n\s*(?:On\s+.{0,240}\bwrote:|Отправлено\s+из\b|[^\n]{0,180}\ba\s+(?:scris|écrit)\s*:|"
        r"[^\n]{0,220}\bот\s+(?:Alhazar|team@videocoding\.ai)\b[^\n]*:|"
        r"[^\n]{0,220}تمت\s+كتابة\s+ما\s+يلي[^\n]*:|"
        r"[^\n]{0,220}>?\s*于\s*\d{4}年\d{1,2}月\d{1,2}日[^\n]{0,180}写道[：:]?|"
        r"[^\n]{0,260}\bschrieb\b[^\n]*:|[^\n]{0,260}(?:님이\s*작성|작성:|寫道[：:]))",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    outgoing = re.search(r"\n\s*Hi,\s*\n+\s*I(?:’|')m\s+Alhazar\s+from\s+VideoCoding", text, re.I)
    if outgoing:
        text = text[:outgoing.start()]
    # Remove common promotional signature blocks before safety classification.
    # Phone/social links in a signature are not instructions to move the deal
    # off-platform, and treating them as such creates false-positive holds.
    text = re.split(
        r"\n\s*(?:Contacts?\s*(?:&|and)\s*Connections?|"
        r"(?:\*+\s*)?(?:Call|Email|Follow|Join)\s+(?:Us|Me)\b)",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^(?:from|sent|to|subject|von|gesendet|an|betreff|发件人|发送时间|收件人|主题):\s", stripped, re.I):
            break
        if re.match(r"^on\s+.+\bwrote:\s*$", stripped, re.I) or re.match(
            r"^-{2,}\s*replied message\s*-{2,}$", stripped, re.I
        ):
            break
        kept.append(line)
    return "\n".join(kept).strip()[:12000]


def safety_flags(message: str) -> tuple[str, ...]:
    flags: list[str] = []
    if REJECTION_RE.search(message):
        flags.append("rejection")
    if PRODUCT_ISSUE_RE.search(message):
        flags.append("product_issue")
    if PRODUCT_EXAMPLE_RE.search(message):
        flags.append("product_example")
    if TARGET_CHANNEL_QUESTION_RE.search(message):
        flags.append("target_channel_question")
    if CONDITIONAL_DEAL_RE.search(message):
        flags.append("conditional_deal")
    if PAID_TEST_RE.search(message):
        flags.append("conditional_deal")
    if CAMPAIGN_QUESTION_RE.search(message):
        flags.append("campaign_question")
    pricing_text = message
    if re.search(r'\brate\s+card\b[^.!?\n]{0,90}\b(?:after\s+test(?:ing)?|once|later|at\s+that\s+point)\b', message, re.I):
        pricing_text = re.sub(r'\brate\s+card\b', 'future pricing document', pricing_text, flags=re.I)
    if (re.search(r'\b(?:coupon|creator\s+code|trial|pro\s+access|free\s+month)\b', message, re.I)
            and not re.search(r'\b(?:my|our|standard)\s+(?:rate|price|fee)|\b(?:i|we)\s+charge\b', message, re.I)):
        pricing_text = re.sub(r'[$€£]\s*0(?:[.,]0+)?(?!\d|[.,]\d)', 'free trial', pricing_text)
    if RATE_SIGNAL_RE.search(pricing_text):
        flags.append("rate_signal")
    return tuple(flags)


def pre_send_hold_reason(template_key: str | None, message: str) -> str | None:
    """Return a user-facing hold reason when a fixed reply is not context-safe."""
    key = str(template_key or "")
    flags = set(safety_flags(message))
    if "rejection" in flags:
        return "对方包含拒绝、退订或停止合作信号；禁止自动发送，需停止推进并复核联系人状态。"
    if "product_issue" in flags:
        return "对方报告产品问题或负面测试反馈；固定模板不能替代具体问题回复。"
    if "product_example" in flags:
        return "对方索要产品输出示例或演示；Campaign 模板没有回答该产品问题。"
    if "target_channel_question" in flags:
        return "对方要求确认具体合作频道；当前模板没有回答该问题。"
    if "conditional_deal" in flags:
        return "对方提出付款、合同、订阅或外部流程条件；必须转人工处理。"
    if "campaign_question" in flags and key not in {"coupon_test_campaign_details", "campaign_details_after_coupon"}:
        return "对方同时询问预算、brief、交付、时间线或合作细节；固定模板无法完整回答。"
    if key in {"coupon_test_campaign_details", "campaign_details_after_coupon"} and "rate_signal" in flags:
        return "当前来信已经包含创作者报价；必须使用报价场景模板或转人工。"
    if key == "ask_rate" and "rate_signal" in flags:
        return "当前来信已经包含报价；禁止再次发送询价模板。"
    if key in {"coupon_test", "coupon_test_t2v_knowledge_v1"} and "rate_signal" in flags:
        return "当前来信同时包含报价；必须改用已报价 Coupon 模板或转人工，不能发送普通 Coupon 模板。"
    return None


def automation_rule_mode(db: sqlite3.Connection, rule_key: str) -> str:
    """Read the executable policy; missing, disabled, or invalid rules fail closed."""
    try:
        row = db.execute(
            "SELECT mode,enabled FROM automation_rules WHERE rule_key=?",
            (rule_key,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "missing"
    if not row or not int(row[1] or 0):
        return "disabled"
    mode = str(row[0] or "").strip().lower()
    return mode if mode in {"auto", "draft", "manual"} else "invalid"


def template_automation_mode(db: sqlite3.Connection, template_key: str | None) -> str:
    key = str(template_key or "")
    rule_key = AUTOMATION_RULE_BY_TEMPLATE.get(key)
    if not rule_key and key.startswith("first_contact"):
        rule_key = "first_contact"
    return automation_rule_mode(db, rule_key) if rule_key else "missing"


def template_allows_auto_send(db: sqlite3.Connection, template_key: str | None) -> bool:
    return template_automation_mode(db, template_key) == "auto"
