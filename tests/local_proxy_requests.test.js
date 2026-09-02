"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "proxy_static", "classic", "app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const htmlSource = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "classic", "index.html"), "utf8");
const stylesSource = fs.readFileSync(path.join(__dirname, "..", "proxy_static", "classic", "styles.css"), "utf8");
const start = source.indexOf("function requestRecordKey");
const end = source.indexOf("function populateRequestProviders");
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = vm.createContext({});
vm.runInContext(
  `${source.slice(start, end)}\nthis.api = {
    formatRequestDuration,
    requestReasoningEffortLabel,
    requestResultLabel,
    requestActualProviderLabel,
  };`,
  context,
  { filename: sourcePath },
);

test("formats short and long request durations", () => {
  assert.equal(context.api.formatRequestDuration(420), "420ms");
  assert.equal(context.api.formatRequestDuration(4200), "4.2s");
  assert.equal(context.api.formatRequestDuration(125000), "2:05");
});

test("labels request reasoning effort", () => {
  assert.equal(context.api.requestReasoningEffortLabel("none"), "关闭");
  assert.equal(context.api.requestReasoningEffortLabel("minimal"), "极低");
  assert.equal(context.api.requestReasoningEffortLabel("low"), "低");
  assert.equal(context.api.requestReasoningEffortLabel("medium"), "中");
  assert.equal(context.api.requestReasoningEffortLabel("high"), "高");
  assert.equal(context.api.requestReasoningEffortLabel("xhigh"), "极高");
  assert.equal(context.api.requestReasoningEffortLabel(null), "—");
  assert.equal(context.api.requestReasoningEffortLabel("custom"), "custom");
});

test("uses ten aligned request columns including the mapped model", () => {
  assert.match(htmlSource, />模型<\/span><span>映射模型<\/span>/);
  assert.match(htmlSource, /request-column-reasoning">推理强度/);
  assert.match(source, /upstreamModel\.textContent = item\.upstream_model \|\| "—"/);
  assert.match(source, /row\.append\(status, startedAt, session, route, model, upstreamModel, reasoning, duration, token, result\)/);
  assert.match(stylesSource, /--request-columns:[^;]+;/);
  assert.match(stylesSource, /\.request-upstream-model\.mapped \{ color: var\(--teal\); font-weight: 700; \}/);
  assert.match(stylesSource, /\.request-column-duration, \.request-column-token \{ text-align: right; \}/);
  assert.match(stylesSource, /\.request-column-status, \.request-column-time, \.request-column-reasoning, \.request-column-result \{ text-align: center; \}/);
  assert.doesNotMatch(stylesSource, /\.request-table-header span:nth-child/);
});

test("labels running, successful, and failed request results", () => {
  assert.equal(
    context.api.requestResultLabel({ state: "running", phase: "connecting" }),
    "连接上游",
  );
  assert.equal(
    context.api.requestResultLabel({ state: "running", phase: "waiting_first_chunk" }),
    "等待首包",
  );
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
    "200",
  );
  assert.equal(
    context.api.requestResultLabel({ succeeded: false, error_summary: "响应流中断" }),
    "响应流中断",
  );
  assert.equal(
    context.api.requestResultLabel({
      succeeded: false,
      error_kind: "client_disconnected",
      error_summary: "客户端在响应完成前断开连接",
    }),
    "客户端取消",
  );
  assert.equal(
    context.api.requestResultLabel({
      succeeded: false,
      error_kind: "session_superseded",
      error_summary: "已由同会话新请求接管",
    }),
    "同会话新请求接管",
  );
  assert.match(source, /\["client_disconnected", "session_superseded"\]/);
  assert.match(source, /const CONTROL_REQUEST_TIMEOUT_MS = 8_000/);
  assert.match(source, /async function fetchControl\(/);
  assert.match(source, /本地中转响应超时，请检查服务是否卡住/);
  assert.match(source, /stream_idle_timeout: "上游长时间无数据"/);
});
test("keeps the actual provider visible in each request row", () => {
  assert.equal(
    context.api.requestActualProviderLabel({ state: "running", provider_name: "公司" }),
    "正在使用 · 公司",
  );
  assert.equal(
    context.api.requestActualProviderLabel({ state: "failed", provider_name: "zzzcoding" }),
    "zzzcoding",
  );
  assert.doesNotMatch(source, /request-route-select|claimRequestRouteControl/);
});
test("opens the request page without provider or status filters", () => {
  const openStart = source.indexOf("function openAllRequests");
  const openEnd = source.indexOf("function switchView");
  assert.notEqual(openStart, -1);
  assert.notEqual(openEnd, -1);
  const openSource = source.slice(openStart, openEnd);
  assert.match(openSource, /requestProvider\.value = ""/);
  assert.match(openSource, /requestStatus\.value = "all"/);
  assert.doesNotMatch(source, /悬停查看会话，点击进入请求页/);
});
test("labels duplicate routed sessions without exposing thread ids", () => {
  const start = source.indexOf("function sessionRouteOptionLabel");
  const end = source.indexOf("function renderSessionRouteProviders");
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const routeContext = vm.createContext({});
  vm.runInContext(
    `function formatRetryTime() { return "16:55:57"; }\n${source.slice(start, end)}\nthis.label = sessionRouteOptionLabel;`,
    routeContext,
    { filename: sourcePath },
  );

  assert.equal(
    routeContext.label({ name: "设备打卡", active: false, updated_at: 1 }, 2, 2),
    "设备打卡 · 会话 2/2 · 16:55:57",
  );
  assert.doesNotMatch(
    routeContext.label({ name: "设备打卡", active: false, updated_at: 1 }, 1, 1),
    /thread|session_key/i,
  );
});

test("throttles session route refreshes and keeps cached items while refreshing", () => {
  const start = source.indexOf("async function readSessionRoutes");
  const end = source.indexOf("function openSessionRoutePopover");
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const readSource = source.slice(start, end);

  assert.match(source, /const SESSION_ROUTE_CACHE_MS = 60_000/);
  assert.match(readSource, /if \(sessionRouteLoading\) return/);
  assert.match(readSource, /Date\.now\(\) - sessionRouteLastReadAt < SESSION_ROUTE_CACHE_MS/);
  assert.match(readSource, /if \(!hadItems\) sessionRouteItems = \[\]/);
  assert.match(source, /readSessionRoutes\(\{ quiet: true, force: true \}\)/);
});

test("serializes status polling and pauses hidden control tabs", () => {
  const start = source.indexOf("async function readStatus");
  const end = source.indexOf("async function readHealthStatus");
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const readStatusSource = source.slice(start, end);

  assert.match(source, /let statusRequestActive = false/);
  assert.match(
    readStatusSource,
    /controlRequestActive \|\| statusRequestActive \|\| \(!force && document\.hidden\)/,
  );
  assert.match(readStatusSource, /statusRequestActive = true/);
  assert.match(readStatusSource, /finally \{\s*statusRequestActive = false/);
  assert.match(source, /document\.addEventListener\("visibilitychange"/);
  assert.match(source, /readStatus\(\{ quiet: true, force: true \}\)/);
});

test("renders quiet request refreshes only after fresh data arrives", () => {
  const requestStart = source.indexOf("async function readRequests");
  const requestEnd = source.indexOf("function openAllRequests");
  const statusStart = source.indexOf("function renderStatus");
  const statusEnd = source.indexOf("async function readStatus");
  assert.notEqual(requestStart, -1);
  assert.notEqual(requestEnd, -1);
  assert.notEqual(statusStart, -1);
  assert.notEqual(statusEnd, -1);

  assert.match(
    source.slice(requestStart, requestEnd),
    /requestLoading = true;\s*if \(!quiet\) renderRequests\(\)/,
  );
  assert.doesNotMatch(source.slice(statusStart, statusEnd), /renderRequests\(\)/);
});

test("keeps request pagination on the shared table scroll container", () => {
  assert.match(source, /const requestTableShell = document\.querySelector\("\.request-table-shell"\)/);
  assert.match(source, /requestTableShell\.addEventListener\("scroll"/);
  assert.match(source, /requestTableShell\.scrollHeight\s*- requestTableShell\.scrollTop\s*- requestTableShell\.clientHeight/);
  assert.doesNotMatch(source, /requestList\.addEventListener\("scroll"/);
});

test("uses route-aware draining request counts", () => {
  const start = source.indexOf("const drainingProviders");
  const end = source.indexOf("renderProviderList();", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const drainingSource = source.slice(start, end);

  assert.match(drainingSource, /provider\.draining_requests/);
  assert.doesNotMatch(drainingSource, /!provider\.current/);
  assert.doesNotMatch(drainingSource, /provider\.active_requests/);
});

test("deduplicates active session names for the provider hover list", () => {
  const activeStart = source.indexOf("function activeSessionNames");
  const activeEnd = source.indexOf("function renderActiveSessionsPopover");
  assert.notEqual(activeStart, -1);
  assert.notEqual(activeEnd, -1);
  const activeContext = vm.createContext({});
  vm.runInContext(
    `${source.slice(activeStart, activeEnd)}\nthis.activeSessionNames = activeSessionNames;`,
    activeContext,
    { filename: sourcePath },
  );

  assert.deepEqual(
    Array.from(activeContext.activeSessionNames({
      active_sessions: [
        { name: "设备打卡" },
        { name: "设备打卡" },
        { name: "Codex服务可用检测" },
      ],
    })),
    ["设备打卡", "Codex服务可用检测"],
  );
});
