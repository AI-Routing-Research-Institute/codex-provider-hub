"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function requestRecordKey");
const end = source.indexOf("function populateRequestProviders");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({});
vm.runInContext(
  `${source.slice(start, end)}\nthis.api = { formatRequestDuration, requestResultLabel };`,
  context,
  { filename: sourcePath },
);

test("formats short and long request durations", () => {
  assert.equal(context.api.formatRequestDuration(420), "420ms");
  assert.equal(context.api.formatRequestDuration(4200), "4.2s");
  assert.equal(context.api.formatRequestDuration(125000), "2:05");
});

test("labels running, successful, and failed request results", () => {
  assert.equal(
    context.api.requestResultLabel({ state: "running", outcome: "receiving" }),
    "接收中",
  );
  assert.equal(
    context.api.requestResultLabel({ state: "running", outcome: "retrying", retry_count: 2 }),
    "第 3 次尝试",
  );
  assert.equal(
    context.api.requestResultLabel({ succeeded: true, status_code: 200 }),
    "HTTP 200",
  );
  assert.equal(
    context.api.requestResultLabel({ succeeded: false, error_summary: "响应流中断" }),
    "响应流中断",
  );
});

test("defers list replacement while a provider route select is focused", () => {
  const renderStart = source.indexOf("function requestRouteSelectIsFocused");
  const renderEnd = source.indexOf("async function readRequests");
  assert.notEqual(renderStart, -1);
  assert.notEqual(renderEnd, -1);
  const renderContext = vm.createContext({
    document: {
      activeElement: {
        classList: { contains: (name) => name === "request-route-select" },
      },
    },
  });
  vm.runInContext(
    `let requestRouteSelectActive = false;
     let requestRenderPending = false;
     const requestList = {};
     ${source.slice(renderStart, renderEnd)}
     renderRequests();
     this.wasDeferred = requestRenderPending;`,
    renderContext,
    { filename: sourcePath },
  );

  assert.equal(renderContext.wasDeferred, true);
});
