"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "classic", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "classic", "index.html"), "utf8");

test("renders provider upload controls without exposing credentials", () => {
  assert.match(html, /id="runtime-show-status-upload" type="checkbox"/);
  assert.match(source, /show_status_upload: runtimeShowStatusUploadInput\.checked/);
  assert.match(source, /latestRuntimeSettings\?\.show_status_upload !== false/);
  assert.match(source, /uiConfig\.features\.status_upload === true/);
  assert.match(source, /uploadButton\.textContent = "上传检测"/);
  assert.match(source, /provider\.status_upload_supported !== false/);
  assert.match(source, /\/api\/providers\/\$\{encodeURIComponent\(provider\.provider_id\)\}\/status-upload\/preview/);
  assert.match(source, /body: JSON\.stringify\(\{ models \}\)/);
  assert.doesNotMatch(source, /JSON\.stringify\(\{[^}]*api_key/);
});

test("supports one-time SSH initialization in the upload dialog", () => {
  assert.match(html, /id="status-upload-password" type="password"/);
  assert.match(source, /\/api\/status-upload\/bootstrap/);
  assert.match(source, /statusUploadSsh\.hidden = settings\.initialized === true/);
  assert.match(source, /statusUploadChangeTarget\.addEventListener/);
});

test("renders the remote monitoring management view", () => {
  assert.match(html, /data-view="monitor"/);
  assert.match(html, /id="monitor-view"/);
  assert.match(source, /api\/status-management/);
  assert.match(source, /立即检测/);
  assert.match(source, /删除服务器监控供应商/);
  assert.match(source, /api\/status-management\/providers\/\$\{encodeURIComponent\(provider\.id\)\}\/probe/);
  assert.doesNotMatch(source, /statusUrl\.replace\([^\n]+api\/manual-probes/);
  assert.match(source, /queued: "排队中"/);
  assert.match(source, /alignMonitorProviders\(monitorProviders, payload\.providers\)/);
  assert.match(source, /action: "disable"/);
  assert.match(source, /probe_mode: "manual_only"/);
  assert.match(source, /statusUrlForWindow\(configuredUrl\)/);
});
