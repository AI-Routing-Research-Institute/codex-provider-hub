"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function usageWindowLabel");
const end = source.indexOf("function renderUsageHistoryPopover");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({});
vm.runInContext(
  `${source.slice(start, end)}\n` +
    "this.api = { usageStatusLabel, usageHistoryMetaText };",
  context,
  { filename: sourcePath },
);

test("labels successful, stream-level, and HTTP failures", () => {
  assert.equal(context.api.usageStatusLabel({ succeeded: true, status_code: 200 }), "成功");
  assert.equal(context.api.usageStatusLabel({ succeeded: false, status_code: 200 }), "流级失败");
  assert.equal(context.api.usageStatusLabel({ succeeded: false, status_code: 503 }), "HTTP 503 失败");
});

test("shows the failed request count only when present", () => {
  assert.equal(
    context.api.usageHistoryMetaText("today", 165, { failed_requests: 59 }),
    "今日 · 165 条 · 59 条失败",
  );
  assert.equal(
    context.api.usageHistoryMetaText("all", 12, { failed_requests: 0 }),
    "全部 · 12 条",
  );
});
