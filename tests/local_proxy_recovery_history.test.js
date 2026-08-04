"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "local_proxy_static", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function sameRecoveryHistorySnapshot");
const end = source.indexOf("async function readRecoveryHistory");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({ RECOVERY_HISTORY_DISPLAY_LIMIT: 500 });
vm.runInContext(
  `${source.slice(start, end)}\n` +
    "this.api = { sameRecoveryHistorySnapshot, recoveryHistoryForDisplay };",
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

test("deduplicates the latest item and caps the displayed history", () => {
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

  assert.equal(displayed.items.length, 500);
  assert.equal(displayed.items[0].request_id, 501);
  assert.equal(displayed.items[1].request_id, 500);
  assert.equal(displayed.items[499].request_id, 2);
  assert.equal(displayed.total_count, 901);
  assert.equal(displayed.truncated, true);
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
