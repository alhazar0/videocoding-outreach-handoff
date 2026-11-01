import { env } from "cloudflare:workers";
import { NextRequest, NextResponse } from "next/server";
import { microsoftOutboundBlockReason } from "@/app/lib/mail-delivery";

type Bindings = {
  DB: D1Database;
};

type SeedContact = {
  id: number;
  email: string;
  channel_name: string;
  channel_url: string;
  platform: string;
  category: string;
  ai_match: boolean;
  curated_ai: boolean;
  ai_confidence: string;
  validation_status: string;
  verification_proof: string;
  email_domain: string;
  subscribers: number | null;
  country: string;
  language: string;
  topic: string;
  priority: string;
  fit_score: number;
  source_file: string;
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
  deal_status: string;
  do_not_contact: boolean;
  notes: string;
};

type SeedPayload = {
  report: Record<string, unknown>;
  contacts: SeedContact[];
};

const MICROSOFT_DAILY_TARGET = 700;
const GOOGLE_DAILY_TARGET = 50;
const DAILY_SEND_MIN = MICROSOFT_DAILY_TARGET + GOOGLE_DAILY_TARGET;
const DAILY_SEND_TARGET = DAILY_SEND_MIN;
const DAILY_SEND_HARD_CAP = DAILY_SEND_MIN;
const TARGET_DAILY_CAPACITY = 1000;
const TARGET_NEW_CONTACTS = 10000;

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    channel_name TEXT NOT NULL,
    channel_url TEXT NOT NULL,
    platform TEXT NOT NULL,
    category TEXT NOT NULL,
    ai_match INTEGER NOT NULL DEFAULT 0,
    curated_ai INTEGER NOT NULL DEFAULT 0,
    ai_confidence TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    verification_proof TEXT NOT NULL,
    email_domain TEXT NOT NULL,
    subscribers INTEGER,
    country TEXT NOT NULL,
    language TEXT NOT NULL,
    topic TEXT NOT NULL,
    priority TEXT NOT NULL,
    fit_score INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    source_count INTEGER NOT NULL,
    source_rows INTEGER NOT NULL,
    planned_send_date TEXT,
    planned_sender_email TEXT,
    send_timezone TEXT,
    timezone_source TEXT NOT NULL DEFAULT 'unknown',
    timezone_confidence TEXT NOT NULL DEFAULT 'unknown',
    send_window TEXT,
    next_eligible_send_at TEXT,
    outreach_status TEXT NOT NULL DEFAULT 'not_contacted',
    first_sent_at TEXT,
    followup1_due TEXT,
    followup2_due TEXT,
    reply_status TEXT NOT NULL DEFAULT 'no_reply',
    reply_received_at TEXT,
    reply_from TEXT,
    reply_summary TEXT,
    rate_info TEXT,
    next_action TEXT,
    deal_status TEXT NOT NULL DEFAULT 'not_started',
    do_not_contact INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
  )`,
  `CREATE INDEX IF NOT EXISTS contacts_ai_queue_idx
   ON contacts(curated_ai, validation_status, planned_send_date, fit_score)`,
  `CREATE INDEX IF NOT EXISTS contacts_filters_idx
   ON contacts(category, validation_status, outreach_status)`,
  `CREATE TABLE IF NOT EXISTS outreach_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS outreach_send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL,
    campaign_kind TEXT NOT NULL DEFAULT 'creator_initial',
    campaign_day_et TEXT NOT NULL,
    status TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT,
    template_version TEXT,
    UNIQUE(contact_id, campaign_kind)
  )`,
  `CREATE TABLE IF NOT EXISTS outreach_sender_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_email TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    daily_target INTEGER NOT NULL,
    daily_hard_cap INTEGER NOT NULL,
    min_interval_seconds INTEGER NOT NULL,
    reply_to_email TEXT NOT NULL,
    sending_domain TEXT NOT NULL,
    configuration_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )`,
];

let initialization: Promise<void> | null = null;

function bindings() {
  return env as unknown as Bindings;
}

async function ensureReplyColumns(DB: D1Database) {
  const columns = await DB.prepare("PRAGMA table_info(contacts)").all<{ name: string }>();
  const existing = new Set(columns.results.map((column) => column.name));
  const additions = [
    ["planned_sender_email", "TEXT"],
    ["reply_received_at", "TEXT"],
    ["reply_from", "TEXT"],
    ["reply_summary", "TEXT"],
    ["rate_info", "TEXT"],
    ["next_action", "TEXT"],
  ];
  for (const [name, type] of additions) {
    if (!existing.has(name)) {
      await DB.prepare(`ALTER TABLE contacts ADD COLUMN ${name} ${type}`).run();
    }
  }
}

async function ensureSendLogColumns(DB: D1Database) {
  const columns = await DB.prepare("PRAGMA table_info(outreach_send_log)").all<{ name: string }>();
  const existing = new Set(columns.results.map((column) => column.name));
  if (!existing.has("template_version")) {
    await DB.prepare("ALTER TABLE outreach_send_log ADD COLUMN template_version TEXT").run();
  }
  const additions = [
    ["sender_email", "TEXT"],
    ["provider", "TEXT"],
    ["experiment_id", "TEXT"],
    ["variant", "TEXT"],
    ["recipient_timezone", "TEXT"],
    ["timezone_source", "TEXT"],
    ["send_window", "TEXT"],
    ["scheduled_for", "TEXT"],
  ];
  for (const [name, type] of additions) {
    if (!existing.has(name)) {
      await DB.prepare(`ALTER TABLE outreach_send_log ADD COLUMN ${name} ${type}`).run();
    }
  }
}

async function ensureCapacityColumns(DB: D1Database) {
  const columns = await DB.prepare("PRAGMA table_info(contacts)").all<{ name: string }>();
  const existing = new Set(columns.results.map((column) => column.name));
  const additions = [
    ["send_timezone", "TEXT"],
    ["timezone_source", "TEXT NOT NULL DEFAULT 'unknown'"],
    ["timezone_confidence", "TEXT NOT NULL DEFAULT 'unknown'"],
    ["send_window", "TEXT"],
    ["next_eligible_send_at", "TEXT"],
  ];
  for (const [name, type] of additions) {
    if (!existing.has(name)) {
      await DB.prepare(`ALTER TABLE contacts ADD COLUMN ${name} ${type}`).run();
    }
  }
  await DB.prepare(`CREATE INDEX IF NOT EXISTS contacts_sender_queue_idx
    ON contacts(planned_send_date,planned_sender_email,next_eligible_send_at)`).run();
  await DB.prepare(`INSERT INTO outreach_sender_accounts(
      sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
      reply_to_email,sending_domain,configuration_note,created_at,updated_at
    ) VALUES('creator@outreach.videocoding.ai','google_workspace_smtp','configuration_required',50,50,120,
      'team@videocoding.ai','outreach.videocoding.ai',
      'Workspace created. Complete SPF, DKIM, DMARC and local SMTP authorization before enabling the 50/day channel.',datetime('now'),datetime('now'))
    ON CONFLICT(sender_email) DO UPDATE SET provider='google_workspace_smtp',daily_target=50,
      daily_hard_cap=50,min_interval_seconds=120,reply_to_email='team@videocoding.ai',
      sending_domain='outreach.videocoding.ai',updated_at=datetime('now')`).run();
  await DB.prepare(`INSERT INTO outreach_sender_accounts(
      sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
      reply_to_email,sending_domain,configuration_note,created_at,updated_at
    ) VALUES('team@videocoding.ai','microsoft_graph','active',700,700,45,
      'team@videocoding.ai','videocoding.ai',
      'Current reply mailbox; campaign target raised to 700/day with 45-60 second jitter by operator on 2026-08-17.',datetime('now'),datetime('now'))
    ON CONFLICT(sender_email) DO UPDATE SET daily_target=700,daily_hard_cap=700,
      min_interval_seconds=45,
      configuration_note='Current reply mailbox; campaign target raised to 700/day with 45-60 second jitter by operator on 2026-08-17.',
      updated_at=datetime('now')`).run();
  await DB.prepare(`INSERT INTO outreach_sender_accounts(
      sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
      reply_to_email,sending_domain,configuration_note,created_at,updated_at
    ) VALUES('pending@outreach.videocoding.ai','dedicated_external_sender','configuration_required',800,800,1,
      'team@videocoding.ai','outreach.videocoding.ai',
      'Requires a dedicated external email service plus SPF, DKIM, DMARC and suppression handling.',datetime('now'),datetime('now'))
    ON CONFLICT(sender_email) DO NOTHING`).run();

  const quarantine = await DB.prepare(
    "SELECT value FROM outreach_settings WHERE key='legacy_leading_n_quarantine_v1'",
  ).first<{ value: string }>();
  if (quarantine?.value !== "complete") {
    await DB.prepare(`UPDATE contacts SET validation_status='needs_validation',planned_send_date=NULL,
      next_eligible_send_at=NULL,notes=notes || CASE WHEN notes='' THEN '' ELSE char(10) END ||
      '隔离：旧数据邮箱本地部分以异常 n 开头；该模式历史退信率过高，需重新核验。',updated_at=datetime('now')
      WHERE source_file NOT LIKE 'continuous_lead_discovery:%' AND lower(substr(email,1,1))='n'
        AND outreach_status='not_contacted' AND first_sent_at IS NULL AND validation_status='send_ready'`).run();
    await DB.prepare(`INSERT INTO outreach_settings(key,value,updated_at)
      VALUES('legacy_leading_n_quarantine_v1','complete',datetime('now'))
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at`).run();
  }
}

function easternDate(dayOffset = 0) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const year = Number(parts.find((part) => part.type === "year")?.value);
  const month = Number(parts.find((part) => part.type === "month")?.value);
  const day = Number(parts.find((part) => part.type === "day")?.value);
  const current = new Date(Date.UTC(year, month - 1, day + dayOffset));
  return current.toISOString().slice(0, 10);
}


// The background scheduler is the sole owner of campaign reservations.
// Page initialization must never re-plan contacts or overwrite queue_policy.

async function initializeDatabase(request: NextRequest) {
  if (initialization) return initialization;
  initialization = (async () => {
    const { DB } = bindings();
    for (const statement of schemaStatements) {
      await DB.prepare(statement).run();
    }
    await ensureReplyColumns(DB);
    await ensureSendLogColumns(DB);
    await ensureCapacityColumns(DB);
    const countRow = await DB.prepare("SELECT COUNT(*) AS count FROM contacts").first<{ count: number }>();
    if ((countRow?.count ?? 0) === 0) {
      const seedUrl = new URL("/data/contacts_seed.json", request.url);
      const seedResponse = await fetch(new Request(seedUrl));
      if (!seedResponse.ok) {
        throw new Error(`Unable to load contact seed: ${seedResponse.status}`);
      }
      const payload = (await seedResponse.json()) as SeedPayload;
      const insertSql = `INSERT OR IGNORE INTO contacts (
        id, email, channel_name, channel_url, platform, category, ai_match, curated_ai,
        ai_confidence, validation_status, verification_proof, email_domain, subscribers,
        country, language, topic, priority, fit_score, source_file, source_count, source_rows,
        planned_send_date, planned_sender_email, outreach_status, first_sent_at, followup1_due, followup2_due,
        reply_status, deal_status, do_not_contact, notes, updated_at
      ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
      )`;
      const generatedAt = `${String(payload.report.generated_at ?? "2026-07-28")}T00:00:00Z`;
      for (let start = 0; start < payload.contacts.length; start += 80) {
        const statements = payload.contacts.slice(start, start + 80).map((contact) =>
          DB.prepare(insertSql).bind(
            contact.id,
            contact.email,
            contact.channel_name,
            contact.channel_url,
            contact.platform,
            contact.category,
            contact.ai_match ? 1 : 0,
            contact.curated_ai ? 1 : 0,
            contact.ai_confidence,
            contact.validation_status,
            contact.verification_proof,
            contact.email_domain,
            contact.subscribers,
            contact.country,
            contact.language,
            contact.topic,
            contact.priority,
            contact.fit_score,
            contact.source_file,
            contact.source_count,
            contact.source_rows,
            contact.planned_send_date,
            contact.planned_sender_email ?? null,
            contact.outreach_status,
            contact.first_sent_at,
            contact.followup1_due,
            contact.followup2_due,
            contact.reply_status,
            contact.deal_status,
            contact.do_not_contact ? 1 : 0,
            contact.notes,
            generatedAt,
          )
        );
        await DB.batch(statements);
      }
    }
  })().catch((error) => {
    initialization = null;
    throw error;
  });
  return initialization;
}

async function summaryResponse(request: NextRequest) {
  await initializeDatabase(request);
  const { DB } = bindings();
  const todayEastern = easternDate();
  const summary = await DB.prepare(`SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN validation_status = 'send_ready' THEN 1 ELSE 0 END) AS send_ready,
    SUM(CASE WHEN validation_status = 'needs_validation' THEN 1 ELSE 0 END) AS needs_validation,
    SUM(CASE WHEN validation_status = 'invalid' THEN 1 ELSE 0 END) AS invalid,
    SUM(CASE WHEN curated_ai = 1 AND validation_status = 'send_ready' THEN 1 ELSE 0 END) AS confirmed_ai,
    SUM(CASE WHEN validation_status = 'send_ready' AND outreach_status = 'not_contacted'
      AND first_sent_at IS NULL AND do_not_contact = 0 THEN 1 ELSE 0 END) AS remaining_send_ready,
    SUM(CASE WHEN ai_match = 1 AND curated_ai = 0 AND validation_status = 'send_ready' THEN 1 ELSE 0 END) AS ai_candidates,
    (SELECT COUNT(*) FROM outreach_send_log WHERE status = 'sent') AS sent,
    SUM(CASE WHEN reply_status NOT IN ('no_reply', '') THEN 1 ELSE 0 END) AS replied,
    SUM(CASE WHEN reply_status = 'interested' THEN 1 ELSE 0 END) AS interested,
    SUM(CASE WHEN deal_status = 'accepted' THEN 1 ELSE 0 END) AS deals,
    (SELECT COUNT(*) FROM outreach_send_log
      WHERE status = 'sent' AND campaign_day_et = ?) AS today_sent
  FROM contacts`).bind(todayEastern).first<Record<string, number>>();
  const nextQueue = await DB.prepare(`SELECT planned_send_date AS date, COUNT(*) AS count
    FROM contacts
    WHERE validation_status = 'send_ready'
      AND outreach_status = 'not_contacted'
      AND do_not_contact = 0
      AND first_sent_at IS NULL AND COALESCE(subscribers,0)>=1000
      AND planned_send_date IS NOT NULL
      AND NOT EXISTS(SELECT 1 FROM outreach_send_log l WHERE l.contact_id=contacts.id AND l.campaign_kind='creator_initial')
    GROUP BY planned_send_date
    ORDER BY planned_send_date
    LIMIT 1`).first<{ date: string; count: number }>();
  const categoryResult = await DB.prepare(`SELECT category, COUNT(*) AS count
    FROM contacts
    GROUP BY category
    ORDER BY count DESC`).all<{ category: string; count: number }>();
  const variantResult = await DB.prepare(`SELECT
      template_version,
      COUNT(*) AS sent,
      SUM(CASE WHEN EXISTS (
        SELECT 1 FROM inbox_events e
        WHERE e.contact_id = l.contact_id
          AND e.event_type IN ('reply', 'interested')
          AND LOWER(e.sender_email) <> 'redacted-3c374d2fef@example.com'
      ) THEN 1 ELSE 0 END) AS qualified_replies,
      SUM(CASE WHEN EXISTS (
        SELECT 1 FROM inbox_events e
        WHERE e.contact_id = l.contact_id AND e.event_type IN ('bounce','sender_restricted')
          AND datetime(e.received_at)>=datetime(l.sent_at)
      ) THEN 1 ELSE 0 END) AS bounces
    FROM outreach_send_log l
    WHERE l.status = 'sent' AND l.template_version IS NOT NULL
    GROUP BY template_version
    ORDER BY template_version`).all<{
      template_version: string;
      sent: number;
      qualified_replies: number;
    bounces: number;
  }>();
  const senderCapacity = await DB.prepare(`SELECT
      SUM(CASE WHEN status IN ('active','active_warmup') THEN 1 ELSE 0 END) AS active_senders,
      SUM(CASE WHEN status IN ('active','active_warmup') THEN daily_target ELSE 0 END) AS active_daily_capacity,
      SUM(CASE WHEN status='configuration_required' THEN daily_target ELSE 0 END) AS pending_daily_capacity
    FROM outreach_sender_accounts`).first<Record<string, number>>();
  const senderAccounts = await DB.prepare(`SELECT
      sender_email,provider,status,daily_target,daily_hard_cap,min_interval_seconds,
      reply_to_email,sending_domain,configuration_note
    FROM outreach_sender_accounts
    ORDER BY CASE provider WHEN 'microsoft_graph' THEN 0 WHEN 'google_workspace_smtp' THEN 1 ELSE 2 END`).all<{
        sender_email: string;
        provider: string;
        status: string;
        daily_target: number;
        daily_hard_cap: number;
        min_interval_seconds: number;
        reply_to_email: string;
        sending_domain: string;
        configuration_note: string;
      }>();
  const healthRows = await DB.prepare(`SELECT key,value,updated_at FROM outreach_settings
    WHERE key IN ('outreach_health_preflight','outreach_health_zero-send','outreach_health_progress')`).all<{
      key: string;
      value: string;
      updated_at: string;
    }>();
  const outreachHealth = Object.fromEntries(healthRows.results.map((row) => {
    try {
      return [row.key.replace('outreach_health_', ''), { ...JSON.parse(row.value), updated_at: row.updated_at }];
    } catch {
      return [row.key.replace('outreach_health_', ''), { status: 'invalid', updated_at: row.updated_at }];
    }
  }));
  const timezoneCoverage = await DB.prepare(`SELECT
      SUM(CASE WHEN send_timezone IS NOT NULL AND timezone_source='public_country' THEN 1 ELSE 0 END) AS known,
      SUM(CASE WHEN timezone_source='deterministic_global_cohort' THEN 1 ELSE 0 END) AS fallback,
      SUM(CASE WHEN next_eligible_send_at IS NOT NULL THEN 1 ELSE 0 END) AS scheduled
    FROM contacts
    WHERE validation_status='send_ready' AND outreach_status='not_contacted'
      AND first_sent_at IS NULL AND do_not_contact=0`).first<Record<string, number>>();
  const windowResult = await DB.prepare(`SELECT COALESCE(send_window,'unplanned') AS window,COUNT(*) AS count
    FROM contacts
    WHERE validation_status='send_ready' AND outreach_status='not_contacted'
      AND first_sent_at IS NULL AND do_not_contact=0
    GROUP BY COALESCE(send_window,'unplanned') ORDER BY count DESC`).all<{ window: string; count: number }>();
  const deliveryHealth = await DB.prepare(`WITH recent AS (
      SELECT contact_id,sent_at FROM outreach_send_log
      WHERE status='sent' AND campaign_kind='creator_initial'
      ORDER BY datetime(sent_at) DESC LIMIT 200
    ) SELECT COUNT(*) AS sent,
      SUM(CASE WHEN EXISTS(SELECT 1 FROM inbox_events e WHERE e.contact_id=recent.contact_id
        AND e.event_type IN ('bounce','sender_restricted') AND datetime(e.received_at)>=datetime(recent.sent_at)) THEN 1 ELSE 0 END) AS bounces
    FROM recent`).first<Record<string, number>>();
  const recentSent = Number(deliveryHealth?.sent ?? 0);
  const recentBounces = Number(deliveryHealth?.bounces ?? 0);
  const rollingBounceRate = recentSent > 0 ? (recentBounces / recentSent) * 100 : 0;
  const activeDailyCapacity = Number(senderCapacity?.active_daily_capacity ?? DAILY_SEND_TARGET);
  const microsoftBlock = await microsoftOutboundBlockReason(DB);
  return NextResponse.json({
    ...summary,
    campaign_day_et: todayEastern,
    next_queue_date: nextQueue?.date ?? null,
    next_queue_count: nextQueue?.count ?? 0,
    categories: categoryResult.results,
    ab_variants: variantResult.results,
    timezone_windows: windowResult.results,
    source_files: 758,
    source_occurrences: 355041,
    generated_at: "2026-07-28",
    daily_send_min: DAILY_SEND_MIN,
    daily_send_target: DAILY_SEND_TARGET,
    daily_send_hard_cap: DAILY_SEND_HARD_CAP,
    daily_remaining: Math.max(0, DAILY_SEND_TARGET - Number(summary?.today_sent ?? 0)),
    active_senders: Number(senderCapacity?.active_senders ?? 1),
    active_daily_capacity: activeDailyCapacity,
    pending_daily_capacity: Number(senderCapacity?.pending_daily_capacity ?? 0),
    sender_accounts: senderAccounts.results,
    outreach_health: outreachHealth,
    target_daily_capacity: TARGET_DAILY_CAPACITY,
    target_new_contacts: TARGET_NEW_CONTACTS,
    days_to_target_current: Math.ceil(TARGET_NEW_CONTACTS / Math.max(1, activeDailyCapacity)),
    days_to_target_scaled: Math.ceil(TARGET_NEW_CONTACTS / TARGET_DAILY_CAPACITY),
    timezone_known: Number(timezoneCoverage?.known ?? 0),
    timezone_fallback: Number(timezoneCoverage?.fallback ?? 0),
    timezone_scheduled: Number(timezoneCoverage?.scheduled ?? 0),
    rolling_delivery_sample: recentSent,
    rolling_bounces: recentBounces,
    rolling_bounce_rate: Math.round(rollingBounceRate * 100) / 100,
    scale_ready: !microsoftBlock && recentSent >= 100 && rollingBounceRate < 2,
  });
}

async function contactsResponse(request: NextRequest) {
  await initializeDatabase(request);
  const { DB } = bindings();
  const params = request.nextUrl.searchParams;
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const limit = Math.min(100, Math.max(20, Number(params.get("limit") ?? "40") || 40));
  const filters: string[] = ["1 = 1"];
  const values: unknown[] = [];
  const queue = params.get("queue");
  const category = params.get("category");
  const validation = params.get("validation");
  const status = params.get("status");
  const query = params.get("q")?.trim().toLowerCase();
  const todayEastern = easternDate();

  if (queue === "today_ai") {
    filters.push(
      "validation_status = 'send_ready'",
      "outreach_status = 'not_contacted'",
      "do_not_contact = 0",
      `planned_send_date = (
        SELECT MIN(planned_send_date)
        FROM contacts
        WHERE validation_status = 'send_ready'
          AND outreach_status = 'not_contacted'
          AND do_not_contact = 0
      )`,
    );
  } else if (queue === "confirmed_ai") {
    filters.push("curated_ai = 1", "validation_status = 'send_ready'");
  } else if (queue === "ai_candidates") {
    filters.push("ai_match = 1", "curated_ai = 0");
  } else if (queue === "replies") {
    filters.push("reply_status NOT IN ('no_reply', '')");
  } else if (queue === "today_sent") {
    filters.push(
      `EXISTS (
        SELECT 1 FROM outreach_send_log l
        WHERE l.contact_id = contacts.id
          AND l.status = 'sent'
          AND l.campaign_day_et = ?
      )`,
    );
    values.push(todayEastern);
  }
  if (category) {
    filters.push("category = ?");
    values.push(category);
  }
  if (validation) {
    filters.push("validation_status = ?");
    values.push(validation);
  }
  if (status) {
    filters.push("outreach_status = ?");
    values.push(status);
  }
  if (query) {
    filters.push("(LOWER(email) LIKE ? OR LOWER(channel_name) LIKE ? OR LOWER(topic) LIKE ?)");
    const like = `%${query}%`;
    values.push(like, like, like);
  }
  const where = filters.join(" AND ");
  const totalRow = await DB.prepare(`SELECT COUNT(*) AS count FROM contacts WHERE ${where}`)
    .bind(...values)
    .first<{ count: number }>();
  const order = queue === "today_sent"
    ? "first_sent_at DESC, channel_name ASC"
    : queue === "replies"
    ? "reply_received_at DESC, updated_at DESC"
    : queue === "confirmed_ai" || queue === "today_ai"
    ? "planned_send_date ASC, fit_score DESC, subscribers DESC"
    : "fit_score DESC, subscribers DESC, channel_name ASC";
  const rows = await DB.prepare(`SELECT * FROM contacts
    WHERE ${where}
    ORDER BY ${order}
    LIMIT ? OFFSET ?`)
    .bind(...values, limit, (page - 1) * limit)
    .all<Record<string, unknown>>();
  return NextResponse.json({
    rows: rows.results,
    total: totalRow?.count ?? 0,
    page,
    limit,
    pages: Math.max(1, Math.ceil((totalRow?.count ?? 0) / limit)),
  });
}

export async function GET(request: NextRequest) {
  try {
    let lastError: unknown;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        if (request.nextUrl.searchParams.get("view") === "summary") {
          return await summaryResponse(request);
        }
        return await contactsResponse(request);
      } catch (error) {
        lastError = error;
        const message = error instanceof Error ? error.message : String(error);
        if (!/D1_ERROR|SQLITE_BUSY|internal error/i.test(message) || attempt === 4) throw error;
        await new Promise((resolve) => setTimeout(resolve, 150 * (attempt + 1)));
      }
    }
    throw lastError;
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unknown database error" },
      { status: 500 },
    );
  }
}

export async function PATCH(request: NextRequest) {
  try {
    await initializeDatabase(request);
    const { DB } = bindings();
    const body = (await request.json()) as {
      id: number;
      outreach_status?: string;
      first_sent_at?: string;
      reply_status?: string;
      reply_received_at?: string;
      reply_from?: string;
      reply_summary?: string;
      rate_info?: string;
      next_action?: string;
      deal_status?: string;
      do_not_contact?: boolean;
      ai_match?: boolean;
      curated_ai?: boolean;
      notes?: string;
    };
    if (!body.id) {
      return NextResponse.json({ error: "Contact id is required" }, { status: 400 });
    }

    const allowedOutreach = new Set([
      "not_contacted", "personalized", "drafted", "sent", "followup1",
      "followup2", "replied", "interested", "closed", "bounced", "sender_restricted", "opted_out",
    ]);
    const fields: string[] = [];
    const values: unknown[] = [];
    if (body.outreach_status && allowedOutreach.has(body.outreach_status)) {
      fields.push("outreach_status = ?");
      values.push(body.outreach_status);
      if (body.outreach_status === "sent") {
        fields.push("first_sent_at = COALESCE(first_sent_at, datetime('now'))");
        fields.push("followup1_due = COALESCE(followup1_due, datetime('now', '+24 hours'))");
        fields.push("followup2_due = COALESCE(followup2_due, date('now', '+4 days'))");
      } else if (body.outreach_status === "replied" || body.outreach_status === "interested") {
        fields.push("first_sent_at = COALESCE(first_sent_at, datetime('now'))");
      }
    }
    if (body.reply_status) {
      fields.push("reply_status = ?");
      values.push(body.reply_status);
    }
    if (typeof body.first_sent_at === "string") {
      fields.push("first_sent_at = ?");
      values.push(body.first_sent_at.slice(0, 40));
    }
    if (typeof body.reply_received_at === "string") {
      fields.push("reply_received_at = ?");
      values.push(body.reply_received_at.slice(0, 40));
    }
    if (typeof body.reply_from === "string") {
      fields.push("reply_from = ?");
      values.push(body.reply_from.slice(0, 320));
    }
    if (typeof body.reply_summary === "string") {
      fields.push("reply_summary = ?");
      values.push(body.reply_summary.slice(0, 2000));
    }
    if (typeof body.rate_info === "string") {
      fields.push("rate_info = ?");
      values.push(body.rate_info.slice(0, 1200));
    }
    if (typeof body.next_action === "string") {
      fields.push("next_action = ?");
      values.push(body.next_action.slice(0, 1200));
    }
    if (body.deal_status) {
      fields.push("deal_status = ?");
      values.push(body.deal_status);
    }
    if (typeof body.do_not_contact === "boolean") {
      fields.push("do_not_contact = ?");
      values.push(body.do_not_contact ? 1 : 0);
    }
    if (typeof body.ai_match === "boolean") {
      fields.push("ai_match = ?");
      values.push(body.ai_match ? 1 : 0);
    }
    if (typeof body.curated_ai === "boolean") {
      fields.push("curated_ai = ?");
      values.push(body.curated_ai ? 1 : 0);
    }
    if (typeof body.notes === "string") {
      fields.push("notes = ?");
      values.push(body.notes.slice(0, 4000));
    }
    if (!fields.length) {
      return NextResponse.json({ error: "No valid updates supplied" }, { status: 400 });
    }
    fields.push("updated_at = datetime('now')");
    await DB.prepare(`UPDATE contacts SET ${fields.join(", ")} WHERE id = ?`)
      .bind(...values, body.id)
      .run();
    const updated = await DB.prepare("SELECT * FROM contacts WHERE id = ?")
      .bind(body.id)
      .first<Record<string, unknown>>();
    return NextResponse.json(updated);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unknown database error" },
      { status: 500 },
    );
  }
}
