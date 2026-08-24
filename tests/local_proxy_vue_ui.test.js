"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.join(__dirname, "..", "proxy_static", "src");
const apiPath = path.join(root, "api.js");
const apiSource = fs.readFileSync(apiPath, "utf8");

function loadApi(pathname, fetchImpl = async () => new Response("{}", {
  headers: { "Content-Type": "application/json" },
})) {
  const source = apiSource.replaceAll("export ", "");
  const context = vm.createContext({
    Headers,
    Response,
    fetch: fetchImpl,
    window: { location: { pathname } },
  });
  vm.runInContext(
    `${source}\nthis.api = { controlBase, controlUrl, controlFetch, jsonOptions };`,
    context,
    { filename: apiPath },
  );
  return context.api;
}

test("derives service-specific API bases and supports the Vite root", () => {
  assert.equal(loadApi("/control/codex/").controlBase, "/control/codex");
  assert.equal(loadApi("/control/claude/").controlBase, "/control/claude");
  assert.equal(loadApi("/").controlBase, "/control/codex");
  assert.equal(loadApi("/control/claude/").controlUrl("api/status"), "/control/claude/api/status");
  assert.equal(
    loadApi("/control/codex/").controlUrl("/control/codex/api/codex-config"),
    "/control/codex/api/codex-config",
  );
});

test("sends the local control header and returns JSON", async () => {
  let observed;
  const api = loadApi("/control/codex/", async (url, options) => {
    observed = { url, options };
    return new Response('{"status":"ok"}', {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  const payload = await api.controlFetch("/api/status", { method: "POST" });

  assert.equal(payload.status, "ok");
  assert.equal(observed.url, "/control/codex/api/status");
  assert.equal(observed.options.cache, "no-store");
  assert.equal(observed.options.headers.get("X-Local-Proxy-Control"), "1");
});

test("surfaces backend error details", async () => {
  const api = loadApi("/control/codex/", async () => new Response(
    '{"detail":"供应商不存在"}',
    { status: 404, headers: { "Content-Type": "application/json" } },
  ));

  await assert.rejects(() => api.controlFetch("/api/providers/missing"), /供应商不存在/);
});

test("Vue views use the shared API helper and clean up polling timers", () => {
  const files = [
    "App.vue",
    "components/ConnectionStrip.vue",
    "components/ProvidersView.vue",
    "components/RequestsView.vue",
    "components/SettingsView.vue",
    "components/RuntimeView.vue",
    "components/MonitorView.vue",
  ];
  const sources = files.map(file => fs.readFileSync(path.join(root, file), "utf8"));

  for (const source of sources) {
    assert.match(source, /controlFetch/);
    assert.doesNotMatch(source, /fetch\(['"]\/control\//);
  }
  for (const index of [1, 2, 3]) {
    assert.match(sources[index], /onBeforeUnmount/);
    assert.match(sources[index], /window\.clearInterval\(timer\)/);
  }
});

test("Vue templates preserve the original desktop console structure", () => {
  const app = fs.readFileSync(path.join(root, "App.vue"), "utf8");
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  const requests = fs.readFileSync(path.join(root, "components", "RequestsView.vue"), "utf8");
  const settings = fs.readFileSync(path.join(root, "components", "SettingsView.vue"), "utf8");
  const select = fs.readFileSync(path.join(root, "components", "ui", "UiSelect.vue"), "utf8");
  const icon = fs.readFileSync(path.join(root, "components", "ui", "UiIcon.vue"), "utf8");
  const styles = fs.readFileSync(path.join(root, "styles.css"), "utf8");

  assert.match(app, /class="stage"/);
  assert.match(app, /class="app-window"/);
  assert.match(app, /class="footer"/);
  assert.match(providers, /class="workspace view-panel"/);
  assert.match(providers, /class="provider-pane"/);
  assert.match(providers, /class="routing-panel"/);
  assert.match(providers, /class="usage-summary"/);
  assert.match(providers, /class="provider-list-shell"/);
  assert.match(requests, /class="request-table-header"/);
  assert.match(requests, /class="request-row"/);
  assert.match(settings, /UiSelect/);
  assert.match(select, /role="listbox"/);
  assert.match(select, /@keydown="onTriggerKeydown"/);
  assert.match(icon, /viewBox="0 0 24 24"/);
  assert.doesNotMatch(providers, /<select\b/);
  assert.doesNotMatch(requests, /<select\b/);
  assert.doesNotMatch(settings, /<select\b/);
  assert.match(styles, /\.stage\s*\{[^}]*width:\s*100%[^}]*min-height:\s*100vh[^}]*min-height:\s*100dvh/s);
  assert.match(styles, /\.app-window\s*\{[^}]*width:\s*100%[^}]*height:\s*100vh[^}]*height:\s*100dvh/s);
  assert.doesNotMatch(styles, /\.app-window\s*\{[^}]*width:\s*min\(1440px, 100%\)/s);
  assert.match(styles, /\.workspace\s*\{[^}]*grid-template-columns:\s*minmax\(520px, 1fr\) 340px/s);
  assert.match(styles, /--provider-grid-columns:/);
  assert.match(styles, /--request-columns:/);
  assert.match(styles, /scrollbar-gutter:\s*stable/);
  assert.match(styles, /@media\s*\(max-width:\s*860px\)[\s\S]*\.workspace\s*\{\s*grid-template-columns:\s*1fr;/s);
});
