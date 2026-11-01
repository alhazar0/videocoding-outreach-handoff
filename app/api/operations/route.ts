import { env } from "cloudflare:workers";
import { NextRequest, NextResponse } from "next/server";
import { refreshAccessToken } from "@/app/lib/microsoft";
import { microsoftOutboundBlockReason } from "@/app/lib/mail-delivery";
import { currentReplyText, requestsCardlessAccess, pricingEvidenceText } from "@/app/lib/reply-text";
import replySnapshotSQL from "@/app/lib/reply-context-snapshot.sql?raw";
import { REPLY_CONTEXT_VERSION, contextFingerprint, prepareReplyContext, currentEvidenceMatches } from "@/app/lib/reply-context";

type Bindings = {
  DB: D1Database;
  OPENAI_API_KEY?: string;
  OPENAI_MODEL?: string;
  PRODUCT_ADMIN_API_ORIGIN?: string;
  PRODUCT_ADMIN_BEARER_TOKEN?: string;
};

type InboxRow = {
  id: string;
  contact_id: number | null;
  event_type: string;
  subject: string;
  body_preview: string;
  body_text: string | null;
  sender_email: string;
  channel_name: string | null;
  contact_email: string | null;
  reply_status: string | null;
  coupon_status: string | null;
  usage_verification_status?: string | null;
  initial_template_key?: string | null;
};

type TemplateRow = {
  template_key: string;
  name?: string;
  stage?: string;
  subject: string;
  body: string;
  risk_level: string;
  enabled?: number;
  approval_status?: string;
};

type ReplyIntent =
  | "wants_test"
  | "activation_help"
  | "cardless_trial"
  | "plans_to_test"
  | "claims_tested"
  | "price_received"
  | "price_and_tested"
  | "product_question"
  | "product_issue"
  | "asks_campaign_details"
  | "contract_terms"
  | "privacy_question"
  | "complaint"
  | "not_interested"
  | "automated"
  | "thanks_only"
  | "other";

type AIReplyDecision = {
  intent: ReplyIntent;
  template_key:
    | "coupon_test"
    | "coupon_test_t2v_knowledge_v1"
    | "coupon_test_rate_received"
    | "coupon_test_campaign_details"
    | "campaign_details_after_coupon"
    | "ask_registered_email"
    | "product_question_hold"
    | "test_claim_checking"
    | "test_planned_ack"
    | "rate_received"
    | null;
  confidence: number;
  language: string;
  registered_email: string | null;
  test_status: "not_started" | "wants_test" | "testing" | "completed" | "unknown";
  questions: string[];
  risk_level: "low" | "medium" | "high";
  needs_human: boolean;
  rationale: string;
  secondary_intents: ReplyIntent[];
  current_request_summary: string;
  conversation_state: "new_request" | "continues_prior" | "supersedes_prior" | "conflicts_with_prior" | "ambiguous";
  evidence: Array<{ intent: string; source: "current_message" | "thread_history" | "profile_state"; text: string }>;
  contradictions: string[];
  missing_context: string[];
  context_complete: boolean;
  template_gap: boolean;
  template_gap_reason: string | null;
  proposed_template: null | {
    name: string;
    stage: string;
    subject: string;
    body: string;
    required_context: string[];
  };
};

const COUPON = "FREETEST100";
const AI_AUTO_ROUTE_THRESHOLD = 0.9;
const allowedWorkStatuses = new Set(["pending", "approved", "sent", "ignored", "rejected", "failed"]);
const replyIntentValues = [
  "wants_test", "activation_help", "cardless_trial", "plans_to_test", "claims_tested",
  "price_received", "price_and_tested", "product_question", "product_issue", "asks_campaign_details",
  "contract_terms", "privacy_question", "complaint", "not_interested", "automated", "thanks_only", "other",
];
const aiReplyJsonSchema = {
  type: "object",
  additionalProperties: false,
  required: [
    "intent", "template_key", "confidence", "language", "registered_email", "test_status", "questions",
    "risk_level", "needs_human", "rationale", "secondary_intents", "current_request_summary",
    "conversation_state", "evidence", "contradictions", "missing_context", "context_complete", "template_gap",
    "template_gap_reason", "proposed_template",
  ],
  properties: {
    intent: { type: "string", enum: replyIntentValues },
    template_key: { type: ["string", "null"], enum: ["coupon_test", "coupon_test_t2v_knowledge_v1", "coupon_test_rate_received", "coupon_test_campaign_details", "campaign_details_after_coupon", "ask_registered_email", "product_question_hold", "test_claim_checking", "test_planned_ack", "rate_received", null] },
    confidence: { type: "number", minimum: 0, maximum: 1 },
    language: { type: "string", maxLength: 80 },
    registered_email: { type: ["string", "null"], maxLength: 320 },
    test_status: { type: "string", enum: ["not_started", "wants_test", "testing", "completed", "unknown"] },
    questions: { type: "array", maxItems: 10, items: { type: "string", maxLength: 500 } },
    risk_level: { type: "string", enum: ["low", "medium", "high"] },
    needs_human: { type: "boolean" },
    rationale: { type: "string", maxLength: 2000 },
    secondary_intents: { type: "array", minItems: 1, maxItems: 12, items: { type: "string", enum: replyIntentValues } },
    current_request_summary: { type: "string", maxLength: 2000 },
    conversation_state: { type: "string", enum: ["new_request", "continues_prior", "supersedes_prior", "conflicts_with_prior", "ambiguous"] },
    evidence: {
      type: "array", minItems: 1, maxItems: 24,
      items: {
        type: "object", additionalProperties: false, required: ["intent", "source", "text"],
        properties: {
          intent: { type: "string", maxLength: 100 },
          source: { type: "string", enum: ["current_message", "thread_history", "profile_state"] },
          text: { type: "string", maxLength: 500 },
        },
      },
    },
    contradictions: { type: "array", maxItems: 12, items: { type: "string", maxLength: 500 } },
    missing_context: { type: "array", maxItems: 12, items: { type: "string", maxLength: 500 } },
    context_complete: { type: "boolean" },
    template_gap: { type: "boolean" },
    template_gap_reason: { type: ["string", "null"], maxLength: 1000 },
    proposed_template: {
      type: ["object", "null"], additionalProperties: false,
      required: ["name", "stage", "subject", "body", "required_context"],
      properties: {
        name: { type: "string", maxLength: 300 },
        stage: { type: "string", maxLength: 120 },
        subject: { type: "string", maxLength: 300 },
        body: { type: "string", maxLength: 12000 },
        required_context: { type: "array", maxItems: 12, items: { type: "string", maxLength: 300 } },
      },
    },
  },
};

const defaultTemplates = [
  {
    key: "first_contact",
    name: "首次邀请合作",
    stage: "first_contact",
    subject: "A paid collaboration with VideoCoding",
    risk: "medium",
    body: `Hi,

I’m Alhazar from VideoCoding. I think it could be a useful fit for the kind of videos you create, and I’d love to explore a paid collaboration with you.

You upload the talking-head cut you want to keep, and VideoCoding takes care of the rest—deciding where visuals are needed, creating the B-roll and motion graphics, and handling the captions, music, and sound design.

Here’s the site:
https://videocoding.ai

If you’d like to test it on one of your own videos, reply and I’ll send you a code for one free month. If you like the result, we can discuss the promotion and your rate.

Best,
Alhazar Anwar`,
  },
  {
    key: "first_contact_legacy_light",
    name: "首次邀请 · Legacy-light（历史最佳修正版）",
    stage: "first_contact",
    subject: "A paid collaboration with VideoCoding",
    risk: "medium",
    body: `Hi {{channel_name}},

I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you.

VideoCoding turns a talking-head cut into a finished video with B-roll, motion graphics, captions, music, and sound design.

You can see it here:
https://videocoding.ai

If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0. After you try it on one real video, we can discuss the promotion format and your rate.

Best,
Alhazar Anwar
VideoCoding`,
  },
  {
    key: "first_contact_t2v_knowledge_v1",
    name: "首次邀请 · T2V 知识类 v1",
    stage: "first_contact",
    subject: "A paid collaboration with VideoCoding",
    risk: "medium",
    body: `Hi {{channel_name}},

I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you.

With just a short prompt, VideoCoding can generate a complete video up to 10 minutes long—from research and scripting to narration, scenes, motion, music, and the final edit.

You can see some examples here:
https://videocoding.ai/?mode=t2v

I think it could be a useful fit for the kind of knowledge videos you create. If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0.

After you try it with one real topic, we can discuss the promotion format and your rate.

Best,
Alhazar Anwar
VideoCoding`,
  },
  {
    key: "coupon_test",
    name: "愿意测试 · 发送 Coupon",
    stage: "coupon_ready",
    subject: "Your VideoCoding Pro Test Access + Instructions",
    risk: "medium",
    body: `Hi,

Sure — here’s the code for one month of VideoCoding Pro:

{{coupon_code}}

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter {{coupon_code}}
4. Make sure the total shows $0.00 before confirming

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

When you get a chance, try it with a talking-head video you would normally publish. If anything doesn’t work, just reply here and I’ll help.

Once you’ve tried it, let me know what you think. If it’s a good fit, we can talk about the promotion and your rate.

Best,
Alhazar`,
  },
  {
    key: "coupon_test_t2v_knowledge_v1",
    name: "愿意测试 · T2V 知识类 Coupon v1",
    stage: "coupon_ready",
    subject: "Your VideoCoding Pro Test Access + Instructions",
    risk: "medium",
    body: `Hi,

Sure — here’s the code for one month of VideoCoding Pro:

{{coupon_code}}

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter {{coupon_code}}
4. Make sure the total shows $0.00 before confirming

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

For a useful test, enter one real topic you would normally cover and start with a short prompt. VideoCoding can turn it into a complete video up to 10 minutes long, including the research, script, narration, scenes, motion, music, and final edit.

Once you’ve tried it, let me know what you think. If it’s a good fit, we can talk about the promotion and your rate.

Best,
Alhazar`,
  },
  {
    key: "coupon_test_rate_received",
    name: "已报价并愿意测试 · 发送 Coupon",
    stage: "coupon_ready",
    subject: "Re: {{original_subject}}",
    risk: "medium",
    body: `Hi,

Thanks for sharing your rates. I’ve recorded them for review. I’m not confirming a campaign or budget yet, but here’s the code so you can test VideoCoding first:

{{coupon_code}}

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter {{coupon_code}}
4. Make sure the total shows $0.00 before confirming

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

Once you’ve tested it on one of your own videos, let me know how it went. We can then review the fit, promotion format, requirements, timeline, and your rate together.

Best,
Alhazar`,
  },
  {
    key: "coupon_test_campaign_details",
    name: "先测试 · 再谈 Campaign 细节",
    stage: "coupon_ready",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

Thanks for asking. Yes, if we decide to move forward after the test, this would be a paid collaboration.

We haven’t set the exact brief or budget for your channel yet. I’d prefer to do that after you’ve tried VideoCoding on one of your own videos, rather than guess before you know how it fits your workflow.

That way, if we work together, you’ll be talking about something you’ve actually used.

Here’s the code for one month of VideoCoding Pro:

{{coupon_code}}

To activate it:

1. Sign in or create an account at https://videocoding.ai
2. Click Upgrade to Pro
3. Click Add promotion code and enter {{coupon_code}}
4. Make sure the total shows $0.00 before confirming

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

Please try it with a video you would normally publish and send me your honest feedback. If it feels like a good fit for both sides, we can then discuss the promotion format, deliverables, timing, and your rate.

Best,
Alhazar Anwar`,
  },
  {
    key: "campaign_details_after_coupon",
    name: "已发测试码 · 测试后再谈合作细节",
    stage: "test_pending",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

Yes, this would be a paid collaboration if we both decide it’s a fit.

Before we lock in the format or budget, I’d like you to actually use VideoCoding on one of your own videos. I’d rather build the brief around how it fits your content than guess before you’ve tried it.

I sent the test code earlier — it’s FREETEST100.

Redeeming this code through checkout starts a subscription. The first month is fully discounted, and it renews automatically at the price shown at checkout unless you cancel before renewal. You can cancel from your account settings. If you prefer a creator test without adding a card or automatic renewal, reply with your registration email before checking out so we can check the account for direct trial access.

Try it on a video you’d normally publish and tell me honestly what worked and what didn’t. If it feels right for your channel, send me your rate and we’ll work out the format, deliverables, timing, and budget from there.

Best,
Alhazar`,
  },
  {
    key: "ask_registered_email",
    name: "不愿绑卡 · 询问注册邮箱",
    stage: "membership_email_needed",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

No problem — you don’t need to add a card for the creator test. Please send me the email address you used to register for VideoCoding, and I’ll check the account so we can arrange the one-month access from our side.

Best,
Alhazar Anwar`,
  },
  {
    key: "account_not_found",
    name: "后台未找到账号",
    stage: "account_not_found",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

I couldn’t find a VideoCoding account under {{registered_email}}. Please create the account first at https://videocoding.ai and reply when it’s ready. I’ll check it again before arranging access.

Best,
Alhazar Anwar`,
  },
  {
    key: "membership_activated",
    name: "后台会员已开通",
    stage: "testing",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

Done — I’ve activated one month of VideoCoding Pro for the account registered under {{registered_email}}.

You don’t need to add a card. The access ends automatically after 30 days, so there is no automatic charge. You can sign in at https://videocoding.ai and start testing it with one of your own videos.

Once you’ve tried it, let me know how it went and we can discuss the promotion format and your rate.

Best,
Alhazar Anwar`,
  },
  {
    key: "product_question_hold",
    name: "产品问题 · 正在确认",
    stage: "product_question",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

Thanks for flagging this. I’m checking the exact behavior with the product team so I don’t give you the wrong information. I’ll follow up as soon as I have a confirmed answer.

Best,
Alhazar Anwar`,
  },
  {
    key: "test_followup",
    name: "测试进度跟进",
    stage: "testing",
    subject: "Re: {{original_subject}}",
    risk: "medium",
    body: `Hi,

Just checking in to see whether you had a chance to test VideoCoding with one of your videos. If anything was unclear or didn’t work as expected, let me know and I’ll help.

Best,
Alhazar Anwar`,
  },
  {
    key: "initial_followup_24h",
    name: "首次合作邀请 · 24小时无回复跟进",
    stage: "followup",
    subject: "Re: A paid collaboration with VideoCoding",
    risk: "medium",
    body: `Hi {{channel_name}},

Just following up in case my last email got buried. We’d still be interested in exploring a paid collaboration with you.

If you’d like to test VideoCoding on one of your own videos, reply and I’ll send you a code for one free month.

Best,
Alhazar Anwar`,
  },
  {
    key: "rate_followup_24h",
    name: "真实测试完成 · 24小时未报价跟进",
    stage: "price_needed",
    subject: "Re: VideoCoding collaboration",
    risk: "medium",
    body: `Hi,

Just following up after your VideoCoding test. When you have a moment, could you share your rate for a dedicated YouTube video and, if available, a 60–90 second integration?

Best,
Alhazar Anwar`,
  },
  {
    key: "registered_email_followup_24h",
    name: "不绑卡申请 · 24小时未提供注册邮箱跟进",
    stage: "membership_email_needed",
    subject: "Re: VideoCoding creator access",
    risk: "medium",
    body: `Hi,

Just following up — when you have a moment, please send me the email address you used to register for VideoCoding. I’ll check the account before arranging the one-month creator access from our side.

Best,
Alhazar Anwar`,
  },
  {
    key: "test_claim_checking",
    name: "声称已测试 · 先核验账号",
    stage: "test_claimed",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

Thanks for the update. Before we move on to collaboration pricing, could you confirm the email address you used to register for VideoCoding? I want to make sure I’m checking the correct creator test account.

Best,
Alhazar Anwar`,
  },
  {
    key: "test_planned_ack",
    name: "准备测试 · 等待真实结果",
    stage: "testing",
    subject: "Re: {{original_subject}}",
    risk: "medium",
    body: `Hi,

Thanks — that sounds good. Please test VideoCoding with one of your own videos and let me know how the completed result works for you.

Once you’ve completed the test, we can discuss the promotion format, deliverables, timeline, and your rate. If anything blocks you during the test, let me know and I’ll help.

Best,
Alhazar Anwar`,
  },
  {
    key: "test_login_only",
    name: "只登录未测试 · 请完成一次生成",
    stage: "test_login_only",
    subject: "Re: {{original_subject}}",
    risk: "medium",
    body: `Hi,

Thanks for signing in. I checked the creator test account, but I don’t see a completed video generation yet. Could you upload a short talking-head clip and run one complete test before we discuss the promotion?

If anything is stopping you during upload or generation, let me know what you’re seeing and I’ll help.

Best,
Alhazar Anwar`,
  },
  {
    key: "test_started_incomplete",
    name: "已开始未完成 · 询问卡点",
    stage: "test_started",
    subject: "Re: {{original_subject}}",
    risk: "medium",
    body: `Hi,

Thanks for trying it. I can see that a video test was started, but it hasn’t completed yet. Did you run into an issue during upload or generation?

If you tell me where it stopped or send a screenshot, I’ll help get the test completed before we discuss the promotion.

Best,
Alhazar Anwar`,
  },
  {
    key: "ask_rate",
    name: "测试完成 · 询问报价",
    stage: "price_needed",
    subject: "Re: {{original_subject}}",
    risk: "medium",
    body: `Hi,

Great to hear the test went well. Could you share your rate for a dedicated YouTube video and, if available, a 60–90 second integration? It would also be helpful to know your usual turnaround time.

Best,
Alhazar Anwar`,
  },
  {
    key: "rate_received",
    name: "已收到报价",
    stage: "price_received",
    subject: "Re: {{original_subject}}",
    risk: "high",
    body: `Hi,

Thanks for sharing your rates and collaboration options. I’ve recorded the details for our marketing team to review. I’m not confirming a campaign yet, but we’ll come back to you when we have a suitable campaign and format.

Best,
Alhazar Anwar`,
  },
  {
    key: "marketing_listed",
    name: "加入广告合作评估名单",
    stage: "marketing_review",
    subject: "Re: {{original_subject}}",
    risk: "medium",
    body: `Hi,

Thanks for testing VideoCoding and for sharing your rates. I’ve added your channel and collaboration details to our creator advertising list for the marketing team to review.

We’ll reach out when we have a campaign that matches your audience and format. We really appreciate the time you took to test the product and share your feedback.

Best,
Alhazar Anwar`,
  },
  {
    key: "not_interested",
    name: "不感兴趣 · 礼貌结束",
    stage: "closed",
    subject: "Re: {{original_subject}}",
    risk: "low",
    body: `Hi,

Thanks for letting me know. No problem at all — I won’t follow up again. I appreciate your time.

Best,
Alhazar Anwar`,
  },
];

const copyTemplateVersions: Record<string, number> = {
  first_contact: 3,
  coupon_test: 14,
  coupon_test_t2v_knowledge_v1: 2,
  coupon_test_rate_received: 2,
  ask_rate: 3,
  marketing_listed: 3,
  coupon_test_campaign_details: 3,
  campaign_details_after_coupon: 2,
};

const defaultRules = [
  ["first_contact", "首次邀请", "auto", "medium", "仅使用已启用且人工批准的首次邀请模板自动发送。"],
  ["coupon_reply", "发送 Coupon", "auto", "medium", "有意向回复满 30 分钟后自动发送固定 Coupon 模板。"],
  ["membership_grant", "后台开会员", "manual", "high", "必须核对注册邮箱并人工批准。"],
  ["product_question", "产品问题", "manual", "high", "知识库无确定答案时禁止自动回答。"],
  ["test_verification", "真实测试核验", "manual", "high", "对方声称测试后，必须根据后台视频任务核验；不能仅凭邮件进入询价。"],
  ["price_request", "询问报价", "draft", "medium", "使用固定询价模板。"],
  ["price_received", "收到报价", "manual", "high", "不得自动接受价格或承诺合作。"],
  ["marketing_list", "加入营销评估名单", "draft", "medium", "只确认进入评估，不承诺合作。"],
  ["opt_out", "退订与停止联系", "auto", "low", "立即停止后续发送并写入禁止联系。"],
  ["ignore", "自动回复与无关邮件", "auto", "low", "记录原因，不发送任何回复。"],
  ["semantic_template_router", "AI 上下文模板分流", "draft", "medium", "AI 先核对完整会话、产品状态和全部意图；模板缺失时只创建禁用的待审核候选，绝不自动发送。"],
];

function DB() {
  return (env as unknown as Bindings).DB;
}

async function ensureOperationsSchema() {
  const db = DB();
  const schemaVersion = "2026-10-08-reply-context-v1";
  try {
    const current = await db.prepare("SELECT value FROM outreach_settings WHERE key='operations_schema_version'")
      .first<{ value: string }>();
    if (current?.value === schemaVersion) return;
  } catch {
    // A brand-new database may not have outreach_settings yet; initialize below.
  }
  const statements = [
    `CREATE TABLE IF NOT EXISTS reply_context_checks (work_item_id INTEGER PRIMARY KEY,checked_at TEXT NOT NULL)`,
    `CREATE TABLE IF NOT EXISTS outreach_templates (
      template_key TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      stage TEXT NOT NULL,
      subject TEXT NOT NULL,
      body TEXT NOT NULL,
      risk_level TEXT NOT NULL DEFAULT 'medium',
      enabled INTEGER NOT NULL DEFAULT 1,
      version INTEGER NOT NULL DEFAULT 1,
      approval_status TEXT NOT NULL DEFAULT 'approved',
      intent_signature TEXT,
      context_requirements_json TEXT NOT NULL DEFAULT '[]',
      source_work_item_id INTEGER,
      creation_reason TEXT NOT NULL DEFAULT '',
      example_count INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL
    )`,
    `CREATE TABLE IF NOT EXISTS automation_rules (
      rule_key TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      mode TEXT NOT NULL,
      risk_level TEXT NOT NULL,
      description TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL
    )`,
    `CREATE TABLE IF NOT EXISTS creator_profiles (
      contact_id INTEGER PRIMARY KEY,
      workflow_stage TEXT NOT NULL DEFAULT 'lead',
      registered_email TEXT,
      account_status TEXT NOT NULL DEFAULT 'unknown',
      membership_status TEXT NOT NULL DEFAULT 'none',
      membership_expires_at TEXT,
      coupon_code TEXT,
      coupon_sent_at TEXT,
      test_status TEXT NOT NULL DEFAULT 'not_started',
      test_started_at TEXT,
      test_completed_at TEXT,
      product_user_id TEXT,
      usage_verification_status TEXT NOT NULL DEFAULT 'not_checked',
      usage_total_tasks INTEGER NOT NULL DEFAULT 0,
      usage_active_tasks INTEGER NOT NULL DEFAULT 0,
      usage_completed_tasks INTEGER NOT NULL DEFAULT 0,
      usage_last_login_at TEXT,
      usage_last_task_at TEXT,
      usage_verified_at TEXT,
      usage_source TEXT,
      coupon_redemption_status TEXT NOT NULL DEFAULT 'not_checked',
      coupon_redeemed_code TEXT,
      coupon_redemption_verified_at TEXT,
      usage_failed_tasks INTEGER NOT NULL DEFAULT 0,
      usage_canceled_tasks INTEGER NOT NULL DEFAULT 0,
      usage_last_seen_at TEXT,
      product_sync_error TEXT,
      price_dedicated REAL,
      price_integration REAL,
      price_shorts REAL,
      currency TEXT,
      turnaround TEXT,
      marketing_status TEXT NOT NULL DEFAULT 'not_ready',
      updated_at TEXT NOT NULL
    )`,
    `CREATE TABLE IF NOT EXISTS outreach_work_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      inbox_event_id TEXT UNIQUE,
      contact_id INTEGER,
      kind TEXT NOT NULL,
      workflow_stage TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      risk_level TEXT NOT NULL,
      confidence REAL,
      template_key TEXT,
      subject TEXT NOT NULL DEFAULT '',
      draft_body TEXT NOT NULL DEFAULT '',
      extracted_json TEXT NOT NULL DEFAULT '{}',
      proposed_action_json TEXT NOT NULL DEFAULT '[]',
      decision_reason TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      resolved_at TEXT,
      resolved_by TEXT
    )`,
    `CREATE INDEX IF NOT EXISTS outreach_work_items_status_idx
      ON outreach_work_items(status, risk_level, created_at DESC)`,
    `CREATE INDEX IF NOT EXISTS outreach_work_items_contact_status_idx
      ON outreach_work_items(contact_id,status,id DESC)`,
    `CREATE INDEX IF NOT EXISTS inbox_events_contact_received_idx
      ON inbox_events(contact_id,received_at DESC,id DESC)`,
    `CREATE INDEX IF NOT EXISTS inbox_events_conversation_received_idx
      ON inbox_events(conversation_id,received_at DESC,id DESC)`,
    `CREATE TABLE IF NOT EXISTS outreach_sent_messages (
      id TEXT PRIMARY KEY,
      conversation_id TEXT,
      contact_id INTEGER NOT NULL,
      recipient_email TEXT NOT NULL,
      subject TEXT NOT NULL DEFAULT '',
      body_text TEXT NOT NULL DEFAULT '',
      sent_at TEXT NOT NULL,
      synced_at TEXT NOT NULL
    )`,
    `CREATE INDEX IF NOT EXISTS outreach_sent_messages_contact_idx
      ON outreach_sent_messages(contact_id,sent_at DESC)`,
    `CREATE INDEX IF NOT EXISTS outreach_sent_messages_conversation_sent_idx
      ON outreach_sent_messages(conversation_id,sent_at DESC)`,
    `CREATE TABLE IF NOT EXISTS lead_sources (
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
    )`,
    `CREATE TABLE IF NOT EXISTS lead_provenance (
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
    )`,
    `CREATE TABLE IF NOT EXISTS lead_discovery_jobs (
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
    )`,
    `CREATE INDEX IF NOT EXISTS lead_discovery_jobs_queue_idx
      ON lead_discovery_jobs(tier, next_run_at, priority DESC)`,
    `CREATE TABLE IF NOT EXISTS lead_discovery_channels (
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
    )`,
    `CREATE TABLE IF NOT EXISTS lead_discovery_emails (
      email TEXT PRIMARY KEY,
      channel_key TEXT NOT NULL,
      source_url TEXT NOT NULL,
      source_type TEXT NOT NULL,
      validation_status TEXT NOT NULL,
      contact_id INTEGER,
      was_new_contact INTEGER NOT NULL DEFAULT 0,
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL
    )`,
    `CREATE TABLE IF NOT EXISTS lead_discovery_state (
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
    )`,
    `CREATE TABLE IF NOT EXISTS lead_discovery_runs (
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
    )`,
    `CREATE TABLE IF NOT EXISTS outreach_audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      action TEXT NOT NULL,
      entity_type TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      actor TEXT NOT NULL,
      detail_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    )`,
    `CREATE TABLE IF NOT EXISTS coupon_reply_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      inbox_event_id TEXT NOT NULL UNIQUE,
      contact_id INTEGER,
      recipient_email TEXT NOT NULL,
      conversation_id TEXT,
      variant TEXT NOT NULL,
      status TEXT NOT NULL,
      queued_at TEXT NOT NULL,
      sent_at TEXT,
      error TEXT
    )`,
    `CREATE TABLE IF NOT EXISTS creator_data_agent_runs (
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
    )`,
    `CREATE TABLE IF NOT EXISTS creator_followups (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      contact_id INTEGER NOT NULL,
      source_kind TEXT NOT NULL,
      source_id TEXT NOT NULL,
      sequence INTEGER NOT NULL DEFAULT 1,
      template_key TEXT NOT NULL,
      sender_email TEXT NOT NULL,
      conversation_id TEXT,
      source_sent_at TEXT NOT NULL,
      due_at TEXT NOT NULL,
      subject TEXT NOT NULL,
      body TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      queued_at TEXT NOT NULL,
      sent_at TEXT,
      error TEXT,
      updated_at TEXT NOT NULL,
      UNIQUE(source_kind,source_id,sequence)
    )`,
    `CREATE INDEX IF NOT EXISTS creator_followups_due_idx ON creator_followups(status,due_at)`,
    `CREATE TABLE IF NOT EXISTS promotion_creator_list (
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
    )`,
    `CREATE INDEX IF NOT EXISTS promotion_creator_list_status_idx ON promotion_creator_list(list_status,updated_at DESC)`,
  ];
  for (const statement of statements) await db.prepare(statement).run();

  const inboxColumns = await db.prepare("PRAGMA table_info(inbox_events)").all<{ name: string }>();
  if (!inboxColumns.results.some((column: { name: string }) => column.name === "body_text")) {
    await db.prepare("ALTER TABLE inbox_events ADD COLUMN body_text TEXT").run();
  }
  const templateColumns = await db.prepare("PRAGMA table_info(outreach_templates)").all<{ name: string }>();
  const templateColumnNames = new Set(templateColumns.results.map((column: { name: string }) => column.name));
  const templateAdditions = [
    ["approval_status", "TEXT NOT NULL DEFAULT 'approved'"],
    ["intent_signature", "TEXT"],
    ["context_requirements_json", "TEXT NOT NULL DEFAULT '[]'"],
    ["source_work_item_id", "INTEGER"],
    ["creation_reason", "TEXT NOT NULL DEFAULT ''"],
    ["example_count", "INTEGER NOT NULL DEFAULT 0"],
  ];
  for (const [name, definition] of templateAdditions) {
    if (!templateColumnNames.has(name)) await db.prepare(`ALTER TABLE outreach_templates ADD COLUMN ${name} ${definition}`).run();
  }
  await db.prepare(`UPDATE outreach_templates SET approval_status='approved'
    WHERE approval_status IS NULL OR approval_status=''`).run();
  const profileColumns = await db.prepare("PRAGMA table_info(creator_profiles)").all<{ name: string }>();
  const profileColumnNames = new Set(profileColumns.results.map((column: { name: string }) => column.name));
  const profileAdditions = [
    ["product_user_id", "TEXT"],
    ["usage_verification_status", "TEXT NOT NULL DEFAULT 'not_checked'"],
    ["usage_total_tasks", "INTEGER NOT NULL DEFAULT 0"],
    ["usage_active_tasks", "INTEGER NOT NULL DEFAULT 0"],
    ["usage_completed_tasks", "INTEGER NOT NULL DEFAULT 0"],
    ["usage_last_login_at", "TEXT"],
    ["usage_last_task_at", "TEXT"],
    ["usage_verified_at", "TEXT"],
    ["usage_source", "TEXT"],
    ["coupon_redemption_status", "TEXT NOT NULL DEFAULT 'not_checked'"],
    ["coupon_redeemed_code", "TEXT"],
    ["coupon_redemption_verified_at", "TEXT"],
    ["usage_failed_tasks", "INTEGER NOT NULL DEFAULT 0"],
    ["usage_canceled_tasks", "INTEGER NOT NULL DEFAULT 0"],
    ["usage_last_seen_at", "TEXT"],
    ["product_sync_error", "TEXT"],
  ];
  for (const [name, definition] of profileAdditions) {
    if (!profileColumnNames.has(name)) await db.prepare(`ALTER TABLE creator_profiles ADD COLUMN ${name} ${definition}`).run();
  }
  const discoveryEmailColumns = await db.prepare("PRAGMA table_info(lead_discovery_emails)").all<{ name: string }>();
  if (!discoveryEmailColumns.results.some((column: { name: string }) => column.name === "was_new_contact")) {
    await db.prepare("ALTER TABLE lead_discovery_emails ADD COLUMN was_new_contact INTEGER NOT NULL DEFAULT 0").run();
  }
  const discoveryRunColumns = await db.prepare("PRAGMA table_info(lead_discovery_runs)").all<{ name: string }>();
  for (const name of ["new_channels", "duplicate_channels", "eligible_contacts", "active_channels", "inactive_channels", "unknown_activity", "metrics_version"]) {
    if (!discoveryRunColumns.results.some((column: { name: string }) => column.name === name)) {
      await db.prepare(`ALTER TABLE lead_discovery_runs ADD COLUMN ${name} INTEGER NOT NULL DEFAULT 0`).run();
    }
  }
  for (const name of ["worker_key", "selection_mode", "selection_reason"]) {
    if (!discoveryRunColumns.results.some((column: { name: string }) => column.name === name)) {
      await db.prepare(`ALTER TABLE lead_discovery_runs ADD COLUMN ${name} TEXT`).run();
    }
  }
  await db.prepare("CREATE INDEX IF NOT EXISTS discovery_learning_runs_idx ON lead_discovery_runs(started_at,job_id) WHERE metrics_version=1").run();
  await db.prepare("CREATE INDEX IF NOT EXISTS discovery_worker_runs_idx ON lead_discovery_runs(worker_key,id)").run();
  await db.prepare(`UPDATE lead_discovery_emails SET was_new_contact=1
    WHERE contact_id IN (SELECT id FROM contacts WHERE source_file LIKE 'continuous_lead_discovery:%')`).run();

  for (const template of defaultTemplates) {
    await db.prepare(`INSERT OR IGNORE INTO outreach_templates(
      template_key,name,stage,subject,body,risk_level,enabled,version,updated_at
    ) VALUES(?,?,?,?,?,?,1,1,datetime('now'))`)
      .bind(template.key, template.name, template.stage, template.subject, template.body, template.risk)
      .run();
  }
  for (const [templateKey, targetVersion] of Object.entries(copyTemplateVersions)) {
    const template = defaultTemplates.find((candidate) => candidate.key === templateKey);
    if (!template) continue;
    const migration = await db.prepare(`UPDATE outreach_templates SET name=?,stage=?,subject=?,body=?,risk_level=?,
      version=?,updated_at=datetime('now') WHERE template_key=? AND version<?`)
      .bind(template.name, template.stage, template.subject, template.body, template.risk,
        targetVersion, templateKey, targetVersion).run();
    if (Number(migration.meta?.changes ?? 0) > 0) {
      const renderedBody = template.body.replaceAll("{{coupon_code}}", COUPON);
      await db.prepare(`UPDATE outreach_work_items SET draft_body=?,updated_at=datetime('now')
        WHERE template_key=? AND status='pending' AND resolved_by IS NULL`)
        .bind(renderedBody, templateKey).run();
    }
  }
  const membershipActivatedTemplate = defaultTemplates.find((template) => template.key === "membership_activated");
  if (membershipActivatedTemplate) {
    const migration = await db.prepare(`UPDATE outreach_templates SET name=?,stage=?,subject=?,body=?,risk_level=?,
      version=2,updated_at=datetime('now') WHERE template_key='membership_activated' AND version<2`)
      .bind(membershipActivatedTemplate.name, membershipActivatedTemplate.stage, membershipActivatedTemplate.subject,
        membershipActivatedTemplate.body, membershipActivatedTemplate.risk).run();
    if (Number(migration.meta?.changes ?? 0) > 0) {
      await db.prepare(`UPDATE outreach_work_items SET subject=?,draft_body=replace(?, '{{registered_email}}',
        coalesce((SELECT registered_email FROM creator_profiles WHERE contact_id=outreach_work_items.contact_id), '{{registered_email}}')),
        updated_at=datetime('now')
        WHERE template_key='membership_activated' AND status='pending' AND resolved_by IS NULL`)
        .bind(membershipActivatedTemplate.subject, membershipActivatedTemplate.body).run();
    }
  }
  for (const rule of defaultRules) {
    await db.prepare(`INSERT OR IGNORE INTO automation_rules(
      rule_key,name,mode,risk_level,description,enabled,updated_at
    ) VALUES(?,?,?,?,?,1,datetime('now'))`).bind(...rule).run();
  }
  await db.prepare(`UPDATE automation_rules SET name='AI 上下文模板分流',
    description='AI 先核对完整会话、产品状态和全部意图；模板缺失时只创建禁用的待审核候选，绝不自动发送。',
    updated_at=datetime('now') WHERE rule_key='semantic_template_router'`).run();
  const sources = [
    ["历史邮箱主库", "existing_inventory", "data/contact_master.csv", "approved_existing", 14187, 14176],
    ["YouTube 公开频道简介", "youtube_public", "outputs/youtube_english_expanded/english_talking_head_channels.json", "public_business_contact", 523, 438],
    ["YouTube 简介链接的官方网站", "official_website", "outputs/youtube_english_expanded/enrich_sites_expanded.py", "public_business_contact", 523, 438],
    ["人工授权导入", "manual_import", "运营台上传", "operator_confirmed", 0, 0],
    ["YouTube 24小时公开邮箱发现", "youtube_public_continuous", "scripts/continuous_lead_discovery.py", "public_business_contact", 0, 0],
  ];
  for (const source of sources) {
    await db.prepare(`INSERT OR IGNORE INTO lead_sources(
      name,source_type,location,policy_status,enabled,discovered_count,imported_count,updated_at
    ) VALUES(?,?,?,?,1,?,?,datetime('now'))`).bind(...source).run();
  }
  await db.prepare(`INSERT INTO outreach_settings(key,value,updated_at)
    VALUES('coupon_automation_mode','auto',datetime('now'))
    ON CONFLICT(key) DO NOTHING`).run();
  await db.prepare(`INSERT INTO outreach_settings(key,value,updated_at)
    VALUES('lead_discovery_target_new_contacts','1000000',datetime('now'))
    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`).run();
  await db.prepare(`INSERT INTO outreach_settings(key,value,updated_at)
    VALUES('lead_discovery_daily_target_new_contacts','3000',datetime('now'))
    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`).run();
  await db.prepare(`INSERT OR IGNORE INTO lead_discovery_state(
    worker_key,enabled,status,heartbeat_at,updated_at
  ) VALUES('youtube_public_discovery',1,'starting',datetime('now'),datetime('now'))`).run();
  await db.prepare(`INSERT INTO outreach_settings(key,value,updated_at)
    VALUES('operations_schema_version',?,datetime('now'))
    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`)
    .bind(schemaVersion).run();
}

// Schema/default seeding is process initialization work. Re-running the full
// write-heavy routine on every dashboard poll causes avoidable SQLite writer
// contention with the collectors and reply workers.
let operationsSchemaPromise: Promise<void> | null = null;
function ensureOperationsSchemaOnce() {
  if (!operationsSchemaPromise) {
    operationsSchemaPromise = (async () => {
      let lastError: unknown;
      for (let attempt = 0; attempt < 5; attempt += 1) {
        try {
          await ensureOperationsSchema();
          return;
        } catch (error) {
          lastError = error;
          const message = error instanceof Error ? error.message : String(error);
          if (!/D1_ERROR|SQLITE_BUSY|internal error/i.test(message) || attempt === 4) throw error;
          await new Promise((resolve) => setTimeout(resolve, 150 * (attempt + 1)));
        }
      }
      throw lastError;
    })().catch((error) => {
      operationsSchemaPromise = null;
      throw error;
    });
  }
  return operationsSchemaPromise;
}

async function retryD1Write<T>(operation: () => Promise<T>, attempts = 10): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      const message = error instanceof Error ? error.message : String(error);
      if (!/D1_ERROR|SQLITE_BUSY|database is locked|internal error/i.test(message) || attempt === attempts - 1) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, Math.min(4000, 300 * (2 ** attempt))));
    }
  }
  throw lastError;
}

function classify(row: InboxRow) {
  const text = currentReplyText(row.body_text || row.body_preview).toLowerCase();
  const emailMatch = text.match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i);
  const pricingText = pricingEvidenceText(text);
  const priceShared =
    /[$€£]\s?\d[\d,.]*/.test(pricingText) ||
    /\b(?:usd|eur|gbp)\s?\d[\d,.]*/.test(pricingText) ||
    /\b\d[\d,.]*\s?(?:usd|eur|gbp)\b/.test(pricingText) ||
    /\b\d[\d,.]*\s?\$/.test(pricingText) ||
    /\b(?:my|our|standard|current)\s+(?:collaboration\s+)?(?:rate|rates|pricing)\s*(?:is|are|:|=|starts?\s+at)\b/.test(text) ||
    /\b(?:i|we)\s+(?:normally\s+|usually\s+)?(?:charge|quote)\b/.test(text) ||
    /\b(?:attached|here(?:'s| is)|find|see|sharing|send(?:ing)?)\b[^.!?\n]{0,60}\b(?:my|our|the)\s+rate\s+card\b/.test(text);
  const futureTest =
    /\bafter\s+(?:testing|trying|using)\s+(?:videocoding|it|the\s+(?:tool|platform|product))\b[^.!?\n]{0,120}\b(?:we|i)\s+(?:can|will|would|could)\b/.test(text) ||
    /\b(?:once|when)\s+(?:i|we)(?:'ve| have)\s+(?:tested|tried|used)\b/.test(text) ||
    /\b(?:i|we)(?:'ll| will|'m going to| am going to| plan to| intend to| want to| would like to)\b[^.!?\n]{0,100}\b(?:test|try|use|give\b[^.!?\n]{0,40}\ba try)\b/.test(text) ||
    /\b(?:i'd|we'd)\s+be\s+happy\s+to\s+(?:test|try)\b/.test(text) ||
    /\b(?:looking forward to|happy to|would be happy to)\s+(?:trying|testing|try|test)\b/.test(text) ||
    /\bafter\s+(?:i|we)(?:'ve| have)\s+(?:tested|tried|used)\b/.test(text);
  const actualTestClaim = !futureTest && (
    /\b(?:i|we)(?:'ve| have)?\s+(?:now\s+|already\s+|properly\s+|just\s+)?(?:tested|tried|used)\s+(?:videocoding|it|the\s+(?:tool|platform|product))\b/.test(text) ||
    /\b(?:i|we)(?:'ve| have)?\s+now\s+had\s+the\s+chance\s+to\b[^.!?\n]{0,50}\btest\b/.test(text) ||
    /\bgot\s+(?:the\s+)?pro\s+access\s+and\s+tested\b/.test(text) ||
    /\b(?:testing|the\s+test)\s+(?:went|worked)\s+(?:really\s+)?(?:well|great|smoothly)\b/.test(text) ||
    /\b(?:worked\s+out\s+smoothly|initial\s+results\s+look\s+great|tried\s+the\s+output)\b/.test(text) ||
    /\bafter\s+testing\s+(?:videocoding|it|the\s+(?:tool|platform|product))\b/.test(text)
  );
  const couponRequested =
    /\b(?:please|could you|can you|would you)\b[^.!?\n]{0,80}\b(?:send|share|provide)\b[^.!?\n]{0,60}\b(?:code|coupon)\b/.test(text) ||
    /\bsend\s+me\b[^.!?\n]{0,60}\b(?:code|coupon)\b/.test(text);
  const couponAcknowledged =
    /\bthanks?\s+for\b[^.!?\n]{0,50}\b(?:sending|sharing|providing)\b[^.!?\n]{0,50}\b(?:code|coupon)\b/.test(text) ||
    /\b(?:code|coupon)\b[^.!?\n]{0,50}\byou\s+(?:sent|provided|shared)\b/.test(text);
  if (row.event_type === "sender_restricted") {
    return { kind: "ignore", stage: "sender_restricted", template: null, risk: "low", reason: "微软发件账号受限；需管理员处理，不代表收件地址无效，不自动重发。", action: [] };
  }
  if (["delivery_delayed", "delivery_failed"].includes(row.event_type)) {
    return { kind: "ignore", stage: "delivery_review", template: null, risk: "low", reason: "服务商投递报告，不是博主回复；保留投递异常供检查，不自动重发。", action: [] };
  }
  if (row.event_type === "bounce" || row.event_type === "automated") {
    return { kind: "ignore", stage: row.event_type === "bounce" ? "bounced" : "contacted", template: null, risk: "low", reason: "退信或自动回复，不应继续发送。", action: [] };
  }
  if (/not interested|no thanks|remove me|unsubscribe|do not contact/.test(text)) {
    return { kind: "opt_out", stage: "closed", template: null, risk: "low", reason: "对方明确拒绝或要求停止联系；系统静默停止后续发送。", action: [{ type: "do_not_contact" }] };
  }
  if (requestsCardlessAccess(text)) {
    return {
      kind: "membership_request",
      stage: emailMatch ? "membership_pending" : "membership_email_needed",
      template: "ask_registered_email",
      risk: "high",
      reason: emailMatch ? "对方不愿绑卡；已检测到可能的注册邮箱，仍需人工核对。" : "对方不愿绑卡；需要先取得注册邮箱。",
      action: [{ type: "verify_registered_email", email: emailMatch?.[0] ?? null }, { type: "manual_membership_review" }],
      registeredEmail: emailMatch?.[0] ?? null,
    };
  }
  if (priceShared && actualTestClaim) {
    return { kind: "price_and_test_claim", stage: "price_received_unverified", template: "test_claim_checking", risk: "high", reason: "对方已提供报价，也声称完成测试；报价可以记录，但必须先用产品后台的视频任务核验真实测试。", action: [{ type: "extract_price" }, { type: "verify_product_usage" }, { type: "marketing_review" }] };
  }
  if (priceShared) {
    return { kind: "price_received", stage: "price_received", template: "rate_received", risk: "high", reason: "检测到明确金额、rate card 或对方陈述自己的报价；禁止自动接受。", action: [{ type: "extract_price" }, { type: "marketing_review" }] };
  }
  if (actualTestClaim) {
    return { kind: "test_claim", stage: "test_claimed", template: "test_claim_checking", risk: "high", reason: "对方声称已经测试；必须先用产品后台的视频任务记录核验，不能仅凭邮件直接询价。", action: [{ type: "verify_product_usage" }] };
  }
  if (couponRequested) {
    return { kind: "coupon_reply", stage: "coupon_ready", template: "coupon_test", risk: "medium", reason: "对方明确索要测试 Coupon；使用固定模板并等待人工批准。", action: [{ type: "send_coupon", code: COUPON }] };
  }
  if (futureTest || couponAcknowledged) {
    return { kind: "test_planned", stage: "testing", template: "test_planned_ack", risk: "medium", reason: couponAcknowledged ? "对方确认已收到测试码并准备测试，尚未声称完成测试，也没有提供报价。" : "对方只表达将来准备测试，尚未声称完成测试，也没有提供报价。", action: [{ type: "wait_for_completed_test" }] };
  }
  if (/error|bug|not working|stuck|upload|processing|privacy|terms|data|footage|how does|does videocoding/.test(text)) {
    return { kind: "product_question", stage: "product_question", template: "product_question_hold", risk: "high", reason: "产品、故障或隐私问题需要确认事实后回复。", action: [{ type: "human_product_answer" }] };
  }
  if (/try|test|access|code|coupon|one month|free month|signed up|created an account/.test(text)) {
    return { kind: "coupon_reply", stage: "coupon_ready", template: "coupon_test", risk: "medium", reason: "对方表达测试意愿；使用人工模板并等待批准。", action: [{ type: "send_coupon", code: COUPON }] };
  }
  return { kind: "human_review", stage: "replied", template: null, risk: "high", reason: "没有匹配已批准模板，需要人工阅读完整上下文。", action: [{ type: "human_review" }] };
}

function renderTemplate(template: TemplateRow, row: InboxRow, registeredEmail?: string | null) {
  const values: Record<string, string> = {
    original_subject: row.subject.replace(/^((re|fw|fwd):\s*)+/i, ""),
    coupon_code: COUPON,
    registered_email: registeredEmail || "the email you registered with",
    channel_name: row.channel_name || "",
  };
  const replace = (value: string) => value.replace(/{{([a-z_]+)}}/gi, (_, key: string) => values[key] ?? "");
  return { subject: replace(template.subject), body: replace(template.body) };
}

function replySubject(value: unknown) {
  const normalized = String(value || "").trim();
  const original = normalized.replace(/^((re|fw|fwd):\s*)+/i, "").trim() || "VideoCoding";
  return `Re: ${original}`;
}

function escapeHtml(value: string) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function linkifyHtml(value: string) {
  return value.replace(/https:\/\/[^\s<]+/g, (url) => `<a href="${url}" style="color:#175cd3">${url}</a>`);
}

function couponBodyToHtml(body: string) {
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const blocks: string[] = [];
  let listItems: string[] = [];
  const flushList = () => {
    if (!listItems.length) return;
    blocks.push(`<ol style="margin:8px 0 18px;padding-left:24px">${listItems.join("")}</ol>`);
    listItems = [];
  };
  for (const rawLine of lines) {
    const line = rawLine.trim();
    const numbered = line.match(/^\d+\.\s+(.+)$/);
    if (numbered) {
      listItems.push(`<li style="margin:0 0 9px"><strong>${linkifyHtml(escapeHtml(numbered[1]))}</strong></li>`);
      continue;
    }
    flushList();
    if (!line) continue;
    const content = linkifyHtml(escapeHtml(line));
    const isHeading = /^(?:Promo Code:|How to activate:|Recommended Test:)/i.test(line);
    blocks.push(`<p style="margin:0 0 14px;line-height:1.55">${isHeading ? `<strong>${content}</strong>` : content}</p>`);
  }
  flushList();
  return `<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#17212b">${blocks.join("")}</div>`;
}

async function seedReviewQueue(reconcile = true) {
  const db = DB();
  if (reconcile) await reconcileReplyQueue();
  const rows = await db.prepare(`SELECT e.id,e.contact_id,e.event_type,e.subject,e.body_preview,e.body_text,
    e.sender_email,c.channel_name,c.email AS contact_email,c.reply_status,c.initial_template_key,cr.status AS coupon_status,
    p.usage_verification_status
    FROM inbox_events e
    LEFT JOIN contacts c ON c.id=e.contact_id
    LEFT JOIN coupon_reply_log cr ON cr.inbox_event_id=e.id
    LEFT JOIN creator_profiles p ON p.contact_id=e.contact_id
    LEFT JOIN outreach_work_items existing ON existing.inbox_event_id=e.id
    WHERE existing.id IS NULL
      AND NOT EXISTS(
        SELECT 1 FROM inbox_events newer
        WHERE newer.conversation_id=e.conversation_id
          AND coalesce(e.conversation_id,'')<>''
          AND (datetime(newer.received_at)>datetime(e.received_at)
            OR (datetime(newer.received_at)=datetime(e.received_at) AND newer.id>e.id))
      )
      AND NOT EXISTS(
        SELECT 1 FROM outreach_sent_messages outbound
        WHERE outbound.conversation_id=e.conversation_id
          AND coalesce(e.conversation_id,'')<>''
          AND datetime(outbound.sent_at)>datetime(e.received_at)
      )
    ORDER BY e.received_at ASC LIMIT 50`).all<InboxRow>();
  const templates = await db.prepare("SELECT template_key,subject,body,risk_level FROM outreach_templates WHERE enabled=1")
    .all<TemplateRow>();
  const byKey = new Map<string, TemplateRow>(templates.results.map((template: TemplateRow) => [template.template_key, template]));
  let created = 0;
  for (const row of rows.results) {
    if (row.contact_id) {
      await db.prepare(`INSERT OR IGNORE INTO creator_profiles(contact_id,workflow_stage,updated_at)
        VALUES(?,?,datetime('now'))`).bind(row.contact_id, row.reply_status === "interested" ? "replied" : "contacted").run();
    }
    let decision = classify(row);
    if (decision.template === "coupon_test" && row.initial_template_key === "first_contact_t2v_knowledge_v1") {
      decision = {
        ...decision,
        template: "coupon_test_t2v_knowledge_v1",
        reason: `${decision.reason} T2V 知识类首封已匹配，使用短 prompt 生成最长 10 分钟视频的 Coupon 说明。`,
      };
    }
    if (decision.kind === "price_and_test_claim" && row.usage_verification_status === "completed") {
      decision = {
        kind: "price_received", stage: "price_received", template: "rate_received", risk: "high",
        reason: "报价已经收到，且产品后台已确认真实完成测试；进入报价记录与营销评估。",
        action: [{ type: "extract_price" }, { type: "marketing_review" }],
      };
    }
    if (row.coupon_status === "sent") {
      decision = {
        kind: "coupon_reply", stage: "testing", template: "coupon_test", risk: "medium",
        reason: "Coupon 已实际发送；等待对方完成真实视频测试。", action: [{ type: "coupon_already_sent" }],
      };
    }
    if (decision.kind === "opt_out" && row.contact_id) {
      await db.prepare(`UPDATE contacts SET do_not_contact=1,outreach_status='opted_out',reply_status='not_interested',
        next_action='对方已拒绝或退订；停止联系。',updated_at=datetime('now') WHERE id=?`).bind(row.contact_id).run();
    }
    const template = decision.template ? byKey.get(decision.template) : null;
    const rendered = template ? renderTemplate(template, row, "registeredEmail" in decision ? decision.registeredEmail : null) : { subject: "", body: "" };
    const result = await db.prepare(`INSERT OR IGNORE INTO outreach_work_items(
      inbox_event_id,contact_id,kind,workflow_stage,status,risk_level,confidence,template_key,
      subject,draft_body,extracted_json,proposed_action_json,decision_reason,created_at,updated_at
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))`)
      .bind(
        row.id,
        row.contact_id,
        decision.kind,
        decision.stage,
        decision.kind === "ignore" || decision.kind === "opt_out" ? "ignored" : row.coupon_status === "sent" ? "sent" : "pending",
        decision.risk,
        null,
        decision.template,
        rendered.subject,
        rendered.body,
        JSON.stringify({ registered_email: "registeredEmail" in decision ? decision.registeredEmail : null }),
        JSON.stringify(decision.action),
        decision.reason,
      ).run();
    if (row.coupon_status === "sent") {
      await db.prepare(`UPDATE outreach_work_items SET kind=?,workflow_stage=?,risk_level=?,template_key=?,
        subject=?,draft_body=?,decision_reason=?,proposed_action_json=?,updated_at=datetime('now')
        WHERE inbox_event_id=? AND status='sent' AND kind<>'coupon_reply'`)
        .bind(decision.kind, decision.stage, decision.risk, decision.template, rendered.subject, rendered.body,
          decision.reason, JSON.stringify(decision.action), row.id).run();
    } else {
      await db.prepare(`UPDATE outreach_work_items SET kind=?,workflow_stage=?,risk_level=?,template_key=?,
        subject=?,draft_body=?,decision_reason=?,proposed_action_json=?,updated_at=datetime('now')
        WHERE inbox_event_id=? AND status='pending' AND resolved_by IS NULL
          AND kind<>? AND template_key IN ('rate_received','test_claim_checking','coupon_test','coupon_test_t2v_knowledge_v1','test_planned_ack')`)
        .bind(decision.kind, decision.stage, decision.risk, decision.template, rendered.subject, rendered.body,
          decision.reason, JSON.stringify(decision.action), row.id, decision.kind).run();
    }
    if ((decision.kind === "test_claim" || decision.kind === "price_and_test_claim") && row.contact_id) {
      await db.prepare(`UPDATE creator_profiles SET workflow_stage='test_claimed',test_status='claimed',updated_at=datetime('now')
        WHERE contact_id=? AND usage_verification_status IN ('not_checked','account_not_found')`).bind(row.contact_id).run();
      await db.prepare(`UPDATE outreach_work_items SET kind='test_claim',workflow_stage='test_claimed',risk_level='high',
        template_key='test_claim_checking',subject=?,draft_body=?,decision_reason=?,proposed_action_json=?,updated_at=datetime('now')
        WHERE inbox_event_id=? AND status='pending' AND kind='test_feedback'`)
        .bind(rendered.subject, rendered.body, decision.reason, JSON.stringify(decision.action), row.id).run();
    }
    if (decision.kind === "test_planned" && row.contact_id) {
      await db.prepare(`UPDATE creator_profiles SET workflow_stage='testing',test_status='wants_test',updated_at=datetime('now')
        WHERE contact_id=? AND usage_verification_status<>'completed'`).bind(row.contact_id).run();
    }
    if (Number(result.meta?.changes ?? 0) > 0) created += 1;
  }
  return created;
}

const canonicalReplyQueueCte = `WITH ranked_inbound AS (
    SELECT e.id,e.conversation_id,e.received_at,
      ROW_NUMBER() OVER(
        PARTITION BY coalesce(nullif(e.conversation_id,''),'message:' || e.id)
        ORDER BY datetime(e.received_at) DESC,e.id DESC
      ) AS conversation_rank
    FROM inbox_events e
  ), conversation_outbound AS (
    SELECT conversation_id,MAX(datetime(sent_at)) AS last_sent_at
    FROM outreach_sent_messages WHERE coalesce(conversation_id,'')<>'' GROUP BY conversation_id
  ), actionable_work_items AS (
    SELECT w.id,w.status,w.risk_level,w.kind,w.template_key,w.confidence,w.decision_reason,w.extracted_json,
      e.received_at,e.conversation_id,
      coalesce(cast(w.contact_id AS TEXT),'email:' || lower(e.sender_email)) AS creator_key
    FROM outreach_work_items w
    JOIN inbox_events e ON e.id=w.inbox_event_id
    JOIN ranked_inbound ranked ON ranked.id=e.id AND ranked.conversation_rank=1
    LEFT JOIN contacts c ON c.id=w.contact_id
    LEFT JOIN conversation_outbound outbound ON outbound.conversation_id=e.conversation_id
    WHERE w.status IN ('pending','approved')
      AND e.event_type IN ('interested','reply')
      AND coalesce(c.do_not_contact,0)=0
      AND (outbound.last_sent_at IS NULL OR outbound.last_sent_at<=datetime(e.received_at))
  )`;

const conversationCenterCte = `${canonicalReplyQueueCte}, actionable_conversations AS (
    SELECT coalesce(nullif(e.conversation_id,''),'message:' || e.id) AS conversation_key,
      w.id AS work_item_id,w.status AS work_status,w.kind,w.workflow_stage,w.risk_level,
      w.template_key,w.subject AS draft_subject,w.draft_body,w.decision_reason
    FROM actionable_work_items actionable
    JOIN outreach_work_items w ON w.id=actionable.id
    JOIN inbox_events e ON e.id=w.inbox_event_id
  ), all_conversation_messages AS (
    SELECT coalesce(nullif(e.conversation_id,''),'message:' || e.id) AS conversation_key,
      e.contact_id,e.received_at AS message_at,'inbound' AS direction,e.id AS message_id,
      e.subject,coalesce(e.body_text,e.body_preview,'') AS body_text,e.event_type,
      e.sender_name,e.sender_email AS peer_email,e.web_link
    FROM inbox_events e
    UNION ALL
    SELECT coalesce(nullif(s.conversation_id,''),'sent:' || s.id) AS conversation_key,
      s.contact_id,s.sent_at AS message_at,'outbound' AS direction,s.id AS message_id,
      s.subject,coalesce(s.body_text,'') AS body_text,'sent' AS event_type,
      'VideoCoding' AS sender_name,s.recipient_email AS peer_email,NULL AS web_link
    FROM outreach_sent_messages s
  ), ranked_conversation_messages AS (
    SELECT message.*,
      ROW_NUMBER() OVER(PARTITION BY conversation_key ORDER BY datetime(message_at) DESC,message_id DESC) AS message_rank,
      COUNT(*) OVER(PARTITION BY conversation_key) AS message_count
    FROM all_conversation_messages message
  ), conversation_rows AS (
    SELECT latest.conversation_key,latest.contact_id,latest.message_at,latest.direction,latest.message_id,
      latest.subject,latest.body_text,latest.event_type,latest.sender_name,latest.peer_email,latest.web_link,
      latest.message_count,c.channel_name,c.email AS contact_email,c.channel_url,coalesce(c.do_not_contact,0) AS do_not_contact,
      actionable.work_item_id,actionable.work_status,actionable.kind,actionable.workflow_stage,actionable.risk_level,
      actionable.template_key,actionable.draft_subject,actionable.draft_body,actionable.decision_reason,
      CASE
        WHEN coalesce(c.do_not_contact,0)=1 OR latest.event_type IN ('opted_out','bounce') THEN 'closed'
        WHEN actionable.work_item_id IS NOT NULL THEN 'needs_reply'
        WHEN latest.direction='outbound' THEN 'waiting_creator'
        ELSE 'reviewed'
      END AS conversation_status
    FROM ranked_conversation_messages latest
    LEFT JOIN contacts c ON c.id=latest.contact_id
    LEFT JOIN actionable_conversations actionable ON actionable.conversation_key=latest.conversation_key
    WHERE latest.message_rank=1
  )`;

async function conversationList(request: NextRequest) {
  const db = DB();
  const page = Math.max(1, Number(request.nextUrl.searchParams.get("page") || "1") || 1);
  const limit = Math.min(100, Math.max(20, Number(request.nextUrl.searchParams.get("limit") || "30") || 30));
  const query = String(request.nextUrl.searchParams.get("q") || "").trim().toLowerCase().slice(0, 200);
  const requestedStatus = String(request.nextUrl.searchParams.get("status") || "all");
  const status = ["all", "needs_reply", "waiting_creator", "reviewed", "closed"].includes(requestedStatus)
    ? requestedStatus
    : "all";
  const filterSql = `WHERE (?='' OR instr(lower(coalesce(channel_name,'') || ' ' || coalesce(contact_email,'') || ' ' ||
      coalesce(peer_email,'') || ' ' || coalesce(subject,'') || ' ' || coalesce(body_text,'')),?)>0)
    AND (?='all' OR conversation_status=?)`;
  const bindings = [query, query, status, status];
  const [rows, filteredCount, statusCounts] = await Promise.all([
    db.prepare(`${conversationCenterCte} SELECT * FROM conversation_rows ${filterSql}
      ORDER BY CASE conversation_status WHEN 'needs_reply' THEN 0 WHEN 'waiting_creator' THEN 1 WHEN 'reviewed' THEN 2 ELSE 3 END,
        datetime(message_at) DESC,conversation_key DESC LIMIT ? OFFSET ?`)
      .bind(...bindings, limit, (page - 1) * limit).all<Record<string, unknown>>(),
    db.prepare(`${conversationCenterCte} SELECT COUNT(*) AS count FROM conversation_rows ${filterSql}`)
      .bind(...bindings).first<{ count: number }>(),
    db.prepare(`${conversationCenterCte} SELECT conversation_status,COUNT(*) AS count
      FROM conversation_rows GROUP BY conversation_status`).all<{ conversation_status: string; count: number }>(),
  ]);
  const summary = Object.fromEntries(statusCounts.results.map((row) => [row.conversation_status, Number(row.count || 0)]));
  const total = Number(filteredCount?.count ?? 0);
  return {
    rows: rows.results,
    total,
    page,
    limit,
    pages: Math.max(1, Math.ceil(total / limit)),
    summary: {
      total: Object.values(summary).reduce((sum, value) => sum + value, 0),
      needs_reply: Number(summary.needs_reply || 0),
      waiting_creator: Number(summary.waiting_creator || 0),
      reviewed: Number(summary.reviewed || 0),
      closed: Number(summary.closed || 0),
    },
  };
}

async function conversationDetail(conversationKey: string) {
  if (!conversationKey || conversationKey.length > 800) throw new Error("无效会话标识");
  const db = DB();
  const [conversation, messages] = await Promise.all([
    db.prepare(`${conversationCenterCte} SELECT * FROM conversation_rows WHERE conversation_key=? LIMIT 1`)
      .bind(conversationKey).first<Record<string, unknown>>(),
    db.prepare(`WITH all_messages AS (
      SELECT coalesce(nullif(e.conversation_id,''),'message:' || e.id) AS conversation_key,
        e.id AS message_id,e.received_at AS message_at,'inbound' AS direction,e.subject,
        coalesce(e.body_text,e.body_preview,'') AS body_text,e.sender_name,e.sender_email AS sender_email,
        'team@videocoding.ai' AS recipient_email,e.event_type,e.web_link
      FROM inbox_events e
      UNION ALL
      SELECT coalesce(nullif(s.conversation_id,''),'sent:' || s.id) AS conversation_key,
        s.id AS message_id,s.sent_at AS message_at,'outbound' AS direction,s.subject,
        coalesce(s.body_text,'') AS body_text,'VideoCoding' AS sender_name,
        'team@videocoding.ai' AS sender_email,s.recipient_email,'sent' AS event_type,NULL AS web_link
      FROM outreach_sent_messages s
    ) SELECT * FROM all_messages WHERE conversation_key=?
      ORDER BY datetime(message_at) ASC,message_id ASC`).bind(conversationKey).all<Record<string, unknown>>(),
  ]);
  if (!conversation) return null;
  return {
    conversation,
    messages: messages.results,
    can_reply: conversation.conversation_status === "needs_reply" && Boolean(conversation.work_item_id),
    reply: conversation.work_item_id ? {
      work_item_id: Number(conversation.work_item_id),
      status: conversation.work_status,
      kind: conversation.kind,
      workflow_stage: conversation.workflow_stage,
      risk_level: conversation.risk_level,
      template_key: conversation.template_key,
      subject: replySubject(conversation.draft_subject || conversation.subject),
      body: conversation.draft_body || "",
      decision_reason: conversation.decision_reason,
    } : null,
  };
}

async function reconcileReplyQueue() {
  const db = DB();
  const sent = await db.prepare(`UPDATE outreach_work_items SET status='sent',
      resolved_at=coalesce(resolved_at,datetime('now')),resolved_by='operations_queue_reconciler',
      decision_reason='已同步到该会话中更晚的出站邮件；无需再次回复。',updated_at=datetime('now')
    WHERE status IN ('pending','approved') AND EXISTS(
      SELECT 1 FROM inbox_events current JOIN outreach_sent_messages outbound
        ON outbound.conversation_id=current.conversation_id
      WHERE current.id=outreach_work_items.inbox_event_id
        AND coalesce(current.conversation_id,'')<>''
        AND datetime(outbound.sent_at)>datetime(current.received_at)
    )`).run();
  const superseded = await db.prepare(`UPDATE outreach_work_items SET status='superseded',
      resolved_at=coalesce(resolved_at,datetime('now')),resolved_by='operations_queue_reconciler',
      decision_reason='同一会话已有更新的来信；旧草稿已失效。',updated_at=datetime('now')
    WHERE status IN ('pending','approved') AND EXISTS(
      SELECT 1 FROM inbox_events current JOIN inbox_events newer
        ON newer.conversation_id=current.conversation_id
      WHERE current.id=outreach_work_items.inbox_event_id
        AND coalesce(current.conversation_id,'')<>''
        AND (datetime(newer.received_at)>datetime(current.received_at)
          OR (datetime(newer.received_at)=datetime(current.received_at) AND newer.id>current.id))
    )`).run();
  const ignored = await db.prepare(`UPDATE outreach_work_items SET status='ignored',
      resolved_at=coalesce(resolved_at,datetime('now')),resolved_by='operations_queue_reconciler',
      decision_reason='联系人已拒绝、退订或被抑制；禁止继续回复。',updated_at=datetime('now')
    WHERE status IN ('pending','approved') AND EXISTS(
      SELECT 1 FROM contacts c WHERE c.id=outreach_work_items.contact_id AND coalesce(c.do_not_contact,0)=1
    )`).run();
  return {
    sent_resolved: Number(sent.meta?.changes ?? 0),
    older_superseded: Number(superseded.meta?.changes ?? 0),
    dnc_ignored: Number(ignored.meta?.changes ?? 0),
  };
}

async function dashboardData() {
  const db = DB();
  const [
    counts, usageCounts, workItems, templates, rules, sources, stages, audit, couponMode, replyAIProvider, productDataAgent, dataAgentRun,
    followupCounts, promotionCreators, efficiencyCounts, guardrailCounts, efficiencyPilotSetting,
    discoveryWorkers, discoverySummary, discoveryEmailSummary, contactSummary, discoveryTarget, discoveryDailyTarget,
    discoveryTiers, discoveryJobs, discoveryChannels, discoveryLearning, discoveryDecisions,
  ] = await Promise.all([
    db.prepare(`${canonicalReplyQueueCte} SELECT
      SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
      COUNT(*) AS actionable,
      COUNT(DISTINCT creator_key) AS actionable_creators,
      (SELECT COUNT(*) FROM outreach_work_items WHERE status='pending') AS raw_pending,
      SUM(CASE WHEN risk_level='high' THEN 1 ELSE 0 END) AS high_risk,
      SUM(CASE WHEN kind='membership_request' THEN 1 ELSE 0 END) AS membership_requests,
      SUM(CASE WHEN kind IN ('price_received','price_and_test_claim') THEN 1 ELSE 0 END) AS price_reviews,
      SUM(CASE WHEN template_key IS NOT NULL AND kind<>'human_review' THEN 1 ELSE 0 END) AS template_matched,
      SUM(CASE WHEN kind='human_review' THEN 1 ELSE 0 END) AS human_only,
      SUM(CASE WHEN confidence IS NOT NULL THEN 1 ELSE 0 END) AS ai_routed,
      SUM(CASE WHEN json_valid(extracted_json) AND json_extract(extracted_json,'$.context_audit.version')='${REPLY_CONTEXT_VERSION}' THEN 1 ELSE 0 END) AS context_reviewed,
      SUM(CASE WHEN confidence IS NULL THEN 1 ELSE 0 END) AS rules_only,
      (SELECT COUNT(*) FROM outreach_work_items WHERE status='sent') AS sent,
      (SELECT COUNT(*) FROM outreach_work_items WHERE status='ignored') AS ignored
      FROM actionable_work_items`).first<Record<string, number>>(),
    db.prepare(`SELECT
      SUM(CASE WHEN usage_verification_status='not_checked' AND test_status='claimed' THEN 1 ELSE 0 END) AS claimed_unverified,
      SUM(CASE WHEN usage_verification_status='account_only' THEN 1 ELSE 0 END) AS account_only,
      SUM(CASE WHEN usage_verification_status='started' THEN 1 ELSE 0 END) AS started_incomplete,
      SUM(CASE WHEN usage_verification_status='completed' THEN 1 ELSE 0 END) AS real_testers
      FROM creator_profiles`).first<Record<string, number>>(),
    db.prepare(`${canonicalReplyQueueCte} SELECT w.*,c.channel_name,c.email,c.channel_url,e.sender_email,e.received_at,e.body_preview,e.body_text,
      p.registered_email,p.account_status,p.membership_status,p.test_status,p.marketing_status,
      p.product_user_id,p.usage_verification_status,p.usage_total_tasks,p.usage_active_tasks,p.usage_completed_tasks,
      p.usage_last_login_at,p.usage_last_task_at,p.usage_verified_at,p.usage_source,
      p.coupon_redemption_status,p.coupon_redeemed_code,p.coupon_redemption_verified_at,p.product_sync_error,
      CASE
        WHEN w.status NOT IN ('pending','approved') THEN 99
        WHEN w.kind IN ('price_received','price_and_test_claim','test_verified') OR w.workflow_stage='marketing_review' THEN 1
        WHEN w.template_key IS NOT NULL AND w.kind<>'human_review' THEN 2
        WHEN julianday('now')-julianday(e.received_at)>=3 THEN 3
        ELSE 4
      END AS efficiency_priority
      FROM outreach_work_items w
      LEFT JOIN contacts c ON c.id=w.contact_id
      LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
      LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
      WHERE w.id IN (SELECT id FROM actionable_work_items) OR w.id IN (
        SELECT recent.id FROM outreach_work_items recent
        WHERE recent.status NOT IN ('pending','approved') ORDER BY recent.updated_at DESC LIMIT 200
      )
      ORDER BY CASE w.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
        efficiency_priority ASC,datetime(e.received_at) ASC,w.created_at ASC
      LIMIT 5000`).all<Record<string, unknown>>(),
    db.prepare("SELECT * FROM outreach_templates ORDER BY stage,template_key").all<Record<string, unknown>>(),
    db.prepare("SELECT * FROM automation_rules ORDER BY CASE mode WHEN 'manual' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,rule_key").all<Record<string, unknown>>(),
    db.prepare("SELECT * FROM lead_sources ORDER BY id").all<Record<string, unknown>>(),
    db.prepare(`SELECT workflow_stage,COUNT(*) AS count FROM creator_profiles GROUP BY workflow_stage ORDER BY count DESC`).all<Record<string, unknown>>(),
    db.prepare("SELECT * FROM outreach_audit_log ORDER BY created_at DESC LIMIT 30").all<Record<string, unknown>>(),
    db.prepare("SELECT value FROM outreach_settings WHERE key='coupon_automation_mode'").first<{ value: string }>(),
    db.prepare("SELECT value FROM outreach_settings WHERE key='reply_ai_provider'").first<{ value: string }>(),
    db.prepare("SELECT value FROM outreach_settings WHERE key='product_data_agent_configured'").first<{ value: string }>(),
    db.prepare("SELECT * FROM creator_data_agent_runs ORDER BY id DESC LIMIT 1").first<Record<string, unknown>>(),
    db.prepare(`SELECT
      SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
      SUM(CASE WHEN status='pending' AND datetime(due_at)<=datetime('now') THEN 1 ELSE 0 END) AS due,
      SUM(CASE WHEN status='sent' AND date(sent_at)=date('now') THEN 1 ELSE 0 END) AS sent_today,
      SUM(CASE WHEN status='manual_required' THEN 1 ELSE 0 END) AS manual_required
      FROM creator_followups`).first<Record<string, number>>(),
    db.prepare(`SELECT * FROM promotion_creator_list
      ORDER BY CASE list_status WHEN 'waiting' THEN 0 WHEN 'approved' THEN 1 WHEN 'launched' THEN 2 ELSE 3 END,
      updated_at DESC LIMIT 1000`).all<Record<string, unknown>>(),
    db.prepare(`${canonicalReplyQueueCte}, inbound_first AS (
        SELECT conversation_id,MIN(datetime(received_at)) AS first_inbound_at
        FROM inbox_events
        WHERE event_type IN ('interested','reply') AND coalesce(conversation_id,'')<>''
        GROUP BY conversation_id
      ) SELECT
      (SELECT COUNT(DISTINCT coalesce(nullif(conversation_id,''),id)) FROM inbox_events
        WHERE event_type IN ('interested','reply') AND datetime(received_at)>=datetime('now','-24 hours')) AS inbound_24h,
      (SELECT COUNT(DISTINCT sent.conversation_id) FROM outreach_sent_messages sent
        JOIN inbound_first first_inbound ON first_inbound.conversation_id=sent.conversation_id
        WHERE datetime(sent.sent_at)>first_inbound.first_inbound_at
          AND datetime(sent.sent_at)>=datetime('now','-24 hours')) AS resolved_24h,
      SUM(CASE WHEN julianday('now')-julianday(received_at)>=1 THEN 1 ELSE 0 END) AS over_24h,
      SUM(CASE WHEN julianday('now')-julianday(received_at)>=3 THEN 1 ELSE 0 END) AS over_72h,
      SUM(CASE WHEN template_key IS NOT NULL AND kind<>'human_review' THEN 1 ELSE 0 END) AS fast_lane,
      SUM(CASE WHEN kind='human_review' AND instr(decision_reason,'对方同时询问预算、brief、交付、时间线或合作细节')=1 THEN 1 ELSE 0 END) AS blocked_campaign_policy,
      SUM(CASE WHEN kind='human_review' AND instr(decision_reason,'对方报告产品问题或负面测试反馈')=1 THEN 1 ELSE 0 END) AS blocked_product_facts,
      SUM(CASE WHEN kind='human_review' AND instr(decision_reason,'对方提出付款、合同、订阅或外部流程条件')=1 THEN 1 ELSE 0 END) AS blocked_commercial_terms
      FROM actionable_work_items`).first<Record<string, number>>(),
    db.prepare(`SELECT
      (SELECT COUNT(*) FROM outreach_work_items WHERE status='sending') +
      (SELECT COUNT(*) FROM outreach_send_log WHERE status='sending') +
      (SELECT COUNT(*) FROM creator_followups WHERE status='sending') AS stuck_sending,
      (SELECT COUNT(*) FROM outreach_send_log WHERE status='delivery_unknown') AS delivery_unknown,
      (SELECT COUNT(*) FROM outreach_send_log WHERE status='failed' AND datetime(coalesce(sent_at,queued_at))>=datetime('now','-24 hours')) +
      (SELECT COUNT(*) FROM creator_followups WHERE status='failed' AND datetime(updated_at)>=datetime('now','-24 hours')) AS send_failures_24h,
      (SELECT coalesce(SUM(repeated.n-1),0) FROM (
        SELECT entity_id,COUNT(*) AS n FROM outreach_audit_log
        WHERE action='verified_test_routed' AND datetime(created_at)>=datetime('now','-15 minutes')
        GROUP BY entity_id HAVING COUNT(*)>1
      ) repeated) AS state_repeats_15m`).first<Record<string, number>>(),
    db.prepare("SELECT value FROM outreach_settings WHERE key='efficiency_2x_pilot_v1'").first<{ value: string }>(),
    db.prepare("SELECT * FROM lead_discovery_state ORDER BY worker_key").all<Record<string, unknown>>(),
    db.prepare(`SELECT
      COUNT(*) AS total_jobs,
      SUM(CASE WHEN last_completed_at IS NOT NULL THEN 1 ELSE 0 END) AS initial_pass_complete,
      SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued_jobs,
      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_jobs,
      SUM(videos_scanned) AS videos_scanned,
      SUM(channels_found) AS channels_found,
      SUM(public_emails_found) AS public_emails_found,
      SUM(contacts_imported) AS contacts_imported
      FROM lead_discovery_jobs WHERE status<>'retired'`).first<Record<string, number>>(),
    db.prepare(`SELECT
      COUNT(*) AS unique_public_emails,
      SUM(CASE WHEN was_new_contact=1 THEN 1 ELSE 0 END) AS new_unique_contacts,
      SUM(CASE WHEN was_new_contact=1 AND julianday(first_seen_at)>=julianday('now','-24 hours') THEN 1 ELSE 0 END) AS new_last_24h,
      SUM(CASE WHEN was_new_contact=1 AND julianday(first_seen_at)>=julianday('now','-6 hours') THEN 1 ELSE 0 END) AS new_last_6h,
      SUM(CASE WHEN was_new_contact=1 AND contact_id IN (
        SELECT id FROM contacts WHERE validation_status='send_ready' AND COALESCE(subscribers,0)>=1000
      ) THEN 1 ELSE 0 END) AS qualified_unique_contacts,
      SUM(CASE WHEN was_new_contact=1 AND julianday(first_seen_at)>=julianday('now','-24 hours') AND contact_id IN (
        SELECT id FROM contacts WHERE validation_status='send_ready' AND COALESCE(subscribers,0)>=1000
      ) THEN 1 ELSE 0 END) AS qualified_last_24h,
      SUM(CASE WHEN was_new_contact=1 AND julianday(first_seen_at)>=julianday('now','-6 hours') AND contact_id IN (
        SELECT id FROM contacts WHERE validation_status='send_ready' AND COALESCE(subscribers,0)>=1000
      ) THEN 1 ELSE 0 END) AS qualified_last_6h,
      SUM(CASE WHEN was_new_contact=0 AND contact_id IS NOT NULL THEN 1 ELSE 0 END) AS existing_duplicates,
      SUM(CASE WHEN contact_id IS NULL THEN 1 ELSE 0 END) AS pending_validation
      FROM lead_discovery_emails`).first<Record<string, number>>(),
    db.prepare("SELECT COUNT(*) AS master_contacts FROM contacts").first<Record<string, number>>(),
    db.prepare("SELECT value FROM outreach_settings WHERE key='lead_discovery_target_new_contacts'").first<{ value: string }>(),
    db.prepare("SELECT value FROM outreach_settings WHERE key='lead_discovery_daily_target_new_contacts'").first<{ value: string }>(),
    db.prepare(`SELECT tier,
      COUNT(*) AS total_jobs,
      SUM(CASE WHEN last_completed_at IS NOT NULL THEN 1 ELSE 0 END) AS initial_pass_complete,
      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_jobs,
      SUM(videos_scanned) AS videos_scanned,
      SUM(channels_found) AS channels_found,
      SUM(public_emails_found) AS public_emails_found,
      SUM(contacts_imported) AS contacts_imported
      FROM lead_discovery_jobs WHERE status<>'retired' GROUP BY tier ORDER BY tier`).all<Record<string, unknown>>(),
    db.prepare(`SELECT id,tier,tier_key,query,language,status,run_count,videos_scanned,channels_found,
      public_emails_found,contacts_imported,last_completed_at,next_run_at,last_error
      FROM lead_discovery_jobs
      WHERE status<>'retired'
      ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'error' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
        tier ASC,priority DESC,last_completed_at DESC LIMIT 18`).all<Record<string, unknown>>(),
    db.prepare(`SELECT channel_key,channel_name,channel_url,best_tier,matched_query,matched_video_title,
      matched_video_url,language,scan_status,email_count,last_seen_at
      FROM lead_discovery_channels ORDER BY last_seen_at DESC LIMIT 12`).all<Record<string, unknown>>(),
    db.prepare(`SELECT count(*) AS measured_runs,coalesce(sum(eligible_contacts),0) AS eligible_contacts,
      coalesce(sum(channels_found),0) AS channels,coalesce(sum(duplicate_channels),0) AS duplicates,
      coalesce(sum(active_channels),0) AS active_channels,coalesce(sum(inactive_channels),0) AS inactive_channels,
      coalesce(sum(unknown_activity),0) AS unknown_activity
      FROM lead_discovery_runs WHERE metrics_version=1 AND status='completed'
      AND started_at>=strftime('%Y-%m-%dT%H:%M:%SZ','now','-24 hours')`).first<Record<string, number>>(),
    db.prepare(`SELECT r.worker_key,j.query,r.selection_mode,r.selection_reason,r.started_at,r.status
      FROM lead_discovery_runs r JOIN lead_discovery_jobs j ON j.id=r.job_id
      WHERE r.id IN (SELECT max(id) FROM lead_discovery_runs WHERE worker_key LIKE 'collector-%' GROUP BY worker_key)
      AND r.selection_mode IS NOT NULL ORDER BY r.worker_key`).all<Record<string, unknown>>(),
  ]);
  const bindings = env as unknown as Bindings;
  const workers = discoveryWorkers.results;
  const onlineWorkers = workers.filter((worker) => {
    const heartbeat = worker.heartbeat_at ? new Date(String(worker.heartbeat_at)).getTime() : 0;
    return Number(worker.enabled ?? 0) === 1 && !["stopped", "paused"].includes(String(worker.status))
      && heartbeat > 0 && Date.now() - heartbeat < 20 * 60 * 1000;
  });
  const discoveryState = onlineWorkers[0] ?? workers.find((worker) => worker.worker_key === "youtube_public_discovery") ?? workers[0];
  const workerOnline = onlineWorkers.length > 0;
  const targetNewContacts = Math.max(1, Number(discoveryTarget?.value ?? 1000000));
  const dailyTarget = Math.max(1, Number(discoveryDailyTarget?.value ?? 3000));
  const rawNewUniqueContacts = Number(discoveryEmailSummary?.new_unique_contacts ?? 0);
  const rawNewLast24h = Number(discoveryEmailSummary?.new_last_24h ?? 0);
  const newUniqueContacts = Number(discoveryEmailSummary?.qualified_unique_contacts ?? 0);
  const newLast24h = Number(discoveryEmailSummary?.qualified_last_24h ?? 0);
  const newLast6h = Number(discoveryEmailSummary?.qualified_last_6h ?? 0);
  const actionable = Number(counts?.actionable ?? 0);
  const fastLane = Number(efficiencyCounts?.fast_lane ?? 0);
  const fastLanePct = actionable > 0 ? Math.round((fastLane / actionable) * 1000) / 10 : 0;
  type EfficiencyPilotBaseline = {
    started_at: string;
    actionable: number;
    resolved_24h: number;
    fast_lane_pct: number;
    over_72h: number;
  };
  let efficiencyPilot: EfficiencyPilotBaseline | null = null;
  try {
    const parsed = JSON.parse(efficiencyPilotSetting?.value || "null") as Partial<EfficiencyPilotBaseline> | null;
    if (parsed && typeof parsed.started_at === "string") {
      efficiencyPilot = {
        started_at: parsed.started_at,
        actionable: Number(parsed.actionable ?? actionable),
        resolved_24h: Number(parsed.resolved_24h ?? efficiencyCounts?.resolved_24h ?? 0),
        fast_lane_pct: Number(parsed.fast_lane_pct ?? fastLanePct),
        over_72h: Number(parsed.over_72h ?? efficiencyCounts?.over_72h ?? 0),
      };
    }
  } catch {
    efficiencyPilot = null;
  }
  if (!efficiencyPilot) {
    efficiencyPilot = {
      started_at: new Date().toISOString(),
      actionable,
      resolved_24h: Number(efficiencyCounts?.resolved_24h ?? 0),
      fast_lane_pct: fastLanePct,
      over_72h: Number(efficiencyCounts?.over_72h ?? 0),
    };
    await db.prepare(`INSERT INTO outreach_settings(key,value,updated_at)
      VALUES('efficiency_2x_pilot_v1',?,datetime('now')) ON CONFLICT(key) DO NOTHING`)
      .bind(JSON.stringify(efficiencyPilot)).run();
  }
  const promotionQuoteReady = promotionCreators.results.filter((row) =>
    Boolean(String(row.quote_source_text || row.rate_info || "").trim()) ||
    row.price_dedicated !== null || row.price_integration !== null || row.price_shorts !== null
  ).length;
  const promotionApproved = promotionCreators.results.filter((row) => row.list_status === "approved" || row.list_status === "launched").length;
  return {
    summary: {
      pending: Number(counts?.pending ?? 0),
      actionable: Number(counts?.actionable ?? 0),
      actionable_creators: Number(counts?.actionable_creators ?? 0),
      raw_pending: Number(counts?.raw_pending ?? 0),
      high_risk: Number(counts?.high_risk ?? 0),
      membership_requests: Number(counts?.membership_requests ?? 0),
      price_reviews: Number(counts?.price_reviews ?? 0),
      template_matched: Number(counts?.template_matched ?? 0),
      human_only: Number(counts?.human_only ?? 0),
      ai_routed: Number(counts?.ai_routed ?? 0),
      context_reviewed: Number(counts?.context_reviewed ?? 0),
      context_pending_review: Math.max(0, Number(counts?.actionable ?? 0) - Number(counts?.context_reviewed ?? 0)),
      rules_only: Number(counts?.rules_only ?? 0),
      sent: Number(counts?.sent ?? 0),
      ignored: Number(counts?.ignored ?? 0),
      claimed_unverified: Number(usageCounts?.claimed_unverified ?? 0),
      account_only: Number(usageCounts?.account_only ?? 0),
      started_incomplete: Number(usageCounts?.started_incomplete ?? 0),
      real_testers: Number(usageCounts?.real_testers ?? 0),
      coupon_mode: couponMode?.value ?? "review",
      ai_configured: Boolean(bindings.OPENAI_API_KEY) || replyAIProvider?.value === "local_codex",
      ai_provider: replyAIProvider?.value === "local_codex" ? "local_codex" : bindings.OPENAI_API_KEY ? "responses_api" : "pending_local_codex",
      ai_model: replyAIProvider?.value === "local_codex" ? "本机 Codex（ChatGPT 登录）" : bindings.OPENAI_MODEL || "gpt-5-mini",
      usage_sync_configured: productDataAgent?.value === "1" || Boolean(bindings.PRODUCT_ADMIN_API_ORIGIN && bindings.PRODUCT_ADMIN_BEARER_TOKEN),
      data_agent_status: String(dataAgentRun?.status ?? "not_started"),
      data_agent_last_run_at: dataAgentRun?.completed_at ?? dataAgentRun?.started_at ?? null,
      coupons_redeemed: Number(dataAgentRun?.coupons_redeemed ?? 0),
      memberships_granted: Number(dataAgentRun?.memberships_granted ?? 0),
      followups_pending: Number(followupCounts?.pending ?? 0),
      followups_due: Number(followupCounts?.due ?? 0),
      followups_sent_today: Number(followupCounts?.sent_today ?? 0),
      followups_manual_required: Number(followupCounts?.manual_required ?? 0),
      promotion_waiting: promotionCreators.results.filter((row) => row.list_status === "waiting").length,
      promotion_quote_ready: promotionQuoteReady,
      promotion_needs_quote: Math.max(0, promotionCreators.results.length - promotionQuoteReady),
      promotion_approved: promotionApproved,
      efficiency_pilot_started_at: efficiencyPilot.started_at,
      efficiency_baseline_actionable: efficiencyPilot.actionable,
      efficiency_baseline_resolved_24h: efficiencyPilot.resolved_24h,
      efficiency_target_resolved_24h: Math.max(1, efficiencyPilot.resolved_24h * 2),
      efficiency_baseline_fast_lane_pct: efficiencyPilot.fast_lane_pct,
      efficiency_target_fast_lane_pct: Math.min(100, Math.round(efficiencyPilot.fast_lane_pct * 20) / 10),
      efficiency_baseline_over_72h: efficiencyPilot.over_72h,
      efficiency_target_over_72h: Math.floor(efficiencyPilot.over_72h / 2),
      reply_inbound_24h: Number(efficiencyCounts?.inbound_24h ?? 0),
      reply_resolved_24h: Number(efficiencyCounts?.resolved_24h ?? 0),
      reply_flow_ratio_pct: Number(efficiencyCounts?.inbound_24h ?? 0) > 0
        ? Math.round((Number(efficiencyCounts?.resolved_24h ?? 0) / Number(efficiencyCounts?.inbound_24h ?? 1)) * 1000) / 10
        : 0,
      actionable_over_24h: Number(efficiencyCounts?.over_24h ?? 0),
      actionable_over_72h: Number(efficiencyCounts?.over_72h ?? 0),
      fast_lane: fastLane,
      fast_lane_pct: fastLanePct,
      blocked_campaign_policy: Number(efficiencyCounts?.blocked_campaign_policy ?? 0),
      blocked_product_facts: Number(efficiencyCounts?.blocked_product_facts ?? 0),
      blocked_commercial_terms: Number(efficiencyCounts?.blocked_commercial_terms ?? 0),
      stuck_sending: Number(guardrailCounts?.stuck_sending ?? 0),
      delivery_unknown: Number(guardrailCounts?.delivery_unknown ?? 0),
      send_failures_24h: Number(guardrailCounts?.send_failures_24h ?? 0),
      state_repeats_15m: Number(guardrailCounts?.state_repeats_15m ?? 0),
    },
    work_items: workItems.results,
    promotion_creators: promotionCreators.results,
    templates: templates.results,
    rules: rules.results,
    sources: sources.results,
    stages: stages.results,
    audit: audit.results,
    discovery: {
      state: discoveryState ?? null,
      workers,
      learning: discoveryLearning,
      decisions: discoveryDecisions.results,
      online: workerOnline,
      summary: {
        total_jobs: Number(discoverySummary?.total_jobs ?? 0),
        initial_pass_complete: Number(discoverySummary?.initial_pass_complete ?? 0),
        queued_jobs: Number(discoverySummary?.queued_jobs ?? 0),
        error_jobs: Number(discoverySummary?.error_jobs ?? 0),
        videos_scanned: Number(discoverySummary?.videos_scanned ?? 0),
        channels_found: Number(discoverySummary?.channels_found ?? 0),
        public_emails_found: Number(discoverySummary?.public_emails_found ?? 0),
        contacts_imported: Number(discoverySummary?.contacts_imported ?? 0),
        master_contacts: Number(contactSummary?.master_contacts ?? 0),
        unique_public_emails: Number(discoveryEmailSummary?.unique_public_emails ?? 0),
        new_unique_contacts: newUniqueContacts,
        new_last_24h: newLast24h,
        new_last_6h: newLast6h,
        raw_new_unique_contacts: rawNewUniqueContacts,
        raw_new_last_24h: rawNewLast24h,
        daily_target: dailyTarget,
        daily_progress_percent: Math.min(100, Math.round((newLast24h / dailyTarget) * 1000) / 10),
        projected_daily: newLast6h * 4,
        active_workers: onlineWorkers.length,
        existing_duplicates: Number(discoveryEmailSummary?.existing_duplicates ?? 0),
        pending_validation: Number(discoveryEmailSummary?.pending_validation ?? 0),
        target_new_contacts: targetNewContacts,
        target_remaining: Math.max(0, targetNewContacts - newUniqueContacts),
        target_progress_percent: Math.min(100, Math.round((newUniqueContacts / targetNewContacts) * 1000) / 10),
      },
      tiers: discoveryTiers.results,
      jobs: discoveryJobs.results,
      recent_channels: discoveryChannels.results,
      coverage_note: "每日进度只计算公开订阅数 ≥1,000 且邮箱域 MX 有效的唯一联系人；原始新增另行保留。MX 不能保证具体邮箱账户一定存在，最终仍以退信与抑制记录继续清洗。",
    },
  };
}

async function writeAudit(action: string, entityType: string, entityId: string | number, detail: unknown) {
  await DB().prepare(`INSERT INTO outreach_audit_log(action,entity_type,entity_id,actor,detail_json,created_at)
    VALUES(?,?,?,'operator',?,datetime('now'))`).bind(action, entityType, String(entityId), JSON.stringify(detail ?? {})).run();
}

type ProductUsageEvidence = {
  status: "account_not_found" | "account_only" | "started" | "completed";
  registeredEmail: string;
  productUserId?: string | null;
  totalTasks: number;
  activeTasks: number;
  completedTasks: number;
  lastLoginAt?: string | null;
  lastTaskAt?: string | null;
  source: "product_admin_api" | "operator_verified";
};

async function applyProductUsageEvidence(workItemId: number, evidence: ProductUsageEvidence) {
  const db = DB();
  const item = await db.prepare(`SELECT w.contact_id,w.kind,e.subject,e.body_preview,e.body_text,e.sender_email,
    c.channel_name,c.email AS contact_email,c.reply_status
    FROM outreach_work_items w
    LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
    LEFT JOIN contacts c ON c.id=w.contact_id
    WHERE w.id=?`).bind(workItemId).first<InboxRow & { kind: string }>();
  if (!item?.contact_id) throw new Error("无法匹配联系人");

  const mapping = {
    account_not_found: {
      kind: "test_account_missing", stage: "account_not_found", template: "account_not_found", risk: "high",
      testStatus: "claimed_unverified", accountStatus: "not_found", reason: "产品后台未找到该注册邮箱，不能认定已经测试。",
    },
    account_only: {
      kind: "test_not_started", stage: "test_login_only", template: "test_login_only", risk: "medium",
      testStatus: "not_started", accountStatus: "found", reason: "产品后台有账号或登录记录，但视频任务数为 0，尚未真实测试。",
    },
    started: {
      kind: "test_incomplete", stage: "test_started", template: "test_started_incomplete", risk: "medium",
      testStatus: "started", accountStatus: "found", reason: "产品后台检测到视频任务，但没有成功完成的视频，测试尚未完成。",
    },
    completed: {
      kind: "test_verified", stage: "price_needed", template: "ask_rate", risk: "medium",
      testStatus: "completed_verified", accountStatus: "found", reason: "产品后台确认至少有一个成功完成的视频任务，标记为真实测试者。",
    },
  } as const;
  const decision = evidence.status === "completed" && item.kind === "price_and_test_claim" ? {
    kind: "price_received", stage: "price_received", template: "rate_received", risk: "high",
    testStatus: "completed_verified", accountStatus: "found", reason: "产品后台确认至少有一个成功完成的视频任务；真实测试已核验，且报价已经收到。",
  } : mapping[evidence.status];
  const template = await db.prepare(`SELECT template_key,subject,body,risk_level FROM outreach_templates
    WHERE template_key=? AND enabled=1`).bind(decision.template).first<TemplateRow>();
  if (!template) throw new Error("测试核验话术模板不存在");
  const rendered = renderTemplate(template, item, evidence.registeredEmail);

  await db.prepare(`INSERT INTO creator_profiles(
      contact_id,workflow_stage,registered_email,account_status,test_status,test_started_at,test_completed_at,
      product_user_id,usage_verification_status,usage_total_tasks,usage_active_tasks,usage_completed_tasks,
      usage_last_login_at,usage_last_task_at,usage_verified_at,usage_source,updated_at
    ) VALUES(?,?,?,?,?,CASE WHEN ?>0 THEN coalesce(?,datetime('now')) ELSE NULL END,
      CASE WHEN ?>0 THEN coalesce(?,datetime('now')) ELSE NULL END,?,?,?,?,?,?,?,?,datetime('now'))
    ON CONFLICT(contact_id) DO UPDATE SET
      workflow_stage=excluded.workflow_stage,registered_email=excluded.registered_email,account_status=excluded.account_status,
      test_status=excluded.test_status,test_started_at=CASE WHEN excluded.usage_total_tasks>0 THEN coalesce(creator_profiles.test_started_at,excluded.test_started_at) ELSE creator_profiles.test_started_at END,
      test_completed_at=CASE WHEN excluded.usage_completed_tasks>0 THEN coalesce(creator_profiles.test_completed_at,excluded.test_completed_at) ELSE creator_profiles.test_completed_at END,
      product_user_id=excluded.product_user_id,usage_verification_status=excluded.usage_verification_status,
      usage_total_tasks=excluded.usage_total_tasks,usage_active_tasks=excluded.usage_active_tasks,
      usage_completed_tasks=excluded.usage_completed_tasks,usage_last_login_at=excluded.usage_last_login_at,
      usage_last_task_at=excluded.usage_last_task_at,usage_verified_at=datetime('now'),usage_source=excluded.usage_source,
      updated_at=datetime('now')`)
    .bind(
      item.contact_id, decision.stage, evidence.registeredEmail, decision.accountStatus, decision.testStatus,
      evidence.totalTasks, evidence.lastTaskAt ?? null, evidence.completedTasks, evidence.lastTaskAt ?? null,
      evidence.productUserId ?? null, evidence.status, evidence.totalTasks, evidence.activeTasks, evidence.completedTasks,
      evidence.lastLoginAt ?? null, evidence.lastTaskAt ?? null, evidence.source,
    ).run();
  await db.prepare(`UPDATE outreach_work_items SET kind=?,workflow_stage=?,status='pending',risk_level=?,template_key=?,
    subject=?,draft_body=?,decision_reason=?,extracted_json=?,proposed_action_json=?,resolved_at=NULL,updated_at=datetime('now')
    WHERE id=?`)
    .bind(
      decision.kind, decision.stage, decision.risk, decision.template, rendered.subject, rendered.body, decision.reason,
      JSON.stringify(evidence), JSON.stringify(evidence.status === "completed"
        ? item.kind === "price_and_test_claim" ? [{ type: "marketing_review" }] : [{ type: "request_rate" }]
        : [{ type: "complete_real_product_test" }]), workItemId,
    ).run();
  await writeAudit("product_usage_verified", "contact", item.contact_id, evidence);
}

async function fetchProductUsage(registeredEmail: string): Promise<ProductUsageEvidence> {
  const bindings = env as unknown as Bindings;
  if (!bindings.PRODUCT_ADMIN_API_ORIGIN || !bindings.PRODUCT_ADMIN_BEARER_TOKEN) {
    throw new Error("产品后台自动核验尚未配置；可先在下方根据后台记录人工标记。 ");
  }
  const origin = bindings.PRODUCT_ADMIN_API_ORIGIN.replace(/\/+$/, "");
  const query = new URLSearchParams({ q: registeredEmail, page: "1", limit: "10" });
  const response = await fetch(`${origin}/api/admin/users?${query.toString()}`, {
    headers: { authorization: `Bearer ${bindings.PRODUCT_ADMIN_BEARER_TOKEN}`, accept: "application/json" },
  });
  const payload = await response.json() as {
    items?: Array<{
      id: string;
      email: string;
      lastLoginAt?: string | null;
      activity?: { visits?: number; totalTasks?: number; activeTasks?: number; completedTasks?: number; lastSeenAt?: string | null; lastTaskAt?: string | null };
    }>;
    error?: string;
  };
  if (!response.ok) throw new Error(payload.error || `产品后台核验失败：${response.status}`);
  const user = (payload.items || []).find((candidate) => candidate.email.trim().toLowerCase() === registeredEmail);
  if (!user) return { status: "account_not_found", registeredEmail, totalTasks: 0, activeTasks: 0, completedTasks: 0, source: "product_admin_api" };
  const totalTasks = Number(user.activity?.totalTasks || 0);
  const activeTasks = Number(user.activity?.activeTasks || 0);
  const completedTasks = Number(user.activity?.completedTasks || 0);
  const status = completedTasks > 0 ? "completed" : totalTasks > 0 ? "started" : "account_only";
  return {
    status, registeredEmail, productUserId: user.id, totalTasks, activeTasks, completedTasks,
    lastLoginAt: user.activity?.lastSeenAt || user.lastLoginAt || null,
    lastTaskAt: user.activity?.lastTaskAt || null, source: "product_admin_api",
  };
}

const replyContextProjection = `w.id,w.kind,w.workflow_stage,w.status,w.risk_level,w.template_key,w.decision_reason,
      e.id AS inbox_event_id,e.event_type,e.received_at,e.subject,e.body_preview,e.body_text,e.sender_email,
      c.id AS contact_id,c.channel_name,c.email AS contact_email,c.initial_template_key,c.reply_status,
      c.rate_info,c.do_not_contact,c.next_action,c.first_sent_at,
      p.registered_email,p.account_status,p.test_status,p.membership_status,p.membership_expires_at,
      p.coupon_code,p.coupon_sent_at,p.coupon_redemption_status,p.coupon_redeemed_code,
      p.usage_verification_status,p.usage_total_tasks,p.usage_active_tasks,p.usage_completed_tasks,
      p.usage_last_login_at,p.usage_last_task_at,p.usage_verified_at,p.usage_source,
      p.price_dedicated,p.price_integration,p.price_shorts,p.currency,p.turnaround,p.marketing_status,
      w.extracted_json`;

async function attachReplyContext(row: Record<string, unknown>): Promise<Record<string, unknown>> {
  const snapshot = await DB().prepare(replySnapshotSQL).bind(Number(row.id)).first<{ snapshot_json: string }>();
  if (!snapshot) throw new Error("会话快照不存在");
  const raw = snapshot.snapshot_json;
  const parsed = JSON.parse(raw);
  return { ...row, ...prepareReplyContext(raw), context_fingerprint: await contextFingerprint(raw),
    captured_sent_context_count: parsed.captured_sent_count, expected_sent_context_count: parsed.expected_sent_count,
    deterministic_current_classification: currentClassification({ ...row, body_text: parsed.body }) };
}

function currentClassification(row: Record<string, unknown>) {
  return classify({
    id: String(row.inbox_event_id || row.id), contact_id: row.contact_id ? Number(row.contact_id) : null,
    event_type: String(row.event_type || "reply"), subject: String(row.subject || ""),
    body_text: String(row.body_text || ""), body_preview: String(row.body_preview || ""),
    sender_email: String(row.sender_email || ""), channel_name: null, contact_email: null,
    reply_status: null, coupon_status: null,
  });
}

async function loadAIReplyContext(id: number): Promise<Record<string, unknown> | null> {
  const row = await DB().prepare(`${canonicalReplyQueueCte} SELECT ${replyContextProjection}
    FROM outreach_work_items w
    JOIN actionable_work_items actionable ON actionable.id=w.id
    LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
    LEFT JOIN contacts c ON c.id=w.contact_id
    LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
    WHERE w.id=?`).bind(id).first<Record<string, unknown>>();
  return row ? attachReplyContext(row) : null;
}

function validateAIReplyDecision(value: unknown): AIReplyDecision {
  const input = value as Partial<AIReplyDecision> | null;
  const intents = new Set<ReplyIntent>([
    "wants_test", "activation_help", "cardless_trial", "plans_to_test", "claims_tested",
    "price_received", "price_and_tested", "product_question", "product_issue", "asks_campaign_details",
    "contract_terms", "privacy_question", "complaint", "not_interested", "automated", "thanks_only", "other",
  ]);
  const templates = new Set<AIReplyDecision["template_key"]>([
    "coupon_test", "coupon_test_t2v_knowledge_v1", "coupon_test_rate_received", "coupon_test_campaign_details", "ask_registered_email", "product_question_hold",
    "campaign_details_after_coupon", "test_claim_checking", "test_planned_ack", "rate_received", null,
  ]);
  const testStatuses = new Set<AIReplyDecision["test_status"]>(["not_started", "wants_test", "testing", "completed", "unknown"]);
  const risks = new Set<AIReplyDecision["risk_level"]>(["low", "medium", "high"]);
  const conversationStates = new Set<AIReplyDecision["conversation_state"]>([
    "new_request", "continues_prior", "supersedes_prior", "conflicts_with_prior", "ambiguous",
  ]);
  if (!input || !intents.has(input.intent as ReplyIntent)) throw new Error("Codex 返回了无效意图");
  if (!templates.has(input.template_key as AIReplyDecision["template_key"])) throw new Error("Codex 返回了非白名单模板");
  if (typeof input.confidence !== "number" || !Number.isFinite(input.confidence) || input.confidence < 0 || input.confidence > 1) {
    throw new Error("Codex 返回了无效置信度");
  }
  if (!testStatuses.has(input.test_status as AIReplyDecision["test_status"])) throw new Error("Codex 返回了无效测试状态");
  if (!risks.has(input.risk_level as AIReplyDecision["risk_level"])) throw new Error("Codex 返回了无效风险等级");
  if (typeof input.needs_human !== "boolean") throw new Error("Codex 返回了无效人工审核标记");
  if (!Array.isArray(input.questions) || input.questions.some((question) => typeof question !== "string")) {
    throw new Error("Codex 返回了无效问题列表");
  }
  const registeredEmail = input.registered_email === null || input.registered_email === undefined
    ? null
    : String(input.registered_email).trim().toLowerCase().slice(0, 320);
  if (registeredEmail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(registeredEmail)) throw new Error("Codex 提取了无效注册邮箱");
  const stringList = (raw: unknown, limit: number, itemLimit: number) => Array.isArray(raw)
    ? raw.filter((item): item is string => typeof item === "string").slice(0, limit).map((item) => item.slice(0, itemLimit))
    : [];
  const secondaryIntents = Array.isArray(input.secondary_intents)
    ? input.secondary_intents.filter((intent): intent is ReplyIntent => intents.has(intent as ReplyIntent)).slice(0, 12)
    : [];
  if (!secondaryIntents.includes(input.intent as ReplyIntent)) secondaryIntents.unshift(input.intent as ReplyIntent);
  const conversationState = conversationStates.has(input.conversation_state as AIReplyDecision["conversation_state"])
    ? input.conversation_state as AIReplyDecision["conversation_state"]
    : "ambiguous";
  const evidence = Array.isArray(input.evidence) ? input.evidence.slice(0, 24).flatMap((raw) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as Record<string, unknown>;
    const source = String(item.source || "");
    if (!["current_message", "thread_history", "profile_state"].includes(source)) return [];
    return [{
      intent: String(item.intent || "").slice(0, 100),
      source: source as "current_message" | "thread_history" | "profile_state",
      text: String(item.text || "").slice(0, 500),
    }];
  }) : [];
  const contradictions = stringList(input.contradictions, 12, 500);
  const missingContext = stringList(input.missing_context, 12, 500);
  const contextComplete = Boolean(input.context_complete) && missingContext.length === 0;
  const templateGap = Boolean(input.template_gap);
  if (templateGap && input.template_key !== null) throw new Error("模板缺口判断不能同时选择现有模板");
  let proposedTemplate: AIReplyDecision["proposed_template"] = null;
  if (templateGap && input.proposed_template && typeof input.proposed_template === "object") {
    const proposal = input.proposed_template as AIReplyDecision["proposed_template"];
    if (proposal) {
      const proposalBody = String(proposal.body || "").slice(0, 12000);
      const unknownVariables = [...proposalBody.matchAll(/{{([a-z_]+)}}/gi)]
        .map((match) => match[1].toLowerCase())
        .filter((key) => !["original_subject", "coupon_code", "registered_email", "channel_name"].includes(key));
      if (unknownVariables.length) throw new Error(`候选模板包含未批准变量：${[...new Set(unknownVariables)].join(", ")}`);
      if (proposalBody.trim()) {
        proposedTemplate = {
          name: String(proposal.name || "候选回复模板").slice(0, 300),
          stage: String(proposal.stage || "candidate_review").slice(0, 120),
          subject: String(proposal.subject || "Re: {{original_subject}}").slice(0, 300),
          body: proposalBody,
          required_context: stringList(proposal.required_context, 12, 300),
        };
      }
    }
  }
  return {
    intent: input.intent as ReplyIntent,
    template_key: input.template_key as AIReplyDecision["template_key"],
    confidence: input.confidence,
    language: String(input.language || "unknown").slice(0, 80),
    registered_email: registeredEmail,
    test_status: input.test_status as AIReplyDecision["test_status"],
    questions: input.questions.slice(0, 10).map((question) => question.slice(0, 500)),
    risk_level: input.risk_level as AIReplyDecision["risk_level"],
    needs_human: input.needs_human,
    rationale: String(input.rationale || "").slice(0, 2000),
    secondary_intents: secondaryIntents,
    current_request_summary: String(input.current_request_summary || input.rationale || "").slice(0, 2000),
    conversation_state: conversationState,
    evidence,
    contradictions,
    missing_context: missingContext,
    context_complete: contextComplete,
    template_gap: templateGap,
    template_gap_reason: input.template_gap_reason === null || input.template_gap_reason === undefined
      ? null : String(input.template_gap_reason).slice(0, 1000),
    proposed_template: proposedTemplate,
  };
}

async function createOrReuseTemplateCandidate(
  workItemId: number,
  result: AIReplyDecision,
): Promise<TemplateRow & { template_key: string }> {
  if (!result.proposed_template) throw new Error("候选模板内容不存在");
  const db = DB();
  const intentSignature = JSON.stringify({
    intents: [...new Set(result.secondary_intents)].sort(),
    language: result.language.trim().toLowerCase(),
    questions: result.questions.map((question) => question.trim().toLowerCase()).sort(),
  });
  const existing = await db.prepare(`SELECT template_key,name,stage,subject,body,risk_level,enabled,approval_status
    FROM outreach_templates WHERE approval_status='pending_review' AND intent_signature=?
    ORDER BY updated_at DESC LIMIT 1`).bind(intentSignature).first<TemplateRow>();
  if (existing) {
    await db.prepare(`UPDATE outreach_templates SET example_count=example_count+1,
      creation_reason=?,updated_at=datetime('now') WHERE template_key=?`)
      .bind(result.template_gap_reason || result.rationale, existing.template_key).run();
    await writeAudit("template_candidate_reused", "template", existing.template_key, {
      work_item_id: workItemId,
      intent_signature: intentSignature,
    });
    return existing as TemplateRow & { template_key: string };
  }
  const slug = result.intent.replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "other";
  const key = `candidate_${slug}_${workItemId}`;
  const proposal = result.proposed_template;
  await db.prepare(`INSERT INTO outreach_templates(
      template_key,name,stage,subject,body,risk_level,enabled,version,approval_status,intent_signature,
      context_requirements_json,source_work_item_id,creation_reason,example_count,updated_at
    ) VALUES(?,?,?,?,?,'high',0,1,'pending_review',?,?,?,?,1,datetime('now'))`)
    .bind(
      key, proposal.name, proposal.stage, proposal.subject, proposal.body, intentSignature,
      JSON.stringify(proposal.required_context), workItemId, result.template_gap_reason || result.rationale,
    ).run();
  await writeAudit("template_candidate_created", "template", key, {
    work_item_id: workItemId,
    intent_signature: intentSignature,
    current_request_summary: result.current_request_summary,
  });
  return {
    template_key: key,
    name: proposal.name,
    stage: proposal.stage,
    subject: proposal.subject,
    body: proposal.body,
    risk_level: "high",
    enabled: 0,
    approval_status: "pending_review",
  };
}

async function applyAIReplyDecision(id: number, rawResult: unknown, source: "responses_api" | "local_codex", expectedFingerprint?: string) {
  const db = DB();
  const row = await loadAIReplyContext(id);
  if (!row) throw new Error("审核事项不存在");
  if (["bounce", "sender_restricted", "delivery_delayed", "delivery_failed", "automated"].includes(String(row.event_type)))
    throw new Error("服务商投递报告或自动邮件不是博主诉求，禁止改为可回复事项");
  const templateCatalog = await DB().prepare(`SELECT template_key,name,stage,subject,body,risk_level
    FROM outreach_templates WHERE enabled=1 AND approval_status='approved'
    ORDER BY stage,template_key`).all<Record<string, unknown>>();
  if (row.status !== "pending") throw new Error("审核事项已经处理，未覆盖现有结果");
  if (!expectedFingerprint || expectedFingerprint !== row.context_fingerprint)
    throw new Error("上下文已变化或缺少会话快照，必须重新分析；未应用旧判断");
  const result = validateAIReplyDecision(rawResult);
  const delivery = row.context_delivery as { complete: boolean; missing: string[] };
  if (!delivery.complete) {
    result.context_complete = false;
    result.missing_context = [...new Set([...result.missing_context, ...delivery.missing])];
  }
  const capturedSentContext = Number(row.captured_sent_context_count || 0);
  const expectedSentContext = Number(row.expected_sent_context_count || 0);
  if (capturedSentContext < expectedSentContext) {
    result.context_complete = false;
    const coverageReason = `已发送历史正文仅覆盖 ${capturedSentContext}/${expectedSentContext} 封`;
    if (!result.missing_context.includes(coverageReason)) result.missing_context.push(coverageReason);
  }
  const isT2V = row.initial_template_key === "first_contact_t2v_knowledge_v1";
  const testAccessPreviouslySent = Boolean(row.coupon_sent_at);
  const actualTestUnderway = ["active", "trialing"].includes(String(row.membership_status || "")) ||
    ["started", "completed"].includes(String(row.usage_verification_status || "")) ||
    ["claimed", "completed"].includes(String(row.test_status || ""));
  const asksCampaignDetails = result.secondary_intents.includes("asks_campaign_details");
  const campaignDetailsTemplate = actualTestUnderway
    ? null
    : testAccessPreviouslySent ? "campaign_details_after_coupon" : "coupon_test_campaign_details";
  const couponTemplate = result.secondary_intents.includes("price_received")
    ? "coupon_test_rate_received"
    : asksCampaignDetails ? campaignDetailsTemplate
    : isT2V ? "coupon_test_t2v_knowledge_v1" : "coupon_test";
  const routes: Record<ReplyIntent, {
    kind: string; stage: string; template: string | null; risk: "low" | "medium" | "high"; actions: Array<Record<string, unknown>>;
  }> = {
    wants_test: { kind: "coupon_reply", stage: "coupon_ready", template: couponTemplate, risk: "medium", actions: [{ type: "send_coupon", code: COUPON }] },
    activation_help: { kind: "coupon_reply", stage: "coupon_ready", template: couponTemplate, risk: "medium", actions: [{ type: "send_coupon_instructions", code: COUPON }] },
    cardless_trial: { kind: "membership_request", stage: result.registered_email ? "membership_pending" : "membership_email_needed", template: "ask_registered_email", risk: "high", actions: [{ type: "manual_membership_review" }] },
    plans_to_test: { kind: "test_planned", stage: "testing", template: "test_planned_ack", risk: "medium", actions: [{ type: "wait_for_completed_test" }] },
    claims_tested: { kind: "test_claim", stage: "test_claimed", template: "test_claim_checking", risk: "high", actions: [{ type: "verify_product_usage" }] },
    price_received: { kind: "price_received", stage: "price_received", template: "rate_received", risk: "high", actions: [{ type: "extract_price" }, { type: "marketing_review" }] },
    price_and_tested: { kind: "price_and_test_claim", stage: "price_received_unverified", template: "test_claim_checking", risk: "high", actions: [{ type: "extract_price" }, { type: "verify_product_usage" }] },
    product_question: { kind: "product_question", stage: "product_question", template: "product_question_hold", risk: "high", actions: [{ type: "human_product_answer" }] },
    product_issue: { kind: "product_question", stage: "product_issue", template: "product_question_hold", risk: "high", actions: [{ type: "human_product_answer" }] },
    asks_campaign_details: actualTestUnderway
      ? { kind: "human_review", stage: "campaign_details", template: null, risk: "high", actions: [{ type: "human_campaign_answer" }] }
      : testAccessPreviouslySent
        ? { kind: "test_reminder", stage: "test_pending", template: "campaign_details_after_coupon", risk: "high", actions: [{ type: "remind_test_before_campaign_negotiation" }] }
        : { kind: "coupon_reply", stage: "coupon_ready", template: "coupon_test_campaign_details", risk: "high", actions: [{ type: "send_coupon", code: COUPON }, { type: "test_before_campaign_negotiation" }] },
    contract_terms: { kind: "human_review", stage: "contract_review", template: null, risk: "high", actions: [{ type: "human_contract_review" }] },
    privacy_question: { kind: "human_review", stage: "privacy_review", template: null, risk: "high", actions: [{ type: "human_privacy_answer" }] },
    complaint: { kind: "human_review", stage: "complaint_review", template: null, risk: "high", actions: [{ type: "human_complaint_response" }] },
    not_interested: { kind: "opt_out", stage: "closed", template: null, risk: "low", actions: [{ type: "do_not_contact" }] },
    automated: { kind: "ignore", stage: "contacted", template: null, risk: "low", actions: [] },
    thanks_only: { kind: "ignore", stage: "contacted", template: null, risk: "low", actions: [] },
    other: { kind: "human_review", stage: "replied", template: null, risk: "high", actions: [{ type: "human_review" }] },
  };
  let route = routes[result.intent];
  const noReplyIntents = new Set<ReplyIntent>(["not_interested", "automated", "thanks_only"]);
  const actionableIntents = [...new Set(result.secondary_intents.filter((intent) => !noReplyIntents.has(intent)))];
  const compoundActionable = actionableIntents.length > 1;
  const hasCurrentEvidence = result.evidence.some((item) => item.source === "current_message" && item.intent === result.intent
    && currentEvidenceMatches(String(row.body_text || ""), item.text));
  const contextConflict = result.contradictions.length > 0 || result.conversation_state === "conflicts_with_prior" ||
    result.conversation_state === "ambiguous";
  let candidateTemplate: (TemplateRow & { template_key: string }) | null = null;
  let candidateBlockedReason = "";
  if (result.template_gap) {
    if (result.questions.length > 0) candidateBlockedReason = "当前仍有未解决问题，必须先补充经过确认的答案";
    else if (!result.context_complete) candidateBlockedReason = "上下文仍不完整，不能创建可复用模板";
    else if (!hasCurrentEvidence) candidateBlockedReason = "缺少来自当前来信的直接意图证据";
    else if (contextConflict) candidateBlockedReason = "当前来信与历史或状态存在冲突";
    else if (!result.proposed_template) candidateBlockedReason = "缺少有事实依据的候选模板正文";
    else candidateTemplate = await createOrReuseTemplateCandidate(id, result);
  }
  const alreadyBeyondCardlessSetup = result.intent === "cardless_trial" && (
    ["active", "trialing"].includes(String(row.membership_status || "")) ||
    ["started", "completed"].includes(String(row.usage_verification_status || "")) ||
    ["claimed", "completed"].includes(String(row.test_status || ""))
  );
  const protectedKinds = new Set(["membership_request", "product_question", "test_claim", "price_and_test_claim", "price_received", "opt_out"]);
  const currentSafety = currentClassification(row);
  const conflictsWithSafetyRule = protectedKinds.has(currentSafety.kind)
    && String(currentSafety.template || "") !== String(route.template || "");
  const invalidTemplate = !result.template_gap && result.template_key !== route.template;
  const routingFailure = result.confidence < AI_AUTO_ROUTE_THRESHOLD || invalidTemplate || conflictsWithSafetyRule ||
    result.intent === "other" || alreadyBeyondCardlessSetup || !hasCurrentEvidence || !result.context_complete ||
    contextConflict || result.template_gap;
  const requiresHumanApproval = result.needs_human || compoundActionable || result.questions.length > 0;
  let reason = result.rationale;
  if (routingFailure) {
    const causes = [
      result.confidence < AI_AUTO_ROUTE_THRESHOLD ? `AI 置信度低于 ${Math.round(AI_AUTO_ROUTE_THRESHOLD * 100)}%` : "",
      invalidTemplate ? "AI 选择的模板与服务器白名单路由不一致" : "",
      conflictsWithSafetyRule ? "AI 判断与高风险确定性规则冲突" : "",
      result.intent === "other" ? "没有匹配已批准模板" : "",
      alreadyBeyondCardlessSetup ? "现有会员或测试状态表明该免绑卡请求已过期" : "",
      !hasCurrentEvidence ? "缺少当前来信的直接证据" : "",
      !result.context_complete ? `上下文不完整：${result.missing_context.join("；") || "未说明缺失项"}` : "",
      contextConflict ? `上下文存在冲突：${result.contradictions.join("；") || result.conversation_state}` : "",
      result.template_gap ? candidateTemplate
        ? `已创建待审核模板 ${candidateTemplate.template_key}`
        : `存在模板缺口但暂不创建：${candidateBlockedReason || result.template_gap_reason || "证据不足"}` : "",
    ].filter(Boolean).join("；");
    route = {
      kind: "human_review",
      stage: candidateTemplate ? "template_candidate_review" : "replied",
      template: candidateTemplate?.template_key || null,
      risk: "high",
      actions: candidateTemplate
        ? [{ type: "review_template_candidate", template_key: candidateTemplate.template_key }]
        : [{ type: "human_review" }],
    };
    reason = `${causes}。转人工处理。AI 摘要：${result.rationale}`;
  } else if (requiresHumanApproval) {
    const approvalCauses = [
      result.needs_human ? "意图判断要求人工复核" : "",
      compoundActionable ? `当前来信包含复合意图：${actionableIntents.join(", ")}` : "",
      result.questions.length > 0 ? `当前来信有 ${result.questions.length} 个问题，发送前必须逐项核对` : "",
    ].filter(Boolean).join("；");
    route = {
      ...route,
      risk: "high",
      actions: [...route.actions, { type: "human_approval_required" }],
    };
    reason = `${approvalCauses}。已匹配覆盖完整诉求的模板，但禁止自动发送。AI 摘要：${result.rationale}`;
  }
  // An invitation and its coupon can acquire different provider conversation IDs.
  // Close only a fully understood, single access request with a later real coupon
  // to the exact same recipient; an unresolved question must remain open.
  if (!routingFailure && !requiresHumanApproval && route.kind === "coupon_reply" && result.intent === "wants_test") {
    const laterCoupons = await db.prepare(`SELECT id,sent_at,body_text FROM outreach_sent_messages
      WHERE contact_id=? AND lower(trim(recipient_email))=lower(trim(?))
        AND datetime(sent_at)>datetime(?) AND instr(body_text,?)>0
      ORDER BY datetime(sent_at) ASC`).bind(Number(row.contact_id), String(row.sender_email || ""), String(row.received_at), COUPON)
      .all<{ id: string; sent_at: string; body_text: string }>();
    const answered = laterCoupons.results.find((sent: { id: string; sent_at: string; body_text: string }) => {
      const text = currentReplyText(sent.body_text);
      return text.includes(COUPON) && /(?:here[’']?s (?:your |the )?code|promo(?:tion)? code\s*:|creator code\s*:)/i.test(text);
    });
    if (answered) {
      route = { kind: "ignore", stage: "already_answered", template: null, risk: "low",
        actions: [{ type: "already_answered", sent_message_id: answered.id, sent_at: answered.sent_at }] };
      reason = `该请求之后已向同一收件人发送测试码（${answered.sent_at}）；已核对真实出站正文，关闭跨会话重复待办。`;
    }
  }
  const template = candidateTemplate || (route.template
    ? await db.prepare(`SELECT template_key,subject,body,risk_level FROM outreach_templates
        WHERE template_key=? AND enabled=1 AND approval_status='approved'`)
      .bind(route.template).first<TemplateRow>()
    : null);
  if (route.template && !template) {
    route = { kind: "human_review", stage: "replied", template: null, risk: "high", actions: [{ type: "human_review" }] };
    reason = `AI 选择的模板未启用或不存在，已转人工。AI 摘要：${result.rationale}`;
  }
  const inboxRow = row as unknown as InboxRow;
  const rendered = template ? renderTemplate(template, inboxRow, result.registered_email) : { subject: "", body: "" };
  const outputText = JSON.stringify({ ...result, context_audit: {
    version: REPLY_CONTEXT_VERSION, fingerprint: expectedFingerprint,
    delivery_complete: delivery.complete, reviewed_at: new Date().toISOString(),
    route_kind: route.kind, route_template_key: route.template,
    draft_fingerprint: await contextFingerprint(rendered.body),
  } });
  const resolvedStatus = route.kind === "ignore" || route.kind === "opt_out" ? "ignored" : "pending";
  const router = source === "local_codex" ? "local_codex_router" : "ai_router";
  const latest = await loadAIReplyContext(id);
  if (!latest || latest.context_fingerprint !== expectedFingerprint)
    throw new Error("上下文在应用判断前已变化，必须重新分析");
  await db.prepare(`UPDATE outreach_work_items SET kind=?,workflow_stage=?,status=?,confidence=?,risk_level=?,template_key=?,
      subject=?,draft_body=?,extracted_json=?,proposed_action_json=?,decision_reason=?,resolved_at=CASE WHEN ?='ignored' THEN datetime('now') ELSE NULL END,
      resolved_by=CASE WHEN ?='ignored' THEN ? ELSE NULL END,updated_at=datetime('now') WHERE id=? AND status='pending'`)
    .bind(route.kind, route.stage, resolvedStatus, result.confidence, route.risk, route.template,
      rendered.subject, rendered.body, outputText, JSON.stringify(route.actions), reason,
      resolvedStatus, resolvedStatus, router, id).run();
  if (route.kind === "opt_out" && row.contact_id) {
    await db.prepare(`UPDATE contacts SET do_not_contact=1,outreach_status='opted_out',reply_status='not_interested',
      next_action='对方已拒绝或退订；停止联系。',updated_at=datetime('now') WHERE id=?`).bind(Number(row.contact_id)).run();
  }
  if (route.kind === "membership_request" && row.contact_id) {
    await db.prepare(`INSERT INTO creator_profiles(contact_id,workflow_stage,registered_email,test_status,updated_at)
      VALUES(?,?,?,'wants_test',datetime('now'))
      ON CONFLICT(contact_id) DO UPDATE SET workflow_stage=excluded.workflow_stage,
        registered_email=coalesce(excluded.registered_email,creator_profiles.registered_email),
        test_status=CASE WHEN creator_profiles.test_status IN ('claimed','completed') THEN creator_profiles.test_status ELSE 'wants_test' END,
        updated_at=datetime('now')`)
      .bind(Number(row.contact_id), route.stage, result.registered_email).run();
  }
  if ((route.kind === "test_claim" || route.kind === "price_and_test_claim") && row.contact_id) {
    await db.prepare(`INSERT INTO creator_profiles(contact_id,workflow_stage,test_status,updated_at)
      VALUES(?,'test_claimed','claimed',datetime('now'))
      ON CONFLICT(contact_id) DO UPDATE SET workflow_stage='test_claimed',test_status='claimed',updated_at=datetime('now')`)
      .bind(Number(row.contact_id)).run();
  }
  await writeAudit("ai_analyzed", "work_item", id, { source, ...result });
  return { configured: true, source, result, route: { ...route, reason } };
}

async function analyzeWithAI(id: number) {
  const bindings = env as unknown as Bindings;
  if (!bindings.OPENAI_API_KEY) return { configured: false, queued_for_local_codex: true };
  const row = await loadAIReplyContext(id);
  if (!row) throw new Error("审核事项不存在");
  const templateCatalog = await DB().prepare(`SELECT template_key,subject,body FROM outreach_templates
    WHERE enabled=1 AND approval_status='approved' AND template_key IN (
      'coupon_test','coupon_test_t2v_knowledge_v1','coupon_test_rate_received',
      'coupon_test_campaign_details','campaign_details_after_coupon','ask_registered_email',
      'product_question_hold','test_claim_checking','test_planned_ack','rate_received'
    )`).all<TemplateRow>();
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { authorization: `Bearer ${bindings.OPENAI_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({
      model: bindings.OPENAI_MODEL || "gpt-5-mini",
      instructions: `Understand the newest inbound creator message using the complete labeled inbound/outbound thread and current product/profile state. Identify every live intent, cite evidence for each, list contradictions and missing context, and select an approved template only when its actual copy answers every current intent and question. Current state overrides stale history. If captured_sent_context_count is lower than expected_sent_context_count, context_complete must be false. Never treat login, signup, coupon receipt, or activation as a completed test. Never treat a budget question as the creator's rate. Compound actionable messages, questions, product issues, prices, contracts, privacy, complaints, test claims, and contradictions always need human review.

If no approved template covers the complete reply, set template_gap true. Propose a reusable candidate only when context_complete is true, questions is empty, and every statement is grounded in supplied facts. Never invent product facts, budget, dates, payment or legal terms, account state, or promises. When facts are missing or any question is unresolved, leave proposed_template null and list the missing facts in missing_context. Candidate variables are limited to {{original_subject}}, {{coupon_code}}, {{registered_email}}, and {{channel_name}}. Return only the requested schema.`,
      input: JSON.stringify({ approved_template_catalog: templateCatalog.results, record: row }),
      store: false,
      text: {
        format: {
          type: "json_schema",
          name: "creator_reply_decision",
          strict: true,
          schema: aiReplyJsonSchema,
        },
      },
    }),
  });
  const payload = await response.json() as { output?: Array<{ content?: Array<{ type?: string; text?: string }> }>; error?: { message?: string } };
  if (!response.ok) throw new Error(payload.error?.message || "AI分析失败");
  const outputText = payload.output?.flatMap((item) => item.content ?? []).find((item) => item.type === "output_text")?.text;
  if (!outputText) throw new Error("AI没有返回结构化结果");
  return applyAIReplyDecision(id, JSON.parse(outputText), "responses_api", String(row.context_fingerprint));
}

async function analyzePendingWithAI(limit = 20) {
  const bindings = env as unknown as Bindings;
  if (!bindings.OPENAI_API_KEY) return { configured: false, analyzed: 0, failed: 0 };
  const rows = await DB().prepare(`${canonicalReplyQueueCte} SELECT w.id FROM outreach_work_items w
    JOIN actionable_work_items actionable ON actionable.id=w.id
    WHERE w.status='pending' AND w.confidence IS NULL
    ORDER BY CASE w.risk_level WHEN 'high' THEN 0 ELSE 1 END,w.created_at ASC LIMIT ?`)
    .bind(Math.max(1, Math.min(50, limit))).all<{ id: number }>();
  let analyzed = 0;
  let failed = 0;
  for (const row of rows.results) {
    try {
      await analyzeWithAI(Number(row.id));
      analyzed += 1;
    } catch (error) {
      failed += 1;
      await writeAudit("ai_analysis_failed", "work_item", row.id, { error: error instanceof Error ? error.message : String(error) });
    }
  }
  return { configured: true, analyzed, failed };
}

async function localCodexPendingBatch(limit = 10, options: {
  olderThanHours?: number;
  reanalyze?: boolean;
  updatedBefore?: string;
  partitionCount?: number;
  partitionIndex?: number;
  itemIds?: number[];
} = {}) {
  const conditions = ["w.status='pending'"];
  const bindings: Array<string | number> = [];
  if (!options.reanalyze) conditions.push("w.confidence IS NULL");
  if (options.olderThanHours && options.olderThanHours > 0) {
    conditions.push("datetime(e.received_at)<datetime('now',?)");
    bindings.push(`-${Math.min(24 * 3650, options.olderThanHours)} hours`);
  }
  if (options.updatedBefore) {
    conditions.push("datetime(w.updated_at)<datetime(?)");
    bindings.push(options.updatedBefore);
  }
  if (options.partitionCount && options.partitionCount > 1 && options.partitionIndex !== undefined) {
    conditions.push("(w.id % ?)=?");
    bindings.push(options.partitionCount, options.partitionIndex);
  }
  if (options.itemIds?.length) {
    conditions.push(`w.id IN (${options.itemIds.map(() => "?").join(",")})`);
    bindings.push(...options.itemIds);
  }
  const rows = await DB().prepare(`${canonicalReplyQueueCte} SELECT ${replyContextProjection}
    FROM outreach_work_items w
    JOIN actionable_work_items actionable ON actionable.id=w.id
    LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
    LEFT JOIN contacts c ON c.id=w.contact_id
    LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
    LEFT JOIN reply_context_checks checks ON checks.work_item_id=w.id
    WHERE ${conditions.join(" AND ")}
    ORDER BY ${options.reanalyze ? "coalesce(checks.checked_at,'') ASC," : ""} datetime(e.received_at) ASC,w.id ASC
    LIMIT ?`).bind(...bindings, Math.max(1, Math.min(20, limit)) * (options.reanalyze && !options.itemIds?.length ? 3 : 1)).all<Record<string, unknown>>();
  const items: Record<string, unknown>[] = [];
  for (const row of rows.results) {
    const context = await attachReplyContext(row);
    await DB().prepare(`INSERT INTO reply_context_checks(work_item_id,checked_at) VALUES(?,?)
      ON CONFLICT(work_item_id) DO UPDATE SET checked_at=excluded.checked_at`)
      .bind(Number(row.id), new Date().toISOString()).run();
    let prior: { context_audit?: { version?: string; fingerprint?: string; route_kind?: string; route_template_key?: string | null } } = {};
    try { prior = JSON.parse(String(row.extracted_json || '{}')); } catch { /* reanalyze invalid records */ }
    if (options.reanalyze && !options.itemIds?.length &&
        prior.context_audit?.version === REPLY_CONTEXT_VERSION &&
        prior.context_audit?.fingerprint === context.context_fingerprint &&
        prior.context_audit?.route_kind === row.kind &&
        prior.context_audit?.route_template_key === row.template_key) continue;
    items.push(context);
    if (items.length >= Math.max(1, Math.min(20, limit))) break;
  }
  return items;
}

export async function GET(request: NextRequest) {
  try {
    await ensureOperationsSchemaOnce();
    const view = request.nextUrl.searchParams.get("view");
    if (view === "conversations") return NextResponse.json(await conversationList(request));
    if (view === "conversation") {
      const detail = await conversationDetail(String(request.nextUrl.searchParams.get("key") || ""));
      if (!detail) return NextResponse.json({ error: "会话不存在" }, { status: 404 });
      return NextResponse.json(detail);
    }
    let lastError: unknown;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        return NextResponse.json(await dashboardData());
      } catch (error) {
        lastError = error;
        const message = error instanceof Error ? error.message : String(error);
        if (!/D1_ERROR|SQLITE_BUSY|internal error/i.test(message) || attempt === 4) throw error;
        await new Promise((resolve) => setTimeout(resolve, 150 * (attempt + 1)));
      }
    }
    throw lastError;
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "无法读取运营中心" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    await ensureOperationsSchemaOnce();
    const body = await request.json() as Record<string, unknown>;
    const action = String(body.action || "");
    const db = DB();

    if (action === "refresh_queue") {
      const created = await seedReviewQueue();
      const reconciled = await reconcileReplyQueue();
      await writeAudit("queue_refreshed", "system", "review_queue", { created, reconciled });
      return NextResponse.json({ created, reconciled, ...(await dashboardData()) });
    }

    if (action === "analyze_pending") {
      const created = await seedReviewQueue();
      const analysis = await analyzePendingWithAI(Number(body.limit || 20));
      await writeAudit("pending_queue_routed", "system", "review_queue", { created, ...analysis });
      return NextResponse.json({ created, analysis, ...(await dashboardData()) });
    }

    if (action === "local_codex_batch") {
      const created = body.reanalyze === true ? 0 : await seedReviewQueue(false);
      const olderThanHours = Number(body.older_than_hours || 0);
      const updatedBefore = typeof body.updated_before === "string" && body.updated_before.trim()
        ? body.updated_before.trim().slice(0, 80)
        : undefined;
      const partitionCount = Math.max(1, Math.min(8, Math.trunc(Number(body.partition_count || 1))));
      const rawPartitionIndex = Math.trunc(Number(body.partition_index || 0));
      const partitionIndex = Math.max(0, Math.min(partitionCount - 1, rawPartitionIndex));
      const itemIds = Array.isArray(body.item_ids)
        ? body.item_ids.map(Number).filter((id) => Number.isInteger(id) && id > 0).slice(0, 20)
        : [];
      const items = await localCodexPendingBatch(Number(body.limit || 10), {
        olderThanHours: Number.isFinite(olderThanHours) ? olderThanHours : 0,
        reanalyze: body.reanalyze === true,
        updatedBefore,
        partitionCount,
        partitionIndex,
        itemIds,
      });
      // Expose only templates that the deterministic router is allowed to
      // select. Context-specific reviewed templates remain approved for
      // explicit manifests, but must never be chosen by the general AI loop.
      const templates = await db.prepare(`SELECT template_key,name,stage,subject,body,risk_level
        FROM outreach_templates WHERE enabled=1 AND approval_status='approved'
          AND template_key IN (
            'coupon_test','coupon_test_t2v_knowledge_v1','coupon_test_rate_received',
            'coupon_test_campaign_details','campaign_details_after_coupon',
            'ask_registered_email','product_question_hold','test_claim_checking',
            'test_planned_ack','rate_received'
          )
        ORDER BY stage,template_key`).all<Record<string, unknown>>();
      return NextResponse.json({ created, provider: "local_codex", items, templates: templates.results });
    }

    if (action === "apply_local_codex_decisions") {
      const decisions = Array.isArray(body.decisions) ? body.decisions.slice(0, 20) : [];
      if (!decisions.length) return NextResponse.json({ error: "Codex 没有返回可应用的判断" }, { status: 400 });
      let analyzed = 0;
      let failed = 0;
      const errors: Array<{ id: number; error: string }> = [];
      for (const raw of decisions) {
        const entry = raw as Record<string, unknown>;
        const id = Number(entry.id);
        if (!Number.isInteger(id) || id <= 0) {
          failed += 1;
          errors.push({ id: 0, error: "无效工作项 ID" });
          continue;
        }
        try {
          await retryD1Write(() => applyAIReplyDecision(id, entry, "local_codex", String(entry.context_fingerprint || "")));
          analyzed += 1;
        } catch (error) {
          failed += 1;
          const message = error instanceof Error ? error.message : String(error);
          errors.push({ id, error: message.slice(0, 300) });
          await writeAudit("ai_analysis_failed", "work_item", id, { source: "local_codex", error: message });
        }
      }
      if (analyzed > 0) {
        await db.prepare(`INSERT INTO outreach_settings(key,value,updated_at) VALUES('reply_ai_provider','local_codex',datetime('now'))
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`).run();
      }
      return NextResponse.json({ analysis: { configured: true, provider: "local_codex", analyzed, failed }, errors });
    }

    if (action === "set_discovery_enabled") {
      const enabled = Boolean(body.enabled);
      await db.prepare(`UPDATE lead_discovery_state SET enabled=?,status=?,last_error=NULL,updated_at=datetime('now')
        WHERE worker_key GLOB 'collector-*'`).bind(enabled ? 1 : 0, enabled ? "starting" : "paused").run();
      await writeAudit(enabled ? "lead_discovery_resumed" : "lead_discovery_paused", "system", "lead_discovery_workers", {});
      return NextResponse.json(await dashboardData());
    }

    if (action === "retry_discovery_errors") {
      const retried = await db.prepare(`UPDATE lead_discovery_jobs SET status='queued',next_run_at=datetime('now'),
        last_error=NULL,updated_at=datetime('now') WHERE status='error'`).run();
      await writeAudit("lead_discovery_errors_retried", "system", "youtube_public_discovery", { count: Number(retried.meta?.changes ?? 0) });
      return NextResponse.json(await dashboardData());
    }

    if (action === "run_discovery_now") {
      const job = await db.prepare(`SELECT id FROM lead_discovery_jobs
        WHERE status IN ('queued','waiting','error')
        ORDER BY tier ASC,run_count ASC,priority DESC,last_completed_at ASC,id ASC LIMIT 1`)
        .first<{ id: number }>();
      if (!job) return NextResponse.json({ error: "当前没有可运行的发现任务" }, { status: 409 });
      await db.prepare(`UPDATE lead_discovery_jobs SET status='queued',next_run_at=datetime('now'),updated_at=datetime('now') WHERE id=?`)
        .bind(job.id).run();
      await db.prepare(`UPDATE lead_discovery_state SET enabled=1,status='starting',updated_at=datetime('now')
        WHERE worker_key GLOB 'collector-*'`).run();
      await writeAudit("lead_discovery_run_requested", "lead_discovery_job", job.id, {});
      return NextResponse.json(await dashboardData());
    }

    if (action === "update_template") {
      const key = String(body.template_key || "");
      const subject = String(body.subject || "").slice(0, 300);
      const templateBody = String(body.body || "").slice(0, 12000);
      if (!key || !templateBody.trim()) return NextResponse.json({ error: "模板正文不能为空" }, { status: 400 });
      await db.prepare(`UPDATE outreach_templates SET subject=?,body=?,version=version+1,updated_at=datetime('now') WHERE template_key=?`)
        .bind(subject, templateBody, key).run();
      await writeAudit("template_updated", "template", key, { subject, body_length: templateBody.length });
      return NextResponse.json(await dashboardData());
    }

    if (action === "approve_template_candidate") {
      const key = String(body.template_key || "");
      const candidate = await db.prepare(`SELECT template_key,subject,body,approval_status FROM outreach_templates
        WHERE template_key=?`).bind(key).first<Record<string, unknown>>();
      if (!candidate) return NextResponse.json({ error: "候选模板不存在" }, { status: 404 });
      if (candidate.approval_status !== "pending_review") {
        return NextResponse.json({ error: "该模板不处于待审核状态" }, { status: 409 });
      }
      if (!String(candidate.body || "").trim()) return NextResponse.json({ error: "候选模板正文不能为空" }, { status: 400 });
      await db.prepare(`UPDATE outreach_templates SET approval_status='approved',enabled=1,version=version+1,
        updated_at=datetime('now') WHERE template_key=? AND approval_status='pending_review'`).bind(key).run();
      await writeAudit("template_candidate_approved", "template", key, { approved_by: "operator" });
      return NextResponse.json(await dashboardData());
    }

    if (action === "reject_template_candidate") {
      const key = String(body.template_key || "");
      const rejected = await db.prepare(`UPDATE outreach_templates SET approval_status='rejected',enabled=0,
        updated_at=datetime('now') WHERE template_key=? AND approval_status='pending_review'`).bind(key).run();
      if (Number(rejected.meta?.changes ?? 0) === 0) {
        return NextResponse.json({ error: "候选模板不存在或已经处理" }, { status: 409 });
      }
      await db.prepare(`UPDATE outreach_work_items SET template_key=NULL,subject='',draft_body='',
        workflow_stage='replied',decision_reason=decision_reason || '；候选模板已被人工拒绝，需重新确认回复内容。',
        updated_at=datetime('now') WHERE template_key=? AND status='pending'`).bind(key).run();
      await writeAudit("template_candidate_rejected", "template", key, { rejected_by: "operator" });
      return NextResponse.json(await dashboardData());
    }

    if (action === "update_rule") {
      const key = String(body.rule_key || "");
      const mode = String(body.mode || "");
      if (!key || !["manual", "draft", "auto"].includes(mode)) return NextResponse.json({ error: "无效自动化模式" }, { status: 400 });
      const rule = await db.prepare("SELECT risk_level FROM automation_rules WHERE rule_key=?").bind(key).first<{ risk_level: string }>();
      if (!rule) return NextResponse.json({ error: "自动化规则不存在" }, { status: 404 });
      if (mode === "auto" && key === "first_contact") {
        const templateState = await db.prepare(`SELECT
          SUM(CASE WHEN enabled=1 AND approval_status='approved' AND length(trim(body))>0 THEN 1 ELSE 0 END) AS approved,
          SUM(CASE WHEN enabled=1 AND (approval_status<>'approved' OR length(trim(body))=0) THEN 1 ELSE 0 END) AS unsafe
          FROM outreach_templates WHERE stage='first_contact'`).first<{ approved: number; unsafe: number }>();
        if (Number(templateState?.approved ?? 0) < 1 || Number(templateState?.unsafe ?? 0) > 0) {
          return NextResponse.json({ error: "首次邀请仅能在全部启用模板均已人工批准且正文完整时自动发送" }, { status: 409 });
        }
      } else if (mode === "auto" && rule.risk_level !== "low" && key !== "coupon_reply") {
        return NextResponse.json({ error: "只有低风险的退订或无关邮件可以自动静默处理" }, { status: 409 });
      }
      if (key === "first_contact") {
        const description = mode === "auto"
          ? "仅使用已启用且人工批准的首次邀请模板自动发送。"
          : "使用人工批准模板；发送前保持人工控制。";
        await db.prepare("UPDATE automation_rules SET mode=?,description=?,updated_at=datetime('now') WHERE rule_key=?")
          .bind(mode, description, key).run();
      } else {
        await db.prepare("UPDATE automation_rules SET mode=?,updated_at=datetime('now') WHERE rule_key=?").bind(mode, key).run();
      }
      if (key === "coupon_reply") {
        await db.prepare(`INSERT INTO outreach_settings(key,value,updated_at) VALUES('coupon_automation_mode',?,datetime('now'))
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`).bind(mode === "auto" ? "auto" : "review").run();
      }
      await writeAudit("rule_updated", "automation_rule", key, { mode });
      return NextResponse.json(await dashboardData());
    }

    if (action === "save_draft") {
      const id = Number(body.id);
      const subject = String(body.subject || "").slice(0, 300);
      const draftBody = String(body.draft_body || "").slice(0, 12000);
      await db.prepare("UPDATE outreach_work_items SET subject=?,draft_body=?,updated_at=datetime('now') WHERE id=?")
        .bind(subject, draftBody, id).run();
      await writeAudit("draft_edited", "work_item", id, { subject, body_length: draftBody.length });
      return NextResponse.json(await dashboardData());
    }

    if (action === "apply_template") {
      const id = Number(body.id);
      const templateKey = String(body.template_key || "");
      const item = await db.prepare(`SELECT w.id,e.subject,e.body_preview,e.body_text,e.sender_email,
        c.channel_name,c.email AS contact_email,c.reply_status,p.registered_email
        FROM outreach_work_items w
        LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
        LEFT JOIN contacts c ON c.id=w.contact_id
        LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
        WHERE w.id=?`).bind(id).first<InboxRow & { registered_email: string | null }>();
      const template = await db.prepare(`SELECT template_key,subject,body,risk_level FROM outreach_templates
        WHERE template_key=? AND enabled=1 AND approval_status='approved'`).bind(templateKey).first<TemplateRow>();
      if (!item || !template) return NextResponse.json({ error: "审核事项或模板不存在" }, { status: 404 });
      const rendered = renderTemplate(template, item, item.registered_email);
      await db.prepare(`UPDATE outreach_work_items SET template_key=?,subject=?,draft_body=?,risk_level=?,updated_at=datetime('now')
        WHERE id=?`).bind(templateKey, rendered.subject, rendered.body, template.risk_level, id).run();
      await writeAudit("template_applied", "work_item", id, { template_key: templateKey });
      return NextResponse.json(await dashboardData());
    }

    if (action === "verify_test_usage") {
      const id = Number(body.id);
      const registeredEmail = String(body.registered_email || "").trim().toLowerCase();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(registeredEmail)) {
        return NextResponse.json({ error: "请先填写对方注册 VideoCoding 的有效邮箱" }, { status: 400 });
      }
      const evidence = await fetchProductUsage(registeredEmail);
      await applyProductUsageEvidence(id, evidence);
      return NextResponse.json({ usage_result: evidence, ...(await dashboardData()) });
    }

    if (action === "mark_test_usage") {
      const id = Number(body.id);
      const registeredEmail = String(body.registered_email || "").trim().toLowerCase();
      const status = String(body.usage_status || "") as ProductUsageEvidence["status"];
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(registeredEmail)) {
        return NextResponse.json({ error: "请先填写对方注册 VideoCoding 的有效邮箱" }, { status: 400 });
      }
      if (!["account_not_found", "account_only", "started", "completed"].includes(status)) {
        return NextResponse.json({ error: "无效的测试核验状态" }, { status: 400 });
      }
      const evidence: ProductUsageEvidence = {
        status,
        registeredEmail,
        totalTasks: status === "started" || status === "completed" ? Math.max(1, Number(body.total_tasks || 1)) : 0,
        activeTasks: status === "started" ? Math.max(0, Number(body.active_tasks || 0)) : 0,
        completedTasks: status === "completed" ? Math.max(1, Number(body.completed_tasks || 1)) : 0,
        source: "operator_verified",
      };
      evidence.totalTasks = Math.max(evidence.totalTasks, evidence.completedTasks);
      await applyProductUsageEvidence(id, evidence);
      return NextResponse.json({ usage_result: evidence, ...(await dashboardData()) });
    }

    if (action === "save_creator_details") {
      const id = Number(body.id);
      const item = await db.prepare("SELECT contact_id FROM outreach_work_items WHERE id=?").bind(id).first<{ contact_id: number | null }>();
      if (!item?.contact_id) return NextResponse.json({ error: "无法匹配联系人" }, { status: 400 });
      const money = (value: unknown) => {
        const parsed = Number(String(value ?? "").replace(/[^0-9.]/g, ""));
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
      };
      const dedicated = money(body.price_dedicated);
      const integration = money(body.price_integration);
      const shorts = money(body.price_shorts);
      const currency = String(body.currency ?? "").trim().slice(0, 8).toUpperCase() || null;
      const currencyLabel = currency || "currency unconfirmed";
      const turnaround = String(body.turnaround || "").slice(0, 160);
      const rateInfo = [
        dedicated === null ? "" : `Dedicated ${currencyLabel} ${dedicated}`,
        integration === null ? "" : `Integration ${currencyLabel} ${integration}`,
        shorts === null ? "" : `Shorts ${currencyLabel} ${shorts}`,
        turnaround ? `Turnaround ${turnaround}` : "",
      ].filter(Boolean).join(" · ");
      await db.prepare(`INSERT INTO creator_profiles(contact_id,workflow_stage,price_dedicated,price_integration,price_shorts,
        currency,turnaround,marketing_status,updated_at)
        VALUES(?,'marketing_review',?,?,?,?,?,'review_list',datetime('now'))
        ON CONFLICT(contact_id) DO UPDATE SET workflow_stage='marketing_review',price_dedicated=excluded.price_dedicated,
          price_integration=excluded.price_integration,price_shorts=excluded.price_shorts,currency=excluded.currency,
          turnaround=excluded.turnaround,marketing_status='review_list',updated_at=datetime('now')`)
        .bind(item.contact_id, dedicated, integration, shorts, currency, turnaround).run();
      await db.prepare(`UPDATE contacts SET rate_info=?,next_action='营销部评估报价与合作形式。',updated_at=datetime('now') WHERE id=?`)
        .bind(rateInfo, item.contact_id).run();
      await db.prepare(`UPDATE outreach_work_items SET workflow_stage='marketing_review',status='approved',updated_at=datetime('now'),resolved_by='operator'
        WHERE id=?`).bind(id).run();
      await writeAudit("creator_rate_saved", "contact", item.contact_id, { dedicated, integration, shorts, currency, turnaround });
      return NextResponse.json(await dashboardData());
    }

    if (action === "set_work_status") {
      const id = Number(body.id);
      const status = String(body.status || "");
      if (!allowedWorkStatuses.has(status)) return NextResponse.json({ error: "无效审核状态" }, { status: 400 });
      await db.prepare(`UPDATE outreach_work_items SET status=?,resolved_at=CASE WHEN ? IN ('ignored','rejected','sent') THEN datetime('now') ELSE NULL END,
        resolved_by='operator',updated_at=datetime('now') WHERE id=?`).bind(status, status, id).run();
      await writeAudit("work_status_changed", "work_item", id, { status });
      return NextResponse.json(await dashboardData());
    }

    if (action === "approve_membership") {
      const id = Number(body.id);
      const item = await db.prepare("SELECT contact_id FROM outreach_work_items WHERE id=?").bind(id).first<{ contact_id: number | null }>();
      if (!item?.contact_id) return NextResponse.json({ error: "无法匹配联系人" }, { status: 400 });
      const registeredEmail = String(body.registered_email || "").trim().toLowerCase();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(registeredEmail)) return NextResponse.json({ error: "请先填写有效的注册邮箱" }, { status: 400 });
      await db.prepare(`INSERT INTO creator_profiles(contact_id,workflow_stage,registered_email,membership_status,updated_at)
        VALUES(?,'membership_pending',?,'approved_pending_backend',datetime('now'))
        ON CONFLICT(contact_id) DO UPDATE SET workflow_stage='membership_pending',registered_email=excluded.registered_email,
          membership_status='approved_pending_backend',updated_at=datetime('now')`).bind(item.contact_id, registeredEmail).run();
      await db.prepare("UPDATE outreach_work_items SET status='approved',updated_at=datetime('now'),resolved_by='operator' WHERE id=?").bind(id).run();
      await writeAudit("membership_approved", "contact", item.contact_id, { registered_email: registeredEmail, execution: "pending_secure_backend_connection" });
      return NextResponse.json({ warning: "已批准，但安全会员后台连接尚未配置；系统不会提前发送开通成功通知。", ...(await dashboardData()) });
    }

    if (action === "confirm_membership_active") {
      const id = Number(body.id);
      const item = await db.prepare(`SELECT w.contact_id,e.subject,e.body_preview,e.body_text,e.sender_email,
        c.channel_name,c.email AS contact_email,c.reply_status,p.registered_email,p.membership_status
        FROM outreach_work_items w
        LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
        LEFT JOIN contacts c ON c.id=w.contact_id
        LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
        WHERE w.id=?`).bind(id).first<InboxRow & { registered_email: string | null; membership_status: string | null }>();
      if (!item?.contact_id || !item.registered_email) return NextResponse.json({ error: "请先批准并填写注册邮箱" }, { status: 409 });
      if (item.membership_status !== "approved_pending_backend") {
        return NextResponse.json({ error: "当前会员请求尚未处于待后台执行状态" }, { status: 409 });
      }
      const template = await db.prepare(`SELECT template_key,subject,body,risk_level FROM outreach_templates
        WHERE template_key='membership_activated' AND enabled=1`).first<TemplateRow>();
      if (!template) return NextResponse.json({ error: "会员开通通知模板不存在" }, { status: 500 });
      const rendered = renderTemplate(template, item, item.registered_email);
      await db.prepare(`UPDATE creator_profiles SET workflow_stage='testing',membership_status='active',
        membership_expires_at=date('now','+30 days'),test_status='wants_test',test_started_at=datetime('now'),updated_at=datetime('now')
        WHERE contact_id=?`).bind(item.contact_id).run();
      await db.prepare(`UPDATE outreach_work_items SET workflow_stage='testing',status='pending',template_key='membership_activated',
        subject=?,draft_body=?,risk_level='high',resolved_at=NULL,updated_at=datetime('now') WHERE id=?`)
        .bind(rendered.subject, rendered.body, id).run();
      await writeAudit("membership_backend_confirmed", "contact", item.contact_id, { registered_email: item.registered_email, duration_days: 30 });
      return NextResponse.json(await dashboardData());
    }

    if (action === "send_reply") {
      const senderBlock = await microsoftOutboundBlockReason(db);
      if (senderBlock) return NextResponse.json({ error: `微软发件账号受限，需管理员处理：${senderBlock}` }, { status: 423 });
      const id = Number(body.id);
      const item = await db.prepare(`SELECT w.*,e.id AS graph_message_id,e.conversation_id,e.received_at,e.event_type,
        p.membership_status,p.usage_verification_status,
        c.do_not_contact FROM outreach_work_items w
        LEFT JOIN inbox_events e ON e.id=w.inbox_event_id
        LEFT JOIN creator_profiles p ON p.contact_id=w.contact_id
        LEFT JOIN contacts c ON c.id=w.contact_id WHERE w.id=?`).bind(id).first<Record<string, unknown>>();
      if (!item) return NextResponse.json({ error: "审核事项不存在" }, { status: 404 });
      if (["bounce", "sender_restricted", "delivery_delayed", "delivery_failed", "automated"].includes(String(item.event_type))) {
        return NextResponse.json({ error: "投递报告或自动邮件不能作为博主邮件回复" }, { status: 409 });
      }
      if (String(item.graph_message_id || "").startsWith("gmail:")) {
        return NextResponse.json({ error: "此邮件来自 Google，必须使用原 Google 会话回复，不能提交至 Microsoft" }, { status: 409 });
      }
      if (item.status === "sent") return NextResponse.json({ error: "这封回复已经发送" }, { status: 409 });
      if (!["pending", "approved"].includes(String(item.status || ""))) {
        return NextResponse.json({ error: "该事项已经失效或处理完成，禁止发送旧草稿" }, { status: 409 });
      }
      if (!String(item.draft_body || "").trim()) return NextResponse.json({ error: "回复正文不能为空" }, { status: 400 });
      if (Number(item.do_not_contact || 0) === 1) {
        return NextResponse.json({ error: "联系人已拒绝或退订，禁止继续回复" }, { status: 409 });
      }
      if (item.conversation_id) {
        const laterOutbound = await db.prepare(`SELECT id FROM outreach_sent_messages
          WHERE conversation_id=? AND datetime(sent_at)>datetime(?) ORDER BY datetime(sent_at) DESC LIMIT 1`)
          .bind(String(item.conversation_id), String(item.received_at || "")).first<{ id: string }>();
        if (laterOutbound) {
          return NextResponse.json({ error: "该会话已经存在更晚的已发送邮件，禁止重复回复" }, { status: 409 });
        }
      }
      if (item.contact_id) {
        const latestInbound = await db.prepare(`SELECT id FROM inbox_events WHERE contact_id=?
          ORDER BY datetime(received_at) DESC,id DESC LIMIT 1`).bind(Number(item.contact_id)).first<{ id: string }>();
        if (latestInbound && String(latestInbound.id) !== String(item.graph_message_id || "")) {
          return NextResponse.json({ error: "该草稿不是基于最新来信生成；请先刷新会话并重新判断意图" }, { status: 409 });
        }
      }
      if (item.template_key) {
        const templatePolicy = await db.prepare(`SELECT enabled,approval_status FROM outreach_templates WHERE template_key=?`)
          .bind(String(item.template_key)).first<{ enabled: number; approval_status: string }>();
        if (!templatePolicy || Number(templatePolicy.enabled) !== 1 || templatePolicy.approval_status !== "approved") {
          return NextResponse.json({ error: "候选模板必须先由人工审核并启用，当前禁止发送" }, { status: 409 });
        }
      }
      const membershipNoticeReady = item.template_key === "membership_activated" && item.membership_status === "active";
      if (item.kind === "membership_request" && !membershipNoticeReady && !["ask_registered_email", "account_not_found"].includes(String(item.template_key || ""))) {
        return NextResponse.json({ error: "会员开通成功通知必须等后台确认后才能发送" }, { status: 409 });
      }
      if (item.template_key === "ask_rate" && item.usage_verification_status !== "completed") {
        return NextResponse.json({ error: "询价前必须确认产品后台至少有一个成功完成的视频任务" }, { status: 409 });
      }
      const token = await refreshAccessToken(db);
      const graphId = encodeURIComponent(String(item.graph_message_id || ""));
      if (["coupon_test", "coupon_test_t2v_knowledge_v1", "coupon_test_rate_received"].includes(String(item.template_key || ""))) {
        const richResponse = await fetch(`https://graph.microsoft.com/v1.0/me/messages/${graphId}/reply`, {
          method: "POST",
          headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
          body: JSON.stringify({
            message: {
              body: { contentType: "HTML", content: couponBodyToHtml(String(item.draft_body)) },
            },
          }),
        });
        if (!richResponse.ok) throw new Error(`Outlook发送格式化回复失败：${richResponse.status}`);
      } else {
        const graphResponse = await fetch(`https://graph.microsoft.com/v1.0/me/messages/${graphId}/reply`, {
          method: "POST",
          headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
          body: JSON.stringify({ comment: String(item.draft_body) }),
        });
        if (!graphResponse.ok) throw new Error(`Outlook回复失败：${graphResponse.status}`);
      }
      await db.prepare("UPDATE outreach_work_items SET status='sent',resolved_at=datetime('now'),resolved_by='operator',updated_at=datetime('now') WHERE id=?").bind(id).run();
      if (item.contact_id && ["coupon_test", "coupon_test_t2v_knowledge_v1", "coupon_test_rate_received"].includes(String(item.template_key || ""))) {
        await db.prepare(`INSERT INTO creator_profiles(contact_id,workflow_stage,coupon_code,coupon_sent_at,test_status,updated_at)
          VALUES(?,'testing',?,datetime('now'),'wants_test',datetime('now'))
          ON CONFLICT(contact_id) DO UPDATE SET workflow_stage='testing',coupon_code=excluded.coupon_code,
            coupon_sent_at=datetime('now'),test_status='wants_test',updated_at=datetime('now')`)
          .bind(Number(item.contact_id), COUPON).run();
      }
      if (item.contact_id && item.template_key === "ask_rate") {
        await db.prepare(`UPDATE creator_profiles SET workflow_stage='price_needed',updated_at=datetime('now')
          WHERE contact_id=? AND usage_verification_status='completed'`)
          .bind(Number(item.contact_id)).run();
      }
      await writeAudit("reply_sent", "work_item", id, { template_key: item.template_key });
      return NextResponse.json(await dashboardData());
    }

    if (action === "analyze") {
      const bindings = env as unknown as Bindings;
      if (!bindings.OPENAI_API_KEY) {
        const id = Number(body.id);
        await db.prepare(`UPDATE outreach_work_items SET confidence=NULL,updated_at=datetime('now')
          WHERE id=? AND status='pending'`).bind(id).run();
        await writeAudit("local_codex_analysis_queued", "work_item", id, {});
        return NextResponse.json({ analysis: { configured: false, queued_for_local_codex: true }, ...(await dashboardData()) });
      }
      return NextResponse.json({ analysis: await analyzeWithAI(Number(body.id)), ...(await dashboardData()) });
    }

    return NextResponse.json({ error: "未知操作" }, { status: 400 });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "运营操作失败" }, { status: 500 });
  }
}
