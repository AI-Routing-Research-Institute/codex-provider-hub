"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "index.html"), "utf8");

test("renders provider upload controls without exposing credentials", () => {
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
