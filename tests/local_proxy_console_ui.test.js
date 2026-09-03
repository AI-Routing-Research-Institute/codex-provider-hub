"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.join(__dirname, "..", "proxy_static", "classic");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "app.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "styles.css"), "utf8");

test("classic console saves the selected UI and reloads without an override", () => {
  assert.match(html, /name="runtime-console-ui" value="classic"/);
  assert.match(html, /name="runtime-console-ui" value="modern"/);
  assert.match(source, /console_ui: selectedConsoleUi \|\| "modern"/);
  assert.match(source, /settings\.console_ui \|\| "modern"/);
  assert.match(source, /window\.location\.href = nextUrl\.toString\(\)/);
  assert.match(source, /nextUrl\.toString\(\) === window\.location\.href/);
  assert.match(source, /window\.location\.reload\(\)/);
  assert.doesNotMatch(source, /setTimeout\(\(\) => \{[\s\S]*searchParams\.delete\("ui"\)/);
  assert.match(source, /searchParams\.delete\("ui"\)/);
  assert.match(styles, /\.setting-segmented/);
  assert.match(styles, /\.setting-readonly-row\s+code\s*\{[^}]*align-self:\s*center/s);
  assert.match(html, /id="update-github-link"/);
  assert.match(html, /href="https:\/\/github\.com\/AI-Routing-Research-Institute\/codex-provider-hub"/);
});

test("classic provider editor round-trips the model rewrite field", () => {
  assert.match(html, /id="provider-editor-model"/);
  assert.match(source, /querySelector\("#provider-editor-model"\)/);
  assert.match(source, /providerEditorModel\.value = provider\.model \|\| ""/);
  assert.match(source, /model: providerEditorModel\.value\.trim\(\)/);
});
