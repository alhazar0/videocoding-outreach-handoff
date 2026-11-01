import { env } from "cloudflare:workers";

export const MICROSOFT_CLIENT_ID = (env as unknown as Record<string, string>).MICROSOFT_CLIENT_ID || "00000000-0000-0000-0000-000000000001";
export const MICROSOFT_TENANT_ID = (env as unknown as Record<string, string>).MICROSOFT_TENANT_ID || "00000000-0000-0000-0000-000000000002";
export const MICROSOFT_SCOPES = "openid offline_access User.Read Mail.Read Mail.Send";
export const CAMPAIGN_START_AT = "2026-07-26T00:00:00.000Z";

type Bindings = {
  DB: D1Database;
};

export type GraphMessage = {
  id: string;
  internetMessageId?: string;
  conversationId?: string;
  receivedDateTime: string;
  sentDateTime?: string;
  subject?: string;
  bodyPreview?: string;
  body?: {
    contentType?: string;
    content?: string;
  };
  from?: {
    emailAddress?: {
      name?: string;
      address?: string;
    };
  };
  toRecipients?: Array<{
    emailAddress?: {
      name?: string;
      address?: string;
    };
  }>;
  webLink?: string;
};

export type TokenPayload = {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  error?: string;
  error_description?: string;
};

export function database() {
  return (env as unknown as Bindings).DB;
}

export async function ensureMicrosoftSchema(DB: D1Database) {
  await DB.prepare(`CREATE TABLE IF NOT EXISTS outreach_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )`).run();
  await DB.prepare(`CREATE TABLE IF NOT EXISTS inbox_events (
    id TEXT PRIMARY KEY,
    internet_message_id TEXT,
    conversation_id TEXT,
    received_at TEXT NOT NULL,
    sender_name TEXT NOT NULL DEFAULT '',
    sender_email TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body_preview TEXT NOT NULL DEFAULT '',
    body_text TEXT,
    event_type TEXT NOT NULL,
    contact_id INTEGER,
    matched_email TEXT,
    web_link TEXT,
    processed_at TEXT NOT NULL
  )`).run();
  const inboxColumns = await DB.prepare("PRAGMA table_info(inbox_events)").all<{ name: string }>();
  if (!inboxColumns.results.some((column: { name: string }) => column.name === "body_text")) {
    await DB.prepare("ALTER TABLE inbox_events ADD COLUMN body_text TEXT").run();
  }
  await DB.prepare(`CREATE INDEX IF NOT EXISTS inbox_events_received_idx
    ON inbox_events(received_at DESC)`).run();
  await DB.prepare(`CREATE INDEX IF NOT EXISTS inbox_events_contact_idx
    ON inbox_events(contact_id, event_type)`).run();
}

export async function getSetting(DB: D1Database, key: string) {
  const row = await DB.prepare(
    "SELECT value FROM outreach_settings WHERE key = ?",
  ).bind(key).first<{ value: string }>();
  return row?.value ?? null;
}

export async function setSetting(DB: D1Database, key: string, value: string) {
  await DB.prepare(`INSERT INTO outreach_settings (key, value, updated_at)
    VALUES (?, ?, datetime('now'))
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`)
    .bind(key, value)
    .run();
}

export function base64Url(bytes: Uint8Array) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export function randomToken(byteLength = 48) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

export async function pkceChallenge(verifier: string) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64Url(new Uint8Array(digest));
}

export async function refreshAccessToken(DB: D1Database) {
  if ((env as unknown as Record<string, string>).VIDEOCODING_ALLOW_LIVE_OPERATIONS !== "1") {
    throw new Error("Handoff copy: Microsoft credential use is disabled");
  }
  const refreshToken = await getSetting(DB, "microsoft_refresh_token");
  if (!refreshToken) {
    throw new Error("Microsoft 365 尚未连接");
  }
  const body = new URLSearchParams({
    client_id: MICROSOFT_CLIENT_ID,
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    scope: MICROSOFT_SCOPES,
  });
  const response = await fetch(
    `https://login.microsoftonline.com/${MICROSOFT_TENANT_ID}/oauth2/v2.0/token`,
    {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
    },
  );
  const payload = await response.json() as TokenPayload;
  if (!response.ok || !payload.access_token) {
    if (payload.error === "invalid_grant") {
      await setSetting(DB, "microsoft_connection_error", "授权已过期，请重新连接");
    }
    throw new Error(payload.error_description ?? "无法刷新 Microsoft 365 授权");
  }
  if (payload.refresh_token && payload.refresh_token !== refreshToken) {
    await setSetting(DB, "microsoft_refresh_token", payload.refresh_token);
  }
  return payload.access_token;
}

export function stripHtml(value: string) {
  return value
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&#39;/gi, "'")
    .replace(/&quot;/gi, "\"")
    .replace(/\r/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n+/g, "\n")
    .trim();
}
