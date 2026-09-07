"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.join(__dirname, "..", "proxy_static", "classic");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "app.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "styles.css"), "utf8");

test("classic conversion setting sends the selected value when the API advertises support", () => {
  const payloadFunction = source.slice(source.indexOf("function runtimePayloadFromForm()"), source.indexOf("function renderRuntimeSettingsSummary()"));
  const context = vm.createContext({
    uiConfig: { features: { deepseek_compatibility: true } },
    runtimeHealthUrlInput: { value: "" }, runtimePortInput: { value: "17890" },
    runtimeDatabaseInput: { value: "source.db" }, runtimeConsoleUiInputs: [{ checked: true, value: "classic" }],
    runtimeResponseHeadersTimeoutInput: {}, runtimeResponseHeadersTimeoutMinutesInput: {},
    runtimeStreamIdleTimeoutInput: {}, runtimeStreamIdleTimeoutMinutesInput: {},
    timeoutSecondsFromEditor: () => 300,
    runtimeDeepseekCompatibilityInput: { checked: false },
  });
  vm.runInContext(payloadFunction, context);
  for (const enabled of [false, true, false]) {
    context.runtimeDeepseekCompatibilityInput.checked = enabled;
    assert.equal(context.runtimePayloadFromForm().deepseek_compatibility_enabled, enabled);
  }
  delete context.uiConfig.features.deepseek_compatibility;
  assert.equal(context.runtimePayloadFromForm().deepseek_compatibility_enabled, undefined);
  assert.match(styles, /\.setting-row\[hidden\]\s*\{\s*display:\s*none;/);
});

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

test("classic provider editor labels the model as a launch default", () => {
  assert.match(html, /id="provider-editor-model"/);
  assert.match(html, /启动默认模型/);
  assert.match(html, /转发时不覆盖客户端模型/);
  assert.doesNotMatch(html, /模型重写/);
  assert.match(source, /querySelector\("#provider-editor-model"\)/);
  assert.match(source, /providerEditorModel\.value = provider\.model \|\| ""/);
  assert.match(source, /model: providerEditorModel\.value\.trim\(\)/);
});
