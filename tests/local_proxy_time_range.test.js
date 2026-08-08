"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "index.html"), "utf8");
const sourcePath = path.join(__dirname, "..", "proxy_static", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function padTimePart");
const end = source.indexOf("function timeRangeSelect");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({ Date });
vm.runInContext(
  `${source.slice(start, end)}\nthis.api = { parseLocalDateTime, formatCustomRange };`,
  context,
  { filename: sourcePath },
);

test("parses local date and time values to millisecond bounds", () => {
  const value = context.api.parseLocalDateTime("2026-08-09", "12:34:56");
  assert.equal(value, new Date("2026-08-09T12:34:56").getTime());
  assert.ok(Number.isNaN(context.api.parseLocalDateTime("", "12:34:56")));
});

test("formats a compact custom range with seconds", () => {
  const startAt = new Date("2026-08-09T12:34:56").getTime();
  const endAt = new Date("2026-08-09T13:35:57").getTime();
  assert.equal(
    context.api.formatCustomRange({ start: startAt, end: endAt }),
    "08/09 12:34:56 至 08/09 13:35:57",
  );
});

test("provides custom controls for usage and seven-day request history", () => {
  assert.match(html, /id="usage-window"[\s\S]*?<option value="custom">自定义时间…<\/option>/);
  assert.match(html, /id="request-window"[\s\S]*?<option value="7d">近 7 天<\/option>/);
  assert.match(html, /id="request-window"[\s\S]*?<option value="custom">自定义时间…<\/option>/);
  assert.match(html, /id="time-range-start-time" type="time" step="1"/);
  assert.match(html, /id="time-range-end-time" type="time" step="1"/);
  assert.match(source, /params\.set\("end_at", String\(range\.end \+ 999\)\)/);
});
