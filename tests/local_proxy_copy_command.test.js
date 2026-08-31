"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "classic", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "classic", "index.html"), "utf8");
const start = source.indexOf("async function copyProviderCommand");
const end = source.indexOf("async function shutdownProxy");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

function runtime(response) {
  const clipboard = [];
  const toasts = [];
  const requests = [];
  const context = vm.createContext({
    CONTROL_HEADER: { "X-Local-Proxy-Control": "1" },
    controlUrl: (value) => `/control/codex${value}`,
    encodeURIComponent,
    fetch: async (url, options) => {
      requests.push({ url, options });
      return response;
    },
    navigator: { clipboard: { writeText: async (value) => clipboard.push(value) } },
    showToast: (...values) => toasts.push(values),
  });
  vm.runInContext(`${source.slice(start, end)}\nthis.copy = copyProviderCommand;`, context);
  return { clipboard, copy: context.copy, requests, toasts };
}

test("copies a provider-specific Codex CLI command", async () => {
  const instance = runtime({
    ok: true,
    json: async () => ({ command: "codex -c provider=test", shell: "powershell" }),
  });
  const button = { disabled: false };
  await instance.copy({ provider_id: "provider/a", name: "Provider A", has_credentials: true }, button);

  assert.deepEqual(instance.clipboard, ["codex -c provider=test"]);
  assert.equal(instance.requests[0].url, "/control/codex/api/providers/provider%2Fa/launch-command");
  assert.equal(instance.requests[0].options.method, "POST");
  assert.equal(instance.requests[0].options.headers["X-Local-Proxy-Control"], "1");
  assert.equal(instance.toasts[0][0], "临时启动命令已复制");
  assert.equal(button.disabled, false);
});

test("shows the backend detail when command generation fails", async () => {
  const instance = runtime({
    ok: false,
    status: 409,
    json: async () => ({ detail: "该供应商没有可用 Key" }),
  });
  const button = { disabled: false };
  await instance.copy({ provider_id: "missing", name: "Missing", has_credentials: true }, button);

  assert.deepEqual(instance.clipboard, []);
  assert.equal(instance.toasts[0][0], "复制临时启动命令失败");
  assert.equal(instance.toasts[0][1], "该供应商没有可用 Key");
  assert.equal(instance.toasts[0][2], "error");
  assert.equal(button.disabled, false);
});

test("lets runtime settings hide provider launch command buttons", () => {
  assert.match(html, /id="runtime-show-launch-command" type="checkbox"/);
  assert.match(source, /show_provider_launch_command: runtimeShowLaunchCommandInput\.checked/);
  assert.match(source, /latestRuntimeSettings !== null/);
  assert.match(source, /latestRuntimeSettings\?\.show_provider_launch_command !== false/);
});
