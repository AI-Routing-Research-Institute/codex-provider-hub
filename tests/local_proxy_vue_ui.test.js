"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { pathToFileURL } = require("node:url");
const vm = require("node:vm");

const root = path.join(__dirname, "..", "proxy_static", "src");
const apiPath = path.join(root, "api.js");
const apiSource = fs.readFileSync(apiPath, "utf8");
const ccSwitchPath = path.join(root, "ccswitch.js");
const ccSwitchSource = fs.readFileSync(ccSwitchPath, "utf8");

function loadApi(pathname, fetchImpl = async () => new Response("{}", {
  headers: { "Content-Type": "application/json" },
}), windowOverrides = {}) {
  const source = apiSource.replaceAll("export ", "");
  const context = vm.createContext({
    AbortController,
    Headers,
    Response,
    fetch: fetchImpl,
    window: {
      location: { pathname },
      setTimeout,
      clearTimeout,
      ...windowOverrides,
    },
  });
  vm.runInContext(
    `${source}\nthis.api = { controlBase, controlUrl, controlFetch, jsonOptions };`,
    context,
    { filename: apiPath },
  );
  return context.api;
}

function loadCcSwitch() {
  const source = ccSwitchSource.replaceAll("export ", "");
  const context = vm.createContext({ URLSearchParams });
  vm.runInContext(
    `${source}\nthis.ccSwitch = { DEFAULT_CC_SWITCH_CODEX_MODEL, LOCAL_PROXY_PLACEHOLDER_KEYS, buildCcSwitchImportDeeplink };`,
    context,
    { filename: ccSwitchPath },
  );
  return context.ccSwitch;
}

test("builds a CC Switch Codex import deeplink for the local proxy", () => {
  const ccSwitch = loadCcSwitch();
  const deeplink = ccSwitch.buildCcSwitchImportDeeplink({
    serviceId: "codex",
    endpoint: "http://127.0.0.1:17890/v1/",
    homepage: "http://127.0.0.1:17890",
    providerName: "Codex 本地中转",
  });
  const params = new URLSearchParams(deeplink.split("?")[1]);

  assert.equal(deeplink.split("?")[0], "ccswitch://v1/import");
  assert.equal(params.get("resource"), "provider");
  assert.equal(params.get("app"), "codex");
  assert.equal(params.get("model"), "gpt-5.6-sol");
  assert.equal(params.get("name"), "Codex 本地中转");
  assert.equal(params.get("homepage"), "http://127.0.0.1:17890");
  assert.equal(params.get("endpoint"), "http://127.0.0.1:17890/v1");
  assert.equal(params.get("apiKey"), "local-codex-proxy");
  assert.equal(params.get("configFormat"), "json");
  assert.equal(params.has("usageScript"), false);
});

test("builds a CC Switch Claude import deeplink without forcing a model", () => {
  const ccSwitch = loadCcSwitch();
  const deeplink = ccSwitch.buildCcSwitchImportDeeplink({
    serviceId: "claude",
    endpoint: "http://127.0.0.1:17890/",
    homepage: "http://127.0.0.1:17890",
    providerName: "Claude Code 本地中转",
  });
  const params = new URLSearchParams(deeplink.split("?")[1]);

  assert.equal(params.get("app"), "claude");
  assert.equal(params.get("endpoint"), "http://127.0.0.1:17890");
  assert.equal(params.get("apiKey"), "local-claude-proxy");
  assert.equal(params.has("model"), false);
});

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

test("bounds control requests and reports stalled local proxy responses", async () => {
  let observed;
  const api = loadApi(
    "/control/codex/",
    async (url, options) => {
      observed = { url, options };
      if (options.signal.aborted) {
        const error = new Error("aborted");
        error.name = "AbortError";
        throw error;
      }
      return new Response("{}", { headers: { "Content-Type": "application/json" } });
    },
    { setTimeout: callback => { callback(); return 1; }, clearTimeout: () => {} },
  );

  await assert.rejects(
    () => api.controlFetch("/api/status"),
    /本地中转响应超时，请检查服务是否卡住/,
  );
  assert.equal(observed.options.signal.aborted, true);
  assert.match(apiSource, /const CONTROL_REQUEST_TIMEOUT_MS = 8_000/);
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

test("provider action visibility follows the saved runtime settings", () => {
  const app = fs.readFileSync(path.join(root, "App.vue"), "utf8");
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  const runtime = fs.readFileSync(path.join(root, "components", "RuntimeView.vue"), "utf8");

  assert.match(app, /<ProvidersView[^>]*:show-launch-command="showProviderLaunchCommand"/);
  assert.match(app, /<ProvidersView[^>]*:show-status-upload="showStatusUpload"/);
  assert.match(app, /<RuntimeView[^>]*@launch-command-visibility-change="showProviderLaunchCommand = \$event"/);
  assert.match(app, /<RuntimeView[^>]*@status-upload-visibility-change="showStatusUpload = \$event"/);
  assert.match(runtime, /defineEmits\(\['launch-command-visibility-change', 'status-upload-visibility-change'\]\)/);
  assert.match(runtime, /emit\('launch-command-visibility-change', settings\.value\.show_provider_launch_command !== false\)/);
  assert.match(runtime, /emit\('status-upload-visibility-change', settings\.value\.show_status_upload !== false\)/);
  assert.match(providers, /provider_launch_command !== false && props\.showLaunchCommand/);
  assert.match(providers, /status_upload !== false && props\.showStatusUpload/);
  assert.match(providers, /showStatusUpload && provider\.status_upload_supported/);
});

test("Vue runtime settings switch between classic and modern consoles", () => {
  const runtime = fs.readFileSync(path.join(root, "components", "RuntimeView.vue"), "utf8");
  const styles = fs.readFileSync(path.join(root, "styles.css"), "utf8");

  assert.match(runtime, /v-model="settings\.console_ui"[^>]*value="classic"/);
  assert.match(runtime, /v-model="settings\.console_ui"[^>]*value="modern"/);
  assert.match(runtime, /console_ui: settings\.value\.console_ui \|\| 'modern'/);
  assert.match(runtime, /const activeConsoleUi = 'modern'/);
  assert.match(runtime, /settings\.value\.console_ui !== activeConsoleUi/);
  assert.doesNotMatch(runtime, /previousConsoleUi = settings\.value\.console_ui/);
  assert.doesNotMatch(runtime, /setTimeout\(\(\) => \{[\s\S]*searchParams\.delete\('ui'\)/);
  assert.match(runtime, /searchParams\.delete\('ui'\)/);
  assert.match(runtime, /window\.location\.href = nextUrl\.toString\(\)/);
  assert.match(runtime, /nextUrl\.toString\(\) === window\.location\.href/);
  assert.match(runtime, /window\.location\.reload\(\)/);
  assert.match(styles, /\.setting-segmented/);
});

test("shared time range selector uses day precision and stays anchored", async () => {
  const selector = fs.readFileSync(path.join(root, "components", "TimeRangeSelect.vue"), "utf8");
  const styles = fs.readFileSync(path.join(root, "styles.css"), "utf8");
  const range = await import(pathToFileURL(path.join(root, "timeRange.js")));
  const start = new Date(2026, 7, 24).getTime();
  const end = new Date(2026, 7, 25).getTime();
  assert.deepEqual(range.preciseRange("2026-08-24", "00:00:00", "2026-08-25", "00:00:00"), { startAt: start, endAt: end });
  assert.deepEqual(range.preciseRange("2026-08-24", "12:30:00", "2026-08-24", "13:45:15"), { startAt: new Date(2026, 7, 24, 12, 30).getTime(), endAt: new Date(2026, 7, 24, 13, 45, 15).getTime() });
  assert.match(selector, /type="date" aria-label="开始日期"/);
  assert.match(selector, /type="date" aria-label="结束日期"/);
  assert.match(selector, /type="time" step="1" aria-label="开始时间"/);
  assert.match(selector, /type="time" step="1" aria-label="结束时间"/);
  assert.match(selector, /pendingValue/);
  assert.match(selector, /dateEdited/);
  assert.match(selector, /startTime\.value = dates\.startTime/);
  assert.match(selector, /endTime\.value = dates\.endTime/);
  assert.match(selector, /if \(usesPreset\) \{[\s\S]*emit\('change', pendingValue\.value\)/);
  assert.match(selector, /preciseRange\(startDate\.value, startTime\.value, endDate\.value, endTime\.value\)/);
  assert.match(styles, /\.time-range-select\s*\{[^}]*position:\s*relative/s);
  assert.match(styles, /\.time-range-select-menu\s*\{[^}]*position:\s*absolute[^}]*top:\s*calc\(100% \+ 6px\)[^}]*left:\s*0/s);
  assert.match(styles, /\.request-filters \.time-range-custom-fields input\s*\{[^}]*min-width:\s*0[^}]*box-sizing:\s*border-box/s);
  assert.match(selector, /closest\('\.request-filters, \.toolbar-actions'\)/);
});

test("Vue templates preserve the original desktop console structure", () => {
  const app = fs.readFileSync(path.join(root, "App.vue"), "utf8");
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  const requests = fs.readFileSync(path.join(root, "components", "RequestsView.vue"), "utf8");
  const settings = fs.readFileSync(path.join(root, "components", "SettingsView.vue"), "utf8");
  const select = fs.readFileSync(path.join(root, "components", "ui", "UiSelect.vue"), "utf8");
  const icon = fs.readFileSync(path.join(root, "components", "ui", "UiIcon.vue"), "utf8");
  const brandIcon = fs.readFileSync(path.join(root, "components", "ui", "BrandIcon.vue"), "utf8");
  const titlebar = fs.readFileSync(path.join(root, "components", "Titlebar.vue"), "utf8");
  const connectionStrip = fs.readFileSync(path.join(root, "components", "ConnectionStrip.vue"), "utf8");
  const styles = fs.readFileSync(path.join(root, "styles.css"), "utf8");

  assert.match(app, /class="stage"/);
  assert.match(app, /class="app-window"/);
  assert.match(app, /<footer class="footer">\s*<ConnectionStrip :config="config" \/>/);
  assert.match(app, /class="primary-button ccs-import-button"[^>]*@click="importToCcSwitch"><UiIcon name="upload"/);
  assert.match(app, /serviceId: config\.value\.service_id/);
  assert.match(app, /buildCcSwitchImportDeeplink/);
  assert.doesNotMatch(app, /footerMessage|Key 仅在复制临时启动命令时写入剪贴板/);
  assert.match(connectionStrip, /固定连接/);
  assert.match(connectionStrip, /本机可用/);
  assert.match(connectionStrip, /数据来源/);
  assert.match(providers, /class="workspace view-panel"/);
  assert.match(providers, /class="provider-pane"/);
  assert.match(providers, /class="routing-panel"/);
  assert.match(providers, /class="usage-summary"/);
  assert.match(providers, /class="provider-list-shell"/);
  assert.match(providers, /<span>配置<\/span>/);
  assert.doesNotMatch(providers, /'认证'/);
  assert.match(providers, /<span v-if="manageMode">操作<\/span>/);
  assert.match(providers, /<div class="provider-meta">\s*<button v-if="showLaunchCommand"[^>]*title="复制临时启动命令"[^>]*>临时启动<\/button>\s*<button v-if="showStatusUpload && provider\.status_upload_supported"/);
  assert.match(providers, /<div v-if="manageMode" class="provider-management-cell">/);
  assert.match(styles, /\.provider-meta\s*\{[^}]*justify-content:\s*center[^}]*flex-wrap:\s*nowrap/s);
  assert.match(styles, /\.provider-list-shell\.managing\s*\{[^}]*--provider-grid-columns:[^}]*184px 92px/s);
  assert.match(styles, /\.provider-editor\s*\{[^}]*position:\s*fixed[^}]*inset:\s*0[^}]*width:\s*min\(640px,[^}]*max-height:\s*min\(820px,[^}]*overflow-y:\s*auto[^}]*scrollbar-width:\s*none[^}]*margin:\s*auto/s);
  assert.match(styles, /\.provider-editor::\-webkit-scrollbar\s*\{\s*display:\s*none;/);
  assert.match(styles, /\.provider-editor form\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/s);
  assert.match(styles, /@media\s*\(max-width:\s*600px\)[\s\S]*\.provider-editor form\s*\{\s*grid-template-columns:\s*1fr;/s);
  assert.match(styles, /@media\s*\(min-width:\s*601px\) and \(max-width:\s*1235px\)[\s\S]*\.provider-row\.managing \.provider-health-cell,[\s\S]*\.provider-row\.managing \.provider-request-cell\s*\{\s*display:\s*none;/s);
  assert.match(styles, /grid-template-areas:\s*"state copy copy" "health health health" "request token token" "config config config"/);
  assert.match(styles, /\.provider-row\.managing\s*\{[^}]*grid-template-areas:\s*"state copy management" "config config config" "health health health"/s);
  const providerTitleStart = providers.indexOf('<div class="provider-title">');
  const providerMetaStart = providers.indexOf('<div class="provider-meta">');
  assert.ok(providerTitleStart >= 0 && providerMetaStart > providerTitleStart);
  assert.doesNotMatch(providers.slice(providerTitleStart, providerMetaStart), /showStatusUpload/);
  assert.match(requests, /class="request-table-header"/);
  assert.match(requests, /class="request-row"/);
  assert.match(requests, />映射模型<\/span>/);
  assert.match(requests, /item\.upstream_model \|\| '—'/);
  assert.ok(providers.indexOf('class="search"') < providers.indexOf('class="time-window-control usage-window-wrap"'));
  assert.ok(requests.indexOf('class="request-search"') < requests.indexOf('request-window-control'));
  assert.match(settings, /UiSelect/);
  assert.match(select, /role="listbox"/);
  assert.match(select, /@keydown="onTriggerKeydown"/);
  assert.match(icon, /viewBox="0 0 24 24"/);
  assert.match(icon, /upload:/);
  assert.match(titlebar, /<BrandIcon :service-id="config\.service_id" :brand-mark="config\.brand_mark"/);
  assert.doesNotMatch(titlebar, /\{\{ config\.brand_mark/);
  assert.doesNotMatch(titlebar, /中转运行中|仅监听本机|class="live-switch"/);
  assert.match(brandIcon, /brand === 'claude'/);
  assert.match(brandIcon, /serviceId\.toLowerCase\(\) === 'claude'/);
  assert.match(brandIcon, /brandMark\.toUpperCase\(\) === 'CC'/);
  assert.match(brandIcon, /viewBox="0 0 16 16"/);
  assert.match(brandIcon, /viewBox="0 0 24 24"/);
  assert.doesNotMatch(providers, /<select\b/);
  assert.doesNotMatch(requests, /<select\b/);
  assert.doesNotMatch(settings, /<select\b/);
  assert.match(providers, /TimeRangeSelect/);
  assert.match(requests, /TimeRangeSelect/);
  assert.doesNotMatch(providers, /TimeRangePopover|time-range-edit/);
  assert.doesNotMatch(requests, /TimeRangePopover|time-range-edit/);
  assert.doesNotMatch(providers, /value: 'custom', label:/);
  assert.doesNotMatch(requests, /value: 'custom', label:/);
  assert.match(requests, /params\.set\('start_at'/);
  assert.match(requests, /params\.set\('end_at'/);
  assert.match(styles, /\.stage\s*\{[^}]*width:\s*100%[^}]*min-height:\s*100vh[^}]*min-height:\s*100dvh/s);
  assert.match(styles, /\.app-window\s*\{[^}]*width:\s*100%[^}]*height:\s*100vh[^}]*height:\s*100dvh/s);
  assert.match(styles, /\.app-window\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\) auto/s);
  assert.doesNotMatch(styles, /\.app-window\s*\{[^}]*width:\s*min\(1440px, 100%\)/s);
  assert.match(styles, /\.workspace\s*\{[^}]*grid-template-columns:\s*minmax\(520px, 1fr\) 340px/s);
  assert.match(styles, /--provider-grid-columns:/);
  assert.match(styles, /--request-columns:/);
  assert.match(styles, /\.request-filters\s*\{[^}]*grid-template-columns:\s*minmax\(220px, 1fr\) 176px 130px minmax\(170px, 220px\)/s);
  assert.match(styles, /scrollbar-gutter:\s*stable/);
  assert.match(styles, /@media\s*\(max-width:\s*860px\)[\s\S]*\.workspace\s*\{\s*grid-template-columns:\s*1fr;/s);
  assert.match(styles, /--control-radius:\s*12px/);
  assert.match(styles, /--control-surface:\s*#ffffff/);
  assert.match(styles, /:root\[data-theme="dark"\][\s\S]*--control-surface:\s*#1b3047/);
  assert.match(styles, /\.ui-select-trigger\s*\{[^}]*background:\s*var\(--control-surface\)[^}]*box-shadow:\s*var\(--control-shadow\)/s);
  assert.match(styles, /\.ui-select-menu\s*\{[^}]*border:\s*1px solid var\(--line-strong\)[^}]*background:\s*var\(--control-surface\)/s);
  assert.match(styles, /\.secondary-button\s*\{[^}]*border-radius:\s*var\(--control-radius\)/s);
  assert.match(styles, /\.secondary-button\s*\{[^}]*background:\s*var\(--control-surface\)/s);
  assert.match(styles, /\.primary-button\s*\{[^}]*border-radius:\s*var\(--control-radius\)/s);
  assert.match(styles, /\.ccs-import-button\s*\{[^}]*display:\s*inline-flex[^}]*gap:\s*7px/s);
  assert.match(styles, /\.power-button\s*\{[^}]*min-height:\s*34px[^}]*padding:\s*5px 13px[^}]*font-size:\s*13px/s);
  assert.match(styles, /\.icon-button\s*\{[^}]*border-radius:\s*var\(--control-radius\)[^}]*background:\s*var\(--control-surface\)/s);
  assert.match(styles, /\.search input\s*\{[^}]*border-radius:\s*var\(--control-radius\)/s);
  assert.match(styles, /\.time-range-edit\s*\{[^}]*border-radius:\s*var\(--control-radius\)/s);
  assert.match(styles, /\.provider-row\s*\{[^}]*border-radius:\s*var\(--panel-radius\)/s);
  assert.match(styles, /\.settings-form\s*\{[^}]*border-radius:\s*var\(--panel-radius\)/s);
  assert.match(styles, /\.settings-form\.update-panel\s*\{[^}]*margin-top:\s*10px/s);
  assert.match(styles, /\.brand-icon\s*\{[^}]*width:\s*24px[^}]*height:\s*24px/s);
  assert.match(styles, /--font-body:\s*13px/);
  assert.match(styles, /--font-meta:\s*12px/);
  assert.match(styles, /\.time-range-heading strong\s*\{[^}]*font-size:\s*16px/s);
  assert.match(styles, /\.time-range-heading span\s*\{[^}]*font-size:\s*var\(--font-meta\)/s);
  assert.match(styles, /\.time-range-fields legend\s*\{[^}]*font-size:\s*var\(--font-meta\)/s);
  assert.match(styles, /\.time-range-fields input\s*\{[^}]*font-size:\s*var\(--font-body\)/s);
  assert.match(styles, /\.request-table-header\s*\{[^}]*font-size:\s*var\(--font-meta\)/s);
  assert.match(styles, /\.request-row\s*\{[^}]*font-size:\s*var\(--font-body\)/s);

  const smallFontSizes = styles.match(/font-size:\s*(?:9|10|11)px/g) ?? [];
  assert.equal(smallFontSizes.length, 2);
  assert.match(styles, /\.view-tab-count\s*\{[^}]*font-size:\s*9px/s);
  assert.match(styles, /\.provider-token-detail-icon\s*\{[^}]*font-size:\s*9px/s);
});

test("Vue request view preserves upstream phase and timeout labels", () => {
  const api = fs.readFileSync(apiPath, "utf8");
  const requests = fs.readFileSync(path.join(root, "components", "RequestsView.vue"), "utf8");
  assert.match(api, /本地中转响应超时，请检查服务是否卡住/);
  assert.match(requests, /connecting: '连接上游'/);
  assert.match(requests, /waiting_first_chunk: '等待首包'/);
  assert.match(requests, /receiving: '接收中'/);
  assert.match(requests, /connection_timeout: '等待上游响应超时'/);
  assert.match(requests, /stream_idle_timeout: '上游长时间无数据'/);
  assert.match(requests, /\['client_disconnected', 'session_superseded'\]/);
  assert.match(requests, /session_superseded: '同会话新请求接管'/);
});

test("Vue Token values use the classic compact formatter and labeled units", () => {
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  const requests = fs.readFileSync(path.join(root, "components", "RequestsView.vue"), "utf8");

  assert.match(providers, /import \{ formatTokenCount \} from '\.\.\/token-format\.js'/);
  assert.match(requests, /import \{ formatTokenCount \} from '\.\.\/token-format\.js'/);
  assert.match(providers, /Token 总量<\/span><strong>\{\{ formatTokenCount\(usage\.total_tokens\) \}\}/);
  assert.match(providers, /输入 Token<\/span><strong>\{\{ formatTokenCount\(usage\.input_tokens\) \}\}/);
  assert.match(providers, /输出 Token<\/span><strong>\{\{ formatTokenCount\(usage\.output_tokens\) \}\}/);
  assert.match(providers, /缓存 Token<\/span><strong>\{\{ formatTokenCount\(usage\.cached_tokens\) \}\}/);
  assert.match(providers, /formatTokenCount\(providerUsage\(provider\.provider_id\)\.total_tokens\)/);
  assert.match(providers, /Token \{\{ formatTokenCount\(usageHistoryTotals\?\.total_tokens/);
  assert.match(requests, /'—' : formatTokenCount\(item\.total_tokens\)/);
  assert.doesNotMatch(providers, /function formatNumber/);
  assert.doesNotMatch(requests, /function formatNumber/);
});

test("Vue provider usage opens request details and positions the popover from its anchor", () => {
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  assert.match(providers, /openUsageHistory\(provider, \$event\.currentTarget\)/);
  assert.match(providers, /\/api\/usage-history\?/);
  assert.match(providers, /getBoundingClientRect\(\)/);
  assert.match(providers, /addEventListener\('resize', positionUsageHistory\)/);
  assert.doesNotMatch(providers, /详细请求请在“请求”页面查看/);
});

test("Vue session routing uses session titles and anchor-based dynamic positioning", () => {
  const requests = fs.readFileSync(path.join(root, "components", "RequestsView.vue"), "utf8");
  assert.match(requests, /ref="routeAnchor"/);
  assert.match(requests, /openSessionRoutes\(\$event\.currentTarget\)/);
  assert.match(requests, /String\(session\.name \|\| '未知会话'\)/);
  assert.match(requests, /value: session\.session_key/);
  assert.match(requests, /getBoundingClientRect\(\)/);
  assert.match(requests, /addEventListener\('resize', positionRoutePopover\)/);
  assert.doesNotMatch(requests, /top: 120px/);
  assert.doesNotMatch(requests, /session\.session_name \|\| session\.thread_name \|\| session\.session_key/);
});

test("Vue active provider requests navigate with a provider filter", () => {
  const app = fs.readFileSync(path.join(root, "App.vue"), "utf8");
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  const requests = fs.readFileSync(path.join(root, "components", "RequestsView.vue"), "utf8");
  assert.match(providers, /@click\.stop=\"\$emit\('navigate-requests', provider\.provider_id\)\"/);
  assert.match(providers, /:disabled=\"manageMode\"[^>]*:title=\"sessionNames\(provider\)\"/);
  assert.match(app, /@navigate-requests=\"navigateToProviderRequests\"/);
  assert.match(app, /:navigation-provider-id=\"requestProviderId\"/);
  assert.match(app, /activeView\.value = 'requests'/);
  assert.match(requests, /navigationProviderId: \{ type: String, default: '' \}/);
  assert.match(requests, /providerFilter\.value = providerId/);
  assert.match(requests, /query\.value = ''/);
  assert.match(requests, /statusFilter\.value = 'all'/);
});

test("Vue temporary launch copies the command and reports the result", () => {
  const app = fs.readFileSync(path.join(root, "App.vue"), "utf8");
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");

  assert.match(providers, /navigator\.clipboard\.writeText/);
  assert.match(providers, /emit\('notify', \{ title: '复制成功'/);
  assert.match(providers, /emit\('notify', \{ title: '复制失败'/);
  assert.match(app, /@notify="showProviderToast"/);
  assert.match(app, /showToast\(message\.title, message\.detail, message\.tone\)/);
});

test("modern toasts open at the top center without moving classic toasts", () => {
  const app = fs.readFileSync(path.join(root, "App.vue"), "utf8");
  const modernStyles = fs.readFileSync(path.join(root, "styles.css"), "utf8");
  const classicSource = fs.readFileSync(path.join(root, "..", "classic", "app.js"), "utf8");
  const classicHtml = fs.readFileSync(path.join(root, "..", "classic", "index.html"), "utf8");
  const classicStyles = fs.readFileSync(path.join(root, "..", "classic", "styles.css"), "utf8");

  assert.doesNotMatch(app, /toast-icon|toast\.detail/);
  assert.match(app, /<strong>\{\{ toast\.title \}\}<\/strong>/);
  assert.match(modernStyles, /\.toast-region\s*\{[^}]*position:\s*fixed[^}]*top:[^}]*left:\s*50%[^}]*transform:\s*translateX\(-50%\)/s);
  assert.match(modernStyles, /\.toast\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 28px[^}]*align-items:\s*center[^}]*transform:\s*translateY\(-8px\)/s);
  assert.match(modernStyles, /\.toast-close\s*\{[^}]*align-self:\s*center/s);
  assert.doesNotMatch(modernStyles, /\.toast-icon/);
  assert.match(modernStyles, /@media\s*\(max-width:\s*600px\)[\s\S]*\.toast-region\s*\{[^}]*top:[^}]*right:\s*12px[^}]*left:\s*12px[^}]*transform:\s*none/s);
  assert.match(classicStyles, /\.toast-region\s*\{[^}]*bottom:\s*24px/s);
  assert.match(classicStyles, /\.toast-icon/);
  assert.match(classicStyles, /\.toast \.toast-icon\s*\{[^}]*display:\s*grid[^}]*place-items:\s*center[^}]*align-self:\s*center[^}]*justify-self:\s*center[^}]*margin-top:\s*0/s);
  assert.match(classicStyles, /\.toast-icon-graphic\s*\{[^}]*width:\s*16px[^}]*height:\s*16px[^}]*display:\s*block[^}]*stroke:\s*currentColor/s);
  assert.doesNotMatch(classicStyles, /\.toast-icon-graphic\s*\{[^}]*transform:/s);
  assert.match(classicSource, /createElementNS\("http:\/\/www\.w3\.org\/2000\/svg", "svg"\)/);
  assert.match(classicSource, /tone === "error" \? "M12 7v6m0 4h\.01" : "m6\.5 12\.5 3\.5 3\.5 7\.5-8"/);
  assert.doesNotMatch(classicSource, /icon\.textContent = tone === "error"/);
  assert.match(classicHtml, /styles\.css\?v=32/);
  assert.match(classicHtml, /app\.js\?v=37/);
  assert.doesNotMatch(classicStyles, /\.toast-region\s*\{[^}]*top:/s);
});

test("Vue recovery details load history and use dynamic popover positioning", () => {
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  assert.match(providers, /v-if="hasRecoveryDetails" ref="recoveryDetailsButton"/);
  assert.match(providers, /api\/recovery-history\?/);
  assert.match(providers, /recoveryNextCursor/);
  assert.match(providers, /item\.provider_id/);
  assert.match(providers, /item\.summary/);
  assert.match(providers, /getBoundingClientRect\(\)/);
  assert.match(providers, /addEventListener\('resize', positionRecoveryDetails\)/);
  assert.match(providers, /removeEventListener\('resize', positionRecoveryDetails\)/);
});

test("Vue provider health details use the clicked anchor and include model history", () => {
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  assert.match(providers, /openHealthDetails\(provider, \$event\.currentTarget\)/);
  assert.match(providers, /healthLastChecked\(selectedHealth\)/);
  assert.match(providers, /healthAvailabilityValue\(selectedHealth\.availability\)/);
  assert.match(providers, /formatLatency\(selectedHealth\.latest_latency\)/);
  assert.doesNotMatch(providers, /healthAvailability\(selectedHealth\)/);
  assert.doesNotMatch(providers, /healthLatency\(selectedHealth\)/);
  assert.match(providers, /healthModels\(selectedHealth\)/);
  assert.match(providers, /historyEntries\(healthFor\(provider\)\?\.history, 16\)/);
  assert.match(providers, /24h \{\{ healthAvailability\(provider\) \}\}/);
  assert.match(providers, /<div class="provider-health-summary">/);
  assert.match(providers, /function isHealthDetailsOpen\(provider\)/);
  assert.match(providers, /const prefixMatches = candidates\.filter/);
  assert.match(providers, /prefixMatches\.length === 1 \? prefixMatches\[0\] : null/);
  assert.match(providers, /function historyEntries\(history, limit\)/);
  assert.match(providers, /while \(items\.length < limit\)/);
  assert.match(providers, /:title="historyTitle\(entry\)"/);
  assert.match(providers, /function historyTitle\(entry\)/);
  assert.match(providers, /handleHistoryPointerMove/);
  assert.match(providers, /handleHistoryPointerLeave/);
  assert.match(providers, /handleHistoryClick/);
  assert.match(providers, /history-detail-popover show/);
  assert.match(providers, /function positionHistoryDetail\(\)/);
  assert.match(providers, /hideHistoryDetail\(\{ force: true \}\)/);
  assert.match(providers, /history: model\.history/);
  assert.match(providers, /historyEntries\(model\.history, 60\)/);
  assert.match(providers, /`\$\{model\.model\} 最近检测历史`/);
  assert.match(providers, /historyMeta\(historyDetail\)/);
  assert.match(providers, /historyReason\(historyDetail\)/);
  assert.match(providers, /healthStateTone\(entry\?\.state\)/);
  assert.match(providers, /\['healthy', 'degraded', 'recovering', 'down'\]\.includes\(state\) \? state : 'unknown'/);
  assert.match(providers, /function formatLatency\(value\)/);
  assert.match(providers, /new Date\(value\)/);
  assert.match(providers, /return formatLatency\(health\?\.latest_latency/);
  assert.match(providers, /health\?\.latest_latency \?\? health\?\.latency_ms/);
  assert.match(providers, /healthFor\(provider\)\?\.last_checked/);
  assert.match(providers, /最近 60 次/);
  assert.match(providers, /positionHealthDetails/);
  assert.match(providers, /window\.addEventListener\('resize', positionHealthDetails\)/);
  assert.match(providers, /window\.removeEventListener\('resize', positionHealthDetails\)/);
  assert.doesNotMatch(providers, /style="top: 96px/);
  const styles = fs.readFileSync(path.join(root, "styles.css"), "utf8");
  assert.doesNotMatch(styles, /provider-health-history\.full \.provider-health-mark \{ flex: 0 0 4px;/);
});

test("provider editor round-trips the model rewrite field", () => {
  const providers = fs.readFileSync(path.join(root, "components", "ProvidersView.vue"), "utf8");
  assert.match(providers, /v-model\.trim="form\.model"/);
  assert.match(providers, /model: detail\.model \|\| ''/);
  assert.match(providers, /model: form\.value\.model/);
});
