"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type OperationsSummary = {
  pending: number;
  actionable: number;
  actionable_creators: number;
  raw_pending: number;
  high_risk: number;
  membership_requests: number;
  price_reviews: number;
  template_matched: number;
  human_only: number;
  ai_routed: number;
  context_reviewed: number;
  context_pending_review: number;
  rules_only: number;
  claimed_unverified: number;
  account_only: number;
  started_incomplete: number;
  real_testers: number;
  sent: number;
  ignored: number;
  coupon_mode: string;
  ai_configured: boolean;
  ai_provider: string;
  ai_model: string;
  usage_sync_configured: boolean;
  data_agent_status: string;
  data_agent_last_run_at: string | null;
  coupons_redeemed: number;
  memberships_granted: number;
  followups_pending: number;
  followups_due: number;
  followups_sent_today: number;
  followups_manual_required: number;
  promotion_waiting: number;
  promotion_quote_ready: number;
  promotion_needs_quote: number;
  promotion_approved: number;
  efficiency_pilot_started_at: string;
  efficiency_baseline_actionable: number;
  efficiency_baseline_resolved_24h: number;
  efficiency_target_resolved_24h: number;
  efficiency_baseline_fast_lane_pct: number;
  efficiency_target_fast_lane_pct: number;
  efficiency_baseline_over_72h: number;
  efficiency_target_over_72h: number;
  reply_inbound_24h: number;
  reply_resolved_24h: number;
  reply_flow_ratio_pct: number;
  actionable_over_24h: number;
  actionable_over_72h: number;
  fast_lane: number;
  fast_lane_pct: number;
  blocked_campaign_policy: number;
  blocked_product_facts: number;
  blocked_commercial_terms: number;
  stuck_sending: number;
  delivery_unknown: number;
  send_failures_24h: number;
  state_repeats_15m: number;
};

type PromotionCreator = {
  contact_id: number;
  creator_email: string;
  registered_email: string | null;
  channel_name: string;
  channel_url: string | null;
  subscribers: number | null;
  category: string | null;
  country: string | null;
  language: string | null;
  test_status: string;
  completed_videos: number;
  usage_verified_at: string | null;
  interest_status: string;
  interest_evidence: string;
  price_dedicated: number | null;
  price_integration: number | null;
  price_shorts: number | null;
  currency: string | null;
  turnaround: string | null;
  rate_info: string | null;
  quote_source_text: string | null;
  quote_received_at: string | null;
  promotion_formats: string | null;
  list_status: string;
  added_at: string;
  updated_at: string;
};

type WorkItem = {
  id: number;
  kind: string;
  workflow_stage: string;
  status: string;
  risk_level: string;
  confidence: number | null;
  template_key: string | null;
  subject: string;
  draft_body: string;
  extracted_json: string;
  proposed_action_json: string;
  decision_reason: string;
  channel_name: string | null;
  channel_url: string | null;
  email: string | null;
  sender_email: string | null;
  received_at: string | null;
  body_preview: string | null;
  body_text: string | null;
  registered_email: string | null;
  membership_status: string | null;
  test_status: string | null;
  marketing_status: string | null;
  product_user_id: string | null;
  usage_verification_status: string | null;
  usage_total_tasks: number | null;
  usage_active_tasks: number | null;
  usage_completed_tasks: number | null;
  usage_last_login_at: string | null;
  usage_last_task_at: string | null;
  usage_verified_at: string | null;
  usage_source: string | null;
  coupon_redemption_status: string | null;
  coupon_redeemed_code: string | null;
  coupon_redemption_verified_at: string | null;
  product_sync_error: string | null;
  efficiency_priority: number;
};

type OutreachTemplate = {
  template_key: string;
  name: string;
  stage: string;
  subject: string;
  body: string;
  risk_level: string;
  enabled: number;
  version: number;
  approval_status: "approved" | "pending_review" | "rejected";
  intent_signature: string | null;
  context_requirements_json: string;
  source_work_item_id: number | null;
  creation_reason: string;
  example_count: number;
  updated_at: string;
};

type AutomationRule = {
  rule_key: string;
  name: string;
  mode: string;
  risk_level: string;
  description: string;
  enabled: number;
  updated_at: string;
};

type LeadSource = {
  id: number;
  name: string;
  source_type: string;
  location: string;
  policy_status: string;
  enabled: number;
  discovered_count: number;
  imported_count: number;
  last_run_at: string | null;
  last_error: string | null;
};

type StageCount = { workflow_stage: string; count: number };

type DiscoveryState = {
  worker_key: string;
  enabled: number;
  status: string;
  current_job_id: number | null;
  active_tier: number | null;
  current_query: string | null;
  heartbeat_at: string | null;
  last_completed_at: string | null;
  last_error: string | null;
};

type DiscoveryTier = {
  tier: number;
  total_jobs: number;
  initial_pass_complete: number;
  error_jobs: number;
  videos_scanned: number;
  channels_found: number;
  public_emails_found: number;
  contacts_imported: number;
};

type DiscoveryJob = {
  id: number;
  tier: number;
  tier_key: string;
  query: string;
  language: string;
  status: string;
  run_count: number;
  videos_scanned: number;
  channels_found: number;
  public_emails_found: number;
  contacts_imported: number;
  last_completed_at: string | null;
  next_run_at: string;
  last_error: string | null;
};

type DiscoveryChannel = {
  channel_key: string;
  channel_name: string;
  channel_url: string;
  best_tier: number;
  matched_query: string;
  matched_video_title: string | null;
  matched_video_url: string | null;
  language: string;
  scan_status: string;
  email_count: number;
  last_seen_at: string;
};

type DiscoveryData = {
  state: DiscoveryState | null;
  workers: DiscoveryState[];
  online: boolean;
  learning: { measured_runs: number; eligible_contacts: number; channels: number; duplicates: number;
    active_channels: number; inactive_channels: number; unknown_activity: number } | null;
  decisions: { worker_key: string; query: string; selection_mode: string; selection_reason: string; started_at: string; status: string }[];
  summary: {
    total_jobs: number;
    initial_pass_complete: number;
    queued_jobs: number;
    error_jobs: number;
    videos_scanned: number;
    channels_found: number;
    public_emails_found: number;
    contacts_imported: number;
    master_contacts: number;
    unique_public_emails: number;
    new_unique_contacts: number;
    new_last_24h: number;
    new_last_6h: number;
    daily_target: number;
    daily_progress_percent: number;
    projected_daily: number;
    active_workers: number;
    existing_duplicates: number;
    pending_validation: number;
    target_new_contacts: number;
    target_remaining: number;
    target_progress_percent: number;
  };
  tiers: DiscoveryTier[];
  jobs: DiscoveryJob[];
  recent_channels: DiscoveryChannel[];
  coverage_note: string;
};

type OperationsData = {
  summary: OperationsSummary;
  work_items: WorkItem[];
  promotion_creators: PromotionCreator[];
  templates: OutreachTemplate[];
  rules: AutomationRule[];
  sources: LeadSource[];
  stages: StageCount[];
  discovery: DiscoveryData;
  warning?: string;
  error?: string;
};

const kindLabels: Record<string, string> = {
  coupon_reply: "愿意测试 / 发 Coupon",
  membership_request: "不愿绑卡 / 会员处理",
  product_question: "产品或使用问题",
  test_feedback: "旧记录 · 待核验测试",
  test_claim: "声称已测试 · 待核验",
  test_planned: "准备测试 · 尚未完成",
  price_and_test_claim: "已报价格 · 测试待核验",
  test_account_missing: "未找到测试账号",
  test_not_started: "只有账号/登录 · 未测试",
  test_incomplete: "已开始 · 未完成",
  test_verified: "真实测试已确认",
  price_received: "收到报价",
  opt_out: "拒绝或退订",
  ignore: "无需回复",
  human_review: "人工判断",
};

const stageLabels: Record<string, string> = {
  lead: "新线索",
  contacted: "已联系",
  replied: "已回复",
  coupon_ready: "待发 Coupon",
  membership_email_needed: "待注册邮箱",
  membership_pending: "待开会员",
  testing: "测试中",
  test_claimed: "声称已测试 · 待核验",
  test_login_only: "只有账号/登录",
  test_started: "已开始未完成",
  test_completed: "测试完成",
  price_needed: "待询价",
  price_received: "收到报价",
  price_received_unverified: "已报价 · 测试待核验",
  marketing_review: "营销评估名单",
  human_review_blocked: "上下文不完整 · 人工处理",
  delivery_reconciliation: "投递结果待核对",
  product_question: "产品问题",
  product_issue: "产品问题 · 需具体回复",
  campaign_details: "合作细节待确认",
  contract_review: "合同与付款条款",
  privacy_review: "隐私与条款审核",
  complaint_review: "投诉处理",
  template_candidate_review: "新模板待审核",
  closed: "已结束",
  bounced: "退信",
};

const usageLabels: Record<string, string> = {
  not_checked: "尚未核验",
  account_not_found: "后台未找到账号",
  account_only: "只有账号/登录，0 个任务",
  started: "有任务，但 0 个完成",
  completed: "真实测试 · 有完成视频",
};

const modeLabels: Record<string, string> = {
  manual: "必须人工处理",
  draft: "自动准备草稿",
  auto: "自动静默处理",
};

const efficiencyPriorityLabels: Record<number, string> = {
  1: "P1 · 推广/报价推进",
  2: "P2 · 已有模板快线",
  3: "P3 · 超 72 小时",
  4: "P4 · 新回复深度判断",
  99: "已完成",
};

function displayDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

type DecisionContext = {
  secondary_intents?: string[];
  current_request_summary?: string;
  conversation_state?: string;
  evidence?: Array<{ intent?: string; source?: string; text?: string }>;
  contradictions?: string[];
  missing_context?: string[];
  context_complete?: boolean;
  template_gap?: boolean;
  template_gap_reason?: string | null;
};

function decisionContext(value: string): DecisionContext | null {
  try {
    const parsed = JSON.parse(value) as DecisionContext;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function useOperationsData() {
  const [data, setData] = useState<OperationsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch("/api/operations", { cache: "no-store" });
      const payload = await response.json() as OperationsData;
      if (!response.ok) throw new Error(payload.error || "无法读取自动化工作台");
      setData(payload);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取自动化工作台");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(reload, 0);
    return () => window.clearTimeout(timer);
  }, [reload]);

  const post = useCallback(async (key: string, payload: Record<string, unknown>) => {
    try {
      setBusy(key);
      setNotice("");
      setError("");
      const response = await fetch("/api/operations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json() as OperationsData & { created?: number; analysis?: { configured?: boolean; analyzed?: number; failed?: number } };
      if (!response.ok) throw new Error(result.error || "操作失败");
      setData(result);
      if (result.warning) setNotice(result.warning);
      else if (payload.action === "refresh_queue") setNotice(`审核队列已刷新，新增 ${result.created ?? 0} 项。`);
      else if (payload.action === "analyze_pending" && result.analysis?.configured === false) setNotice(`已把新会话放入队列；本机 Codex 后台会继续批量选择白名单模板。`);
      else if (payload.action === "analyze_pending") setNotice(`已新增 ${result.created ?? 0} 项，并由 AI 分流 ${result.analysis?.analyzed ?? 0} 项；失败 ${result.analysis?.failed ?? 0} 项。`);
      else if (payload.action === "analyze" && result.analysis?.configured === false) setNotice("已放入本机 Codex 重新判断队列；后台完成后会显示模板和置信度。");
      else setNotice("已保存。所有变更都已写入审计记录。");
      return result;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
      return false;
    } finally {
      setBusy("");
    }
  }, []);

  return { data, loading, busy, notice, error, reload, post };
}

function Feedback({ notice, error }: { notice: string; error: string }) {
  if (!notice && !error) return null;
  return <div className={`ops-feedback ${error ? "ops-feedback-error" : ""}`}>{error || notice}</div>;
}

function EmptyState({ title, copy }: { title: string; copy: string }) {
  return <section className="ops-empty"><strong>{title}</strong><p>{copy}</p></section>;
}

export function OperationsCenter() {
  const { data, loading, busy, notice, error, reload, post } = useOperationsData();
  const [filter, setFilter] = useState("priority");
  const [openId, setOpenId] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<number, { subject: string; body: string }>>({});
  const [registeredEmails, setRegisteredEmails] = useState<Record<number, string>>({});
  const [rates, setRates] = useState<Record<number, { dedicated: string; integration: string; shorts: string; currency: string; turnaround: string }>>({});

  useEffect(() => {
    if (!data) return;
    setDrafts((current) => {
      const next = { ...current };
      for (const item of data.work_items) next[item.id] ??= { subject: item.subject || "", body: item.draft_body || "" };
      return next;
    });
    setRegisteredEmails((current) => {
      const next = { ...current };
      for (const item of data.work_items) next[item.id] ??= item.registered_email || item.email || item.sender_email || "";
      return next;
    });
  }, [data]);

  const rows = useMemo(() => {
    if (!data) return [];
    if (filter === "all") return data.work_items;
    if (filter === "priority") return data.work_items.filter((item) => ["pending", "approved"].includes(item.status) && item.efficiency_priority <= 2);
    if (filter === "fast") return data.work_items.filter((item) => ["pending", "approved"].includes(item.status) && item.template_key && item.kind !== "human_review");
    if (filter === "stale") return data.work_items.filter((item) => item.status === "pending" && item.received_at && Date.now() - new Date(item.received_at).getTime() >= 72 * 60 * 60 * 1000);
    if (filter === "campaign_policy") return data.work_items.filter((item) => item.status === "pending" && item.decision_reason.startsWith("对方同时询问预算、brief、交付、时间线或合作细节"));
    if (filter === "product_facts") return data.work_items.filter((item) => item.status === "pending" && item.decision_reason.startsWith("对方报告产品问题或负面测试反馈"));
    if (filter === "commercial_terms") return data.work_items.filter((item) => item.status === "pending" && item.decision_reason.startsWith("对方提出付款、合同、订阅或外部流程条件"));
    if (filter === "high") return data.work_items.filter((item) => item.status === "pending" && item.risk_level === "high");
    return data.work_items.filter((item) => item.status === filter);
  }, [data, filter]);

  if (loading && !data) return <section className="ops-loading">正在装载审核工作台…</section>;
  if (!data) return <EmptyState title="工作台暂时不可用" copy={error || "请稍后重试。"} />;

  const throughputProgress = Math.min(100, Math.round((data.summary.reply_resolved_24h / Math.max(1, data.summary.efficiency_target_resolved_24h)) * 100));
  const fastLaneProgress = Math.min(100, Math.round((data.summary.fast_lane_pct / Math.max(0.1, data.summary.efficiency_target_fast_lane_pct)) * 100));
  const standardizableBlockers = data.summary.blocked_campaign_policy + data.summary.blocked_product_facts + data.summary.blocked_commercial_terms;
  const guardrailsHealthy = data.summary.stuck_sending === 0 && data.summary.send_failures_24h === 0 &&
    data.summary.state_repeats_15m === 0 && data.summary.delivery_unknown === 0;

  const saveDraft = (item: WorkItem) => post(`draft-${item.id}`, {
    action: "save_draft",
    id: item.id,
    subject: drafts[item.id]?.subject ?? item.subject,
    draft_body: drafts[item.id]?.body ?? item.draft_body,
  });

  const applyTemplate = async (item: WorkItem, templateKey: string) => {
    const result = await post(`template-${item.id}`, { action: "apply_template", id: item.id, template_key: templateKey });
    if (!result) return;
    const updated = result.work_items.find((row) => row.id === item.id);
    if (updated) setDrafts((current) => ({ ...current, [item.id]: { subject: updated.subject, body: updated.draft_body } }));
  };

  const confirmMembership = async (item: WorkItem) => {
    const result = await post(`member-done-${item.id}`, { action: "confirm_membership_active", id: item.id });
    if (!result) return;
    const updated = result.work_items.find((row) => row.id === item.id);
    if (updated) setDrafts((current) => ({ ...current, [item.id]: { subject: updated.subject, body: updated.draft_body } }));
  };

  const updateDraftFromResult = (itemId: number, result: OperationsData | false) => {
    if (!result) return;
    const updated = result.work_items.find((row) => row.id === itemId);
    if (updated) setDrafts((current) => ({ ...current, [itemId]: { subject: updated.subject, body: updated.draft_body } }));
  };

  const verifyTestUsage = async (item: WorkItem) => {
    const result = await post(`usage-${item.id}`, {
      action: "verify_test_usage",
      id: item.id,
      registered_email: registeredEmails[item.id] || "",
    });
    updateDraftFromResult(item.id, result);
  };

  const markTestUsage = async (item: WorkItem, usageStatus: "account_not_found" | "account_only" | "started" | "completed") => {
    if (usageStatus === "completed" && !window.confirm("确认产品后台至少显示 1 个成功完成的视频任务？\n\n仅登录、访问页面或创建未完成任务都不能标为真实测试。")) return;
    const result = await post(`usage-${item.id}`, {
      action: "mark_test_usage",
      id: item.id,
      registered_email: registeredEmails[item.id] || "",
      usage_status: usageStatus,
    });
    updateDraftFromResult(item.id, result);
  };

  const sendReply = async (item: WorkItem) => {
    const saved = await saveDraft(item);
    if (!saved) return;
    const confirmed = window.confirm(`确认现在通过 Outlook 回复 ${item.channel_name || item.sender_email || "这位博主"}？\n\n系统会原样发送你刚刚检查过的模板内容。`);
    if (!confirmed) return;
    await post(`send-${item.id}`, { action: "send_reply", id: item.id });
  };

  return (
    <div className="page-stack ops-page">
      <section className="ops-safety-banner">
        <div><span className="section-kicker">CONTEXT-FIRST REPLY CONTROL</span><h2>先理解完整会话和真实状态，再决定是否使用或创建模板。</h2></div>
        <div className="ops-safety-state"><i /><strong>关键事项人工审核</strong><span>Coupon：{data.summary.coupon_mode === "auto" ? "有意向后 30 分钟自动发送" : "先审核"}</span></div>
      </section>

      <section className="efficiency-pilot" aria-label="14 天效率翻倍实验">
        <div className="efficiency-pilot-heading">
          <div><span className="section-kicker">14-DAY 2× EFFICIENCY PILOT</span><h3>效率翻倍实验已启动</h3><p>翻倍的是有效闭环，不是盲目翻倍发信量。基线锁定于 {displayDate(data.summary.efficiency_pilot_started_at)}。</p></div>
          <span className={`efficiency-health ${guardrailsHealthy ? "healthy" : "attention"}`}>{guardrailsHealthy ? "安全护栏正常" : "有护栏项待处理"}</span>
        </div>
        <div className="efficiency-kpis">
          <article>
            <span>24h 有效闭环</span><strong>{data.summary.reply_resolved_24h} <small>/ 目标 {data.summary.efficiency_target_resolved_24h}</small></strong>
            <div className="efficiency-progress"><i style={{ width: `${throughputProgress}%` }} /></div>
            <p>基线 {data.summary.efficiency_baseline_resolved_24h} 个会话</p>
          </article>
          <article>
            <span>24h 流入 / 闭环率</span><strong>{data.summary.reply_inbound_24h} <small>/ {data.summary.reply_flow_ratio_pct}%</small></strong>
            <p>闭环率达到 100% 后，积压才不再增长</p>
          </article>
          <article>
            <span>完整模板快线</span><strong>{data.summary.fast_lane_pct}% <small>/ 目标 {data.summary.efficiency_target_fast_lane_pct}%</small></strong>
            <div className="efficiency-progress"><i style={{ width: `${fastLaneProgress}%` }} /></div>
            <p>{data.summary.fast_lane} 个已有完整模板</p>
          </article>
          <article>
            <span>超 72h 积压</span><strong>{data.summary.actionable_over_72h} <small>/ 目标 ≤ {data.summary.efficiency_target_over_72h}</small></strong>
            <p>基线 {data.summary.efficiency_baseline_over_72h} 个，14 天内减半</p>
          </article>
        </div>
        <div className="efficiency-guardrails">
          <span className={data.summary.stuck_sending === 0 ? "ok" : "bad"}>发送卡死 <strong>{data.summary.stuck_sending}</strong></span>
          <span className={data.summary.state_repeats_15m === 0 ? "ok" : "bad"}>15m 重复状态 <strong>{data.summary.state_repeats_15m}</strong></span>
          <span className={data.summary.send_failures_24h === 0 ? "ok" : "bad"}>24h 发送失败 <strong>{data.summary.send_failures_24h}</strong></span>
          <span className={data.summary.delivery_unknown === 0 ? "ok" : "warn"}>历史投递待核 <strong>{data.summary.delivery_unknown}</strong></span>
        </div>
        <div className="efficiency-blockers">
          <div><strong>{standardizableBlockers}</strong><span>个会话卡在“缺业务事实”，不能靠模板猜答。点击分类查看原邮件。</span></div>
          <button onClick={() => setFilter("campaign_policy")}><strong>{data.summary.blocked_campaign_policy}</strong><span>预算 / brief / 交付</span></button>
          <button onClick={() => setFilter("product_facts")}><strong>{data.summary.blocked_product_facts}</strong><span>具体产品问题</span></button>
          <button onClick={() => setFilter("commercial_terms")}><strong>{data.summary.blocked_commercial_terms}</strong><span>付款 / 合同条件</span></button>
        </div>
      </section>

      <section className="ops-metrics">
        <article><span>真实待处理</span><strong>{data.summary.actionable}</strong><small>{data.summary.actionable_creators} 位博主 · 原始 pending {data.summary.raw_pending}</small></article>
        <article className="ops-metric-risk"><span>高风险事项</span><strong>{data.summary.high_risk}</strong><small>会员、报价、产品问题</small></article>
        <article><span>会员请求</span><strong>{data.summary.membership_requests}</strong><small>必须核对注册邮箱</small></article>
        <article><span>报价待记录</span><strong>{data.summary.price_reviews}</strong><small>不会自动接受报价</small></article>
        <article><span>已匹配固定模板</span><strong>{data.summary.template_matched}</strong><small>需核对全部诉求及发送权限</small></article>
        <article className="ops-metric-risk"><span>必须人工判断</span><strong>{data.summary.human_only}</strong><small>低置信度或模板外内容</small></article>
        <article><span>新规则已复核</span><strong>{data.summary.context_reviewed ?? 0}</strong><small>待复核 {data.summary.context_pending_review ?? data.summary.actionable} · 发送前再次核对上下文</small></article>
        <article><span>规则先行</span><strong>{data.summary.rules_only}</strong><small>AI 未处理时仍安全归类</small></article>
      </section>

      <section className="usage-metrics" aria-label="真实测试核验统计">
        <article><span>声称已测试 · 待核验</span><strong>{data.summary.claimed_unverified}</strong><small>不会直接进入询价</small></article>
        <article><span>只有账号或登录</span><strong>{data.summary.account_only}</strong><small>视频任务数为 0</small></article>
        <article><span>开始但未完成</span><strong>{data.summary.started_incomplete}</strong><small>需要询问卡点</small></article>
        <article className="usage-metric-verified"><span>真实测试者</span><strong>{data.summary.real_testers}</strong><small>至少 1 个成功视频</small></article>
        <article><span>数据总控 Agent</span><strong>{data.summary.usage_sync_configured ? "已连接" : "待连接"}</strong><small>{data.summary.data_agent_status} · {displayDate(data.summary.data_agent_last_run_at)}</small></article>
      </section>

      <section className="promotion-panel" aria-label="待推广博主名单">
        <div className="panel-heading">
          <div><span className="section-kicker">READY FOR CAMPAIGN</span><h3>待推广博主名单</h3><p>只收录真实完成测试、明确愿意推广的人；报价与原始证据会一直保留。</p></div>
          <div className="promotion-summary">
            <span><strong>{data.summary.promotion_waiting}</strong> 位等待推广</span>
            <span><strong>{data.summary.followups_due}</strong> 封已到期跟进</span>
            <span><strong>{data.summary.followups_sent_today}</strong> 封今日已跟进</span>
            {data.summary.followups_manual_required > 0 && <span><strong>{data.summary.followups_manual_required}</strong> 封原个人邮箱需人工</span>}
          </div>
        </div>
        <div className="promotion-list">
          {data.promotion_creators.length === 0 && <EmptyState title="还没有待推广博主" copy="真实测试完成且明确愿意推广后，会自动进入这里。" />}
          {data.promotion_creators.map((creator) => {
            const currency = creator.currency || "币种待确认";
            const prices = [
              creator.price_dedicated === null ? "" : `独立视频 ${currency} ${creator.price_dedicated}`,
              creator.price_integration === null ? "" : `口播植入 ${currency} ${creator.price_integration}`,
              creator.price_shorts === null ? "" : `Shorts ${currency} ${creator.price_shorts}`,
            ].filter(Boolean);
            return (
              <article key={creator.contact_id}>
                <div className="promotion-person">
                  <span className="promotion-state">{creator.list_status === "waiting" ? "等待推广" : creator.list_status}</span>
                  {creator.channel_url ? <a href={creator.channel_url} target="_blank" rel="noreferrer">{creator.channel_name}</a> : <strong>{creator.channel_name}</strong>}
                  <small>{creator.creator_email}{creator.registered_email && creator.registered_email !== creator.creator_email ? ` · 注册 ${creator.registered_email}` : ""}</small>
                </div>
                <div><small>真实测试</small><strong>完成 {creator.completed_videos} 个视频</strong><span>{displayDate(creator.usage_verified_at)}</span></div>
                <div><small>合作形式与报价</small><strong>{prices.join(" · ") || creator.rate_info || "已愿意推广 · 待报价"}</strong><span>{creator.promotion_formats || "形式待确认"}{creator.turnaround ? ` · ${creator.turnaround}` : ""}</span></div>
                <details>
                  <summary>查看原始报价 / 推广意向</summary>
                  <pre>{(creator.quote_source_text || creator.interest_evidence || "暂无原文").slice(0, 3000)}</pre>
                </details>
              </article>
            );
          })}
        </div>
      </section>

      <Feedback notice={notice} error={error} />

      <section className="ops-toolbar">
        <div className="ops-filters">
          {[["priority", "今日优先"], ["fast", "模板快线"], ["stale", "超 72h"], ["pending", "全部待审"], ["high", "只看高风险"], ["approved", "已批准"], ["sent", "已发送"], ["ignored", "已忽略"], ["all", "全部"]].map(([key, label]) => (
            <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>
          ))}
        </div>
        <div className="ops-toolbar-actions">
          <button className="sync-button" disabled={busy === "refresh"} onClick={() => post("refresh", { action: "refresh_queue" })}>
            {busy === "refresh" ? "刷新中…" : "扫描全部新回复"}
          </button>
          <button className="sync-button" disabled={busy === "route-all"} onClick={() => post("route-all", { action: "analyze_pending", limit: 50 })}>
            {busy === "route-all" ? "分流中…" : "AI 批量选择模板"}
          </button>
        </div>
      </section>

      <section className="ops-queue">
        {rows.length === 0 && <EmptyState title="这个视图没有事项" copy="新回复同步后会自动进入这里；自动回复和退订会被静默归档。" />}
        {rows.map((item) => {
          const expanded = openId === item.id;
          const draft = drafts[item.id] ?? { subject: item.subject, body: item.draft_body };
          const incoming = item.body_text || item.body_preview || "暂无正文";
          const itemRates = rates[item.id] ?? { dedicated: "", integration: "", shorts: "", currency: "", turnaround: "" };
          const context = decisionContext(item.extracted_json);
          const selectedTemplate = data.templates.find((candidate) => candidate.template_key === item.template_key);
          const candidateTemplateBlocked = selectedTemplate?.approval_status === "pending_review" || selectedTemplate?.approval_status === "rejected";
          return (
            <article className={`ops-work-card risk-${item.risk_level}`} key={item.id}>
              <button className="ops-work-summary" onClick={() => setOpenId(expanded ? null : item.id)}>
                <span className={`ops-risk ops-risk-${item.risk_level}`}>{item.risk_level === "high" ? "高风险" : item.risk_level === "medium" ? "需审核" : "低风险"}</span>
                <span className="ops-person"><strong>{item.channel_name || item.sender_email || "未匹配博主"}</strong><small>{efficiencyPriorityLabels[item.efficiency_priority] || "待排序"} · {item.email || item.sender_email || "—"}</small></span>
                <span className="ops-intent"><strong>{kindLabels[item.kind] || item.kind}</strong><small>{stageLabels[item.workflow_stage] || item.workflow_stage}</small></span>
                <span className={`ops-status ops-status-${item.status}`}>{item.status === "pending" ? "待审核" : item.status === "sent" ? "已发送" : item.status === "approved" ? "已批准" : item.status === "ignored" ? "已忽略" : item.status}</span>
                <span className="ops-chevron">{expanded ? "−" : "+"}</span>
              </button>

              {expanded && (
                <div className="ops-work-detail">
                  <div className="ops-context-grid">
                    <div className="ops-incoming">
                      <div className="ops-block-heading"><span>对方原始回复</span><small>{displayDate(item.received_at)}</small></div>
                      <h4>{item.subject || "无主题"}</h4>
                      <pre>{incoming}</pre>
                    </div>
                    <div className="ops-decision">
                      <div className="ops-block-heading"><span>系统判断</span><small>{item.confidence === null ? "规则判断" : `AI ${(item.confidence * 100).toFixed(0)}%`}</small></div>
                      <strong>{kindLabels[item.kind] || item.kind}</strong>
                      <p>{item.decision_reason}</p>
                      {context && (
                        <details>
                          <summary>查看意图与上下文证据</summary>
                          <p><strong>当前诉求：</strong>{context.current_request_summary || "未提供摘要"}</p>
                          <p><strong>全部意图：</strong>{context.secondary_intents?.join(" · ") || "未提供"}</p>
                          <p><strong>会话关系：</strong>{context.conversation_state || "未判断"} · {context.context_complete ? "上下文完整" : "上下文不完整"}</p>
                          {context.missing_context?.length ? <p><strong>缺失信息：</strong>{context.missing_context.join("；")}</p> : null}
                          {context.contradictions?.length ? <p><strong>冲突：</strong>{context.contradictions.join("；")}</p> : null}
                          {context.evidence?.length ? <ul>{context.evidence.map((evidence, index) => <li key={`${evidence.intent}-${index}`}><strong>{evidence.intent}</strong> · {evidence.source}：{evidence.text}</li>)}</ul> : null}
                        </details>
                      )}
                      <dl>
                        <div><dt>当前阶段</dt><dd>{stageLabels[item.workflow_stage] || item.workflow_stage}</dd></div>
                        <div><dt>测试状态</dt><dd>{item.test_status || "未记录"}</dd></div>
                        <div><dt>真实使用核验</dt><dd>{usageLabels[item.usage_verification_status || "not_checked"] || item.usage_verification_status}</dd></div>
                        <div><dt>视频任务</dt><dd>{Number(item.usage_total_tasks || 0)} 个 / 完成 {Number(item.usage_completed_tasks || 0)} 个</dd></div>
                        <div><dt>Coupon 核验</dt><dd>{item.coupon_redemption_status === "redeemed" ? `已使用 ${item.coupon_redeemed_code || ""}` : item.coupon_redemption_status === "discount_applied_unattributed" ? "已使用折扣（代码待归因）" : item.coupon_redemption_status === "not_redeemed" ? "未使用" : "待同步"}</dd></div>
                        <div><dt>会员状态</dt><dd>{item.membership_status || "未处理"}</dd></div>
                      </dl>
                      <button className="ops-text-button" disabled={busy === `ai-${item.id}`} onClick={() => post(`ai-${item.id}`, { action: "analyze", id: item.id })}>
                        {data.summary.ai_configured ? "用本机 Codex 重新判断模板" : "加入本机 Codex 判断队列"}
                      </button>
                    </div>
                  </div>

                  {(item.kind.startsWith("test_") || item.kind === "test_feedback" || item.kind === "price_and_test_claim") && (
                    <div className={`test-verification-panel usage-${item.usage_verification_status || "not_checked"}`}>
                      <div className="test-verification-heading">
                        <div>
                          <strong>核验真实测试</strong>
                          <p>只有产品后台出现至少 1 个成功完成的视频任务，才标记为真实测试并切换到询价话术。登录、访问页面或 0 个完成任务都不算。</p>
                        </div>
                        <span>{usageLabels[item.usage_verification_status || "not_checked"] || item.usage_verification_status}</span>
                      </div>
                      <div className="test-evidence-grid">
                        <label>注册邮箱<input value={registeredEmails[item.id] ?? ""} placeholder="对方注册 VideoCoding 的邮箱" onChange={(event) => setRegisteredEmails((current) => ({ ...current, [item.id]: event.target.value }))} /></label>
                        <div><small>最后登录</small><strong>{displayDate(item.usage_last_login_at)}</strong></div>
                        <div><small>任务总数</small><strong>{Number(item.usage_total_tasks || 0)}</strong></div>
                        <div><small>成功完成</small><strong>{Number(item.usage_completed_tasks || 0)}</strong></div>
                        <div><small>核验时间</small><strong>{displayDate(item.usage_verified_at)}</strong></div>
                      </div>
                      <div className="test-verification-actions">
                        <button className="primary-button" disabled={!data.summary.usage_sync_configured || Boolean(busy)} onClick={() => verifyTestUsage(item)}>
                          {data.summary.usage_sync_configured ? "从产品后台自动核验" : "自动核验连接待配置"}
                        </button>
                        <span>或根据现有 Admin 后台记录人工标记：</span>
                        <button disabled={Boolean(busy)} onClick={() => markTestUsage(item, "account_not_found")}>未找到账号</button>
                        <button disabled={Boolean(busy)} onClick={() => markTestUsage(item, "account_only")}>只有账号/登录</button>
                        <button disabled={Boolean(busy)} onClick={() => markTestUsage(item, "started")}>已开始未完成</button>
                        <button className="test-complete-button" disabled={Boolean(busy)} onClick={() => markTestUsage(item, "completed")}>确认有完成视频</button>
                      </div>
                    </div>
                  )}

                  {item.kind === "membership_request" && (
                    <div className="ops-action-panel ops-action-danger">
                      <div><strong>不绑卡会员处理</strong><p>{item.membership_status === "active" ? "产品后台已确认会员有效；系统只会使用固定成功模板通知。" : data.summary.usage_sync_configured ? "数据总控会核对注册邮箱；账号已验证且没有冲突订阅时，幂等开通30天并使用固定模板回复。任何异常都会留给人工。" : "安全产品连接尚未完成；系统不会开通，也不会提前发送成功通知。"}</p></div>
                      <input readOnly={item.membership_status === "approved_pending_backend" || item.membership_status === "active"} value={registeredEmails[item.id] ?? ""} placeholder="对方注册 VideoCoding 的邮箱" onChange={(event) => setRegisteredEmails((current) => ({ ...current, [item.id]: event.target.value }))} />
                      {item.membership_status === "approved_pending_backend" ? (
                        <button disabled={busy === `member-done-${item.id}`} onClick={() => confirmMembership(item)}>我已在后台完成开通</button>
                      ) : item.membership_status === "active" ? (
                        <span className="ops-membership-active">已开通 · 待通知</span>
                      ) : (
                        <button disabled={busy === `member-${item.id}`} onClick={() => post(`member-${item.id}`, { action: "approve_membership", id: item.id, registered_email: registeredEmails[item.id] || "" })}>批准开通请求</button>
                      )}
                    </div>
                  )}

                  {(item.kind === "price_received" || item.kind === "price_and_test_claim") && (
                    <div className="ops-action-panel ops-rate-editor">
                      <div><strong>记录报价并进入营销评估</strong><p>保存邮箱、YouTube 账号、报价、测试和营销状态；不会自动接受合作。</p></div>
                      <label>独立视频<input inputMode="decimal" value={itemRates.dedicated} onChange={(event) => setRates((current) => ({ ...current, [item.id]: { ...itemRates, dedicated: event.target.value } }))} /></label>
                      <label>口播植入<input inputMode="decimal" value={itemRates.integration} onChange={(event) => setRates((current) => ({ ...current, [item.id]: { ...itemRates, integration: event.target.value } }))} /></label>
                      <label>Shorts<input inputMode="decimal" value={itemRates.shorts} onChange={(event) => setRates((current) => ({ ...current, [item.id]: { ...itemRates, shorts: event.target.value } }))} /></label>
                      <label>币种<input placeholder="待确认，如 USD / EUR" value={itemRates.currency} onChange={(event) => setRates((current) => ({ ...current, [item.id]: { ...itemRates, currency: event.target.value } }))} /></label>
                      <label>交付时间<input value={itemRates.turnaround} onChange={(event) => setRates((current) => ({ ...current, [item.id]: { ...itemRates, turnaround: event.target.value } }))} /></label>
                      <button disabled={busy === `rate-${item.id}`} onClick={() => post(`rate-${item.id}`, {
                        action: "save_creator_details", id: item.id, price_dedicated: itemRates.dedicated,
                        price_integration: itemRates.integration, price_shorts: itemRates.shorts,
                        currency: itemRates.currency, turnaround: itemRates.turnaround,
                      })}>保存并列入评估名单</button>
                    </div>
                  )}

                  {item.kind !== "ignore" && item.kind !== "opt_out" && (
                    <div className="ops-draft-editor">
                      <div className="ops-block-heading"><span>待发送的人工作文模板</span><small>可以修改；系统不会自行扩写</small></div>
                      {candidateTemplateBlocked && <p className="discovery-error">这是 AI 根据完整上下文创建的候选模板，尚未人工批准；当前禁止发送。请先到模板中心审核、修改并启用。</p>}
                      <div className="ops-template-picker">
                        <label>套用模板</label>
                        <select value={item.template_key || ""} onChange={(event) => applyTemplate(item, event.target.value)}>
                          <option value="" disabled>选择模板</option>
                          {data.templates.filter((template) => template.enabled).map((template) => <option key={template.template_key} value={template.template_key}>{template.name}</option>)}
                        </select>
                      </div>
                      <label>主题<input value={draft.subject} onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: { ...draft, subject: event.target.value } }))} /></label>
                      <label>正文<textarea rows={13} value={draft.body} onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: { ...draft, body: event.target.value } }))} /></label>
                      <div className="ops-card-actions">
                        <button disabled={Boolean(busy)} onClick={() => saveDraft(item)}>只保存草稿</button>
                        <button disabled={Boolean(busy)} onClick={() => post(`ignore-${item.id}`, { action: "set_work_status", id: item.id, status: "ignored" })}>忽略，不回复</button>
                        <button className="primary-button" disabled={Boolean(busy) || item.status === "sent" || candidateTemplateBlocked} onClick={() => sendReply(item)}>确认并立即回复</button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </section>
      <button className="ops-reload" onClick={reload}>重新读取工作台</button>
    </div>
  );
}

export function LeadCenter() {
  const { data, loading, busy, notice, error, reload, post } = useOperationsData();
  if (loading && !data) return <section className="ops-loading">正在检查邮箱来源…</section>;
  if (!data) return <EmptyState title="邮箱来源暂时不可用" copy={error || "请稍后重试。"} />;
  const worker = data.discovery.state;
  const tierLabels: Record<number, { title: string; copy: string }> = {
    1: { title: "编程 AI 精准词", copy: "Codex、Claude Code、Hyperframes、Remotion、Gemini" },
    2: { title: "全部 AI 内容", copy: "多语言 AI 工具、教程、代理、自动化和行业内容" },
    3: { title: "剪辑、科普与口播讲解", copy: "CapCut、AE 等创作工具，以及科普、知识讲解、口播创作者；统计包含历史候选" },
  };
  const statusLabels: Record<string, string> = {
    starting: "正在启动", running: "正在扫描", sleeping: "等待下一轮", idle: "等待到期任务",
    paused: "已暂停", degraded: "退避后重试", stopped: "已停止", queued: "排队中",
    waiting: "已完成本轮", error: "等待重试",
  };
  return (
    <div className="page-stack ops-page">
      <section className="lead-hero">
        <div><span className="section-kicker">PARALLEL CREATOR DISCOVERY</span><h2>总目标 1,000,000，每日新增目标 3,000 个去重邮箱。</h2><p>4 个进程采集 AI，2 个采集剪辑与创作工具，2 个采集科普和口播讲解。最近 30 天发布过视频才可进入发送流程。每四轮至少一轮探索新方向，其余根据合格新增、耗时和去重率分配任务；样本不足时继续探索。采集、核验与发送同步进行。</p></div>
        <dl>
          <div><dt>发现器</dt><dd className={data.discovery.online && worker?.enabled ? "lead-online" : "lead-offline"}>{data.discovery.online && worker?.enabled ? `${data.discovery.summary.active_workers} 个 Worker 运行中` : worker?.enabled ? "待恢复" : "已暂停"}</dd></div>
          <div><dt>主库总量</dt><dd>{data.discovery.summary.master_contacts.toLocaleString()}</dd></div>
        </dl>
      </section>
      <Feedback notice={notice} error={error} />
      <section className="discovery-control-panel">
        <div className="panel-heading">
          <div><span className="section-kicker">LIVE WORKERS</span><h3>{statusLabels[worker?.status || ""] || worker?.status || "尚未启动"}</h3></div>
          <div className="discovery-actions">
            <button disabled={Boolean(busy)} onClick={() => post("discovery-now", { action: "run_discovery_now" })}>立即安排一轮</button>
            {data.discovery.summary.error_jobs > 0 && <button disabled={Boolean(busy)} onClick={() => post("discovery-retry", { action: "retry_discovery_errors" })}>重试 {data.discovery.summary.error_jobs} 个错误</button>}
            <button className={worker?.enabled ? "danger-button" : "primary-button"} disabled={Boolean(busy)} onClick={() => post("discovery-toggle", { action: "set_discovery_enabled", enabled: !worker?.enabled })}>{worker?.enabled ? "暂停发现" : "恢复发现"}</button>
          </div>
        </div>
        <div className="discovery-live-line">
          <div><small>当前层级</small><strong>{worker?.active_tier ? `第 ${worker.active_tier} 层 · ${tierLabels[worker.active_tier]?.title}` : "等待下一任务"}</strong></div>
          <div><small>当前搜索</small><strong>{worker?.current_query || "—"}</strong></div>
          <div><small>最近心跳</small><strong>{displayDate(worker?.heartbeat_at || null)}</strong></div>
        </div>
        {worker?.last_error && <p className="discovery-error">最近错误：{worker.last_error}</p>}
      </section>
      <section className="discovery-metrics">
        <article><span>过去 24 小时 / 每日目标</span><strong>{data.discovery.summary.new_last_24h.toLocaleString()} / {data.discovery.summary.daily_target.toLocaleString()}</strong><small>完成 {data.discovery.summary.daily_progress_percent}%</small></article>
        <article><span>按最近 6 小时推算</span><strong>{data.discovery.summary.projected_daily.toLocaleString()} / 天</strong><small>活跃 Worker：{data.discovery.summary.active_workers}</small></article>
        <article><span>累计 / 1,000,000 目标</span><strong>{data.discovery.summary.new_unique_contacts.toLocaleString()} / {data.discovery.summary.target_new_contacts.toLocaleString()}</strong><small>剩余 {data.discovery.summary.target_remaining.toLocaleString()} · {data.discovery.summary.target_progress_percent}%</small></article>
        <article><span>首轮覆盖任务</span><strong>{data.discovery.summary.initial_pass_complete.toLocaleString()} / {data.discovery.summary.total_jobs.toLocaleString()}</strong><small>关键词 × 语言 × 年份 × 两种排序</small></article>
        <article><span>累计频道命中</span><strong>{data.discovery.summary.channels_found.toLocaleString()}</strong><small>包含不同搜索任务的重复命中</small></article>
        <article><span>唯一公开邮箱 / 待验证</span><strong>{data.discovery.summary.unique_public_emails.toLocaleString()} / {data.discovery.summary.pending_validation.toLocaleString()}</strong><small>主库重复 {data.discovery.summary.existing_duplicates.toLocaleString()} 个，不计入新增</small></article>
      </section>
      <section className="discovery-control-panel">
        <div className="panel-heading"><div><span className="section-kicker">ADAPTIVE SEARCH</span><h3>根据真实产出调整搜索方向</h3></div></div>
        <p>以下仅统计新策略下过去 24 小时已完成的 {data.discovery.learning?.measured_runs || 0} 轮。合格候选要求近 30 天活跃、公开邮箱域有效、至少 1,000 订阅且未重复入库；进入发送队列还需通过内容与身份核验。</p>
        <div className="discovery-live-line">
          <div><small>合格新候选</small><strong>{data.discovery.learning?.eligible_contacts || 0}</strong></div>
          <div><small>搜索重复率</small><strong>{data.discovery.learning?.channels ? `${Math.round(data.discovery.learning.duplicates / data.discovery.learning.channels * 100)}%` : "等待样本"}</strong></div>
          <div><small>活跃 / 停更 / 待核验</small><strong>{data.discovery.learning?.active_channels || 0} / {data.discovery.learning?.inactive_channels || 0} / {data.discovery.learning?.unknown_activity || 0}</strong></div>
        </div>
        {(data.discovery.decisions || []).map((decision) => <div className="discovery-live-line" key={decision.worker_key}>
          <div><small>{decision.worker_key} · {statusLabels[decision.status] || decision.status} · {displayDate(decision.started_at)}</small><strong>{decision.selection_mode === "yield" ? "优先高产方向" : "探索新受众"}</strong></div>
          <div><small>搜索任务</small><strong>{decision.query}</strong></div>
          <div><small>调度依据</small><span>{decision.selection_reason}</span></div>
        </div>)}
      </section>
      <section className="discovery-tier-grid">
        {[1, 2, 3].map((tierNumber) => {
          const tier = data.discovery.tiers.find((item) => Number(item.tier) === tierNumber);
          const completed = Number(tier?.initial_pass_complete || 0);
          const total = Number(tier?.total_jobs || 0);
          const progress = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
          return (
            <article key={tierNumber} className={worker?.active_tier === tierNumber ? "active" : ""}>
              <div><span>优先级 0{tierNumber}</span><strong>{tierLabels[tierNumber].title}</strong><p>{tierLabels[tierNumber].copy}</p></div>
              <div className="discovery-progress"><i style={{ width: `${progress}%` }} /></div>
              <dl><div><dt>首轮</dt><dd>{completed}/{total}</dd></div><div><dt>频道</dt><dd>{Number(tier?.channels_found || 0).toLocaleString()}</dd></div><div><dt>入库</dt><dd>{Number(tier?.contacts_imported || 0).toLocaleString()}</dd></div></dl>
            </article>
          );
        })}
      </section>
      <section className="lead-pipeline">
        <article><span>01</span><strong>发现频道</strong><p>YouTube 搜索与已有 collector 产生候选频道。</p></article>
        <article><span>02</span><strong>提取公开联系信息</strong><p>读取频道公开简介和频道主动链接的官方网站。</p></article>
        <article><span>03</span><strong>活跃核验与去重</strong><p>检查频道近 30 天的实际发布时间、公开联系方式、禁止联系名单和历史发送记录。</p></article>
        <article><span>04</span><strong>进入每日意向队列</strong><p>只有符合发送条件的线索才进入首发队列。</p></article>
      </section>
      <section className="source-table-panel">
        <div className="panel-heading"><div><span className="section-kicker">SOURCE REGISTRY</span><h3>邮箱来源与采集代码</h3></div><button className="sync-button" onClick={reload}>刷新状态</button></div>
        <div className="source-table">
          {data.sources.map((source) => (
            <article key={source.id}>
              <span className={`source-state ${source.enabled ? "source-state-on" : ""}`}>{source.enabled ? "启用" : "停用"}</span>
              <div><strong>{source.name}</strong><code>{source.location}</code></div>
              <div><small>合规依据</small><span>{source.policy_status === "public_business_contact" ? "公开商务联系方式" : source.policy_status === "operator_confirmed" ? "人工确认授权" : "现有历史库"}</span></div>
              <div><small>发现 / 导入</small><span>{Number(source.discovered_count).toLocaleString()} / {Number(source.imported_count).toLocaleString()}</span></div>
              <div><small>最近运行</small><span>{displayDate(source.last_run_at)}</span></div>
            </article>
          ))}
        </div>
      </section>
      <section className="discovery-queue-panel">
        <div className="panel-heading"><div><span className="section-kicker">PERSISTENT QUEUE</span><h3>搜索分片与断点</h3></div><small>共 {data.discovery.summary.total_jobs} 个循环任务</small></div>
        <div className="discovery-job-list">
          {data.discovery.jobs.map((job) => (
            <article key={job.id}>
              <span className={`job-tier tier-${job.tier}`}>P{job.tier}</span>
              <div><strong>{job.query}</strong><small>{job.language} · {job.tier_key}</small></div>
              <div><small>状态</small><span>{statusLabels[job.status] || job.status}</span></div>
              <div><small>视频 / 频道 / 入库</small><span>{job.videos_scanned} / {job.channels_found} / {job.contacts_imported}</span></div>
              <div><small>下次运行</small><span>{displayDate(job.next_run_at)}</span></div>
            </article>
          ))}
        </div>
      </section>
      {data.discovery.recent_channels.length > 0 && (
        <section className="discovery-recent-panel">
          <div className="panel-heading"><div><span className="section-kicker">RECENT MATCHES</span><h3>最近发现的频道与命中视频</h3></div></div>
          <div className="discovery-recent-grid">
            {data.discovery.recent_channels.map((channel) => (
              <article key={channel.channel_key}>
                <span className={`job-tier tier-${channel.best_tier}`}>P{channel.best_tier}</span>
                <div><a href={channel.channel_url} target="_blank" rel="noreferrer">{channel.channel_name}</a><p>{channel.matched_video_title || channel.matched_query}</p><small>{channel.language} · {channel.email_count > 0 ? `发现 ${channel.email_count} 个公开邮箱` : "未发现公开联系邮箱"}</small></div>
              </article>
            ))}
          </div>
        </section>
      )}
      <section className="lead-note"><strong>覆盖边界</strong><p>{data.discovery.coverage_note} 当前持续扩展 YouTube 公开内容，搜索任务完成不代表已找全某类博主。每位候选仍需通过身份、内容及发送资格核验。电脑关机期间采集和发送会暂停，开机后自动续跑。</p></section>
    </div>
  );
}

export function TemplateCenter() {
  const { data, loading, busy, notice, error, post } = useOperationsData();
  const [selected, setSelected] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  const template = data?.templates.find((item) => item.template_key === selected) ?? data?.templates[0];
  useEffect(() => {
    if (!template) return;
    setSelected(template.template_key);
    setSubject(template.subject);
    setBody(template.body);
  }, [template?.template_key, template?.version]);

  if (loading && !data) return <section className="ops-loading">正在装载邮件模板…</section>;
  if (!data || !template) return <EmptyState title="模板暂时不可用" copy={error || "请稍后重试。"} />;

  return (
    <div className="page-stack ops-page">
      <section className="template-control-hero">
        <div><span className="section-kicker">CONTEXT-GROUNDED TEMPLATES</span><h2>已有模板必须覆盖完整意图；缺失时创建候选，但必须人工批准。</h2></div>
        <p>候选模板只会在上下文完整、没有状态冲突时生成，并默认禁用。你可以核对证据、修改内容，再决定是否把它加入可用模板库。</p>
      </section>
      <Feedback notice={notice} error={error} />
      <section className="template-control-grid">
        <aside className="template-list">
          {data.templates.map((item) => <button key={item.template_key} className={item.template_key === template.template_key ? "active" : ""} onClick={() => setSelected(item.template_key)}><strong>{item.name}</strong><small>{item.approval_status === "pending_review" ? "待审核候选 · " : item.approval_status === "rejected" ? "已拒绝 · " : ""}{stageLabels[item.stage] || item.stage} · v{item.version}</small></button>)}
        </aside>
        <article className="template-editor">
          <div className="ops-block-heading"><span>{template.name}</span><small className={`ops-risk ops-risk-${template.risk_level}`}>{template.risk_level === "high" ? "高风险模板" : "发送前审核"}</small></div>
          {template.approval_status === "pending_review" && (
            <div className="ops-action-panel ops-action-danger">
              <div><strong>待人工审核的新模板</strong><p>{template.creation_reason || "现有模板无法覆盖完整意图。"} · 已匹配 {Number(template.example_count || 1)} 个案例。批准前不会出现在可发送模板中。</p></div>
            </div>
          )}
          {template.approval_status === "rejected" && <p className="discovery-error">此候选模板已被拒绝并保持禁用。</p>}
          <label>邮件主题<input value={subject} onChange={(event) => setSubject(event.target.value)} /></label>
          <label>邮件正文<textarea rows={22} value={body} onChange={(event) => setBody(event.target.value)} /></label>
          <div className="template-variables"><strong>允许的固定变量</strong><code>{"{{original_subject}}  {{coupon_code}}  {{registered_email}}  {{channel_name}}"}</code></div>
          <div className="ops-card-actions">
            <button disabled={Boolean(busy)} onClick={() => post(`save-template-${template.template_key}`, { action: "update_template", template_key: template.template_key, subject, body })}>{template.approval_status === "pending_review" ? "保存候选修改" : "保存为新版本"}</button>
            {template.approval_status === "pending_review" && <button className="primary-button" disabled={Boolean(busy)} onClick={() => post(`approve-template-${template.template_key}`, { action: "approve_template_candidate", template_key: template.template_key })}>批准并启用</button>}
            {template.approval_status === "pending_review" && <button className="danger-button" disabled={Boolean(busy)} onClick={() => post(`reject-template-${template.template_key}`, { action: "reject_template_candidate", template_key: template.template_key })}>拒绝候选</button>}
          </div>
        </article>
      </section>
      <section className="rules-panel">
        <div className="panel-heading"><div><span className="section-kicker">AUTOMATION BOUNDARIES</span><h3>每一步允许系统做到哪里</h3></div></div>
        <div className="rules-list">
          {data.rules.map((rule) => (
            <article key={rule.rule_key}>
              <span className={`ops-risk ops-risk-${rule.risk_level}`}>{rule.risk_level === "high" ? "高风险" : rule.risk_level === "medium" ? "需审核" : "低风险"}</span>
              <div><strong>{rule.name}</strong><p>{rule.description}</p></div>
              <select value={rule.mode} onChange={(event) => post(`rule-${rule.rule_key}`, { action: "update_rule", rule_key: rule.rule_key, mode: event.target.value })}>
                <option value="manual">{modeLabels.manual}</option>
                <option value="draft">{modeLabels.draft}</option>
                <option value="auto" disabled={rule.risk_level !== "low" && rule.rule_key !== "coupon_reply"}>{modeLabels.auto}</option>
              </select>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
