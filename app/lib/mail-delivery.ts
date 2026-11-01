import { getSetting, setSetting } from "./microsoft";

export const RESTRICTION_REASON = "550 5.1.8 AS(42004): Microsoft restricted the outbound sender; administrator review required.";
export const RESTRICTION_SUMMARY = "微软发件账号受限；不是收件地址无效。等待管理员处理，不自动重发。";

export function isSenderRestriction(senderEmail: string, text: string): boolean {
  return /^microsoftexchange[0-9a-f]+@[a-z0-9.-]+\.onmicrosoft\.com$/i.test(senderEmail)
    && /\b550\s+5\.1\.8\b|\bbad outbound sender\b|\bAS\(42004\)/i.test(text);
}

export async function microsoftOutboundBlockReason(db: D1Database): Promise<string | null> {
  const reason = await getSetting(db, "microsoft_outbound_block_reason");
  if (reason?.trim()) return reason;
  const account = await db.prepare("SELECT status FROM outreach_sender_accounts WHERE lower(sender_email)='team@videocoding.ai'")
    .first<{ status: string }>();
  return account?.status === "restricted" ? RESTRICTION_REASON : null;
}

export async function recordSenderRestriction(db: D1Database, receivedAt: string) {
  // Persist the transport gate before updating presentation state.
  await setSetting(db, "microsoft_outbound_block_reason", RESTRICTION_REASON);
  await setSetting(db, "microsoft_outbound_restriction_last_seen_at", receivedAt);
  await setSetting(db, "scheduler_status", "sender_restricted");
  await db.prepare("UPDATE outreach_sender_accounts SET status='restricted',updated_at=datetime('now') WHERE lower(sender_email)='team@videocoding.ai'").run();
}
