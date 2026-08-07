"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
assert.equal(source.includes("统一端口由 Codex 控制台管理"), false);
assert.equal(source.includes("shared_port"), false);
const start = source.indexOf("function text(");
const end = source.indexOf("async function readUiConfig");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

function element() {
  return {
    textContent: "",
    href: "",
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    toggleAttribute(name, enabled) { this.attributes[name] = enabled; },
  };
}

test("applies service-specific UI configuration to the shared page", () => {
  const selectors = [
    ".app-window", ".brand-mark", ".brand-copy h1", ".brand-copy p",
    ".console-link", "#proxy-url", "#wire-api", "#runtime-config-label",
    "#runtime-config-hint", "#runtime-port-hint", "#runtime-restart-notice",
    "#copy-config", "#footer-message", "#usage-history-popover",
  ];
  const elements = Object.fromEntries(selectors.map((selector) => [selector, element()]));
  const document = {
    title: "",
    querySelector(selector) { return elements[selector] || null; },
  };
  const context = vm.createContext({ document });
  vm.runInContext(
    `let themeStorageKey = "local-proxy-theme";
     let uiConfig = { features: { usage_history: true } };
     ${source.slice(start, end)}
     this.api = { applyUiConfig, getConfig: () => uiConfig };`,
    context,
    { filename: sourcePath },
  );

  context.api.applyUiConfig({
    display_name: "Claude Code 本地中转",
    brand_mark: "CC",
    client_name: "Claude Code",
    protocol_label: "Messages · SSE",
    proxy_url: "http://127.0.0.1:19001",
    peer_console_label: "Codex 控制台",
    peer_console_url: "http://127.0.0.1:19000/control/",
    config_button_label: "复制 Claude 配置",
    features: { usage_history: true },
  });

  assert.equal(document.title, "Claude Code 本地中转");
  assert.equal(elements[".brand-mark"].textContent, "CC");
  assert.equal(elements["#proxy-url"].textContent, "http://127.0.0.1:19001");
  assert.equal(elements["#wire-api"].textContent, "Messages · SSE");
  assert.equal(elements[".console-link"].href, "http://127.0.0.1:19000/control/");
  assert.equal(elements["#copy-config"].textContent, "复制 Claude 配置");
  assert.equal(elements["#usage-history-popover"].attributes.hidden, false);
  assert.equal(source.includes("copyProviderCommand"), true);
  assert.equal(source.includes("launch-command"), true);
});
