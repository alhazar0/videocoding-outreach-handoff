'use strict';
const test = require('node:test'); const assert = require('node:assert/strict');
const { scheduledReminderAt, withinSendWindow } = require('./renewalSchedule');
test('three local calendar days at 03:00, not a 72-hour subtraction', () => {
  assert.equal(scheduledReminderAt('2026-09-20T12:00:00Z','America/New_York'), '2026-09-17T07:00:00.000Z');
  assert.equal(scheduledReminderAt('2026-09-20T01:00:00Z','America/Los_Angeles'), '2026-09-16T10:00:00.000Z');
  assert.equal(scheduledReminderAt('2026-09-20T12:00:00Z','Asia/Kolkata'), '2026-09-16T21:30:00.000Z');
  assert.equal(scheduledReminderAt('2026-09-20T12:00:00Z','Asia/Kathmandu'), '2026-09-16T21:15:00.000Z');
});
test('uses target-day DST, across month/year boundaries', () => {
  assert.equal(scheduledReminderAt('2026-03-10T12:00:00Z','America/New_York'), '2026-03-07T08:00:00.000Z');
  assert.equal(scheduledReminderAt('2026-11-03T12:00:00Z','America/New_York'), '2026-10-31T07:00:00.000Z');
  assert.equal(scheduledReminderAt('2027-01-02T12:00:00Z','Asia/Shanghai'), '2026-12-29T19:00:00.000Z');
});
test('fails closed for unknown zones and skipped 03:00; sends only inside target window', () => {
  assert.throws(() => scheduledReminderAt('2026-09-20T12:00:00Z',null));
  assert.throws(() => scheduledReminderAt('2026-09-20T12:00:00Z','invalid'));
  assert.throws(() => scheduledReminderAt('2026-04-01T12:00:00Z','Europe/Helsinki'), /does_not_exist/);
  const at='2026-09-17T07:00:00Z';
  assert.equal(withinSendWindow(at,'2026-09-17T06:59:59Z'),false);
  assert.equal(withinSendWindow(at,at),true);
  assert.equal(withinSendWindow(at,'2026-09-17T07:04:59Z'),true);
  assert.equal(withinSendWindow(at,'2026-09-17T07:05:00Z'),false);
});
