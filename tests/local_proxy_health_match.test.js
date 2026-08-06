"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function normalizeProviderEndpoint");
const end = source.indexOf("function normalizeHealthState");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({ URL });
vm.runInContext(
  `let latestHealthStatus;\n${source.slice(start, end)}\n` +
    "this.api = { providerEndpointsMatch, healthStatusForProvider };",
  context,
  { filename: sourcePath },
);

test("matches provider endpoints by exact value or path-segment prefix", () => {
  const { providerEndpointsMatch } = context.api;

  assert.equal(providerEndpointsMatch(
    "https://api.example.test/",
    "api.example.test/v1",
  ), true);
  assert.equal(providerEndpointsMatch(
    "api.example.test/openai",
    "https://api.example.test/openai/v1/",
  ), true);
  assert.equal(providerEndpointsMatch(
    "api.example.test/v1",
    "api.example.test/v10",
  ), false);
  assert.equal(providerEndpointsMatch(
    "api.example.test",
    "api.example.test.evil/v1",
  ), false);
  assert.equal(providerEndpointsMatch(
    "api.example.test:8443",
    "api.example.test/v1",
  ), false);
});

test("prefers exact matches and rejects ambiguous prefix matches", () => {
  vm.runInContext(`latestHealthStatus = {
    providers: [
      { provider_id: "root", base_url: "https://api.example.test" },
      { provider_id: "v1", base_url: "https://api.example.test/v1" },
    ],
  };`, context);

  assert.equal(
    context.api.healthStatusForProvider({ endpoint: "api.example.test/v1" }).provider_id,
    "v1",
  );

  vm.runInContext(`latestHealthStatus = {
    providers: [
      { provider_id: "v1", base_url: "https://api.example.test/v1" },
      { provider_id: "v2", base_url: "https://api.example.test/v2" },
    ],
  };`, context);
  assert.equal(
    context.api.healthStatusForProvider({ endpoint: "api.example.test" }),
    null,
  );
});
