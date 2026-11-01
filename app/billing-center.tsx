"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type BillingRow = {
  reminderTimeZone?: string; reminderAt?: string; reminderLocal?: string; reminderPlanStatus?: string;
  subscriptionId: string; userId: string; email: string; name: string;
  automatic: boolean; periodEnd: string | null; cancelAtPeriodEnd: boolean;
  cancelAt: string | null; status: string; collectionMethod: string; paused: boolean;
  countryCode: string | null; country: string; countrySource: string; ip: string | null;
  amountDue: number | null; currency: string; invoiceError: string | null;
  customerError: string | null; nextPaymentAttempt: string | null;
};
type Snapshot = { rows: BillingRow[]; fetchedAt: string; stale: boolean; error: string | null; partialErrors: number };
const dateTime = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "—";
const day = (value: string | null) => value ? new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value)) : "";
const money = (n: number | null, currency: string) => n === null ? "待确认" : new Intl.NumberFormat("zh-CN", { style: "currency", currency }).format(n / 100);
function stateLabel(r: BillingRow) {
  if (r.cancelAtPeriodEnd || (r.cancelAt && !r.automatic)) return "已取消续费";
  if (r.paused) return "暂停收款";
  if (r.automatic) return r.status === "trialing" ? "试用后扣款" : "自动续费";
  return ({ canceled: "已结束", incomplete_expired: "未完成已过期", incomplete: "支付未完成", past_due: "逾期待处理", unpaid: "未支付", paused: "已暂停" } as Record<string, string>)[r.status] || "非自动扣款";
}
export function BillingCenter() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [reminderFilter, setReminderFilter] = useState("");
  const [country, setCountry] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [sort, setSort] = useState("date");
  const pending = useRef(false);
  const refresh = useCallback(async (force = false) => {
    if (pending.current) return;
    pending.current = true; setBusy(true);
    try {
      const response = await fetch(`/api/billing${force ? "?refresh=1" : ""}`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "无法读取扣款名单");
      setData(result); setError(result.error || "");
    } catch (e) { setError(e instanceof Error ? e.message : "连接失败"); }
    finally { pending.current = false; setBusy(false); }
  }, []);
  useEffect(() => { const initial = window.setTimeout(() => { void refresh(); }, 0); const timer = window.setInterval(() => { void refresh(); }, 60000); return () => { window.clearTimeout(initial); window.clearInterval(timer); }; }, [refresh]);
  const rows = data?.rows;
  const automatic = useMemo(() => (rows || []).filter(r => r.automatic), [rows]);
  const countries = useMemo(() => [...new Set((rows || []).map(r => r.country))].sort((a,b) => a.localeCompare(b, "zh-CN")), [rows]);
  const filtered = useMemo(() => (rows || []).filter(r => {
    const q = query.trim().toLowerCase(), d = day(r.periodEnd);
    return (!q || `${r.email} ${r.name} ${r.userId} ${r.subscriptionId}`.toLowerCase().includes(q)) &&
      (status === "all" || (status === "auto" ? r.automatic : status === "cancel" ? r.cancelAtPeriodEnd || (!!r.cancelAt && !r.automatic) : !r.automatic)) &&
      (!reminderFilter || r.reminderPlanStatus === reminderFilter) && (!country || r.country === country) && (!from || d >= from) && (!to || (!!d && d <= to));
  }).sort((a,b) => sort === "country" ? a.country.localeCompare(b.country, "zh-CN") || (a.periodEnd || "9999").localeCompare(b.periodEnd || "9999") : (a.periodEnd || "9999").localeCompare(b.periodEnd || "9999") || a.email.localeCompare(b.email)), [rows, query, status, country, from, to, sort, reminderFilter]);
  const totals = useMemo(() => {
    const values: Record<string, number> = {};
    for (const r of automatic) if (r.amountDue !== null) values[r.currency] = (values[r.currency] || 0) + r.amountDue;
    return Object.entries(values).map(([c,n]) => money(n,c)).join(" + ") || "—";
  }, [automatic]);
  const dates = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of automatic) { const d = day(r.periodEnd); if (d) counts[d] = (counts[d] || 0) + 1; }
    return Object.entries(counts).sort(([a],[b]) => a.localeCompare(b));
  }, [automatic]);
  function exportCsv() {
    const cells = (v: unknown) => `"${String(v ?? "").replace(/^[=+@\-\t\r]/, "'$&").replaceAll('"','""')}"`;
    const content = [["姓名", "邮箱", "国家(IP)", "定位来源", "状态", "下次预计扣款(北京时间)", "当前周期结束(北京时间)", "预计金额", "币种", "Stripe订阅", "同步时间", "IP时区", "预计提醒(当地时间)", "预计提醒(北京时间)", "提醒计划"], ...filtered.map(r => [r.name,r.email,r.country,r.countrySource,stateLabel(r),r.automatic ? dateTime(r.periodEnd) : "",dateTime(r.periodEnd),r.amountDue === null ? "" : r.amountDue / 100,r.currency,r.subscriptionId,dateTime(data?.fetchedAt || null),r.reminderTimeZone || "",r.reminderLocal || "",dateTime(r.reminderAt || null),r.reminderPlanStatus || ""])].map(r => r.map(cells).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\uFEFF",content], { type: "text/csv;charset=utf-8;" }));
    const a = document.createElement("a"); a.href = url; a.download = `VideoCoding-扣款名单-${day(new Date().toISOString())}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="billing-center">
    <section className="billing-intro">
      <div><p className="billing-eyebrow">VIDEOCODING · SUBSCRIPTIONS</p><h2>自动扣款与用户名单</h2><p>按北京时间排列下一次预计扣款，国家来自 Stripe 保存的 IP 定位。</p></div>
      <div className="billing-sync"><span className={error || data?.stale ? "billing-warning" : "billing-live"}>{busy ? "正在同步 Stripe…" : error || data?.stale ? "数据同步异常" : data ? "● Stripe 实时连接" : "等待连接"}</span><small>每 60 秒自动更新 · {data ? `同步于 ${dateTime(data.fetchedAt)}` : "首次同步可能需要约一分钟"}</small><button onClick={() => void refresh(true)} disabled={busy}>{busy ? "同步中…" : "立即刷新"}</button></div>
    </section>
    {error && <p role="alert" className="billing-alert">{error} {data ? "以下保留上次成功同步的名单，请留意同步时间。" : ""}</p>}
    {data?.stale && !error && <p role="alert" className="billing-alert">名单已超过两分钟未成功更新，请刷新后确认。</p>}
    {data && <>
      <p className="billing-alert">发送计划预览 · 自动邮件尚未启用。按 IP 时区，在扣款日期前 3 天当地 03:00 发送；下表同时显示北京时间。<a href="/billing-email-preview.html" target="_blank" rel="noreferrer"> 查看邮件模板 ↗</a></p>
      <div className="billing-stats">
        <article><span>全部订阅记录</span><strong>{data.rows.length}</strong><small>{new Set(data.rows.map(r => r.userId)).size} 位用户 · 包含取消及失效记录</small></article>
        <article><span>将自动续费</span><strong>{automatic.length}<em> 笔</em></strong><small>{new Set(automatic.map(r => r.userId)).size} 位用户</small></article>
        <article><span>下次预计扣款合计</span><strong>{totals}</strong><small>{automatic.filter(r => r.amountDue === null).length ? `${automatic.filter(r => r.amountDue === null).length} 笔金额待确认，未计入` : "每笔订阅的下一期账单合计"}</small></article>
        <article><span>已取消续费</span><strong>{data.rows.filter(r => r.cancelAtPeriodEnd || (r.cancelAt && !r.automatic)).length}</strong><small>到期后不再自动续费</small></article>
      </div>
      <section className="billing-dates"><div><h3>扣款日期分布</h3><p>点击日期查看当天自动扣款名单</p></div><div className="billing-date-list">{dates.map(([d,n]) => <button className={from === d && to === d ? "selected" : ""} key={d} onClick={() => { setFrom(d); setTo(d); setStatus("auto"); setQuery(""); setCountry(""); }}>{d.slice(5)}<strong>{n} 笔</strong></button>)}</div></section>
      <section className="billing-directory">
        <div className="billing-list-heading"><div><h3>全部名单</h3><p>当前显示 {filtered.length} / {data.rows.length} 笔订阅</p></div><button onClick={exportCsv} disabled={!filtered.length}>导出当前名单 CSV ↗</button></div>
        <div className="billing-filters">
          <label className="billing-search">搜索用户<input placeholder="姓名、邮箱或订阅编号" value={query} onChange={e => setQuery(e.target.value)} /></label>
          <label>提醒计划<select value={reminderFilter} onChange={e => setReminderFilter(e.target.value)}><option value="">全部计划</option><option value="prepared">已计算，待启用</option><option value="timezone_unknown">缺少 IP 时区</option><option value="missed">已错过提醒时间</option><option value="needs_review">需核对</option><option value="excluded">不自动扣款</option></select></label>
          <label>订阅状态<select value={status} onChange={e => setStatus(e.target.value)}><option value="all">全部订阅</option><option value="auto">将自动续费</option><option value="cancel">已取消续费</option><option value="other">所有非自动扣款</option></select></label>
          <label>IP 国家<select value={country} onChange={e => setCountry(e.target.value)}><option value="">所有国家</option>{countries.map(c => <option key={c}>{c}</option>)}</select></label>
          <label>开始日期<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
          <label>结束日期<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
          <label>排序<select value={sort} onChange={e => setSort(e.target.value)}><option value="date">日期从早到晚</option><option value="country">按国家排列</option></select></label>
          <button onClick={() => {setReminderFilter("");setQuery("");setStatus("all");setCountry("");setFrom("");setTo("");setSort("date");}}>重置</button>
        </div>
        <div className="billing-table-wrap"><table className="billing-table"><thead><tr><th>#</th><th>用户 / 邮箱</th><th>国家（IP）</th><th>订阅状态</th><th>下一次预计扣款<small>北京时间 UTC+8</small></th><th>预计金额</th><th>扣款前 3 天提醒<small>当地时间 / 北京时间</small></th><th>Stripe</th></tr></thead><tbody>
          {filtered.map((r,i) => <tr key={r.subscriptionId}><td>{i+1}</td><td><strong>{r.name || "未填写姓名"}</strong><span>{r.email || "未记录邮箱"}</span>{r.customerError && <small className="billing-warning">{r.customerError}</small>}</td><td><strong>{r.country}</strong><small>{r.countrySource}</small>{r.ip && <details><summary>查看 IP</summary><code>{r.ip}</code></details>}</td><td><span className={`billing-badge ${r.automatic ? "auto" : "inactive"}`}>{stateLabel(r)}</span></td><td>{r.automatic ? <><strong>{dateTime(r.periodEnd)}</strong>{r.periodEnd && Date.parse(r.periodEnd) < Date.parse(data.fetchedAt) && <small className="billing-warning">周期已到期，等待 Stripe 更新</small>}</> : <><strong>不自动扣款</strong><small>周期结束：{dateTime(r.periodEnd)}</small></>}</td><td><strong>{r.automatic ? money(r.amountDue,r.currency) : "—"}</strong>{r.automatic && <small>{r.currency.toUpperCase()}{r.invoiceError ? " · 账单待确认" : ""}</small>}</td><td>{r.reminderAt ? <><strong>{r.reminderLocal} · 当地</strong><small>{r.reminderTimeZone}</small><small>北京：{dateTime(r.reminderAt)}</small><small className={r.reminderPlanStatus === "missed" ? "billing-warning" : ""}>{r.reminderPlanStatus === "missed" ? "已错过提醒时间，不补发" : "已计算，待启用"}</small></> : <small>{r.reminderPlanStatus === "excluded" ? "不发送" : r.reminderPlanStatus === "timezone_unknown" ? "缺少 IP 时区，待核对" : "需核对发送时间"}</small>}</td><td><a href={`https://dashboard.stripe.com/subscriptions/${r.subscriptionId}`} target="_blank" rel="noreferrer">查看 ↗</a></td></tr>)}
          {!filtered.length && <tr><td colSpan={8} className="billing-empty">没有符合筛选条件的订阅。可点击“重置”查看全部名单。</td></tr>}
        </tbody></table></div>
      </section>
      <p className="billing-footnote">扣款时间是订阅下一期的预计时间，实际执行及金额以 Stripe 最终账单为准。日期筛选对非自动扣款记录使用周期结束日期。IP 国家反映记录 IP 的位置，可能受 VPN 影响；没有可验证 IP 定位时显示“未知”。{data.partialErrors > 0 ? ` 本次有 ${data.partialErrors} 条记录的客户或账单信息未完整同步。` : ""}</p>
    </>}
    {!data && <div className="billing-empty" role="status">{busy ? "正在读取全部订阅、下一期账单及 IP 国家…" : "暂无可显示数据，请点击立即刷新。"}</div>}
  </div>;
}
