import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("ships the creator outreach dashboard with an empty private-data-free handoff seed", async () => {
  const [page, layout, seedText] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/data/contacts_seed.json", import.meta.url), "utf8"),
  ]);
  const seed = JSON.parse(seedText);

  assert.match(layout, /VideoCoding Creator Outreach OS/);
  assert.match(page, /当前 \{formatNumber\(summary\.active_daily_capacity\)\} \/ 天/);
  assert.match(page, /准备扩到 1000 \/ 天/);
  assert.match(page, /sender-account-list/);
  assert.match(page, /主邮箱随机 45–60 秒/);
  assert.match(page, /当前每日目标 750/);
  assert.match(page, /未联系可投递库存/);
  assert.match(page, /waiting_for_template_assignment/);
  assert.match(page, /window\.setInterval\(refresh, 15_000\)/);
  const api = await readFile(new URL("../app/api/outreach/route.ts", import.meta.url), "utf8");
  assert.doesNotMatch(api, /await ensureQueuePolicy/);
  assert.doesNotMatch(api, /UPDATE contacts SET planned_send_date/);
  assert.match(page, /outlook\.office\.com\/mail\/deeplink\/compose/);
  assert.deepEqual(seed.contacts, []);
  assert.equal(seed.report.total_contacts, 0);
});
