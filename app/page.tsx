"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { LeadCenter, OperationsCenter, TemplateCenter } from "./operations-center";
import { ConversationCenter } from "./conversation-center";
import { FeedbackCenter } from "./feedback-center";
import { BillingCenter } from "./billing-center";

type NavKey = "dashboard" | "operations" | "sent" | "replies" | "ai" | "contacts" | "leads" | "quality" | "templates" | "feedback" | "billing";

type Contact = {
  id: number;
  email: string;
  channel_name: string;
  channel_url: string;
  platform: string;
  category: string;
  ai_match: number;
  curated_ai: number;
  ai_confidence: string;
  validation_status: string;
  verification_proof: string;
  subscribers: number | null;
  country: string;
  language: string;
  topic: string;
  priority: string;
  fit_score: number;
  source_count: number;
  source_rows: number;
  planned_send_date: string | null;
  planned_sender_email: string | null;
  send_timezone: string | null;
  timezone_source: string;
  timezone_confidence: string;
  send_window: string | null;
  next_eligible_send_at: string | null;
  outreach_status: string;
  first_sent_at: string | null;
  followup1_due: string | null;
  followup2_due: string | null;
  reply_status: string;
  reply_received_at: string | null;
  reply_from: string | null;
  reply_summary: string | null;
  rate_info: string | null;
  next_action: string | null;
  deal_status: string;
  do_not_contact: number;
  notes: string;
};

type Summary = {
  total: number;
  send_ready: number;
  needs_validation: number;
  invalid: number;
  confirmed_ai: number;
  remaining_send_ready: number;
  ai_candidates: number;
  sent: number;
  replied: number;
  interested: number;
  deals: number;
  today_sent: number;
  campaign_day_et?: string;
  outreach_health?: { progress?: { status: string; issues?: string[]; checked_at?: string } };
  next_queue_date: string | null;
  next_queue_count: number;
  daily_send_min: number;
  daily_send_target: number;
  daily_send_hard_cap: number;
  daily_remaining: number;
  source_files: number;
  source_occurrences: number;
  generated_at: string;
  categories: { category: string; count: number }[];
  ab_variants: {
    template_version: string;
    sent: number;
    qualified_replies: number;
    bounces: number;
  }[];
  timezone_windows: { window: string; count: number }[];
  active_senders: number;
  active_daily_capacity: number;
  pending_daily_capacity: number;
  sender_accounts: {
    sender_email: string;
    provider: string;
    status: string;
    daily_target: number;
    daily_hard_cap: number;
    min_interval_seconds: number;
    reply_to_email: string;
    sending_domain: string;
    configuration_note: string;
  }[];
  target_daily_capacity: number;
  target_new_contacts: number;
  days_to_target_current: number;
  days_to_target_scaled: number;
  timezone_known: number;
  timezone_fallback: number;
  timezone_scheduled: number;
  rolling_delivery_sample: number;
  rolling_bounces: number;
  rolling_bounce_rate: number;
  scale_ready: boolean;
};

type ListResponse = {
  rows: Contact[];
  total: number;
  page: number;
  limit: number;
  pages: number;
};

type InboxEvent = {
  id: string;
  received_at: string;
  sender_name: string;
  sender_email: string;
  subject: string;
  body_preview: string;
  event_type: "reply" | "interested" | "opted_out" | "bounce" | "sender_restricted" | "automated" | "unmatched";
  contact_id: number | null;
  matched_email: string | null;
  web_link: string | null;
  channel_name: string | null;
  channel_url: string | null;
};

type MicrosoftSyncStatus = {
  connected: boolean;
  mailbox: string | null;
  last_sync_at: string | null;
  last_sent_sync_at: string | null;
  connection_error: string | null;
  total_events: number;
  replies: number;
  bounces: number;
  unmatched: number;
  recent_events: InboxEvent[];
  imported?: number;
  sent_scanned?: number;
  sent_matched?: number;
  scheduler_status: string;
  scheduler_last_run_at: string | null;
  scheduler_last_result: {
    day_et?: string;
    selected?: number;
    sent?: number;
    failed?: number;
    total_sent_today?: number;
    status?: string;
  } | null;
  scheduler_next_run_at: string | null;
};

const navItems: { key: NavKey; label: string; marker: string }[] = [
  { key: "billing", label: "自动扣款", marker: "11" },
  { key: "dashboard", label: "运营总览", marker: "01" },
  { key: "operations", label: "回复审核台", marker: "02" },
  { key: "sent", label: "今日已发送", marker: "03" },
  { key: "replies", label: "所有会话", marker: "04" },
  { key: "feedback", label: "博主反馈", marker: "10" },
  { key: "ai", label: "首发意向队列", marker: "05" },
  { key: "contacts", label: "全部联系人", marker: "06" },
  { key: "leads", label: "邮箱增长", marker: "07" },
  { key: "quality", label: "数据健康", marker: "08" },
  { key: "templates", label: "模板与规则", marker: "09" },
];

const statusLabels: Record<string, string> = {
  not_contacted: "未联系",
  personalized: "已个性化",
  drafted: "草稿完成",
  sent: "已发送",
  followup1: "跟进 1",
  followup2: "跟进 2",
  replied: "已回复",
  interested: "有兴趣",
  closed: "已关闭",
  bounced: "退信",
  sender_restricted: "发件账号受限",
  opted_out: "退订",
};

const validationLabels: Record<string, string> = {
  send_ready: "可发送",
  needs_validation: "待验证",
  invalid: "已隔离",
};

const categoryLabels: Record<string, string> = {
  "AI & Tech": "AI 与科技",
  "Education & Science": "教育与科学",
  "Needs Review": "待分类",
  "Lifestyle & Reviews": "生活方式与评测",
  "Business & Marketing": "商业与营销",
  "Health & Fitness": "健康与健身",
  "Podcast & Interviews": "播客与访谈",
  "Finance & Economics": "金融与经济",
  "History & Society": "历史与社会",
  "News & Commentary": "新闻与评论",
  "Creative & Media": "创意与媒体",
  "Career & Growth": "职业与成长",
  "Professional Services": "专业服务",
};

const LEGACY_LIGHT_EMAIL_BODY = `Hi {{channel_name}},

I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you.

VideoCoding turns a talking-head cut into a finished video with B-roll, motion graphics, captions, music, and sound design.

You can see it here:
https://videocoding.ai

If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0. After you try it on one real video, we can discuss the promotion format and your rate.

Best,
Alhazar Anwar
VideoCoding`;

function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat("zh-CN").format(value ?? 0);
}

function formatDate(value: string | null) {
  if (!value) return "—";
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    weekday: "short",
  }).format(parsed);
}

function formatDateTime(value: string | null) {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(parsed);
}

function initials(name: string) {
  const clean = name.trim();
  if (!clean) return "VC";
  const pieces = clean.split(/\s+/).filter(Boolean);
  return (pieces.length > 1 ? `${pieces[0][0]}${pieces[1][0]}` : clean.slice(0, 2)).toUpperCase();
}

function deliveryLabel(contact: Contact) {
  if (contact.reply_status !== "no_reply") return "已回复";
  if (contact.notes.includes("initial event delivered")) return "已投递";
  if (contact.notes.includes("initial event sent")) return "投递中";
  return "Microsoft 365 已接受";
}

function MetricCard({
  eyebrow,
  value,
  note,
  tone = "ink",
}: {
  eyebrow: string;
  value: string;
  note: string;
  tone?: "ink" | "blue" | "lime" | "amber";
}) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-topline">
        <span>{eyebrow}</span>
        <span className="metric-spark" aria-hidden="true" />
      </div>
      <strong>{value}</strong>
      <p>{note}</p>
    </article>
  );
}

function ContactTable({
  rows,
  loading,
  onSelect,
  onStatusChange,
}: {
  rows: Contact[];
  loading: boolean;
  onSelect: (contact: Contact) => void;
  onStatusChange: (contact: Contact, status: string) => void;
}) {
  if (loading) {
    return (
      <div className="table-loading">
        <span className="loader" />
        <p>正在读取联系人库…</p>
      </div>
    );
  }
  if (!rows.length) {
    return <div className="empty-state">没有符合当前条件的联系人。</div>;
  }
  return (
    <div className="table-scroll">
      <table className="contact-table">
        <thead>
          <tr>
            <th>联系人</th>
            <th>主题与分类</th>
            <th>验证</th>
            <th>订阅数</th>
            <th>计划日期</th>
            <th>外联状态</th>
            <th aria-label="打开详情" />
          </tr>
        </thead>
        <tbody>
          {rows.map((contact) => (
            <tr key={contact.id}>
              <td>
                <button className="contact-identity" onClick={() => onSelect(contact)}>
                  <span className="avatar">{initials(contact.channel_name)}</span>
                  <span>
                    <strong>{contact.channel_name}</strong>
                    <small>{contact.email}</small>
                  </span>
                </button>
              </td>
              <td>
                <span className="topic-line">{contact.topic || "暂无主题说明"}</span>
                <span className={`tag ${contact.curated_ai ? "tag-ai" : ""}`}>
                  {contact.curated_ai ? "AI 已确认" : categoryLabels[contact.category] ?? contact.category}
                </span>
              </td>
              <td>
                <span className={`validation validation-${contact.validation_status}`}>
                  <i />
                  {validationLabels[contact.validation_status] ?? contact.validation_status}
                </span>
                <small className="proof">{contact.verification_proof}</small>
              </td>
              <td className="number-cell">{contact.subscribers ? formatNumber(contact.subscribers) : "—"}</td>
              <td>{formatDate(contact.planned_send_date)}</td>
              <td>
                <select
                  className={`status-select status-${contact.outreach_status}`}
                  value={contact.outreach_status}
                  onChange={(event) => onStatusChange(contact, event.target.value)}
                  aria-label={`更新 ${contact.channel_name} 的状态`}
                >
                  {Object.entries(statusLabels).map(([value, label]) => (
                    <option value={value} key={value}>{label}</option>
                  ))}
                </select>
              </td>
              <td>
                <button className="row-arrow" onClick={() => onSelect(contact)} aria-label="打开联系人详情">
                  →
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Home() {
  const [active, setActive] = useState<NavKey>("dashboard");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [list, setList] = useState<ListResponse>({ rows: [], total: 0, page: 1, limit: 50, pages: 1 });
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [loadingRows, setLoadingRows] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Contact | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [validation, setValidation] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [microsoft, setMicrosoft] = useState<MicrosoftSyncStatus | null>(null);
  const [syncingInbox, setSyncingInbox] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(queryInput.trim());
      setPage(1);
    }, 320);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

  const refreshSummary = useCallback(async () => {
    try {
      setLoadingSummary(true);
      const response = await fetch("/api/outreach?view=summary", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "无法读取总览");
      setSummary(payload);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取联系人库");
    } finally {
      setLoadingSummary(false);
    }
  }, []);

  const refreshRows = useCallback(async () => {
    if (active === "operations" || active === "replies" || active === "leads" || active === "quality" || active === "templates" || active === "feedback" || active === "billing") {
      setLoadingRows(false);
      return;
    }
    try {
      setLoadingRows(true);
      const params = new URLSearchParams({
        page: String(active === "dashboard" ? 1 : page),
        limit: String(active === "dashboard" ? 8 : active === "ai" || active === "sent" ? 100 : 40),
      });
      if (active === "dashboard" || active === "ai") params.set("queue", "today_ai");
      if (active === "sent") params.set("queue", "today_sent");
      if (query) params.set("q", query);
      if (category) params.set("category", category);
      if (validation) params.set("validation", validation);
      if (status) params.set("status", status);
      const response = await fetch(`/api/outreach?${params.toString()}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "无法读取联系人");
      setList(payload);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取联系人");
    } finally {
      setLoadingRows(false);
    }
  }, [active, category, page, query, status, validation]);

  const refreshMicrosoft = useCallback(async () => {
    try {
      const response = await fetch("/api/microsoft/sync", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "无法读取 Microsoft 365 状态");
      setMicrosoft(payload);
    } catch (caught) {
      setMicrosoft((current) => current ? {
        ...current,
        connection_error: caught instanceof Error ? caught.message : "无法读取同步状态",
      } : null);
    }
  }, []);

  const syncMicrosoft = useCallback(async (silent = false) => {
    try {
      if (!silent) setSyncingInbox(true);
      const response = await fetch("/api/microsoft/sync", {
        method: "POST",
        cache: "no-store",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "Microsoft 365 同步失败");
      setMicrosoft(payload);
      await Promise.all([refreshSummary(), refreshRows()]);
      setError("");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Microsoft 365 同步失败";
      setMicrosoft((current) => current ? { ...current, connection_error: message } : null);
      if (!silent) setError(message);
    } finally {
      if (!silent) setSyncingInbox(false);
    }
  }, [refreshRows, refreshSummary]);

  useEffect(() => {
    const timer = window.setTimeout(() => refreshSummary(), 0);
    return () => window.clearTimeout(timer);
  }, [refreshSummary]);

  useEffect(() => {
    // Read local campaign totals, not Graph; mail sends happen outside this tab.
    let refreshing = false;
    const refresh = async () => {
      if (document.hidden || refreshing) return;
      refreshing = true;
      try { await refreshSummary(); } finally { refreshing = false; }
    };
    const timer = window.setInterval(refresh, 15_000);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [refreshSummary]);

  useEffect(() => {
    const timer = window.setTimeout(() => refreshRows(), 0);
    return () => window.clearTimeout(timer);
  }, [refreshRows]);

  useEffect(() => {
    const timer = window.setTimeout(() => refreshMicrosoft(), 0);
    return () => window.clearTimeout(timer);
  }, [refreshMicrosoft]);

  useEffect(() => {
    if (!microsoft?.connected) return;
    // The background inbox-sync service owns Microsoft Graph synchronization.
    // Browser tabs only refresh the locally stored status here; otherwise every
    // open tab starts its own Graph sync and can exhaust the mailbox concurrency
    // limit, leaving the local dashboard worker blocked on long-running requests.
    const poller = window.setInterval(() => refreshMicrosoft(), 60_000);
    return () => {
      window.clearInterval(poller);
    };
  }, [microsoft?.connected, refreshMicrosoft]);

  const selectNav = useCallback((next: NavKey) => {
    setPage(1);
    setSelected(null);
    if (next === "ai") {
      setCategory("");
      setValidation("");
    }
    setActive(next);
  }, []);

  const updateContact = useCallback(async (contact: Contact, outreachStatus: string) => {
    const response = await fetch("/api/outreach", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: contact.id, outreach_status: outreachStatus }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setError(payload.error ?? "更新失败");
      return;
    }
    setList((current) => ({
      ...current,
      rows: current.rows.map((row) => row.id === contact.id ? payload : row),
    }));
    setSelected((current) => current?.id === contact.id ? payload : current);
    refreshSummary();
  }, [refreshSummary]);

  const openMail = useCallback((contact: Contact) => {
    const subject = `A paid collaboration with VideoCoding`;
    const body = LEGACY_LIGHT_EMAIL_BODY.replace("{{channel_name}}", contact.channel_name);
    const composeUrl = `https://outlook.office.com/mail/deeplink/compose?to=${encodeURIComponent(contact.email)}&subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    window.open(composeUrl, "_blank", "noopener,noreferrer");
  }, []);

  const categoryTotal = useMemo(
    () => summary?.categories.reduce((total, item) => total + item.count, 0) ?? 0,
    [summary],
  );
  const schedulerStale = Boolean(
    microsoft?.scheduler_last_run_at
    && Date.now() - new Date(microsoft.scheduler_last_run_at).getTime() > 36 * 60 * 60 * 1000
    && (!microsoft.scheduler_next_run_at || new Date(microsoft.scheduler_next_run_at).getTime() < Date.now()),
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">VC</span>
          <span>
            <strong>VideoCoding</strong>
            <small>Creator Outreach OS</small>
          </span>
        </div>
        <nav aria-label="主导航">
          {navItems.map((item) => (
            <button
              key={item.key}
              className={active === item.key ? "active" : ""}
              onClick={() => selectNav(item.key)}
            >
              <span>{item.marker}</span>
              {item.label}
              {item.key === "sent" && summary && summary.today_sent > 0 && (
                <b className="nav-count">{summary.today_sent}</b>
              )}
              {item.key === "replies" && summary && summary.replied > 0 && (
                <b className="nav-count">{summary.replied}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="launch-card">
            <div className="launch-line">
              <span className={`launch-dot ${microsoft?.connected ? "launch-dot-live" : ""}`} />
              <strong>{microsoft?.connected ? "Outlook 收发状态同步" : "等待连接邮箱"}</strong>
            </div>
            <p>
              {microsoft?.connected
                ? `${microsoft.mailbox ?? "team@videocoding.ai"}；三个全球窗口分散发送，状态每 60 秒同步。`
                : "连接 team@videocoding.ai 后，回复和退信会自动写回联系人状态。"}
            </p>
            <span className={`hold-badge ${microsoft?.connected ? "active-badge" : ""}`}>
              {microsoft?.connected ? "SYNC ON" : "CONNECT"}
            </span>
          </div>
          <div className="operator">
            <span className="avatar avatar-small">AA</span>
            <span><strong>Alhazar Anwar</strong><small>Campaign owner</small></span>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="breadcrumb">推广运营 / {navItems.find((item) => item.key === active)?.label}</p>
            <h1>
              {active === "billing" && "自动扣款与用户名单"}
              {active === "dashboard" && "AI 博主推广控制台"}
              {active === "operations" && "博主回复审核与自动化"}
              {active === "sent" && "今日发送记录"}
              {active === "replies" && "所有博主会话"}
              {active === "ai" && "AI 首发队列"}
              {active === "contacts" && "全量联系人库"}
              {active === "leads" && "邮箱来源与持续增长"}
              {active === "quality" && "数据健康中心"}
              {active === "templates" && "邮件模板与合作话术"}
            </h1>
          </div>
          <div className="top-actions">
            <a className="sync-button" href="/coupon-audit">优惠码补发名单</a>
            <span className={`last-sync ${microsoft?.connected ? "" : "last-sync-off"}`}>
              <i />
              {microsoft?.connected
                ? `Microsoft 365 · ${microsoft.last_sync_at ? `同步于 ${formatDateTime(microsoft.last_sync_at)}` : "已连接"}`
                : "Microsoft 365 尚未连接"}
            </span>
            {microsoft?.connected ? (
              <button
                className="sync-button"
                disabled={syncingInbox}
                onClick={() => syncMicrosoft(false)}
              >
                {syncingInbox ? "同步中…" : "同步 Outlook"}
              </button>
            ) : (
              <button
                className="sync-button sync-button-connect"
                onClick={() => { window.location.href = "/api/microsoft/connect"; }}
              >
                连接 Microsoft 365
              </button>
            )}
            <button className="primary-button" onClick={() => selectNav("operations")}>
              打开回复审核台
            </button>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <strong>暂时无法读取数据</strong>
            <span>{error}</span>
            <button onClick={() => { refreshSummary(); refreshRows(); }}>重试</button>
          </div>
        )}

        {schedulerStale && (
          <div className="error-banner">
            <strong>自动发送状态已过期</strong>
            <span>上次运行：{formatDateTime(microsoft?.scheduler_last_run_at ?? null)}。系统正在恢复，恢复前不会把旧队列误算成“今天已发送”。</span>
          </div>
        )}

        {loadingSummary && !summary && active !== "billing" ? (
          <section className="boot-screen">
            <span className="loader loader-large" />
            <h2>正在装载联系人主库</h2>
            <p>首次打开会建立本地数据库，之后状态更新会持续保存。</p>
          </section>
        ) : (
          <>
            {active === "operations" && <OperationsCenter />}
            {active === "leads" && <LeadCenter />}
            {active === "dashboard" && summary && (
              <div className="page-stack">
                <section className="hero-strip">
                  <div>
                    <span className="section-kicker">PHASE 01 · AI CREATOR OUTREACH</span>
                    {microsoft?.scheduler_status === "sender_restricted"
                      ? <h2>微软发件账号受限，<br />主通道等待管理员处理。</h2>
                      : <h2>当前 {formatNumber(summary.active_daily_capacity)} / 天，<br />准备扩到 1000 / 天。</h2>}
                    <p>
                      {microsoft?.scheduler_status === "sender_restricted"
                        ? <>微软返回 550 5.1.8，主邮箱初次联系与回复均已暂停。接口接受不代表成功送达；请由管理员检查 <a href="https://security.microsoft.com/restrictedentities" target="_blank" rel="noreferrer">Microsoft 365 受限实体</a>。</>
                        : "先把现有邮箱退信率压到 2% 以下；再启用已认证的专用外发通道。回复仍统一回到 team@videocoding.ai。"}
                    </p>
                  </div>
                  <div className="next-queue-card">
                    <span>下一批剩余</span>
                    <strong>{summary.next_queue_count}</strong>
                    <p>{formatDate(summary.next_queue_date)} · 目标 {summary.daily_send_target}，上限 {summary.daily_send_hard_cap}</p>
                    <small>
                      {microsoft?.scheduler_status === "running"
                        ? "今天批次正在分散发送"
                        : microsoft?.scheduler_status === "sender_restricted"
                          ? "微软发件账号受限，已停止发送，等待管理员处理"
                        : microsoft?.scheduler_status === "waiting_for_recent_activity"
                          ? "库存不足：排队频道尚未通过近 30 天发布视频核验"
                        : microsoft?.scheduler_status === "waiting_for_template_assignment"
                          ? "队列缺货：剩余联系人需要内容核实或批准模板匹配"
                          : microsoft?.scheduler_status === "queue_empty"
                            ? "当前没有可领取的发送任务"
                            : "自动任务：纽约时间 08:00–21:00，13 小时发送窗口"}
                    </small>
                    <button onClick={() => selectNav("ai")}>查看名单 →</button>
                    {summary.replied > 0 && (
                      <button className="reply-shortcut" onClick={() => selectNav("replies")}>
                        查看 {summary.replied} 个回复 →
                      </button>
                    )}
                  </div>
                </section>

                <section className="metrics-grid">
                  <MetricCard eyebrow="全量联系人" value={formatNumber(summary.total)} note="758 个历史来源统一去重" />
                  <MetricCard eyebrow="邮箱可投递" value={formatNumber(summary.send_ready)} note="邮箱验证标签，不代表内容匹配或已排队" tone="blue" />
                  <MetricCard eyebrow="未联系可投递库存" value={formatNumber(summary.remaining_send_ready)} note="仍需粉丝门槛、内容证据、批准模板及排期检查" tone="lime" />
                  <MetricCard eyebrow="全部通道今日提交" value={`${formatNumber(summary.today_sent)} / ${summary.daily_send_target}`} note={`提交不等于送达 · 当前可用通道额度 ${summary.active_daily_capacity} / 天 · 纽约活动日 ${summary.campaign_day_et ?? ""}`} tone="amber" />
                </section>
                {summary.outreach_health?.progress?.status === "alert" && (
                  <section role="status" className="empty-state">
                    <strong>750 封目标存在缺口</strong>
                    <p>{summary.outreach_health.progress.issues?.join("；")}</p>
                    <small>检查时间：{formatDate(summary.outreach_health.progress.checked_at ?? "")}</small>
                  </section>
                )}

                <section className="panel capacity-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="section-kicker">10K DELIVERY CAPACITY</span>
                      <h3>10000 新邮箱触达计划</h3>
                    </div>
                    <span className={`capacity-gate ${summary.scale_ready ? "capacity-gate-ready" : "capacity-gate-blocked"}`}>
                      {summary.scale_ready ? "可开始扩容" : "扩容护栏未通过"}
                    </span>
                  </div>
                  <div className="metrics-grid">
                    <MetricCard eyebrow="当前真实容量" value={`${formatNumber(summary.active_daily_capacity)} / 天`} note={`${summary.active_senders} 个已配置发件通道 · 约 ${summary.days_to_target_current} 个发送日`} tone="blue" />
                    <MetricCard eyebrow="目标容量" value={`${formatNumber(summary.target_daily_capacity)} / 天`} note={`专用外发通道配置完成后，约 ${summary.days_to_target_scaled} 个发送日`} tone="lime" />
                    <MetricCard eyebrow="最近 200 封投递失败" value={`${summary.rolling_bounce_rate}%`} note={`${summary.rolling_bounces} 封退信（含发件账号受限）· 扩容阈值 < 2%`} tone="amber" />
                    <MetricCard eyebrow="真实时区覆盖" value={formatNumber(summary.timezone_known)} note={`${formatNumber(summary.timezone_fallback)} 人使用三窗口随机分流，未假装成已知时区`} />
                  </div>
                  <div className="capacity-actions">
                    <div><strong>原定额度与当前状态</strong><p>{microsoft?.scheduler_status === "sender_restricted" ? "主邮箱原定 700 / 天，当前因账号受限暂停；新邮箱维持原有 50 / 天。" : "主邮箱 700 / 天、新邮箱 50 / 天；美国东部时间 08:00 开始，主邮箱每封随机间隔 45–60 秒，新邮箱每 2 分钟 1 封。"}</p></div>
                    <div><strong>达到 1000 / 天前必须完成</strong><p>配置 outreach 子域名、SPF / DKIM / DMARC、专用外发供应商、退订与退信抑制表。</p></div>
                    <div><strong>回复不变</strong><p>每封仍是单独邮件，Reply-To 指向 team@videocoding.ai，继续进入现有人工审核台。</p></div>
                  </div>
                  <div className="sender-account-list">
                    {summary.sender_accounts.map((sender) => {
                      const statusLabel = sender.status === "active"
                        ? "正式发送"
                        : sender.status === "restricted"
                        ? "账号受限，已暂停"
                        : sender.status === "active_warmup"
                        ? "安全预热"
                        : "等待配置";
                      return (
                        <article className="sender-account" key={sender.sender_email}>
                          <div>
                            <span className="section-kicker">{sender.provider.replaceAll("_", " ")}</span>
                            <strong>{sender.sender_email}</strong>
                            <p>{sender.configuration_note}</p>
                          </div>
                          <dl>
                            <div><dt>状态</dt><dd>{statusLabel}</dd></div>
                            <div><dt>每日上限</dt><dd>{sender.daily_hard_cap}</dd></div>
                            <div><dt>最短间隔</dt><dd>{sender.min_interval_seconds < 60 ? `${sender.min_interval_seconds} 秒` : `${Math.round(sender.min_interval_seconds / 60)} 分钟`}</dd></div>
                            <div><dt>回复邮箱</dt><dd>{sender.reply_to_email}</dd></div>
                          </dl>
                        </article>
                      );
                    })}
                  </div>
                </section>

                {summary.ab_variants.length > 0 && (
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <span className="section-kicker">COPY A/B TEST</span>
                        <h3>新人邮件版本表现</h3>
                      </div>
                      <span className="muted">有效回复已排除自动回复与退信</span>
                    </div>
                    <div className="metrics-grid">
                      {summary.ab_variants.map((variant) => {
                        const replyRate = variant.sent > 0
                          ? Math.round((variant.qualified_replies / variant.sent) * 1000) / 10
                          : 0;
                        const bounceRate = variant.sent > 0
                          ? Math.round((variant.bounces / variant.sent) * 1000) / 10
                          : 0;
                        const challenger = variant.template_version.includes("generative");
                        return (
                          <MetricCard
                            key={variant.template_version}
                            eyebrow={challenger ? "B · 生成式精简版" : "A · 原文对照版"}
                            value={`${replyRate}% 有效回复`}
                            note={`${variant.sent} 封 · ${variant.qualified_replies} 回复 · ${bounceRate}% 退信`}
                            tone={challenger ? "lime" : "blue"}
                          />
                        );
                      })}
                    </div>
                  </section>
                )}

                <section className="dashboard-grid">
                  <article className="panel panel-wide">
                    <div className="panel-heading">
                      <div>
                        <span className="section-kicker">NEXT ACTIONS</span>
                        <h3>首发队列预览</h3>
                      </div>
                      <button className="text-button" onClick={() => selectNav("ai")}>打开每日发送名单</button>
                    </div>
                    <ContactTable
                      rows={list.rows}
                      loading={loadingRows}
                      onSelect={setSelected}
                      onStatusChange={updateContact}
                    />
                  </article>

                  <aside className="panel operations-panel">
                    <div className="panel-heading">
                      <div>
                        <span className="section-kicker">OPERATING RHYTHM</span>
                        <h3>每日执行</h3>
                      </div>
                    </div>
                    <ol className="rhythm-list">
                      <li><span>01</span><div><strong>当前每日目标 750</strong><p>主邮箱 700 + 新邮箱 50</p></div></li>
                      <li><span>02</span><div><strong>美国东部时间 08:00</strong><p>主邮箱随机 45–60 秒，新邮箱每 2 分钟 1 封</p></div></li>
                      <li><span>03</span><div><strong>固定发件人</strong><p>每位博主预先绑定一个邮箱，禁止重复首发</p></div></li>
                      <li><span>04</span><div><strong>两次跟进</strong><p>第 4 天、第 9 天；之后停止</p></div></li>
                    </ol>
                    <div className="guardrail">
                      <span>发送护栏</span>
                      <strong>新邮件 + 跟进 ≤ {summary.daily_send_hard_cap} / 天</strong>
                    <p>最近 200 封退信率达到 2% 或出现投诉时，禁止提升容量。</p>
                    </div>
                  </aside>
                </section>
              </div>
            )}

            {active === "sent" && summary && (
              <div className="page-stack">
                <section className="sent-hero">
                  <div>
                    <span className="section-kicker">RESEND · SHANGHAI TODAY</span>
                    <h2>今天已发送 {formatNumber(summary.today_sent)} 封。</h2>
                    <p>统计口径：美国东部时间当天，由 Microsoft 365 与 Google Workspace 接受的首发邮件。</p>
                  </div>
                  <div className="sent-source-note">
                    <strong>发送记录在哪里？</strong>
                    <p>首发邮件通过 Microsoft Graph 投递，并自动保存在 team@videocoding.ai 的 Outlook Sent Items。</p>
                  </div>
                </section>

                <section className="panel sent-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="section-kicker">DELIVERY LOG</span>
                      <h3>今日收件人</h3>
                    </div>
                    <span className="muted">{formatNumber(list.total)} 条记录</span>
                  </div>
                  {loadingRows ? (
                    <div className="table-loading">
                      <span className="loader" />
                      <p>正在读取发送记录…</p>
                    </div>
                  ) : (
                    <div className="table-scroll">
                      <table className="contact-table sent-table">
                        <thead>
                          <tr>
                            <th>频道</th>
                            <th>收件邮箱</th>
                            <th>发送时间</th>
                            <th>投递状态</th>
                            <th>回复</th>
                            <th>YouTube</th>
                            <th aria-label="打开详情" />
                          </tr>
                        </thead>
                        <tbody>
                          {list.rows.map((contact) => (
                            <tr key={contact.id}>
                              <td>
                                <button className="contact-identity" onClick={() => setSelected(contact)}>
                                  <span className="avatar">{initials(contact.channel_name)}</span>
                                  <span><strong>{contact.channel_name}</strong><small>{contact.category}</small></span>
                                </button>
                              </td>
                              <td>{contact.email}</td>
                              <td>{formatDateTime(contact.first_sent_at)}</td>
                              <td><span className="delivery-pill">{deliveryLabel(contact)}</span></td>
                              <td>
                                <span className={contact.reply_status === "no_reply" ? "reply-none" : "reply-positive"}>
                                  {contact.reply_status === "no_reply" ? "尚未回复" : "有兴趣"}
                                </span>
                              </td>
                              <td>
                                {contact.channel_url ? (
                                  <a className="youtube-link" href={contact.channel_url} target="_blank" rel="noreferrer">
                                    打开频道 ↗
                                  </a>
                                ) : "—"}
                              </td>
                              <td>
                                <button className="row-arrow" onClick={() => setSelected(contact)} aria-label="打开联系人详情">→</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              </div>
            )}

            {active === "replies" && <ConversationCenter />}
            {active === "feedback" && <FeedbackCenter />}
            {active === "billing" && <BillingCenter />}

            {(active === "ai" || active === "contacts") && (
              <div className="page-stack">
                {active === "ai" && summary && (
                  <section className="queue-summary">
                    <div><span>已确认 AI 首发</span><strong>{formatNumber(summary.confirmed_ai)}</strong></div>
                    <div><span>额外 AI 候选</span><strong>{formatNumber(summary.ai_candidates)}</strong><small>需人工复核后加入</small></div>
                    <div><span>当前每日目标</span><strong>{summary.daily_send_target}</strong><small>美东 08:00 · 主邮箱随机 45–60 秒</small></div>
                    <div><span>下次剩余</span><strong>{summary.next_queue_count}</strong><small>{formatDate(summary.next_queue_date)}</small></div>
                  </section>
                )}
                <section className="panel directory-panel">
                  <div className="directory-toolbar">
                    <div className="search-box">
                      <span>⌕</span>
                      <input
                        value={queryInput}
                        onChange={(event) => setQueryInput(event.target.value)}
                        placeholder="搜索频道、邮箱或主题"
                        aria-label="搜索联系人"
                      />
                    </div>
                    {active === "contacts" && (
                      <>
                        <select value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }} aria-label="分类筛选">
                          <option value="">全部分类</option>
                          {summary?.categories.map((item) => (
                            <option value={item.category} key={item.category}>
                              {categoryLabels[item.category] ?? item.category} · {formatNumber(item.count)}
                            </option>
                          ))}
                        </select>
                        <select value={validation} onChange={(event) => { setValidation(event.target.value); setPage(1); }} aria-label="验证状态筛选">
                          <option value="">全部验证状态</option>
                          <option value="send_ready">可发送</option>
                          <option value="needs_validation">待验证</option>
                          <option value="invalid">已隔离</option>
                        </select>
                      </>
                    )}
                    <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }} aria-label="外联状态筛选">
                      <option value="">全部外联状态</option>
                      {Object.entries(statusLabels).map(([value, label]) => (
                        <option value={value} key={value}>{label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="directory-meta">
                    <p>
                      {active === "ai" ? "已确认 AI 队列" : "联系人结果"} ·
                      <strong> {formatNumber(list.total)}</strong> 人
                    </p>
                    <span>第 {list.page} / {list.pages} 页</span>
                  </div>
                  <ContactTable
                    rows={list.rows}
                    loading={loadingRows}
                    onSelect={setSelected}
                    onStatusChange={updateContact}
                  />
                  <div className="pagination">
                    <button disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>← 上一页</button>
                    <span>{formatNumber((list.page - 1) * list.limit + 1)}–{formatNumber(Math.min(list.page * list.limit, list.total))} / {formatNumber(list.total)}</span>
                    <button disabled={page >= list.pages} onClick={() => setPage((value) => value + 1)}>下一页 →</button>
                  </div>
                </section>
              </div>
            )}

            {active === "quality" && summary && (
              <div className="page-stack">
                <section className="quality-hero">
                  <div>
                    <span className="section-kicker">SOURCE OF TRUTH</span>
                    <h2>所有历史资源都保留，<br />发送资格单独控制。</h2>
                  </div>
                  <div className="quality-score">
                    <span>可发送覆盖率</span>
                    <strong>{((summary.send_ready / summary.total) * 100).toFixed(1)}%</strong>
                    <div className="progress"><i style={{ width: `${(summary.send_ready / summary.total) * 100}%` }} /></div>
                  </div>
                </section>
                <section className="quality-grid">
                  <article className="quality-card quality-good">
                    <span className="quality-icon">✓</span>
                    <div><small>可发送</small><strong>{formatNumber(summary.send_ready)}</strong><p>邮箱格式有效，并且至少有“已验证”或 MX 可投递证据。</p></div>
                  </article>
                  <article className="quality-card quality-warn">
                    <span className="quality-icon">!</span>
                    <div><small>待继续验证</small><strong>{formatNumber(summary.needs_validation)}</strong><p>保留在总库，但不会进入每日发送计划。</p></div>
                  </article>
                  <article className="quality-card quality-bad">
                    <span className="quality-icon">×</span>
                    <div><small>格式异常隔离</small><strong>{formatNumber(summary.invalid)}</strong><p>不会出现在任何可发送队列。</p></div>
                  </article>
                </section>
                <section className="dashboard-grid">
                  <article className="panel panel-wide">
                    <div className="panel-heading">
                      <div><span className="section-kicker">CATEGORY COVERAGE</span><h3>全库分类分布</h3></div>
                      <span className="muted">{formatNumber(categoryTotal)} 位联系人</span>
                    </div>
                    <div className="category-bars">
                      {summary.categories.map((item, index) => (
                        <div className="category-row" key={item.category}>
                          <span>{categoryLabels[item.category] ?? item.category}</span>
                          <div><i style={{ width: `${Math.max(2, (item.count / summary.categories[0].count) * 100)}%` }} data-rank={index + 1} /></div>
                          <strong>{formatNumber(item.count)}</strong>
                        </div>
                      ))}
                    </div>
                  </article>
                  <aside className="panel audit-panel">
                    <div className="panel-heading"><div><span className="section-kicker">AUDIT TRAIL</span><h3>本次盘点</h3></div></div>
                    <dl>
                      <div><dt>历史邮箱文件</dt><dd>{formatNumber(summary.source_files)}</dd></div>
                      <div><dt>检查的记录出现次数</dt><dd>{formatNumber(summary.source_occurrences)}</dd></div>
                      <div><dt>去重后邮箱</dt><dd>{formatNumber(summary.total)}</dd></div>
                      <div><dt>AI 已确认首发</dt><dd>{formatNumber(summary.confirmed_ai)}</dd></div>
                      <div><dt>AI 候选复核</dt><dd>{formatNumber(summary.ai_candidates)}</dd></div>
                    </dl>
                    <div className="audit-note">
                      <strong>分类修正</strong>
                      <p>AI 判断不再读取邮箱地址或文件路径，因此不会把 gmail、final-data 等字符串误当作 AI 信号。</p>
                    </div>
                  </aside>
                </section>
              </div>
            )}

            {active === "templates" && <TemplateCenter />}
            {false && (
              <div className="page-stack">
                <section className="template-intro">
                  <div>
                    <span className="section-kicker">SHORT · ONE LINK · PAID</span>
                    <h2>让对方一眼看懂：<br />为什么找他、合作是什么。</h2>
                  </div>
                  <p>首封只做一件事：说明付费合作，并让博主打开 videocoding.ai。不要带附件，不在首封讨论报价、Coupon 或完整合作流程。</p>
                </section>
                <section className="template-grid">
                  <article className="template-card template-primary">
                    <div className="template-header">
                      <span>01 · FIRST EMAIL</span>
                      <button onClick={() => navigator.clipboard.writeText(`Subject: A paid collaboration with VideoCoding\n\n${LEGACY_LIGHT_EMAIL_BODY}`)}>复制模板</button>
                    </div>
                    <p className="subject-line">Subject: A paid collaboration with VideoCoding</p>
                    <div className="email-copy">
                      <p>Hi {"{{channel_name}}"},</p>
                      <p>I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you.</p>
                      <p>VideoCoding turns a talking-head cut into a finished video with B-roll, motion graphics, captions, music, and sound design.</p>
                      <p>You can see it here:<br /><mark>https://videocoding.ai</mark></p>
                      <p>If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0. After you try it on one real video, we can discuss the promotion format and your rate.</p>
                      <p>Best,<br />Alhazar Anwar<br />VideoCoding</p>
                    </div>
                  </article>
                  <div className="template-side">
                    <article className="mini-template">
                      <span>02 · FOLLOW-UP / DAY 4</span>
                      <p>Just following up on the paid VideoCoding AI demo idea. I think the strongest angle for your audience would be {"{{Specific Use Case}}"}. Would it be useful to see the short example?</p>
                    </article>
                    <article className="mini-template">
                      <span>03 · FINAL FOLLOW-UP / DAY 9</span>
                      <p>One last note from me. If a sponsored VideoCoding AI review isn’t a fit right now, no problem at all. I won’t follow up again after this.</p>
                    </article>
                    <article className="rate-card">
                      <span>对方有兴趣后确认</span>
                      <ul>
                        <li>独立视频或口播植入</li>
                        <li>时长、发布日期、链接位置</li>
                        <li>报价、付款条款、修改次数</li>
                        <li>使用权和赞助披露方式</li>
                      </ul>
                    </article>
                  </div>
                </section>
              </div>
            )}
          </>
        )}
      </main>

      {selected && (
        <div className="drawer-backdrop" onClick={() => setSelected(null)}>
          <aside className="detail-drawer" onClick={(event) => event.stopPropagation()} aria-label="联系人详情">
            <button className="drawer-close" onClick={() => setSelected(null)} aria-label="关闭">×</button>
            <div className="drawer-profile">
              <span className="avatar avatar-large">{initials(selected.channel_name)}</span>
              <span className={`tag ${selected.curated_ai ? "tag-ai" : ""}`}>
                {selected.curated_ai ? "AI 已确认" : categoryLabels[selected.category] ?? selected.category}
              </span>
              <h2>{selected.channel_name}</h2>
              <p>{selected.topic || "暂无主题说明"}</p>
            </div>
            <div className="drawer-actions">
              <button className="primary-button" onClick={() => openMail(selected)}>在 Outlook 打开草稿</button>
              <button onClick={() => navigator.clipboard.writeText(selected.email)}>复制邮箱</button>
            </div>
            <dl className="contact-details">
              <div><dt>邮箱</dt><dd>{selected.email}</dd></div>
              <div><dt>验证证据</dt><dd>{selected.verification_proof}</dd></div>
              <div><dt>订阅数</dt><dd>{selected.subscribers ? formatNumber(selected.subscribers) : "未知"}</dd></div>
              <div><dt>计划发送</dt><dd>{formatDate(selected.planned_send_date)}</dd></div>
              <div><dt>计划发件邮箱</dt><dd>{selected.planned_sender_email || "尚未分配"}</dd></div>
              <div><dt>优先级 / 匹配分</dt><dd>{selected.priority} / {selected.fit_score}</dd></div>
              <div><dt>来源覆盖</dt><dd>{selected.source_count} 个文件 · {selected.source_rows} 次出现</dd></div>
            </dl>
            {selected.reply_status !== "no_reply" && (
              <div className="drawer-reply">
                <span>MICROSOFT 365 回复</span>
                <strong>{selected.reply_from || "已回复"}</strong>
                <p>{selected.reply_summary || "已收到回复。"}</p>
                {selected.rate_info && <p><b>报价：</b>{selected.rate_info}</p>}
                {selected.next_action && <p><b>下一步：</b>{selected.next_action}</p>}
              </div>
            )}
            {selected.channel_url && (
              <a className="channel-link" href={selected.channel_url} target="_blank" rel="noreferrer">
                查看频道 ↗
              </a>
            )}
            <div className="drawer-status">
              <label htmlFor="drawer-status">更新外联状态</label>
              <select
                id="drawer-status"
                value={selected.outreach_status}
                onChange={(event) => updateContact(selected, event.target.value)}
              >
                {Object.entries(statusLabels).map(([value, label]) => (
                  <option value={value} key={value}>{label}</option>
                ))}
              </select>
              <p>标记“已发送”后，系统会自动生成第 4 天和第 9 天的跟进日期。</p>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
