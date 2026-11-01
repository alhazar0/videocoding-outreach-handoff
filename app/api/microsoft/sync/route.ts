import { NextResponse } from "next/server";
import { isSenderRestriction, recordSenderRestriction, RESTRICTION_SUMMARY } from "@/app/lib/mail-delivery";
import {
  CAMPAIGN_START_AT,
  GraphMessage,
  database,
  ensureMicrosoftSchema,
  getSetting,
  refreshAccessToken,
  setSetting,
  stripHtml,
} from "@/app/lib/microsoft";

type ContactMatch = {
  id: number;
  email: string;
  channel_name: string;
  channel_url: string;
  first_sent_at: string | null;
};

const NEGATIVE_PATTERNS = [
  /\bnot interested\b/i,
  /\bno thanks\b/i,
  /\bnot a fit\b/i,
  /\bremove me\b/i,
  /\bunsubscribe\b/i,
  /\bdo not contact\b/i,
];

const INTEREST_PATTERNS = [
  /\binterested\b/i,
  /\bcollaborat(?:e|ion)\b/i,
  /\bsponsor(?:ed|ship)?\b/i,
  /\bcampaign brief\b/i,
  /\bcreator access\b/i,
  /\bwould love\b/i,
  /\bsounds good\b/i,
  /\bwant to try\b/i,
  /\bmy rate\b/i,
  /\brate card\b/i,
  /\bprice\b/i,
  /\bhappy to (?:help|discuss|talk)\b/i,
  /\$\s?\d/i,
];

const AUTOMATED_PATTERNS = [
  /\bautomatic reply\b/i,
  /\bautomated response\b/i,
  /\bout of office\b/i,
  /we(?:'|’)ve received your message/i,
  /received your message and will get back/i,
  /we usually respond in/i,
  /\bguest posts?\b/i,
  /\blink insertions?\b/i,
  /congradulations, you are on the list/i,
];

const CAMPAIGN_SUBJECT_PATTERNS = [
  /paid partnership/i,
  /paid promotion/i,
  /still interested in videocoding/i,
  /creator partnership/i,
];

function sender(message: GraphMessage) {
  return {
    name: message.from?.emailAddress?.name?.trim() ?? "",
    email: message.from?.emailAddress?.address?.trim().toLowerCase() ?? "",
  };
}

function messageText(message: GraphMessage) {
  const body = message.body?.content ?? "";
  return stripHtml(body || message.bodyPreview || "");
}

function newestReply(text: string) {
  const markers = [
    /(?:^|\n)\s*On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[\s\S]{0,260}?\bwrote:\s*/i,
    /(?:^|\n)\s*From:\s*[^\n]{1,260}/i,
    /(?:^|\n)\s*-{2,}\s*Original Message\s*-{2,}/i,
  ];
  const indexes = markers
    .map((pattern) => text.search(pattern))
    .filter((index) => index > 0);
  const end = indexes.length ? Math.min(...indexes) : text.length;
  return text.slice(0, end).trim();
}

function isBounce(message: GraphMessage, text: string) {
  const from = sender(message);
  return /undeliverable|delivery status notification|delivery has failed/i.test(message.subject ?? "")
    || /couldn(?:'|’)t be delivered|address not found|delivery failed|recipient address rejected|rejected your message|message was rejected/i.test(text)
    || /microsoft outlook/i.test(from.name) && /delivered|delivery/i.test(text);
}

function failedAddress(text: string) {
  // Preserve the exact envelope recipient before considering explanatory prose.
  const explicit = [...text.matchAll(/^[ \t]*(?:Recipient Address|(?:Final|Original)-Recipient)[ \t]*:[ \t]*(?:rfc822[ \t]*;[ \t]*)?<?([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,})>?[ \t\r]*$/gim)]
    .map((match) => match[1]);
  const addresses = [...explicit, ...(text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) ?? [])];
  return addresses
    .map((email) => email.toLowerCase().replace(/[.,;:]+$/, ""))
    .find((email) =>
      email !== "team@videocoding.ai"
      && !email.includes("microsoft")
      && !email.endsWith(".onmicrosoft.com")
      && !email.endsWith("@videocoding.ai"),
    ) ?? null;
}

function replyClassification(text: string) {
  if (AUTOMATED_PATTERNS.some((pattern) => pattern.test(text))) {
    return {
      eventType: "automated",
      outreachStatus: "sent",
      replyStatus: "no_reply",
      nextAction: "自动回复；无需人工处理。",
      doNotContact: 0,
    };
  }
  if (NEGATIVE_PATTERNS.some((pattern) => pattern.test(text))) {
    return {
      eventType: "opted_out",
      outreachStatus: "opted_out",
      replyStatus: "not_interested",
      nextAction: "无需回复；已加入停止联系名单。",
      doNotContact: 1,
    };
  }
  if (INTEREST_PATTERNS.some((pattern) => pattern.test(text))) {
    return {
      eventType: "interested",
      outreachStatus: "interested",
      replyStatus: "interested",
      nextAction: "回复并提供测试权限、合作 brief，确认形式与报价。",
      doNotContact: 0,
    };
  }
  return {
    eventType: "reply",
    outreachStatus: "replied",
    replyStatus: "replied",
    nextAction: "阅读完整回复并确认下一步。",
    doNotContact: 0,
  };
}

function compactPreview(text: string) {
  return text
    .replace(/\s+/g, " ")
    .replace(/^(hi|hello|hey)\s+[^,]{0,60},?\s*/i, "")
    .trim()
    .slice(0, 700);
}

function isCampaignRelated(message: GraphMessage, contact: ContactMatch | null, bounce: boolean) {
  if (contact || bounce) return true;
  return CAMPAIGN_SUBJECT_PATTERNS.some((pattern) => pattern.test(message.subject ?? ""));
}

async function getContact(DB: D1Database, email: string | null) {
  if (!email) return null;
  return DB.prepare(`SELECT id, email, channel_name, channel_url, first_sent_at
    FROM contacts WHERE LOWER(email) = ? LIMIT 1`)
    .bind(email.toLowerCase())
    .first<ContactMatch>();
}

function channelNameFromSubject(subject: string) {
  const match = subject.match(
    /paid (?:partnership|promotion):\s*(.+?)\s*[×x]\s*videocoding(?:\s+ai)?/i,
  );
  return match?.[1]?.replace(/^(?:re|fw|fwd):\s*/i, "").trim() ?? null;
}

async function getContactFromSubject(DB: D1Database, subject: string) {
  const channelName = channelNameFromSubject(subject);
  if (!channelName) return null;
  return DB.prepare(`SELECT id, email, channel_name, channel_url, first_sent_at
    FROM contacts WHERE LOWER(TRIM(channel_name)) = LOWER(?) LIMIT 1`)
    .bind(channelName)
    .first<ContactMatch>();
}

async function fetchInbox(accessToken: string, since: string) {
  const messages: GraphMessage[] = [];
  const query = new URLSearchParams({
    "$top": "100",
    "$orderby": "receivedDateTime desc",
    "$filter": `receivedDateTime ge ${since}`,
    "$select": [
      "id",
      "internetMessageId",
      "conversationId",
      "receivedDateTime",
      "subject",
      "bodyPreview",
      "body",
      "from",
      "webLink",
    ].join(","),
  });
  let nextUrl: string | null =
    `https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?${query.toString()}`;
  let pages = 0;
  while (nextUrl && pages < 10) {
    const response = await fetch(nextUrl, {
      headers: {
        authorization: `Bearer ${accessToken}`,
        prefer: 'outlook.body-content-type="text"',
      },
    });
    const payload = await response.json() as {
      value?: GraphMessage[];
      "@odata.nextLink"?: string;
      error?: { message?: string };
    };
    if (!response.ok) {
      throw new Error(payload.error?.message ?? "无法读取 Microsoft 365 收件箱");
    }
    messages.push(...(payload.value ?? []));
    nextUrl = payload["@odata.nextLink"] ?? null;
    pages += 1;
  }
  return messages;
}

async function fetchSentItems(accessToken: string, since: string) {
  const messages: GraphMessage[] = [];
  const query = new URLSearchParams({
    "$top": "100",
    "$orderby": "sentDateTime desc",
    "$filter": `sentDateTime ge ${since}`,
    "$select": [
      "id",
      "internetMessageId",
      "conversationId",
      "sentDateTime",
      "subject",
      "toRecipients",
      "webLink",
    ].join(","),
  });
  let nextUrl: string | null =
    `https://graph.microsoft.com/v1.0/me/mailFolders/sentitems/messages?${query.toString()}`;
  let pages = 0;
  while (nextUrl && pages < 10) {
    const response = await fetch(nextUrl, {
      headers: { authorization: `Bearer ${accessToken}` },
    });
    const payload = await response.json() as {
      value?: GraphMessage[];
      "@odata.nextLink"?: string;
      error?: { message?: string };
    };
    if (!response.ok) {
      throw new Error(payload.error?.message ?? "无法读取 Microsoft 365 已发送邮件");
    }
    messages.push(...(payload.value ?? []));
    nextUrl = payload["@odata.nextLink"] ?? null;
    pages += 1;
  }
  return messages;
}

function isCampaignSentMessage(message: GraphMessage) {
  return CAMPAIGN_SUBJECT_PATTERNS.some((pattern) => pattern.test(message.subject ?? ""));
}

async function syncSentMessage(DB: D1Database, message: GraphMessage) {
  if (!message.sentDateTime || !isCampaignSentMessage(message)) return false;
  const recipientEmails = (message.toRecipients ?? [])
    .map((recipient) => recipient.emailAddress?.address?.trim().toLowerCase() ?? "")
    .filter(Boolean);
  if (recipientEmails.length !== 1) return false;
  const contact = await getContact(DB, recipientEmails[0]);
  if (!contact) return false;
  await DB.prepare(`UPDATE contacts SET
    outreach_status = CASE
      WHEN outreach_status IN ('not_contacted', 'personalized', 'drafted') THEN 'sent'
      ELSE outreach_status
    END,
    first_sent_at = COALESCE(first_sent_at, ?),
    followup1_due = COALESCE(followup1_due, datetime(?, '+24 hours')),
    followup2_due = COALESCE(followup2_due, date(?, '+4 days')),
    notes = CASE
      WHEN notes LIKE '%Microsoft 365 sync: sent%' THEN notes
      ELSE notes || CASE WHEN notes = '' THEN '' ELSE char(10) END || 'Microsoft 365 sync: sent imported.'
    END,
    updated_at = datetime('now')
    WHERE id = ?`)
    .bind(message.sentDateTime, message.sentDateTime, message.sentDateTime, contact.id)
    .run();
  return true;
}

async function syncMessage(DB: D1Database, message: GraphMessage) {
  const existing = await DB.prepare("SELECT id FROM inbox_events WHERE id = ?")
    .bind(message.id)
    .first<{ id: string }>();
  if (existing) return { imported: false, eventType: "duplicate" };

  const from = sender(message);
  const text = messageText(message);
  const replyText = newestReply(text);
  const bounce = isBounce(message, text);
  const matchedEmail = bounce ? failedAddress(text) : from.email;
  const contact = await getContact(DB, matchedEmail)
    ?? (!bounce ? await getContactFromSubject(DB, message.subject ?? "") : null);
  if (!isCampaignRelated(message, contact, bounce)) {
    return { imported: false, eventType: "ignored" };
  }
  if (from.email === "team@videocoding.ai") {
    return { imported: false, eventType: "ignored" };
  }

  let eventType = "unmatched";
  let summary = compactPreview(replyText || message.bodyPreview || "");
  if (bounce && isSenderRestriction(from.email, text)) {
    eventType = "sender_restricted";
    summary = RESTRICTION_SUMMARY;
    await recordSenderRestriction(DB, message.receivedDateTime || new Date().toISOString());
  } else if (bounce) {
    eventType = "bounce";
    summary = matchedEmail
      ? `投递失败：${matchedEmail}`
      : "收到退信，但未能自动识别失败地址。";
    if (contact) {
      await DB.prepare(`UPDATE contacts SET
        outreach_status = 'bounced',
        validation_status = 'invalid',
        reply_status = 'no_reply',
        do_not_contact = 1,
        next_action = '邮箱退信，停止发送并寻找新的公开联系方式。',
        notes = CASE
          WHEN notes LIKE '%Microsoft 365 sync: bounce%' THEN notes
          ELSE notes || CASE WHEN notes = '' THEN '' ELSE char(10) END || 'Microsoft 365 sync: bounce detected.'
        END,
        updated_at = datetime('now')
        WHERE id = ?`).bind(contact.id).run();
    }
  } else {
    const classification = replyClassification(replyText);
    eventType = classification.eventType;
    if (contact) {
      await DB.prepare(`UPDATE contacts SET
        outreach_status = ?,
        first_sent_at = COALESCE(first_sent_at, ?),
        reply_status = ?,
        reply_received_at = ?,
        reply_from = ?,
        reply_summary = ?,
        next_action = ?,
        do_not_contact = ?,
        notes = CASE
          WHEN notes LIKE '%Microsoft 365 sync: reply%' THEN notes
          ELSE notes || CASE WHEN notes = '' THEN '' ELSE char(10) END || 'Microsoft 365 sync: reply imported.'
        END,
        updated_at = datetime('now')
        WHERE id = ?`)
        .bind(
          classification.outreachStatus,
          message.receivedDateTime,
          classification.replyStatus,
          message.receivedDateTime,
          from.name ? `${from.name} <${from.email}>` : from.email,
          summary || "已收到回复。",
          classification.nextAction,
          classification.doNotContact,
          contact.id,
        )
        .run();
    }
  }

  await DB.prepare(`INSERT INTO inbox_events (
    id, internet_message_id, conversation_id, received_at, sender_name, sender_email,
    subject, body_preview, body_text, event_type, contact_id, matched_email, web_link, processed_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`)
    .bind(
      message.id,
      message.internetMessageId ?? null,
      message.conversationId ?? null,
      message.receivedDateTime,
      from.name,
      from.email,
      message.subject ?? "",
      summary,
      replyText.slice(0, 12000),
      eventType,
      contact?.id ?? null,
      matchedEmail,
      message.webLink ?? null,
    )
    .run();
  return { imported: true, eventType };
}

async function statusResponse(DB: D1Database) {
  const [
    refreshToken,
    mailbox,
    lastSyncAt,
    lastSentSyncAt,
    connectionError,
    schedulerStatus,
    schedulerLastRunAt,
    schedulerLastResult,
    schedulerNextRunAt,
    counts,
    recent,
  ] = await Promise.all([
    getSetting(DB, "microsoft_refresh_token"),
    getSetting(DB, "microsoft_mailbox"),
    getSetting(DB, "microsoft_last_sync_at"),
    getSetting(DB, "microsoft_last_sent_sync_at"),
    getSetting(DB, "microsoft_connection_error"),
    getSetting(DB, "scheduler_status"),
    getSetting(DB, "scheduler_last_run_at"),
    getSetting(DB, "scheduler_last_result"),
    getSetting(DB, "scheduler_next_run_at"),
    DB.prepare(`SELECT
      COUNT(*) AS total,
      SUM(CASE WHEN event_type IN ('reply', 'interested', 'opted_out') THEN 1 ELSE 0 END) AS replies,
      SUM(CASE WHEN event_type = 'bounce' THEN 1 ELSE 0 END) AS bounces,
      SUM(CASE WHEN contact_id IS NULL THEN 1 ELSE 0 END) AS unmatched
      FROM inbox_events`).first<Record<string, number>>(),
    DB.prepare(`SELECT
      e.*, c.channel_name, c.channel_url
      FROM inbox_events e
      LEFT JOIN contacts c ON c.id = e.contact_id
      ORDER BY e.received_at DESC
      LIMIT 30`).all<Record<string, unknown>>(),
  ]);
  return {
    connected: Boolean(refreshToken),
    mailbox,
    last_sync_at: lastSyncAt,
    last_sent_sync_at: lastSentSyncAt,
    connection_error: connectionError || null,
    scheduler_status: schedulerStatus || "idle",
    scheduler_last_run_at: schedulerLastRunAt,
    scheduler_last_result: schedulerLastResult ? JSON.parse(schedulerLastResult) : null,
    scheduler_next_run_at: schedulerNextRunAt,
    total_events: Number(counts?.total ?? 0),
    replies: Number(counts?.replies ?? 0),
    bounces: Number(counts?.bounces ?? 0),
    unmatched: Number(counts?.unmatched ?? 0),
    recent_events: recent.results,
  };
}

let microsoftSchemaPromise: Promise<void> | null = null;
function ensureMicrosoftSchemaOnce(DB: D1Database) {
  if (!microsoftSchemaPromise) {
    microsoftSchemaPromise = ensureMicrosoftSchema(DB).catch((error) => {
      microsoftSchemaPromise = null;
      throw error;
    });
  }
  return microsoftSchemaPromise;
}

export async function GET() {
  try {
    let lastError: unknown;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        const DB = database();
        await ensureMicrosoftSchemaOnce(DB);
        return NextResponse.json(await statusResponse(DB));
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
      { error: error instanceof Error ? error.message : "无法读取同步状态" },
      { status: 500 },
    );
  }
}

export async function POST() {
  // Microsoft Graph synchronization is intentionally owned by the single
  // background inbox-sync worker. Treat browser POSTs as local status refreshes
  // so stale/open tabs cannot create concurrent mailbox scans.
  return GET();
}
