"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "classic", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const helperStart = source.indexOf("function text");
const helperEnd = source.indexOf("function applyUiConfig");
assert.notEqual(helperStart, -1);
assert.notEqual(helperEnd, -1);

const context = vm.createContext({ uiConfig: { service_id: "codex" } });
vm.runInContext(
  `${source.slice(helperStart, helperEnd)}
  this.api = {
    normalizeViewName: typeof normalizeViewName === "function" ? normalizeViewName : undefined,
    viewStorageKey: typeof viewStorageKey === "function" ? viewStorageKey : undefined,
  };`,
  context,
  { filename: sourcePath },
);

test("normalizes saved console views and unavailable requests", () => {
  assert.equal(typeof context.api.normalizeViewName, "function");
  assert.equal(context.api.normalizeViewName("providers", true), "providers");
  assert.equal(context.api.normalizeViewName("requests", true), "requests");
  assert.equal(context.api.normalizeViewName("settings", true), "settings");
  assert.equal(context.api.normalizeViewName("runtime", true), "runtime");
  assert.equal(context.api.normalizeViewName("invalid", true), "providers");
  assert.equal(context.api.normalizeViewName("requests", false), "providers");
});

test("namespaces the saved view by console service", () => {
  assert.equal(typeof context.api.viewStorageKey, "function");
  assert.equal(context.api.viewStorageKey("codex"), "local-proxy-view-codex");
  assert.equal(context.api.viewStorageKey("claude"), "local-proxy-view-claude");
  assert.equal(context.api.viewStorageKey(""), "local-proxy-view-local");
});

test("persists normalized switches and restores after UI config", () => {
  const switchStart = source.indexOf("function switchView");
  const switchEnd = source.indexOf("function escapeText");
  const initializeStart = source.indexOf("async function initialize");
  assert.notEqual(switchStart, -1);
  assert.notEqual(switchEnd, -1);
  assert.notEqual(initializeStart, -1);
  const switchSource = source.slice(switchStart, switchEnd);
  const initializeSource = source.slice(initializeStart);

  assert.match(switchSource, /function switchView\(viewName, \{ persist = true \} = \{\}\)/);
  assert.match(switchSource, /viewName = normalizeViewName\(/);
  assert.match(switchSource, /if \(persist\) persistView\(viewName\)/);
  assert.match(initializeSource, /await readUiConfig\(\);\s*restoreView\(\);/);
  assert.match(source, /switchView\(savedView, \{ persist: false \}\)/);
});
