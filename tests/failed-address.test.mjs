import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const route = readFileSync(new URL('../app/api/microsoft/sync/route.ts', import.meta.url), 'utf8');
const source = route.slice(route.indexOf('function failedAddress('), route.indexOf('\nfunction replyClassification('));
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const failedAddress = new Function(`${compiled}; return failedAddress;`)();

test('manual sync preserves exact original recipient before explanatory addresses', () => {
  assert.equal(failedAddress("Delivery failed for support@example.com.\nRecipient Address: 'support@example.com"), "'support@example.com");
  assert.equal(failedAddress("Help: other@example.net\nFinal-Recipient: rfc822; o'neil+creator@example.com\r\n"), "o'neil+creator@example.com");
});

test('legacy failure reports still extract recipient and exclude our sender', () => {
  assert.equal(failedAddress('team@videocoding.ai could not deliver to creator@example.com'), 'creator@example.com');
  assert.equal(failedAddress('team@videocoding.ai'), null);
});
