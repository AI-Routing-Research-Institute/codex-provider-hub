"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

async function helpers() {
  return import("../proxy_static/src/monitorOrder.js");
}

test("aligns management providers to the public health-sorted order", async () => {
  const { alignMonitorProviders } = await helpers();
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
    alignMonitorProviders(configured, status).map(provider => [provider.id, provider.state]),
    [["gamma", "healthy"], ["alpha", "down"], ["beta", undefined]],
  );
});

test("builds a status URL without discarding its path or existing parameters", async () => {
  const { statusUrlForWindow } = await helpers();
  assert.equal(
    statusUrlForWindow("http://status.example/codex-status/api/status?foo=bar"),
    "http://status.example/codex-status/api/status?foo=bar&window=24h",
  );
});
