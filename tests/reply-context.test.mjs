import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { currentReplyText } from '../app/lib/reply-text.ts';

const source = readFileSync(new URL('../app/lib/reply-context.ts', import.meta.url), 'utf8')
  .replace(/import .*?;\n/, '').replaceAll('export ', '');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const { prepareReplyContext, currentEvidenceMatches } = new Function('currentReplyText', `${compiled}; return { prepareReplyContext, currentEvidenceMatches };`)(currentReplyText);
const message = (id, body, conversation_id = 'a') => ({ id, body, conversation_id, at: '2026-10-01', direction: 'creator', preview_only: 0 });
const snapshot = (messages, extra = {}) => JSON.stringify({ current_id: 'now', conversation_id: 'a', body: 'Any update?', messages,
  captured_sent_count: 1, expected_sent_count: 1, ...extra });

test('old unanswered request survives more than twelve messages', () => {
  const messages = [message('old-question', 'Does this support Hindi?'), ...Array.from({ length: 20 }, (_, i) => message(`m-${i}`, 'Follow up'))];
  const result = prepareReplyContext(snapshot(messages));
  assert.equal(result.context_delivery.complete, true);
  assert.ok(result.thread_context.includes('Does this support Hindi?'));
});
test('separate conversation is explicitly scoped and current body not duplicated', () => {
  const result = prepareReplyContext(snapshot([message('inbound:now', 'Any update?'), message('other', 'A different deal', 'b')]));
  const history = JSON.parse(result.thread_context);
  assert.equal(history.length, 1);
  assert.equal(history[0].scope, 'related_conversation');
});
test('truncation and preview-only history cannot silently appear complete', () => {
  for (const messageRow of [message('large', 'x'.repeat(60001)), { ...message('preview', 'Excerpt'), preview_only: 1 }]) {
    const result = prepareReplyContext(snapshot([messageRow]));
    assert.equal(result.context_delivery.complete, false);
    assert.ok(result.context_delivery.missing.length > 0);
  }
});
test('quoted invitations are removed from creator evidence', () => {
  const result = prepareReplyContext(snapshot([message('prior', 'Please send access.\n\nOn Monday Alhazar wrote:\nOur rate is $0.') ]));
  assert.equal(JSON.parse(result.thread_context)[0].body, 'Please send access.');
});
test('current evidence must be grounded in the actual new message', () => {
  assert.equal(currentEvidenceMatches("I’m interested. Please send the code.", "I'm interested. ... send the code."), true);
  assert.equal(currentEvidenceMatches('I will give you honest feedback: what works and what does not.', 'I will give you honest feedback.'), true);
  assert.equal(currentEvidenceMatches('I only accept paid deals.', 'Please send the code.'), false);
  assert.equal(currentEvidenceMatches('Any update?\n\nOn Monday Alhazar wrote:\nPlease send the code.', 'Please send the code.'), false);
});
