"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.join(__dirname, "..", "proxy_static");
const modern = fs.readFileSync(
  path.join(root, "src", "components", "ProvidersView.vue"),
  "utf8",
);
const modernStyles = fs.readFileSync(path.join(root, "src", "styles.css"), "utf8");
const classicHtml = fs.readFileSync(path.join(root, "classic", "index.html"), "utf8");
const classic = fs.readFileSync(path.join(root, "classic", "app.js"), "utf8");
const classicStyles = fs.readFileSync(path.join(root, "classic", "styles.css"), "utf8");

test("modern console manages provider model mappings", () => {
  assert.match(modern, /@click\.stop="openModelMappings\(provider\)"[^>]*>映射</);
  assert.match(modern, /class="provider-editor model-mapping-editor"/);
  assert.match(modern, /本地模型[\s\S]*远端模型/);
  assert.match(modern, /\/model-mappings`/);
  assert.match(modern, /jsonOptions\('PUT', \{ mappings: rows \}\)/);
  assert.match(modern, /本地模型和远端模型都不能为空/);
  assert.match(modern, /本地模型不能重复/);
  assert.match(modern, /modelMappingProvider\.value = provider[\s\S]*try \{/);
  assert.match(modern, /catch \(error\) \{[\s\S]*modelMappingError\.value = error\.message/);
  assert.match(modern, /:disabled="modelMappingSaving \|\| !modelMappingLoaded"/);
  assert.match(modernStyles, /\.model-mapping-row/);
  assert.match(
    modernStyles,
    /@media\s*\(max-width:\s*600px\)[\s\S]*\.model-mapping-row/s,
  );
});

test("classic console manages provider model mappings", () => {
  assert.match(classicHtml, /id="model-mapping-editor"/);
  assert.match(classicHtml, /本地模型[\s\S]*远端模型/);
  assert.match(classic, /openModelMappingEditor\(provider\)/);
  assert.match(classic, /\/model-mappings`/);
  assert.match(classic, /method:\s*"PUT"/);
  assert.match(classic, /body:\s*JSON\.stringify\(\{ mappings \}\)/);
  assert.match(classic, /本地模型和远端模型都不能为空/);
  assert.match(classic, /本地模型不能重复/);
  assert.match(classic, /modelMappingSave\.disabled = true;[\s\S]*renderModelMappingRows\(payload\.mappings \|\| \[\]\);[\s\S]*modelMappingSave\.disabled = false;/);
  assert.match(classicStyles, /\.model-mapping-row/);
  assert.match(
    classicStyles,
    /@media\s*\(max-width:\s*600px\)[\s\S]*\.model-mapping-row/s,
  );
});
