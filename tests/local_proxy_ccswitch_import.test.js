"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "classic", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("const DEFAULT_CC_SWITCH_CODEX_MODEL");
const end = source.indexOf("async function fetchControl");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({
  URLSearchParams,
  window: {
    location: {
      pathname: "/control/codex/",
      origin: "http://127.0.0.1:17890",
    },
  },
});
vm.runInContext(
  `${source.slice(start, end)}\nthis.build = buildCcSwitchImportDeeplink;`,
  context,
  { filename: sourcePath },
);

test("builds a Codex CC Switch import deeplink for the legacy console", () => {
  const result = context.build({
    serviceId: "codex",
    endpoint: "http://127.0.0.1:17890/v1/",
    homepage: "http://127.0.0.1:17890",
    providerName: "Codex 本地中转",
  });
  const params = new URL(result).searchParams;

  assert.equal(params.get("app"), "codex");
  assert.equal(params.get("endpoint"), "http://127.0.0.1:17890/v1");
  assert.equal(params.get("apiKey"), "local-codex-proxy");
  assert.equal(params.get("model"), "gpt-5.6-sol");
});

test("builds a Claude CC Switch import deeplink without a Codex model", () => {
  const result = context.build({
    serviceId: "claude",
    endpoint: "http://127.0.0.1:17890",
    homepage: "http://127.0.0.1:17890",
    providerName: "Claude 本地中转",
  });
  const params = new URL(result).searchParams;

  assert.equal(params.get("app"), "claude");
  assert.equal(params.get("apiKey"), "local-claude-proxy");
  assert.equal(params.has("model"), false);
});
