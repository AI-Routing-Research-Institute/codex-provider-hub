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

const context = vm.createContext({
  Date,
  appliedTimeWindows: { usage: "today", requests: "24h" },
  customTimeRanges: { usage: null, requests: null },
});
vm.runInContext(
  `${source.slice(start, end)}
  this.api = {
    parseLocalDateTime,
    formatCustomRange,
    localDayEnd: typeof localDayEnd === "function" ? localDayEnd : undefined,
    validStoredTimeRange: typeof validStoredTimeRange === "function" ? validStoredTimeRange : undefined,
    restoredTimeRangePreference: typeof restoredTimeRangePreference === "function"
      ? restoredTimeRangePreference
      : undefined,
    timeRangeStoragePayload: typeof timeRangeStoragePayload === "function"
      ? timeRangeStoragePayload
      : undefined,
  };`,
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

test("allows a custom range through the final second of today", () => {
  assert.equal(typeof context.api.localDayEnd, "function");
  assert.equal(typeof context.api.validStoredTimeRange, "function");
  const now = new Date(2026, 7, 10, 12, 34, 56, 123).getTime();
  const todayEnd = new Date(2026, 7, 10, 23, 59, 59, 999).getTime();
  const tomorrowStart = new Date(2026, 7, 11, 0, 0, 0, 0).getTime();
  const range = { start: now - 3600_000, end: todayEnd };

  assert.equal(context.api.localDayEnd(now), todayEnd);
  assert.equal(context.api.validStoredTimeRange("usage", range, now), true);
  assert.equal(context.api.validStoredTimeRange("requests", range, now), true);
  assert.equal(
    context.api.validStoredTimeRange(
      "usage",
      { start: range.start, end: tomorrowStart },
      now,
    ),
    false,
  );
  assert.equal(
    context.api.validStoredTimeRange(
      "usage",
      { start: now + 3600_000, end: todayEnd },
      now,
    ),
    false,
  );
  assert.equal(
    context.api.validStoredTimeRange(
      "requests",
      { start: now - 7 * 24 * 3600_000 - 1, end: now },
      now,
    ),
    false,
  );
  assert.equal(
    context.api.validStoredTimeRange(
      "requests",
      { start: now - 7 * 24 * 3600_000 + 1000, end: todayEnd },
      now,
    ),
    false,
  );
});

test("restores the saved window without discarding the last custom range", () => {
  assert.equal(typeof context.api.restoredTimeRangePreference, "function");
  assert.equal(typeof context.api.timeRangeStoragePayload, "function");
  const now = new Date(2026, 7, 10, 12, 0, 0, 0).getTime();
  const range = {
    start: new Date(2026, 7, 10, 0, 0, 0, 0).getTime(),
    end: new Date(2026, 7, 10, 23, 59, 59, 0).getTime(),
  };

  const fixed = context.api.restoredTimeRangePreference(
    "usage",
    { window: "today", range },
    now,
  );
  assert.equal(fixed.window, "today");
  assert.equal(fixed.range.start, range.start);
  assert.equal(fixed.range.end, range.end);

  const custom = context.api.restoredTimeRangePreference(
    "usage",
    { window: "custom", range },
    now,
  );
  assert.equal(custom.window, "custom");

  const legacy = context.api.restoredTimeRangePreference("usage", range, now);
  assert.equal(legacy.window, "custom");

  const invalid = context.api.restoredTimeRangePreference(
    "usage",
    { window: "custom", range: { start: range.end, end: range.start } },
    now,
  );
  assert.equal(invalid.window, "today");
  assert.equal(invalid.range, null);

  context.appliedTimeWindows.usage = "today";
  context.customTimeRanges.usage = range;
  const payload = context.api.timeRangeStoragePayload("usage");
  assert.equal(payload.window, "today");
  assert.equal(payload.range.start, range.start);
  assert.equal(payload.range.end, range.end);
});

test("provides custom controls for usage and seven-day request history", () => {
  assert.match(html, /id="usage-window"[\s\S]*?<option value="custom">自定义时间…<\/option>/);
  assert.match(html, /id="request-window"[\s\S]*?<option value="7d">近 7 天<\/option>/);
  assert.match(html, /id="request-window"[\s\S]*?<option value="custom">自定义时间…<\/option>/);
  assert.match(html, /id="time-range-start-time" type="time" step="1"/);
  assert.match(html, /id="time-range-end-time" type="time" step="1"/);
  assert.match(source, /params\.set\("end_at", String\(range\.end \+ 999\)\)/);
  assert.match(
    source,
    /appliedTimeWindows\.usage = usageWindow\.value;\s*persistTimeRange\("usage"\);/,
  );
  assert.match(
    source,
    /appliedTimeWindows\.requests = requestWindow\.value;\s*persistTimeRange\("requests"\);/,
  );
});
