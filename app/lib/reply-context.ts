import { currentReplyText } from './reply-text';

export const REPLY_CONTEXT_VERSION = '2026-10-08-context-v1';
const MAX_CONTEXT_CHARS = 60000;
type Message = { id: string; conversation_id: string | null; at: string; direction: string; subject: string; body: string; preview_only: number };
type Snapshot = {
  current_id: string; conversation_id: string | null; body: string;
  messages: Message[]; captured_sent_count: number; expected_sent_count: number;
};

export async function contextFingerprint(raw: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(REPLY_CONTEXT_VERSION + '\n' + raw));
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

export function currentEvidenceMatches(body: string, quote: string): boolean {
  const normalize = (value: string) => value.toLowerCase().replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, ' ').trim();
  const text = normalize(currentReplyText(body));
  const fragments = normalize(quote).split(/\s*(?:\.{3}|…)\s*/)
    .map(fragment => fragment.replace(/[.!?:;,]+$/, '')).filter(Boolean);
  if (!fragments.length || fragments.some(fragment => fragment.length < 4)) return false;
  let offset = 0;
  for (const fragment of fragments) {
    const found = text.indexOf(fragment, offset);
    if (found < 0) return false;
    offset = found + fragment.length;
  }
  return true;
}

// Every message has a scope and identity. Related conversations supply background;
// their questions must not silently become requests in the current conversation.
export function prepareReplyContext(raw: string) {
  const snapshot = JSON.parse(raw) as Snapshot;
  const missing: string[] = [];
  const currentBody = currentReplyText(snapshot.body);
  let budget = MAX_CONTEXT_CHARS;
  let omitted = 0;
  const messages = snapshot.messages.map((message, order) => {
    const current = message.id === `inbound:${snapshot.current_id}`;
    const scope = current || (snapshot.conversation_id && message.conversation_id === snapshot.conversation_id)
      ? 'current_conversation' : 'related_conversation';
    return { ...message, order, scope, current, body: message.direction === 'creator' ? currentReplyText(message.body) : message.body };
  });
  // Prefer the complete current conversation; retain chronology within each scope.
  const chosen = [...messages].sort((a, b) => Number(b.scope === 'current_conversation') - Number(a.scope === 'current_conversation'))
    .flatMap(message => {
      if (message.current) return [];
      if (message.body.length > budget) { omitted++; return []; }
      budget -= message.body.length;
      return [message];
    }).sort((a, b) => a.order - b.order);
  if (omitted) missing.push(`历史上下文超出长度限制，省略 ${omitted} 封；不可视为完整会话`);
  if (currentBody.length > MAX_CONTEXT_CHARS) missing.push('当前来信正文超出长度限制');
  const previewOnly = messages.filter(message => message.preview_only || !message.body.trim());
  if (previewOnly.length) missing.push(`有 ${previewOnly.length} 封邮件只有摘要或正文为空，需补全历史`);
  if (snapshot.captured_sent_count < snapshot.expected_sent_count)
    missing.push(`已发送历史正文仅覆盖 ${snapshot.captured_sent_count}/${snapshot.expected_sent_count} 封`);
  return {
    body_text: currentBody.slice(0, MAX_CONTEXT_CHARS),
    thread_context: JSON.stringify(chosen),
    context_delivery: { complete: missing.length === 0, missing, total_messages: messages.length,
      delivered_messages: chosen.length + 1, omitted_messages: omitted, order: 'oldest_first' },
    context_version: REPLY_CONTEXT_VERSION,
  };
}
