"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "local_proxy_static", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function formatRetryTime");
const end = source.indexOf("async function readRecoveryHistory");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({ RECOVERY_HISTORY_DISPLAY_LIMIT: 500 });
vm.runInContext(
  `${source.slice(start, end)}\n` +
    "this.api = { recoveryMetaText, sameRecoveryHistorySnapshot, mergeRecoveryHistoryPages, refreshRecoveryHistoryPages, recoveryHistoryForDisplay };",
  context,
  { filename: sourcePath },
);

function entry(id, recordedAt = id) {
  return {
    recorded_at: recordedAt,
    request_id: id,
    provider_id: "provider",
    attempt: id,
    outcome: "retrying",
  };
}

test("keeps loaded recovery history while prepending a new summary item", () => {
  const detail = {
    window_hours: 24,
    total_count: 3,
    truncated: false,
    items: [entry(3), entry(2), entry(1)],
  };
  const summary = {
    window_hours: 24,
    total_count: 4,
    truncated: true,
    items: [entry(4)],
  };

  const displayed = context.api.recoveryHistoryForDisplay(detail, summary);

  assert.equal(displayed.total_count, 4);
  assert.equal(displayed.truncated, false);
  assert.deepEqual(
    Array.from(displayed.items, (item) => item.request_id),
    [4, 3, 2, 1],
  );
});

test("deduplicates the latest item without imposing a display cap", () => {
  const detailItems = Array.from({ length: 500 }, (_, index) => entry(500 - index));
  const detail = {
    window_hours: 24,
    total_count: 900,
    truncated: true,
    items: detailItems,
  };
  const summary = {
    window_hours: 24,
    total_count: 901,
    truncated: true,
    items: [entry(501), entry(500)],
  };

  const displayed = context.api.recoveryHistoryForDisplay(detail, summary);

  assert.equal(displayed.items.length, 501);
  assert.equal(displayed.items[0].request_id, 501);
  assert.equal(displayed.items[1].request_id, 500);
  assert.equal(displayed.items[499].request_id, 2);
  assert.equal(displayed.items[500].request_id, 1);
  assert.equal(displayed.total_count, 901);
  assert.equal(displayed.truncated, true);
});

test("appends paginated recovery history and keeps the next cursor", () => {
  const current = {
    window_hours: 24,
    total_count: 4,
    truncated: true,
    next_cursor: "page-2",
    items: [entry(4), entry(3), entry(2)],
  };
  const page = {
    window_hours: 24,
    total_count: 4,
    truncated: false,
    next_cursor: null,
    items: [entry(2), entry(1)],
  };

  const merged = context.api.mergeRecoveryHistoryPages(current, page);

  assert.deepEqual(Array.from(merged.items, (item) => item.request_id), [4, 3, 2, 1]);
  assert.equal(merged.next_cursor, null);
  assert.equal(merged.truncated, false);
});

test("refreshes the newest page without losing the older-page cursor", () => {
  const current = {
    total_count: 4,
    next_cursor: "older-page",
    items: [entry(3), entry(2), entry(1)],
  };
  const page = {
    total_count: 5,
    next_cursor: "overlapping-page",
    items: [entry(5), entry(4), entry(3)],
  };

  const refreshed = context.api.refreshRecoveryHistoryPages(current, page);

  assert.deepEqual(
    Array.from(refreshed.items, (item) => item.request_id),
    [5, 4, 3, 2, 1],
  );
  assert.equal(refreshed.next_cursor, "older-page");
  assert.equal(refreshed.truncated, true);
});

test("reuses the loaded detail when the summary snapshot is unchanged", () => {
  const detail = {
    total_count: 1,
    items: [entry(1, 1234)],
  };
  const summary = {
    total_count: 1,
    items: [entry(999, 1234)],
  };

  assert.equal(context.api.sameRecoveryHistorySnapshot(detail, summary), true);
  assert.equal(context.api.recoveryHistoryForDisplay(detail, summary), detail);
});

test("labels failure and request start times while keeping old records compatible", () => {
  const now = Date.now();
  const current = context.api.recoveryMetaText({
    recorded_at: now,
    request_started_at: now - 10_000,
    attempt: 2,
    outcome: "retrying",
  }, "Company");
  const legacy = context.api.recoveryMetaText({
    recorded_at: now,
    request_started_at: null,
    attempt: 1,
    outcome: "retrying",
  }, "Company");

  assert.match(current, /^失败 .+ · 开始 .+ · Company · 第 2 次请求 · 已安排重试$/);
  assert.match(legacy, /^失败 .+ · Company · 第 1 次请求 · 已安排重试$/);
  assert.doesNotMatch(legacy, /开始/);
});
