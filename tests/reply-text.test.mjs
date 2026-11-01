import test from 'node:test';
import assert from 'node:assert/strict';
import { currentReplyText, requestsCardlessAccess, pricingEvidenceText } from '../app/lib/reply-text.ts';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const route = readFileSync(new URL('../app/api/operations/route.ts', import.meta.url), 'utf8');
const source = route.slice(route.indexOf('function classify(row:'), route.indexOf('\nfunction renderTemplate('));
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const classify = new Function('currentReplyText', 'requestsCardlessAccess', 'pricingEvidenceText', 'COUPON', `${compiled}; return classify;`)(currentReplyText, requestsCardlessAccess, pricingEvidenceText, 'FREETEST100');
const row = (body_text) => ({ event_type: 'interested', subject: 'Re: Paid collaboration', body_text, body_preview: '' });

test('German and Korean quoted invitations cannot become creator rates or test claims', () => {
  for (const header of ['Alhazar schrieb am Sa., 26. Sept. 2026, 00:02:', '2026년 9월 26일 Alhazar님이 작성:']) {
    assert.equal(currentReplyText(`Please send a code.\n\n${header}\nPro access at $0. I have tested it.`), 'Please send a code.');
  }
});
test('future rate card does not request cardless access', () => {
  assert.equal(requestsCardlessAccess("Please send a code. I'll share my rate card after testing."), false);
  assert.equal(requestsCardlessAccess("I don't have a credit card. Can you activate it?"), true);
  assert.equal(requestsCardlessAccess('It still asks for my card information.'), true);
});
test('actual routing distinguishes future rate card, quotes and cardless access', () => {
  assert.equal(classify(row("I'd love to test. Please send the creator code. I'll get back with my rate card after testing.")).kind, 'coupon_reply');
  assert.equal(classify(row('Please send the creator code.\n\nAlhazar schrieb am Sa., 00:02:\nPro access at $0.')).kind, 'coupon_reply');
  assert.equal(classify(row('My rate is $900 for a dedicated video.')).kind, 'price_received');
  assert.equal(classify(row("I don't have a debit card. Can you activate my trial?")).kind, 'membership_request');
  assert.equal(classify(row('Please send the creator code for one month of Pro access at $0.')).kind, 'coupon_reply');
  assert.equal(classify(row('Please send a trial code. My rate is $900.')).kind, 'price_received');
  assert.equal(classify(row('Please send the creator code. After testing it on a real project, we can discuss rates.')).kind, 'coupon_reply');
});

test('delivery reports cannot be promoted into creator reply drafts by an AI decision', async () => {
  const source = route.slice(route.indexOf('async function applyAIReplyDecision('), route.indexOf('\nasync function analyzeWithAI('));
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  for (const event_type of ['bounce', 'sender_restricted', 'delivery_delayed', 'delivery_failed', 'automated']) {
    let writes = 0;
    const apply = new Function('DB', 'loadAIReplyContext', `${compiled}; return applyAIReplyDecision;`)(
      () => ({ prepare() { writes++; throw new Error('unexpected database write'); } }),
      async () => ({ id: 1, status: 'pending', event_type }),
    );
    await assert.rejects(apply(1, { intent: 'wants_test' }, 'local_codex', 'stale'), /禁止改为可回复事项/);
    assert.equal(writes, 0);
    assert.equal(classify({ ...row('Please send a code after a delivery failure.'), event_type }).kind, 'ignore');
  }
});
