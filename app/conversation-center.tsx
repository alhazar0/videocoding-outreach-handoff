"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type ConversationStatus = "needs_reply" | "waiting_creator" | "reviewed" | "closed";

type ConversationRow = {
  conversation_key: string;
  contact_id: number | null;
  message_at: string;
  direction: "inbound" | "outbound";
  subject: string;
  body_text: string;
  event_type: string;
  sender_name: string;
  peer_email: string;
  message_count: number;
  channel_name: string | null;
  contact_email: string | null;
  channel_url: string | null;
  conversation_status: ConversationStatus;
  work_item_id: number | null;
  work_status: string | null;
  kind: string | null;
  workflow_stage: string | null;
  risk_level: string | null;
};

type ConversationListResponse = {
  rows: ConversationRow[];
  total: number;
  page: number;
  limit: number;
  pages: number;
  summary: Record<ConversationStatus | "total", number>;
  error?: string;
};

type ConversationMessage = {
  message_id: string;
  message_at: string;
  direction: "inbound" | "outbound";
  subject: string;
  body_text: string;
  sender_name: string;
  sender_email: string;
  recipient_email: string;
  event_type: string;
};

type ConversationDetail = {
  conversation: ConversationRow;
  messages: ConversationMessage[];
  can_reply: boolean;
  reply: null | {
    work_item_id: number;
    status: string;
    kind: string;
    workflow_stage: string;
    risk_level: string;
    template_key: string | null;
    subject: string;
    body: string;
    decision_reason: string;
  };
  error?: string;
};

const statusLabels: Record<ConversationStatus, string> = {
  needs_reply: "待回复",
  waiting_creator: "等待对方",
  reviewed: "已处理",
  closed: "已关闭",
};

function displayDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function compact(value: string, limit = 150) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit)}…` : normalized;
}

function replySubject(value: string) {
  const normalized = String(value || "").trim();
  const original = normalized.replace(/^((re|fw|fwd):\s*)+/i, "").trim() || "VideoCoding";
  return `Re: ${original}`;
}

export function ConversationCenter() {
  const [list, setList] = useState<ConversationListResponse | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [selectedKey, setSelectedKey] = useState("");
  const [status, setStatus] = useState<ConversationStatus | "all">("needs_reply");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(queryInput.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

  const loadList = useCallback(async () => {
    try {
      setLoadingList(true);
      const params = new URLSearchParams({ view: "conversations", page: String(page), limit: "30", status });
      if (query) params.set("q", query);
      const response = await fetch(`/api/operations?${params.toString()}`, { cache: "no-store" });
      const payload = await response.json() as ConversationListResponse;
      if (!response.ok) throw new Error(payload.error || "无法读取会话");
      setList(payload);
      setSelectedKey((current) => payload.rows.some((row) => row.conversation_key === current)
        ? current
        : payload.rows[0]?.conversation_key || "");
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取会话");
    } finally {
      setLoadingList(false);
    }
  }, [page, query, status]);

  const loadDetail = useCallback(async (key: string) => {
    if (!key) {
      setDetail(null);
      return;
    }
    try {
      setLoadingDetail(true);
      const params = new URLSearchParams({ view: "conversation", key });
      const response = await fetch(`/api/operations?${params.toString()}`, { cache: "no-store" });
      const payload = await response.json() as ConversationDetail;
      if (!response.ok) throw new Error(payload.error || "无法读取完整会话");
      setDetail(payload);
      setSubject(replySubject(payload.reply?.subject || payload.conversation.subject || "VideoCoding"));
      setBody(payload.reply?.body || "");
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取完整会话");
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(loadList, 0);
    return () => window.clearTimeout(timer);
  }, [loadList]);

  useEffect(() => {
    const timer = window.setTimeout(() => loadDetail(selectedKey), 0);
    return () => window.clearTimeout(timer);
  }, [loadDetail, selectedKey]);

  const post = useCallback(async (payload: Record<string, unknown>) => {
    const response = await fetch("/api/operations", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json() as { error?: string };
    if (!response.ok) throw new Error(result.error || "操作失败");
    return result;
  }, []);

  const saveDraft = useCallback(async () => {
    if (!detail?.reply?.work_item_id) throw new Error("该会话没有可用的回复任务");
    await post({ action: "save_draft", id: detail.reply.work_item_id, subject, draft_body: body });
  }, [body, detail, post, subject]);

  const handleSave = async () => {
    try {
      setBusy("save");
      setNotice("");
      await saveDraft();
      setNotice("草稿已保存，未发送。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setBusy("");
    }
  };

  const handleSend = async () => {
    if (!detail?.reply?.work_item_id || !body.trim()) return;
    const creator = detail.conversation.channel_name || detail.conversation.peer_email || "这位博主";
    if (!window.confirm(`确认已阅读完整会话，并现在通过 team@videocoding.ai 回复 ${creator}？`)) return;
    try {
      setBusy("send");
      setNotice("");
      setError("");
      await saveDraft();
      await post({ action: "send_reply", id: detail.reply.work_item_id });
      setNotice("回复已发送；已发邮件将在下一次 Outlook 同步后出现在时间线。");
      await Promise.all([loadList(), loadDetail(selectedKey)]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "发送失败");
    } finally {
      setBusy("");
    }
  };

  const statusOptions = useMemo(() => [
    ["needs_reply", "待回复", list?.summary.needs_reply || 0],
    ["waiting_creator", "等待对方", list?.summary.waiting_creator || 0],
    ["reviewed", "已处理", list?.summary.reviewed || 0],
    ["closed", "已关闭", list?.summary.closed || 0],
    ["all", "全部会话", list?.summary.total || 0],
  ] as const, [list]);

  return (
    <div className="page-stack conversation-page">
      <section className="conversation-hero">
        <div><span className="section-kicker">UNIFIED CREATOR CONVERSATIONS</span><h2>所有收发邮件已聚合到平台。</h2><p>按会话查看完整历史，基于最新来信回复；发送仍通过 Microsoft 365 并写入审计记录。</p></div>
        <div className="conversation-hero-count"><span>真实待回复</span><strong>{list?.summary.needs_reply ?? "—"}</strong><small>共 {list?.summary.total?.toLocaleString() || "—"} 个历史会话</small></div>
      </section>

      {(notice || error) && <div className={`ops-feedback ${error ? "ops-feedback-error" : ""}`}>{error || notice}</div>}

      <section className="conversation-controls">
        <div className="conversation-tabs">
          {statusOptions.map(([key, label, count]) => <button key={key} className={status === key ? "active" : ""} onClick={() => { setStatus(key); setPage(1); }}><span>{label}</span><strong>{count.toLocaleString()}</strong></button>)}
        </div>
        <label className="conversation-search"><span>搜索会话</span><input value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="博主、邮箱、主题或正文" /></label>
      </section>

      <section className="conversation-workspace">
        <aside className="conversation-list">
          {loadingList && !list && <div className="conversation-loading">正在读取会话…</div>}
          {!loadingList && list?.rows.length === 0 && <div className="conversation-loading">没有匹配的会话。</div>}
          {list?.rows.map((row) => (
            <button key={row.conversation_key} className={selectedKey === row.conversation_key ? "active" : ""} onClick={() => setSelectedKey(row.conversation_key)}>
              <span className={`conversation-state state-${row.conversation_status}`}>{statusLabels[row.conversation_status]}</span>
              <strong>{row.channel_name || row.sender_name || row.peer_email || "未匹配联系人"}</strong>
              <small>{row.peer_email || row.contact_email || "—"} · {row.message_count} 封</small>
              <p>{compact(row.body_text || row.subject)}</p>
              <time>{displayDate(row.message_at)}</time>
            </button>
          ))}
          {list && list.pages > 1 && <div className="conversation-pagination"><button disabled={page <= 1 || loadingList} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><span>{page} / {list.pages}</span><button disabled={page >= list.pages || loadingList} onClick={() => setPage((value) => Math.min(list.pages, value + 1))}>下一页</button></div>}
        </aside>

        <article className="conversation-detail">
          {!selectedKey && <div className="conversation-empty"><strong>选择一个会话</strong><p>左侧可搜索全部历史收发记录。</p></div>}
          {selectedKey && loadingDetail && !detail && <div className="conversation-empty">正在读取完整会话…</div>}
          {detail && (
            <>
              <header className="conversation-detail-heading">
                <div><span className={`conversation-state state-${detail.conversation.conversation_status}`}>{statusLabels[detail.conversation.conversation_status]}</span><h3>{detail.conversation.channel_name || detail.conversation.sender_name || detail.conversation.peer_email}</h3><p>{detail.conversation.contact_email || detail.conversation.peer_email} · {detail.messages.length} 封邮件</p></div>
                {detail.conversation.channel_url && <a href={detail.conversation.channel_url} target="_blank" rel="noreferrer">YouTube ↗</a>}
              </header>
              <div className="conversation-timeline">
                {detail.messages.map((message) => (
                  <section key={`${message.direction}-${message.message_id}`} className={`conversation-message ${message.direction}`}>
                    <div><strong>{message.direction === "outbound" ? "VideoCoding" : message.sender_name || message.sender_email || "博主"}</strong><time>{displayDate(message.message_at)}</time></div>
                    <small>{message.subject || "无主题"}</small>
                    <pre>{message.body_text || "（无可显示正文）"}</pre>
                  </section>
                ))}
              </div>
              {detail.can_reply && detail.reply ? (
                <section className="conversation-composer">
                  <div className="conversation-decision"><span className={`ops-risk ops-risk-${detail.reply.risk_level}`}>{detail.reply.risk_level === "high" ? "高风险 · 必须人工确认" : "发送前审核"}</span><p>{detail.reply.decision_reason || "请根据完整会话确认回复。"}</p></div>
                  <label>主题<input value={subject} onChange={(event) => setSubject(event.target.value)} /></label>
                  <label>回复正文<textarea rows={12} value={body} onChange={(event) => setBody(event.target.value)} placeholder="阅读完整会话后编辑回复…" /></label>
                  <div className="conversation-composer-actions"><button disabled={Boolean(busy)} onClick={handleSave}>{busy === "save" ? "保存中…" : "只保存草稿"}</button><button className="primary-button" disabled={Boolean(busy) || !body.trim()} onClick={handleSend}>{busy === "send" ? "发送中…" : "确认并通过 Outlook 回复"}</button></div>
                </section>
              ) : (
                <section className="conversation-no-reply"><strong>{detail.conversation.conversation_status === "waiting_creator" ? "最新一封由我们发出，正在等待对方。" : "该会话当前不需要回复。"}</strong><p>当对方有新来信时，会自动进入“待回复”。</p></section>
              )}
            </>
          )}
        </article>
      </section>
    </div>
  );
}
