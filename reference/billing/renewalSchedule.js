'use strict';
const { isIP } = require('node:net');
const DAY = 86400000;
const SEND_WINDOW_MS = 5 * 60000;
function partsAt(date, timeZone) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(date);
  return Object.fromEntries(parts.filter(p => p.type !== 'literal').map(p => [p.type, Number(p.value)]));
}
function localStamp(p) { return Date.UTC(p.year, p.month - 1, p.day, p.hour || 0, p.minute || 0, p.second || 0); }
function scheduledReminderAt(billingAt, timeZone, days = 3) {
  if (!timeZone || !Number.isInteger(days) || days < 1 || days > 30) throw new Error('invalid_reminder_schedule');
  const billing = new Date(billingAt);
  if (!Number.isFinite(billing.getTime())) throw new Error('invalid_billing_date');
  const local = partsAt(billing, timeZone);
  const date = new Date(Date.UTC(local.year, local.month - 1, local.day) - days * DAY);
  const target = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), 3);
  // Find real offsets around the target; this handles DST and fractional offsets.
  // If 03:00 is skipped by a timezone transition, fail closed instead of shifting.
  const candidates = new Set();
  for (let hours = -36; hours <= 36; hours += 6) {
    const sample = target + hours * 3600000;
    const offset = localStamp(partsAt(new Date(sample), timeZone)) - sample;
    const candidate = target - offset;
    if (localStamp(partsAt(new Date(candidate), timeZone)) === target) candidates.add(candidate);
  }
  if (!candidates.size) throw new Error('local_0300_does_not_exist');
  return new Date(Math.min(...candidates)).toISOString();
}
function resolveIpTimezone(ip) {
  if (!isIP(String(ip || ''))) return null;
  // Release bundles carry isolated GeoIP code/data. Lookups never transmit IPs.
  const bundled = require('node:path').join(__dirname, 'vendor/node_modules/geoip-lite');
  const geoip = require('node:fs').existsSync(bundled) ? require(bundled) : require('geoip-lite');
  const result = geoip.lookup(ip);
  if (!result?.timezone) return null;
  try { partsAt(new Date(), result.timezone); } catch { return null; }
  return { timeZone: result.timezone, country: result.country || null, source: 'Stripe customer IP / GeoIP local database' };
}
function withinSendWindow(sendAt, now = new Date()) {
  const elapsed = new Date(now).getTime() - new Date(sendAt).getTime();
  return elapsed >= 0 && elapsed < SEND_WINDOW_MS;
}
module.exports = { scheduledReminderAt, resolveIpTimezone, withinSendWindow, SEND_WINDOW_MS };
