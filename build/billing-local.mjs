import { readFile, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
const localRequire = createRequire(import.meta.url);
const scheduleModule = join(homedir(), 'Projects/videocoding-renewal-reminders/deploy/backend-module/billing/renewalSchedule.js');
function withReminderPlan(snapshot) {
  const { resolveIpTimezone, scheduledReminderAt, SEND_WINDOW_MS } = localRequire(scheduleModule);
  return { ...snapshot, reminderSendingEnabled: false, rows: snapshot.rows.map(row => {
    if (!row.automatic) return { ...row, reminderPlanStatus: 'excluded' };
    const zone = resolveIpTimezone(row.ip);
    if (!zone?.timeZone) return { ...row, reminderPlanStatus: 'timezone_unknown' };
    try {
      const at = scheduledReminderAt(row.periodEnd, zone.timeZone, 3);
      return { ...row, reminderTimeZone: zone.timeZone, reminderAt: at, reminderLocal: new Intl.DateTimeFormat('zh-CN', { timeZone: zone.timeZone, dateStyle: 'short', timeStyle: 'short', hour12: false }).format(new Date(at)), reminderPlanStatus: Date.now() >= Date.parse(at) + SEND_WINDOW_MS ? 'missed' : 'prepared' };
    } catch { return { ...row, reminderTimeZone: zone.timeZone, reminderPlanStatus: 'needs_review' }; }
  }) };
}

const privateDir = join(homedir(), '.videocoding');
const sourceFile = join(homedir(), 'Reports/videocoding-billing-2026-09-07/billing-source.json');
const cacheFile = join(privateDir, 'billing-dashboard-cache.json');
const iso = value => value ? new Date(value * 1000).toISOString() : null;
const id = value => typeof value === 'string' ? value : value?.id;
export function classifySubscription(s) {
  const end = s.status === 'trialing' && s.trial_end ? s.trial_end :
    s.current_period_end || Math.min(...(s.items?.data || []).map(i => i.current_period_end).filter(Boolean));
  const periodEnd = Number.isFinite(end) ? iso(end) : null;
  const automatic = ['active', 'trialing'].includes(s.status) && s.collection_method === 'charge_automatically' &&
    !s.cancel_at_period_end && !s.pause_collection && !(s.cancel_at && s.cancel_at <= end);
  return { automatic, periodEnd, cancelAtPeriodEnd: !!s.cancel_at_period_end, cancelAt: iso(s.cancel_at),
    status: s.status, collectionMethod: s.collection_method, paused: !!s.pause_collection };
}
export function ipLocation(customer) {
  const location = customer.tax?.location;
  const countryCode = customer.tax?.ip_address && location?.source === 'ip_address' ? location.country : null;
  return { countryCode, country: countryCode ? new Intl.DisplayNames(['zh-CN'], { type: 'region' }).of(countryCode) : '未知',
    countrySource: countryCode ? 'Stripe IP 定位' : '未记录可验证的 IP 国家', ip: customer.tax?.ip_address || null };
}
export function isLocalRequest(req) {
  const host = req.headers.host;
  return ['localhost:3000', '127.0.0.1:3000', '[::1]:3000'].includes(host) &&
    ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress) &&
    (!req.headers.origin || req.headers.origin === `http://${host}`) &&
    (!req.headers['sec-fetch-site'] || ['same-origin', 'none'].includes(req.headers['sec-fetch-site']));
}
async function pool(items, fn) {
  const result = new Array(items.length); let next = 0;
  await Promise.all(Array.from({ length: Math.min(8, items.length) }, async () => {
    while (next < items.length) { const index = next++; result[index] = await fn(items[index]); }
  })); return result;
}
export function billingLocal() {
  let snapshot = null, inFlight = null, lastError = null, lastAttempt = 0;
  async function sync() {
    lastAttempt = Date.now();
    const envText = await readFile(join(privateDir, 'stripe-live.env'), 'utf8');
    const key = process.env.STRIPE_SECRET_KEY || envText.match(/^STRIPE_SECRET_KEY\s*=\s*["']?([^\s"']+)/m)?.[1];
    if (!key) throw new Error('missing_key');
    async function get(path, params = {}) {
      const url = new URL(`https://api.stripe.com/v1${path}`);
      for (const [k, v] of Object.entries(params)) if (v) url.searchParams.set(k, v);
      for (let attempt = 0; attempt < 3; attempt++) {
        const response = await fetch(url, { headers: { Authorization: `Bearer ${key}`, 'Stripe-Version': '2024-06-20' }, signal: AbortSignal.timeout(25000) });
        if ((response.status === 429 || response.status >= 500) && attempt < 2) {
          await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1))); continue;
        }
        const body = await response.json();
        if (!response.ok) throw new Error(`stripe_${response.status}`);
        return body;
      }
    }
    const baseline = await readFile(sourceFile, 'utf8').then(JSON.parse).catch(() => ({ rows: [] }));
    const known = new Map(baseline.rows.map(r => [r.subscriptionId, r]));
    const subscriptions = []; let after;
    do {
      const page = await get('/subscriptions', { status: 'all', limit: '100', starting_after: after });
      subscriptions.push(...page.data);
      after = page.has_more ? page.data.at(-1)?.id : null;
    } while (after);
    const scoped = subscriptions.filter(s => known.has(s.id) || (s.metadata?.edition === 'overseas' && s.metadata?.userId));
    const customers = new Map();
    const rows = await pool(scoped, async s => {
      const base = known.get(s.id), customerId = id(s.customer);
      if (!customers.has(customerId)) customers.set(customerId, get(`/customers/${encodeURIComponent(customerId)}`, { 'expand[]': 'tax' }).catch(() => null));
      const customer = await customers.get(customerId);
      const state = classifySubscription(s);
      const row = { subscriptionId: s.id, customerId, userId: s.metadata?.userId || base?.userId || customerId,
        email: customer?.email || base?.email || '', name: customer?.name || base?.name || '', ...state,
        ...ipLocation(customer || {}), amountDue: null, currency: s.currency || 'usd',
        invoiceError: null, customerError: customer ? null : '客户信息暂时无法同步', nextPaymentAttempt: null };
      if (state.automatic) {
        try {
          const invoice = await get('/invoices/upcoming', { subscription: s.id, customer: customerId });
          row.amountDue = invoice.amount_due; row.currency = invoice.currency;
          row.nextPaymentAttempt = iso(invoice.next_payment_attempt);
        } catch { row.invoiceError = '待确认'; }
      }
      return row;
    });
    rows.sort((a,b) => (a.periodEnd || '9999').localeCompare(b.periodEnd || '9999') || a.email.localeCompare(b.email));
    const value = { fetchedAt: new Date().toISOString(), rows, source: 'Stripe Live', refreshSeconds: 60,
      partialErrors: rows.filter(r => r.invoiceError || r.customerError).length };
    await writeFile(cacheFile, JSON.stringify(value), { mode: 0o600 });
    snapshot = value; lastError = null;
  }
  return {
    name: 'videocoding-local-billing',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (req.url?.split('?')[0] === '/api/coupon-audit') {
          res.setHeader('Cache-Control', 'private, no-store');
          res.setHeader('Content-Type', 'application/json; charset=utf-8');
          if (!isLocalRequest(req) || req.method !== 'GET') { res.statusCode=403;res.end('{}');return; }
          try { res.end(await readFile(join(homedir(), 'Reports/coupon-audit-2026-09-10/report.json'), 'utf8')); }
          catch { res.statusCode=503;res.end(JSON.stringify({error:'审查名单尚未生成'})); }
          return;
        }
        if (req.url?.split('?')[0] !== '/api/billing') return next();
        res.setHeader('Cache-Control', 'private, no-store');
        res.setHeader('Content-Type', 'application/json; charset=utf-8');
        res.setHeader('X-Content-Type-Options', 'nosniff');
        if (!isLocalRequest(req)) { res.statusCode = 403; res.end(JSON.stringify({ error: '仅支持本机访问' })); return; }
        if (req.method !== 'GET') { res.statusCode = 405; res.setHeader('Allow', 'GET'); res.end('{}'); return; }
        if (!snapshot) snapshot = await readFile(cacheFile, 'utf8').then(JSON.parse).catch(() => null);
        const force = new URL(req.url, 'http://localhost:3000').searchParams.has('refresh');
        const age = snapshot ? Date.now() - Date.parse(snapshot.fetchedAt) : Infinity;
        if (!inFlight && (age >= 55000 || force) && Date.now() - lastAttempt > 10000) {
          inFlight = sync().catch(() => { lastError = 'Stripe 同步失败，请稍后重试。'; }).finally(() => { inFlight = null; });
        }
        if (inFlight) await inFlight;
        if (!snapshot) { res.statusCode = 503; res.end(JSON.stringify({ error: lastError || '正在连接 Stripe，请稍后重试' })); return; }
        res.end(JSON.stringify({ ...withReminderPlan(snapshot), stale: !!lastError || Date.now() - Date.parse(snapshot.fetchedAt) > 120000, error: lastError }));
      });
    },
  };
}
