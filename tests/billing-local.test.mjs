import test from 'node:test';
import assert from 'node:assert/strict';
import { classifySubscription, ipLocation, isLocalRequest } from '../build/billing-local.mjs';
const base = { status: 'active', collection_method: 'charge_automatically', current_period_end: 1800000000 };
test('excludes canceled renewals, paused collection, overdue and manually collected subscriptions', () => {
  assert.equal(classifySubscription(base).automatic, true);
  for (const change of [{ cancel_at_period_end: true }, { pause_collection: { behavior: 'void' } }, { cancel_at: 1800000000 }, { status: 'past_due' }, { status: 'incomplete_expired' }, { collection_method: 'send_invoice' }]) {
    assert.equal(classifySubscription({ ...base, ...change }).automatic, false, JSON.stringify(change));
  }
});
test('supports trial end, current and item-level Stripe billing periods', () => {
  assert.equal(classifySubscription({ ...base, status: 'trialing', trial_end: 1800100000 }).periodEnd, new Date(1800100000 * 1000).toISOString());
  assert.equal(classifySubscription({ ...base, current_period_end: undefined, items: { data: [{ current_period_end: 1800100000 }, { current_period_end: 1800000000 }] } }).periodEnd, new Date(1800000000 * 1000).toISOString());
  assert.equal(classifySubscription({ ...base, current_period_end: undefined }).periodEnd, null);
});
test('never substitutes billing country for IP country', () => {
  assert.equal(ipLocation({ tax: { ip_address: '192.0.2.1', location: { country: 'US', source: 'ip_address' } } }).countryCode, 'US');
  assert.equal(ipLocation({ tax: { ip_address: '192.0.2.1', location: { country: 'US', source: 'billing_address' } } }).countryCode, null);
  assert.equal(ipLocation({ address: { country: 'US' } }).countryCode, null);
});
test('rejects remote, cross-origin and DNS rebinding requests', () => {
  const req = { headers: { host: 'localhost:3000' }, socket: { remoteAddress: '127.0.0.1' } };
  assert.equal(isLocalRequest(req), true);
  assert.equal(isLocalRequest({ ...req, socket: { remoteAddress: '10.0.0.1' } }), false);
  for (const change of [{ host: 'evil.test:3000' }, { origin: 'https://evil.test' }, { 'sec-fetch-site': 'cross-site' }]) {
    assert.equal(isLocalRequest({ ...req, headers: { ...req.headers, ...change } }), false);
  }
});
