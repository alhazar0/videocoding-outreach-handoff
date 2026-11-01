/** Isolate the current request; quoted history remains available to the AI separately. */
export function currentReplyText(value: string): string {
  const lines = value.replace(/\r\n?/g, "\n").split("\n");
  const kept: string[] = [];
  for (const line of lines) {
    const text = line.trim();
    if (/^(?:from|sent|to|subject|von|gesendet|an|betreff|发件人|发送时间|收件人|主题):\s/i.test(text) ||
        /^-{2,}\s*(?:original|replied) message/i.test(text) ||
        /^on\s+.{0,260}\bwrote:/i.test(text) ||
        /^.{0,260}\b(?:schrieb|écrit|scris)\b.*:/i.test(text) ||
        /^.{0,260}(?:님이\s*작성|작성:|写道[：:]|寫道[：:])/.test(text)) break;
    if (!text.startsWith(">")) kept.push(line);
  }
  return kept.join("\n").trim();
}

export function requestsCardlessAccess(value: string): boolean {
  // A creator's rate card is a pricing document, not an objection to card entry.
  const text = value.replace(/\brate\s+card\b/gi, "rate sheet");
  return /\b(?:credit|debit)\s+card\b|\b(?:without|no|don['’]?t\s+(?:have|use|want)|do\s+not\s+(?:have|use|want))\b[^.!?\n]{0,60}\bcards?\b|\b(?:add|adding|enter|entering|provide|providing|requires?|requiring|asks?|asking)\b[^.!?\n]{0,50}\b(?:cards?|payment\s+(?:details|method))\b/i.test(text);
}

export function pricingEvidenceText(value: string): string {
  if (/\b(?:coupon|creator\s+code|trial|pro\s+access|free\s+month)\b/i.test(value) &&
      !/\b(?:my|our|standard)\s+(?:rate|price|fee)|\b(?:i|we)\s+charge\b/i.test(value)) {
    return value.replace(/[$€£]\s*0(?:[.,]0+)?(?!\d|[.,]\d)/g, 'free trial');
  }
  return value;
}
