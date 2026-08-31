"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "classic", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function monitorProviderId");
const end = source.indexOf("function movableMonitorIndexes");
assert.notEqual(start, -1);
assert.notEqual(end, -1);
const context = vm.createContext({
  URL,
  window: { location: { origin: "http://localhost" } },
});
vm.runInContext(
  `${source.slice(start, end)}\nthis.helpers = { alignMonitorProviders, statusUrlForWindow };`,
  context,
  { filename: sourcePath },
);

test("aligns management providers to the public health-sorted order", () => {
  const { alignMonitorProviders } = context.helpers;
  const configured = [
    { id: "alpha", name: "Alpha" },
    { id: "beta", name: "Beta" },
    { id: "gamma", name: "Gamma" },
  ];
  const status = [
    { provider_id: "gamma", state: "healthy" },
    { provider_id: "alpha", state: "down" },
  ];

  assert.deepEqual(
    Array.from(
      alignMonitorProviders(configured, status),
      (provider) => [provider.id, provider.state],
    ),
    [["gamma", "healthy"], ["alpha", "down"], ["beta", undefined]],
  );
});

test("builds a status URL without discarding its path or existing parameters", () => {
  const { statusUrlForWindow } = context.helpers;
  assert.equal(
    statusUrlForWindow("http://status.example/codex-status/api/status?foo=bar"),
    "http://status.example/codex-status/api/status?foo=bar&window=24h",
  );
});
