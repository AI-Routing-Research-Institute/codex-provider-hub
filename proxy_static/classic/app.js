"use strict";

const CONTROL_HEADER = { "X-Local-Proxy-Control": "1" };
const SESSION_ROUTE_CACHE_MS = 60_000;
const CONTROL_REQUEST_TIMEOUT_MS = 8_000;
const DEFAULT_CC_SWITCH_CODEX_MODEL = "gpt-5.6-sol";
const LOCAL_PROXY_PLACEHOLDER_KEYS = {
  codex: "local-codex-proxy",
  claude: "local-claude-proxy",
};
const CONTROL_BASE = (() => {
  const pathname = window.location?.pathname || "/control/codex/";
  const match = pathname.match(/^\/control\/(codex|claude)(?:\/|$)/);
  return match ? `/control/${match[1]}` : "/control/codex";
})();

function controlUrl(path) {
  return `${CONTROL_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

function buildCcSwitchImportDeeplink({ serviceId, endpoint, homepage, providerName }) {
  const app = serviceId === "claude" ? "claude" : "codex";
  const normalizedEndpoint = String(endpoint || "").replace(/\/+$/, "");
  if (!normalizedEndpoint) throw new Error("本地中转地址不可用");
  const params = new URLSearchParams([
    ["resource", "provider"],
    ["app", app],
    ["name", providerName || "Codex Provider Hub"],
    ["homepage", homepage || normalizedEndpoint],
    ["endpoint", normalizedEndpoint],
    ["apiKey", LOCAL_PROXY_PLACEHOLDER_KEYS[app]],
    ["configFormat", "json"],
  ]);
  if (app === "codex") params.set("model", DEFAULT_CC_SWITCH_CODEX_MODEL);
  return `ccswitch://v1/import?${params.toString()}`;
}

async function fetchControl(path, options = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), CONTROL_REQUEST_TIMEOUT_MS);
  try {
    return await fetch(path, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("本地中转响应超时，请检查服务是否卡住");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

let latestStatus = null;
let latestHealthStatus = null;
let healthStatusError = null;
let pollTimer = null;
let healthPollTimer = null;
let toastTimer = null;
let renderedListSignature = null;
let statusRequestSequence = 0;
let statusRequestActive = false;
let healthRequestSequence = 0;
let controlRequestActive = false;
let healthRequestActive = false;
let monitorManagementLoading = false;
let monitorProviders = [];
let retryFormLoaded = false;
let latestRuntimeSettings = null;
let latestRecoveryHistory = null;
let recoveryHistoryRequestActive = false;
let recoveryHistoryLoadFailed = false;
let recoveryHistoryRetryAfter = 0;
let recoveryDetailsPinned = false;
let recoveryHideTimer = null;
let manageProvidersMode = false;
let draggedProviderId = null;
let healthDetailButton = null;
let historyDetailPinned = false;
let usageHistoryButton = null;
let usageHistoryProvider = null;
let usageHistoryItems = [];
let usageHistoryTotals = null;
let usageHistoryNextCursor = null;
let usageHistoryTotalCount = 0;
let usageHistoryLoading = false;
let usageHistoryRequestSequence = 0;
let usageHistoryWindow = "today";
let usageHistoryError = null;
let activeSessionsButton = null;
let activeSessionsProviderId = null;
let activeSessionsHideTimer = null;
let requestActiveItems = [];
let requestHistoryItems = [];
let requestNextCursor = null;
let requestTotalCount = 0;
let requestLoading = false;
let requestLoadedMore = false;
let requestError = null;
let requestSequence = 0;
let requestSearchTimer = null;
let sessionRouteItems = [];
let sessionRouteSelectedKey = "";
let sessionRouteLoading = false;
let sessionRouteSequence = 0;
let sessionRouteError = null;
let sessionRouteLastReadAt = 0;
let timeRangeTarget = null;
let timeRangeAnchor = null;
let timeRangesRestored = false;
const customTimeRanges = { usage: null, requests: null };
const appliedTimeWindows = { usage: "today", requests: "24h" };
let uiConfig = {
  display_name: "本地中转",
  brand_mark: "LP",
  client_name: "客户端",
  protocol_label: "—",
  proxy_url: "",
  peer_console_label: "切换控制台",
  peer_console_url: "#",
  config_endpoint: controlUrl("/api/config"),
  config_button_label: "复制客户端配置",
  config_location_label: "客户端配置文件",
  config_location_hint: "配置片段的默认位置",
  data_directory: "—",
  config_location: "—",
  restart_config_text: "端口将在退出并重新启动本地中转后生效；届时需要重新复制客户端配置。",
  copy_config_success_title: "配置已复制",
  copy_config_success_detail: "配置片段已复制到剪贴板。",
  shutdown_client_name: "客户端",
  provider_label: "客户端",
  theme_storage_key: "local-proxy-theme",
  features: {
    usage_history: true,
    session_routing: false,
    provider_launch_command: false,
    status_upload: false,
    provider_catalog: false,
  },
};

const providerList = document.querySelector("#provider-list");
const emptyState = document.querySelector("#empty-state");
const searchInput = document.querySelector("#search");
const usageWindow = document.querySelector("#usage-window");
const manageProvidersButton = document.querySelector("#manage-providers");
const providerCatalogActions = document.querySelector("#provider-catalog-actions");
const addProviderButton = document.querySelector("#add-provider");
const importProvidersButton = document.querySelector("#import-providers");
const providerImportMode = document.querySelector("#provider-import-mode");
const providerMetaHeader = document.querySelector("#provider-meta-header");
const healthSource = document.querySelector("#health-source");
const healthSourceDot = document.querySelector("#health-source-dot");
const healthSourceText = document.querySelector("#health-source-text");
const healthRefreshButton = document.querySelector("#health-refresh-button");
const footerMessage = document.querySelector("#footer-message");
const retryForm = document.querySelector("#retry-form");
const runtimeForm = document.querySelector("#runtime-form");
const runtimePortInput = document.querySelector("#runtime-port");
const runtimeDatabaseInput = document.querySelector("#runtime-database-path");
const runtimeHealthUrlInput = document.querySelector("#runtime-health-url");
const runtimeConsoleUiInputs = [...document.querySelectorAll('input[name="runtime-console-ui"]')];
const runtimeShowLaunchCommandInput = document.querySelector("#runtime-show-launch-command");
const runtimeShowStatusUploadInput = document.querySelector("#runtime-show-status-upload");
const themeButton = document.querySelector("#theme-button");
const themeMenu = document.querySelector("#theme-menu");
const recovery = document.querySelector("#recovery");
const recoveryDetailsButton = document.querySelector("#recovery-details-button");
const recoveryPopover = document.querySelector("#recovery-popover");
const recoveryErrorList = document.querySelector("#recovery-error-list");
const recoveryHistoryMeta = document.querySelector("#recovery-history-meta");
const providerHealthPopover = document.querySelector("#provider-health-popover");
const activeSessionsPopover = document.querySelector("#active-sessions-popover");
const usageHistoryPopover = document.querySelector("#usage-history-popover");
const usageHistoryTitle = document.querySelector("#usage-history-title");
const usageHistoryMeta = document.querySelector("#usage-history-meta");
const usageHistorySummary = document.querySelector("#usage-history-summary");
const usageHistoryList = document.querySelector("#usage-history-list");
const usageHistoryMore = document.querySelector("#usage-history-more");
const usageHistoryClose = document.querySelector("#usage-history-close");
const historyDetailPopover = document.querySelector("#history-detail-popover");
const providerEditor = document.querySelector("#provider-editor");
const providerEditorForm = document.querySelector("#provider-editor-form");
const providerEditorTitle = document.querySelector("#provider-editor-title");
const providerEditorMode = document.querySelector("#provider-editor-mode");
const providerEditorId = document.querySelector("#provider-editor-id");
const providerEditorName = document.querySelector("#provider-editor-name");
const providerEditorBaseUrl = document.querySelector("#provider-editor-base-url");
const providerEditorWireApi = document.querySelector("#provider-editor-wire-api");
const providerEditorTransport = document.querySelector("#provider-editor-transport");
const providerEditorApiKey = document.querySelector("#provider-editor-api-key");
const providerEditorClearKey = document.querySelector("#provider-editor-clear-key");
const providerEditorHeaders = document.querySelector("#provider-editor-headers");
const providerEditorQuery = document.querySelector("#provider-editor-query");
const providerEditorError = document.querySelector("#provider-editor-error");
const providerEditorSave = document.querySelector("#provider-editor-save");
const providerEditorDelete = document.querySelector("#provider-editor-delete");
const providerEditorClose = document.querySelector("#provider-editor-close");
const providerEditorCancel = document.querySelector("#provider-editor-cancel");
const requestList = document.querySelector("#request-list");
const requestTableShell = document.querySelector(".request-table-shell");
const requestsEmpty = document.querySelector("#requests-empty");
const requestWindow = document.querySelector("#request-window");
const requestStatus = document.querySelector("#request-status");
const requestProvider = document.querySelector("#request-provider");
const requestQuery = document.querySelector("#request-query");
const sessionRouteSettingsButton = document.querySelector("#session-route-settings");
const sessionRoutePopover = document.querySelector("#session-route-popover");
const sessionRouteSessionSelect = document.querySelector("#session-route-session");
const sessionRouteProviderSelect = document.querySelector("#session-route-provider");
const sessionRouteMeta = document.querySelector("#session-route-meta");
const timeRangePopover = document.querySelector("#time-range-popover");
const timeRangeTitle = document.querySelector("#time-range-title");
const timeRangeHint = document.querySelector("#time-range-hint");
const timeRangeStartDate = document.querySelector("#time-range-start-date");
const timeRangeStartTime = document.querySelector("#time-range-start-time");
const timeRangeEndDate = document.querySelector("#time-range-end-date");
const timeRangeEndTime = document.querySelector("#time-range-end-time");
const timeRangeError = document.querySelector("#time-range-error");
const timeRangeClose = document.querySelector("#time-range-close");
const timeRangeCancel = document.querySelector("#time-range-cancel");
const timeRangeApply = document.querySelector("#time-range-apply");
const statusUploadModal = document.querySelector("#status-upload-modal");
const statusUploadProviderLabel = document.querySelector("#status-upload-provider");
const statusUploadModels = document.querySelector("#status-upload-models");
const statusUploadSsh = document.querySelector("#status-upload-ssh");
const statusUploadError = document.querySelector("#status-upload-error");
const statusUploadSubmit = document.querySelector("#status-upload-submit");
const statusUploadChangeTarget = document.querySelector("#status-upload-change-target");
let statusUploadProvider = null;
let statusUploadPreview = null;
const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
let themeStorageKey = "local-proxy-theme";
const RECOVERY_HISTORY_PAGE_SIZE = 50;
const RECOVERY_HISTORY_RETRY_DELAY_MS = 2000;

function text(selector, value) {
  const element = document.querySelector(selector);
  if (element && value != null) element.textContent = String(value);
}

function closeStatusUploadModal() {
  if (!statusUploadModal) return;
  statusUploadModal.hidden = true;
  statusUploadProvider = null;
  statusUploadPreview = null;
}

function setStatusUploadError(message) {
  if (!statusUploadError) return;
  statusUploadError.hidden = !message;
  statusUploadError.textContent = message || "";
}

function renderUploadModelFields(models) {
  statusUploadModels.replaceChildren();
  for (const model of models) {
    const row = document.createElement("div");
    row.className = "status-upload-model-row";
    const input = document.createElement("input");
    input.type = "text";
    input.value = model;
    input.placeholder = "模型名称";
    input.className = "status-upload-model-input";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button status-upload-remove";
    remove.textContent = "×";
    remove.title = "删除模型";
    remove.addEventListener("click", () => row.remove());
    row.append(input, remove);
    statusUploadModels.append(row);
  }
}

async function openStatusUpload(provider) {
  statusUploadProvider = provider;
  setStatusUploadError("");
  statusUploadProviderLabel.textContent = provider.name;
  statusUploadSsh.hidden = true;
  statusUploadSubmit.disabled = true;
  statusUploadModal.hidden = false;
  try {
    const settingsResponse = await fetch(controlUrl("/api/status-upload/settings"), { cache: "no-store" });
    const settings = settingsResponse.ok ? await settingsResponse.json() : {};
    const previewResponse = await fetch(controlUrl(`/api/providers/${encodeURIComponent(provider.provider_id)}/status-upload/preview`), {
      method: "POST", headers: { ...CONTROL_HEADER, "Content-Type": "application/json" }, body: JSON.stringify({ models: [] }), cache: "no-store",
    });
    const preview = await previewResponse.json().catch(() => ({}));
    if (!previewResponse.ok || preview.supported !== true) throw new Error(preview.detail || preview.reason || "该供应商不支持上传");
    statusUploadPreview = preview;
    renderUploadModelFields(preview.suggested_models || []);
    statusUploadSsh.hidden = settings.initialized === true;
    statusUploadChangeTarget.hidden = settings.initialized !== true;
    document.querySelector("#status-upload-host").value = settings.host || "118.195.178.173";
    document.querySelector("#status-upload-port").value = settings.port || 22;
    document.querySelector("#status-upload-user").value = settings.username || "ubuntu";
    statusUploadSubmit.disabled = false;
  } catch (error) {
    setStatusUploadError(error?.message || "无法读取上传配置");
  }
}

async function submitStatusUpload() {
  if (!statusUploadProvider) return;
  const models = [...statusUploadModels.querySelectorAll("input")].map((input) => input.value.trim()).filter(Boolean);
  if (!models.length) { setStatusUploadError("至少填写一个检测模型"); return; }
  statusUploadSubmit.disabled = true;
  setStatusUploadError("");
  try {
    if (!statusUploadSsh.hidden) {
      const bootstrapResponse = await fetch(controlUrl("/api/status-upload/bootstrap"), {
        method: "POST", headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
        body: JSON.stringify({ host: document.querySelector("#status-upload-host").value.trim(), port: Number(document.querySelector("#status-upload-port").value), username: document.querySelector("#status-upload-user").value.trim(), password: document.querySelector("#status-upload-password").value }),
        cache: "no-store",
      });
      if (!bootstrapResponse.ok) throw new Error((await bootstrapResponse.json().catch(() => ({}))).detail || "SSH 初始化失败");
      statusUploadSsh.hidden = true;
      statusUploadChangeTarget.hidden = false;
    }
    const response = await fetch(controlUrl(`/api/providers/${encodeURIComponent(statusUploadProvider.provider_id)}/status-upload`), {
      method: "POST", headers: { ...CONTROL_HEADER, "Content-Type": "application/json" }, body: JSON.stringify({ models }), cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    const uploadedName = statusUploadProvider.name;
    closeStatusUploadModal();
    showToast("上传成功", `${uploadedName} 已加入服务器检测配置。状态页：${payload.status_url || "服务器 /codex-status/"}`);
  } catch (error) {
    setStatusUploadError(error?.message || "上传失败");
  } finally {
    statusUploadSubmit.disabled = false;
  }
}

function viewStorageKey(serviceId = uiConfig.service_id || "local") {
  return `local-proxy-view-${serviceId || "local"}`;
}

function normalizeViewName(viewName, requestsEnabled = true) {
  const allowed = ["providers", "requests", "settings", "runtime", "monitor"];
  if (!allowed.includes(viewName)) return "providers";
  return viewName === "requests" && !requestsEnabled ? "providers" : viewName;
}

function renderProviderManagementState() {
  if (typeof providerCatalogActions === "undefined" || !providerCatalogActions) return;
  const managing = typeof manageProvidersMode !== "undefined" && manageProvidersMode;
  providerCatalogActions.toggleAttribute(
    "hidden",
    uiConfig.features.provider_catalog !== true || !managing,
  );
  if (typeof providerMetaHeader !== "undefined" && providerMetaHeader) {
    providerMetaHeader.textContent = managing ? "认证与操作" : "认证";
  }
}

function applyUiConfig(config) {
  if (!config || typeof config !== "object") return;
  uiConfig = {
    ...uiConfig,
    ...config,
    features: { ...uiConfig.features, ...(config.features || {}) },
  };
  themeStorageKey = uiConfig.theme_storage_key || "local-proxy-theme";
  document.title = uiConfig.display_name;
  document.querySelector(".app-window").setAttribute("aria-label", `${uiConfig.display_name}控制台`);
  text(".brand-mark", uiConfig.brand_mark);
  text(".brand-copy h1", uiConfig.display_name);
  text(
    ".brand-copy p",
    uiConfig.features.provider_catalog === true
      ? `使用本地供应商目录，切换后无需重启 ${uiConfig.client_name}`
      : `从 CC Switch 读取供应商，切换后无需重启 ${uiConfig.client_name}`,
  );
  const peerLink = document.querySelector(".console-link");
  peerLink.href = uiConfig.peer_console_url || "#";
  peerLink.textContent = uiConfig.peer_console_label || "切换控制台";
  text("#proxy-url", uiConfig.proxy_url || "—");
  text("#wire-api", uiConfig.protocol_label || "—");
  text("#runtime-config-label", uiConfig.config_location_label);
  text("#runtime-config-hint", uiConfig.config_location_hint);
  text("#runtime-data-directory", uiConfig.data_directory);
  text("#runtime-config-location", uiConfig.config_location);
  text("#runtime-port-hint", `只监听 127.0.0.1；修改后需要重启并重新复制 ${uiConfig.client_name} 配置`);
  text("#runtime-restart-notice", uiConfig.restart_config_text);
  text("#copy-config", uiConfig.config_button_label);
  text(
    "#footer-message",
    uiConfig.features.provider_launch_command
      ? "Key 仅在复制临时启动命令时写入剪贴板，不会显示或写入日志"
      : "Key 不会显示，也不会写入页面或日志",
  );
  document.querySelector("#usage-history-popover")?.toggleAttribute(
    "hidden",
    uiConfig.features.usage_history === false,
  );
  document.querySelector('[data-view="requests"]')?.toggleAttribute(
    "hidden",
    uiConfig.features.usage_history === false,
  );
  if (typeof sessionRouteSettingsButton !== "undefined" && sessionRouteSettingsButton) {
    sessionRouteSettingsButton.toggleAttribute(
      "hidden",
      uiConfig.features.session_routing !== true,
    );
  }
  document.querySelector("#runtime-launch-command-row")?.toggleAttribute(
    "hidden",
    uiConfig.features.provider_launch_command !== true,
  );
  document.querySelector("#runtime-status-upload-row")?.toggleAttribute(
    "hidden",
    uiConfig.features.status_upload !== true,
  );
  renderProviderManagementState();
  const refreshButton = document.querySelector("#refresh-button");
  if (uiConfig.features.provider_catalog === true) {
    refreshButton.title = "重新读取本地供应商目录";
    refreshButton.setAttribute("aria-label", "重新读取本地供应商目录");
    text("#runtime-database-hint", "CC Switch 数据库仅作为手动导入来源");
  }
}

function requestsViewEnabled() {
  const button = document.querySelector('.view-tab[data-view="requests"]');
  return Boolean(button && !button.hidden);
}

function persistView(viewName) {
  try {
    localStorage.setItem(viewStorageKey(), viewName);
  } catch (error) {}
}

function restoreView() {
  let savedView = "providers";
  try {
    savedView = localStorage.getItem(viewStorageKey()) || "providers";
  } catch (error) {}
  switchView(savedView, { persist: false });
}

function padTimePart(value) {
  return String(value).padStart(2, "0");
}

function localDateValue(milliseconds) {
  const value = new Date(milliseconds);
  return `${value.getFullYear()}-${padTimePart(value.getMonth() + 1)}-${padTimePart(value.getDate())}`;
}

function localTimeValue(milliseconds) {
  const value = new Date(milliseconds);
  return `${padTimePart(value.getHours())}:${padTimePart(value.getMinutes())}:${padTimePart(value.getSeconds())}`;
}

function parseLocalDateTime(dateValue, timeValue) {
  if (!dateValue || !timeValue) return Number.NaN;
  return new Date(`${dateValue}T${timeValue}`).getTime();
}

function formatCustomRange(range, { includeYear = false } = {}) {
  if (!range) return "";
  const format = (milliseconds) => {
    const value = new Date(milliseconds);
    const date = includeYear
      ? `${value.getFullYear()}-${padTimePart(value.getMonth() + 1)}-${padTimePart(value.getDate())}`
      : `${padTimePart(value.getMonth() + 1)}/${padTimePart(value.getDate())}`;
    return `${date} ${localTimeValue(milliseconds)}`;
  };
  return `${format(range.start)} 至 ${format(range.end)}`;
}

function localDayEnd(milliseconds) {
  const value = new Date(milliseconds);
  value.setHours(23, 59, 59, 999);
  return value.getTime();
}

function defaultTimeWindow(target) {
  return target === "usage" ? "today" : "24h";
}

function validTimeWindow(target, windowName) {
  const allowed = target === "usage"
    ? ["today", "24h", "7d", "30d", "all", "custom"]
    : ["1h", "6h", "24h", "7d", "custom"];
  return allowed.includes(windowName);
}

function validStoredTimeRange(target, range, now = Date.now()) {
  if (!range || !Number.isFinite(range.start) || !Number.isFinite(range.end)) return false;
  if (range.start >= range.end || range.start > now || range.end > localDayEnd(now)) return false;
  if (target !== "requests") return true;
  return range.start >= now - 7 * 24 * 3600_000
    && range.end - range.start <= 7 * 24 * 3600_000;
}

function restoredTimeRangePreference(target, stored, now = Date.now()) {
  const legacy = stored && typeof stored === "object" && !("window" in stored);
  const candidateRange = legacy ? stored : stored?.range;
  const range = validStoredTimeRange(target, candidateRange, now) ? candidateRange : null;
  const candidateWindow = legacy ? "custom" : stored?.window;
  const windowName = validTimeWindow(target, candidateWindow)
    ? candidateWindow
    : defaultTimeWindow(target);
  return windowName === "custom" && !range
    ? { window: defaultTimeWindow(target), range: null }
    : { window: windowName, range };
}

function timeRangeStoragePayload(target) {
  return {
    window: appliedTimeWindows[target],
    range: customTimeRanges[target],
  };
}

function timeRangeSelect(target) {
  return target === "usage" ? usageWindow : requestWindow;
}

function timeRangeStorageKey(target) {
  return `local-proxy-time-range-${uiConfig.service_id || "local"}-${target}`;
}

function persistTimeRange(target) {
  try {
    localStorage.setItem(
      timeRangeStorageKey(target),
      JSON.stringify(timeRangeStoragePayload(target)),
    );
  } catch (error) {}
}

function restoreTimeRanges() {
  if (timeRangesRestored) return;
  timeRangesRestored = true;
  for (const target of ["usage", "requests"]) {
    try {
      const stored = JSON.parse(localStorage.getItem(timeRangeStorageKey(target)) || "null");
      const preference = restoredTimeRangePreference(target, stored);
      customTimeRanges[target] = preference.range;
      appliedTimeWindows[target] = preference.window;
      timeRangeSelect(target).value = preference.window;
    } catch (error) {}
  }
  updateTimeRangeDisplay();
}

function updateTimeRangeDisplay() {
  for (const target of ["usage", "requests"]) {
    document.querySelector(`[data-time-range-edit="${target}"]`)?.classList.toggle(
      "active",
      appliedTimeWindows[target] === "custom",
    );
  }
  const usageLabel = document.querySelector("#usage-range-label");
  const usageRange = appliedTimeWindows.usage === "custom" ? customTimeRanges.usage : null;
  usageLabel.hidden = !usageRange;
  usageLabel.textContent = usageRange ? formatCustomRange(usageRange) : "";
  usageLabel.title = usageRange ? formatCustomRange(usageRange, { includeYear: true }) : "";
}

function timeRangeParams(target) {
  const params = new URLSearchParams();
  const windowName = appliedTimeWindows[target];
  params.set(target === "usage" ? "usage_window" : "window", windowName);
  const range = customTimeRanges[target];
  if (windowName === "custom" && range) {
    params.set("start_at", String(range.start));
    params.set("end_at", String(range.end + 999));
  }
  return params;
}

function positionTimeRangePopover() {
  if (!timeRangeAnchor || timeRangePopover.hidden) return;
  const anchorRect = timeRangeAnchor.getBoundingClientRect();
  const popoverRect = timeRangePopover.getBoundingClientRect();
  const margin = 12;
  const left = Math.min(
    window.innerWidth - popoverRect.width - margin,
    Math.max(margin, anchorRect.left),
  );
  const below = anchorRect.bottom + 8;
  const above = anchorRect.top - popoverRect.height - 8;
  const top = below + popoverRect.height <= window.innerHeight - margin
    ? below
    : Math.max(margin, above);
  timeRangePopover.style.left = `${Math.round(left)}px`;
  timeRangePopover.style.top = `${Math.round(top)}px`;
}

function openTimeRangePopover(target, anchor) {
  timeRangeTarget = target;
  timeRangeAnchor = anchor;
  const now = Math.floor(Date.now() / 1000) * 1000;
  const range = customTimeRanges[target] || { start: now - 3600_000, end: now };
  timeRangeTitle.textContent = target === "usage" ? "自定义 Token 时间" : "自定义请求时间";
  timeRangeHint.textContent = target === "requests"
    ? "精确到秒 · 最多 7 天，仅查询本地已有记录"
    : "精确到秒 · 查询本地已有 Token 记录";
  timeRangeStartDate.value = localDateValue(range.start);
  timeRangeStartTime.value = localTimeValue(range.start);
  timeRangeEndDate.value = localDateValue(range.end);
  timeRangeEndTime.value = localTimeValue(range.end);
  timeRangeError.hidden = true;
  timeRangeError.textContent = "";
  timeRangePopover.hidden = false;
  timeRangePopover.classList.add("show");
  positionTimeRangePopover();
  timeRangeStartDate.focus();
}

function closeTimeRangePopover({ restoreSelection = false } = {}) {
  if (restoreSelection && timeRangeTarget) {
    timeRangeSelect(timeRangeTarget).value = appliedTimeWindows[timeRangeTarget];
  }
  const anchor = timeRangeAnchor;
  timeRangePopover.classList.remove("show");
  timeRangePopover.hidden = true;
  timeRangeTarget = null;
  timeRangeAnchor = null;
  anchor?.focus();
}

function refreshForTimeRange(target) {
  updateTimeRangeDisplay();
  if (target === "usage") {
    closeUsageHistoryPopover();
    renderedListSignature = null;
    void readStatus();
  } else {
    void readRequests({ reset: true });
  }
}

function applyCustomTimeRange() {
  if (!timeRangeTarget) return;
  const start = parseLocalDateTime(timeRangeStartDate.value, timeRangeStartTime.value);
  const end = parseLocalDateTime(timeRangeEndDate.value, timeRangeEndTime.value);
  const now = Date.now();
  let message = "";
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    message = "请完整选择开始日期、时间和结束日期、时间。";
  } else if (start >= end) {
    message = "开始时间必须早于结束时间。";
  } else if (start > now) {
    message = "开始时间不能晚于当前时间。";
  } else if (end > localDayEnd(now)) {
    message = "结束时间不能晚于今天 23:59:59。";
  } else if (
    timeRangeTarget === "requests"
    && (end - start > 7 * 24 * 3600_000 || start < now - 7 * 24 * 3600_000)
  ) {
    message = "请求记录只保留最近 7 天，请在可用范围内选择。";
  }
  if (message) {
    timeRangeError.textContent = message;
    timeRangeError.hidden = false;
    return;
  }
  const target = timeRangeTarget;
  customTimeRanges[target] = {
    start: Math.floor(start / 1000) * 1000,
    end: Math.floor(end / 1000) * 1000,
  };
  appliedTimeWindows[target] = "custom";
  timeRangeSelect(target).value = "custom";
  persistTimeRange(target);
  closeTimeRangePopover();
  refreshForTimeRange(target);
}

async function readUiConfig() {
  try {
    const response = await fetchControl(controlUrl("/api/ui-config"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    applyUiConfig(await response.json());
  } catch {
    applyUiConfig(uiConfig);
  }
  restoreTimeRanges();
}

function themePreference() {
  const value = document.documentElement.dataset.themePreference;
  return ["system", "light", "dark"].includes(value) ? value : "system";
}

function applyTheme(preference, { persist = false } = {}) {
  const resolved = preference === "dark" || (preference === "system" && themeMedia.matches)
    ? "dark"
    : "light";
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.dataset.theme = resolved;
  if (persist) {
    try {
    window.localStorage.setItem(themeStorageKey, preference);
    } catch (error) {}
  }
  const labels = { system: "跟随系统", light: "浅色", dark: "深色" };
  document.querySelector("#theme-icon").textContent = preference === "system"
    ? "◐"
    : resolved === "dark" ? "☾" : "☀";
  themeButton.title = `主题：${labels[preference]}`;
  for (const item of themeMenu.querySelectorAll("[data-theme-value]")) {
    item.setAttribute("aria-checked", String(item.dataset.themeValue === preference));
  }
}

function setThemeMenuOpen(open) {
  themeMenu.hidden = !open;
  themeButton.setAttribute("aria-expanded", String(open));
}

function attemptLabel(value) {
  return Number(value) === -1 ? "无限" : String(value);
}

function positionRecoveryPopover() {
  const buttonRect = recoveryDetailsButton.getBoundingClientRect();
  const routingRect = recovery.closest(".routing-panel").getBoundingClientRect();
  const popoverRect = recoveryPopover.getBoundingClientRect();
  const margin = 12;
  const gap = 12;
  const preferredLeft = routingRect.left - popoverRect.width - gap;
  const fallbackLeft = Math.min(
    window.innerWidth - popoverRect.width - margin,
    Math.max(margin, buttonRect.right - popoverRect.width),
  );
  const left = preferredLeft >= margin ? preferredLeft : fallbackLeft;
  const centeredTop = buttonRect.top + buttonRect.height / 2 - popoverRect.height / 2;
  const top = Math.min(
    window.innerHeight - popoverRect.height - margin,
    Math.max(margin, centeredTop),
  );
  recoveryPopover.style.left = `${Math.round(left)}px`;
  recoveryPopover.style.top = `${Math.round(top)}px`;
}

function showRecoveryDetails({ pinned = false } = {}) {
  if (!recovery.classList.contains("has-details")) return;
  closeActiveSessionsPopover();
  closeUsageHistoryPopover();
  const wasOpen = recoveryPopover.classList.contains("show");
  window.clearTimeout(recoveryHideTimer);
  if (pinned) recoveryDetailsPinned = true;
  recoveryPopover.classList.add("show");
  recoveryDetailsButton.setAttribute("aria-expanded", "true");
  positionRecoveryPopover();
  if (!wasOpen) {
    readRecoveryHistory({ refresh: Boolean(latestRecoveryHistory) });
  }
}

function hideRecoveryDetails({ force = false } = {}) {
  window.clearTimeout(recoveryHideTimer);
  if (recoveryDetailsPinned && !force) return;
  recoveryDetailsPinned = false;
  recoveryPopover.classList.remove("show");
  recoveryDetailsButton.setAttribute("aria-expanded", "false");
}

function scheduleRecoveryDetailsHide() {
  window.clearTimeout(recoveryHideTimer);
  recoveryHideTimer = window.setTimeout(() => hideRecoveryDetails(), 140);
}

function positionActiveSessionsPopover() {
  if (!activeSessionsButton) return;
  const anchorRect = activeSessionsButton.getBoundingClientRect();
  const popoverRect = activeSessionsPopover.getBoundingClientRect();
  const margin = 12;
  const gap = 8;
  const left = Math.min(
    window.innerWidth - popoverRect.width - margin,
    Math.max(margin, anchorRect.left),
  );
  const below = anchorRect.bottom + gap;
  const above = anchorRect.top - popoverRect.height - gap;
  const top = below + popoverRect.height <= window.innerHeight - margin
    ? below
    : Math.max(margin, above);
  activeSessionsPopover.style.left = `${Math.round(left)}px`;
  activeSessionsPopover.style.top = `${Math.round(top)}px`;
}

function activeSessionNames(provider) {
  const sessions = Array.isArray(provider?.active_sessions) ? provider.active_sessions : [];
  return [...new Set(sessions.map((session) => session?.name || "未知会话"))];
}

function renderActiveSessionsPopover(provider) {
  const list = document.createElement("ol");
  list.className = "active-sessions-list";
  for (const sessionName of activeSessionNames(provider)) {
    const item = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "active-session-dot";
    dot.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.textContent = sessionName;
    item.append(dot, name);
    list.append(item);
  }
  activeSessionsPopover.replaceChildren(list);
}

function openActiveSessionsPopover(button, provider) {
  window.clearTimeout(activeSessionsHideTimer);
  hideRecoveryDetails({ force: true });
  closeProviderHealthPopover();
  closeUsageHistoryPopover();
  activeSessionsButton?.setAttribute("aria-expanded", "false");
  activeSessionsButton = button;
  activeSessionsProviderId = provider.provider_id;
  renderActiveSessionsPopover(provider);
  activeSessionsPopover.classList.add("show");
  button.setAttribute("aria-expanded", "true");
  positionActiveSessionsPopover();
}

function closeActiveSessionsPopover() {
  window.clearTimeout(activeSessionsHideTimer);
  activeSessionsPopover.classList.remove("show");
  activeSessionsButton?.setAttribute("aria-expanded", "false");
  activeSessionsButton = null;
  activeSessionsProviderId = null;
}

function scheduleActiveSessionsPopoverClose() {
  window.clearTimeout(activeSessionsHideTimer);
  activeSessionsHideTimer = window.setTimeout(closeActiveSessionsPopover, 140);
}

function formatRetryTime(value) {
  const recordedAt = new Date(Number(value));
  if (Number.isNaN(recordedAt.getTime())) return "刚刚";
  const now = new Date();
  const sameDay = recordedAt.getFullYear() === now.getFullYear()
    && recordedAt.getMonth() === now.getMonth()
    && recordedAt.getDate() === now.getDate();
  return recordedAt.toLocaleString("zh-CN", {
    ...(sameDay ? {} : { month: "2-digit", day: "2-digit" }),
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function recoveryOutcomeLabel(error) {
  return {
    retrying: "已安排重试",
    exhausted: "重试已结束",
    client_disconnected: "客户端已断开",
    passed_through: error?.stage === "after_output" ? "输出后未重放" : "已透传",
  }[error?.outcome] || "已记录";
}

function recoveryMetaText(error, providerName) {
  return [
    `失败 ${formatRetryTime(error.recorded_at)}`,
    error.request_started_at == null
      ? null
      : `开始 ${formatRetryTime(error.request_started_at)}`,
    providerName,
    `第 ${error.attempt} 次请求`,
    recoveryOutcomeLabel(error),
  ].filter(Boolean).join(" · ");
}

function sameRecoveryHistorySnapshot(left, right) {
  const leftLatest = Array.isArray(left?.items) ? left.items[0]?.recorded_at : null;
  const rightLatest = Array.isArray(right?.items) ? right.items[0]?.recorded_at : null;
  return Number(left?.total_count) === Number(right?.total_count)
    && Number(leftLatest || 0) === Number(rightLatest || 0);
}

function recoveryHistoryNeedsDetail(detail, summary) {
  if (!summary || typeof summary !== "object") return false;
  const summaryItems = Array.isArray(summary.items) ? summary.items : [];
  const totalCount = Number(summary.total_count);
  const hasMore = Boolean(summary.truncated)
    || (Number.isFinite(totalCount) && totalCount > summaryItems.length);
  return hasMore && (!detail || !sameRecoveryHistorySnapshot(detail, summary));
}

function shouldRequestRecoveryHistory(
  detail,
  summary,
  {
    popoverOpen = false,
    requestActive = false,
    retryAfter = 0,
    now = Date.now(),
  } = {},
) {
  return popoverOpen
    && !requestActive
    && Number(now) >= Number(retryAfter || 0)
    && recoveryHistoryNeedsDetail(detail, summary);
}

function recoveryHistoryStatusText(
  history,
  { detailLoaded = false, requestActive = false, loadFailed = false } = {},
) {
  const items = Array.isArray(history?.items) ? history.items : [];
  const totalCount = Number.isFinite(Number(history?.total_count))
    ? Number(history.total_count)
    : items.length;
  const windowHours = Number(history?.window_hours) || 24;
  if (!detailLoaded && requestActive) {
    return `近 ${windowHours} 小时 · 正在加载恢复记录`;
  }
  if (!detailLoaded && loadFailed) {
    return `近 ${windowHours} 小时 · 加载失败，正在重试`;
  }
  return history?.truncated
    ? `近 ${windowHours} 小时 · 显示最新 ${items.length}/${totalCount} 条`
    : `近 ${windowHours} 小时 · ${totalCount} 条`;
}

function recoveryHistoryEntryKey(entry) {
  return [
    entry?.recorded_at,
    entry?.request_id,
    entry?.provider_id,
    entry?.attempt,
    entry?.outcome,
  ].map((value) => String(value ?? "")).join("\u0000");
}

function mergeRecoveryHistoryPages(current, page) {
  const currentItems = Array.isArray(current?.items) ? current.items : [];
  const pageItems = Array.isArray(page?.items) ? page.items : [];
  const seen = new Set();
  const items = [];
  for (const entry of [...currentItems, ...pageItems]) {
    const key = recoveryHistoryEntryKey(entry);
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(entry);
  }
  const totalCount = Number(page?.total_count);
  return {
    ...page,
    total_count: Number.isFinite(totalCount) ? totalCount : items.length,
    truncated: Boolean(page?.next_cursor) || totalCount > items.length,
    items,
  };
}

function refreshRecoveryHistoryPages(current, page) {
  if (!current) return page;
  const pageItems = Array.isArray(page?.items) ? page.items : [];
  const currentItems = Array.isArray(current?.items) ? current.items : [];
  const seen = new Set();
  const items = [];
  for (const entry of [...pageItems, ...currentItems]) {
    const key = recoveryHistoryEntryKey(entry);
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(entry);
  }
  const totalCount = Number(page?.total_count);
  const nextCursor = Object.prototype.hasOwnProperty.call(current, "next_cursor")
    ? current.next_cursor
    : page?.next_cursor || null;
  return {
    ...page,
    total_count: Number.isFinite(totalCount) ? totalCount : items.length,
    next_cursor: nextCursor,
    truncated: Boolean(nextCursor) || totalCount > items.length,
    items,
  };
}

function recoveryHistoryForDisplay(detail, summary) {
  if (!detail) return summary;
  if (!summary || sameRecoveryHistorySnapshot(detail, summary)) return detail;
  const detailItems = Array.isArray(detail.items) ? detail.items : [];
  const summaryItems = Array.isArray(summary.items) ? summary.items : [];
  const seen = new Set();
  const items = [];
  for (const entry of [...summaryItems, ...detailItems]) {
    const key = recoveryHistoryEntryKey(entry);
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(entry);
  }
  const summaryTotal = Number(summary.total_count);
  const detailTotal = Number(detail.total_count);
  const totalCount = Number.isFinite(summaryTotal)
    ? summaryTotal
    : Number.isFinite(detailTotal) ? detailTotal : items.length;
  return {
    ...detail,
    window_hours: Number(summary.window_hours) || Number(detail.window_hours) || 24,
    total_count: totalCount,
    truncated: totalCount > items.length,
    items,
  };
}

async function readRecoveryHistory({ loadMore = false, refresh = false } = {}) {
  const summary = latestStatus?.retry?.history;
  if (recoveryHistoryRequestActive) return;
  if (loadMore && !latestRecoveryHistory?.next_cursor) return;
  if (!loadMore && !recoveryHistoryNeedsDetail(latestRecoveryHistory, summary)) return;
  recoveryHistoryRequestActive = true;
  if (!loadMore) {
    recoveryHistoryLoadFailed = false;
    recoveryHistoryRetryAfter = 0;
    if (latestStatus?.retry) renderRecoveryErrors(latestStatus.retry);
  }
  let pageLoaded = false;
  try {
    const params = new URLSearchParams({ limit: String(RECOVERY_HISTORY_PAGE_SIZE) });
    if (loadMore) params.set("cursor", latestRecoveryHistory.next_cursor);
    const response = await fetchControl(`${controlUrl("/api/recovery-history")}?${params}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = await response.json();
    latestRecoveryHistory = loadMore
      ? mergeRecoveryHistoryPages(latestRecoveryHistory, page)
      : refresh
        ? refreshRecoveryHistoryPages(latestRecoveryHistory, page)
        : page;
    pageLoaded = true;
  } catch {
    if (!loadMore) {
      recoveryHistoryLoadFailed = true;
      recoveryHistoryRetryAfter = Date.now() + RECOVERY_HISTORY_RETRY_DELAY_MS;
    }
  } finally {
    recoveryHistoryRequestActive = false;
    if (latestStatus?.retry && (pageLoaded || !loadMore)) {
      renderRecoveryErrors(latestStatus.retry, { appended: pageLoaded && loadMore });
    }
    if (recoveryPopover.classList.contains("show")) positionRecoveryPopover();
  }
}

function renderRecoveryErrors(retry, { appended = false } = {}) {
  const historySummary = retry.history && typeof retry.history === "object"
    ? retry.history
    : null;
  const history = recoveryHistoryForDisplay(latestRecoveryHistory, historySummary);
  const historyItems = Array.isArray(history?.items)
    ? history.items
    : Array.isArray(retry.recent_errors) ? retry.recent_errors : [];
  const hasDetails = historyItems.length > 0;
  recovery.classList.toggle("has-details", hasDetails);
  recoveryDetailsButton.hidden = !hasDetails;
  if (!hasDetails) hideRecoveryDetails({ force: true });
  recoveryHistoryMeta.textContent = recoveryHistoryStatusText(history, {
    detailLoaded: Boolean(latestRecoveryHistory),
    requestActive: recoveryHistoryRequestActive,
    loadFailed: recoveryHistoryLoadFailed,
  });
  const previousScrollTop = recoveryErrorList.scrollTop;
  const previousScrollHeight = recoveryErrorList.scrollHeight;
  recoveryErrorList.replaceChildren();
  for (const error of historyItems) {
    const providerName = latestStatus?.providers?.find(
      (provider) => provider.provider_id === error.provider_id,
    )?.name;
    const item = document.createElement("li");
    const meta = document.createElement("span");
    meta.className = "recovery-error-meta";
    meta.textContent = recoveryMetaText(error, providerName);
    const summary = document.createElement("span");
    summary.className = "recovery-error-summary";
    summary.textContent = error.summary || "上游临时错误";
    item.append(meta, summary);
    recoveryErrorList.append(item);
  }
  if (appended) {
    recoveryErrorList.scrollTop = previousScrollTop;
  } else if (previousScrollTop > 0) {
    recoveryErrorList.scrollTop = previousScrollTop
      + Math.max(0, recoveryErrorList.scrollHeight - previousScrollHeight);
  }
  if (shouldRequestRecoveryHistory(latestRecoveryHistory, historySummary, {
    popoverOpen: recoveryPopover.classList.contains("show"),
    requestActive: recoveryHistoryRequestActive,
    retryAfter: recoveryHistoryRetryAfter,
  })) {
    void readRecoveryHistory({ refresh: Boolean(latestRecoveryHistory) });
  }
}

function populateRetryForm(retry) {
  document.querySelector("#retry-enabled").checked = retry.enabled !== false;
  document.querySelector("#max-attempts").value = String(retry.max_attempts ?? 4);
  document.querySelector("#delay-seconds").value = String(retry.delay_seconds ?? 1);
  document.querySelector("#retry-strategy").value = retry.strategy || "exponential";
  document.querySelector("#max-delay-seconds").value = String(retry.max_delay_seconds ?? 30);
  document.querySelector("#circuit-threshold").value = String(retry.circuit_failure_threshold ?? 3);
  document.querySelector("#circuit-cooldown").value = String(retry.circuit_cooldown_seconds ?? 30);
  retryFormLoaded = true;
  renderSettingsSummary();
}

function retryPayloadFromForm() {
  return {
    enabled: document.querySelector("#retry-enabled").checked,
    max_attempts: Number(document.querySelector("#max-attempts").value),
    delay_seconds: Number(document.querySelector("#delay-seconds").value),
    strategy: document.querySelector("#retry-strategy").value,
    max_delay_seconds: Number(document.querySelector("#max-delay-seconds").value),
    circuit_failure_threshold: Number(document.querySelector("#circuit-threshold").value),
    circuit_cooldown_seconds: Number(document.querySelector("#circuit-cooldown").value),
  };
}

function renderSettingsSummary() {
  const policy = retryPayloadFromForm();
  const state = document.querySelector("#settings-state");
  state.lastChild.textContent = policy.enabled ? "自动恢复已启用" : "自动恢复已关闭";
  document.querySelector("#settings-summary").textContent = policy.enabled
    ? `${policy.max_attempts === -1 ? "无限重试" : `最多尝试 ${policy.max_attempts} 次`}，首次等待 ${policy.delay_seconds} 秒`
    : `临时错误将直接返回 ${uiConfig.client_name}`;
}

function formatRetryKind(kind) {
  const value = String(kind || "");
  if (value.startsWith("http_")) return `HTTP ${value.slice(5)}`;
  return {
    rate_limited: "HTTP 429",
    model_capacity: "模型容量已满",
    connection: "连接上游失败",
    connection_timeout: "等待上游响应超时",
    stream_start: "响应开始前断流",
    stream_idle_timeout: "上游长时间无数据",
    stream_interrupted: "输出后响应流中断",
    upstream_error: "上游请求失败",
  }[value] || "上游临时错误";
}

function formatRetryDelay(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  return `${Number(seconds.toFixed(1))} 秒后重试`;
}

function formatRecoverySummary(recoveryItem, { waiting = false } = {}) {
  const parts = [formatRetryKind(recoveryItem?.kind)];
  if (waiting) {
    const delay = formatRetryDelay(recoveryItem?.delay_seconds);
    if (delay) parts.push(delay);
  }
  return parts.join(" · ");
}

function runtimePayloadFromForm() {
  const healthStatusUrl = runtimeHealthUrlInput.value.trim();
  const selectedConsoleUi = runtimeConsoleUiInputs.find((input) => input.checked)?.value;
  return {
    port: Number(runtimePortInput.value),
    database_path: runtimeDatabaseInput.value.trim(),
    health_status_url: healthStatusUrl || null,
    console_ui: selectedConsoleUi || "modern",
    ...(uiConfig.features.provider_launch_command === true
      ? { show_provider_launch_command: runtimeShowLaunchCommandInput.checked }
      : {}),
    ...(uiConfig.features.status_upload === true
      ? { show_status_upload: runtimeShowStatusUploadInput.checked }
      : {}),
  };
}

function renderRuntimeSettingsSummary() {
  const summary = document.querySelector("#runtime-settings-summary");
  const configuredPort = Number(runtimePortInput.value);
  const activePort = Number(latestRuntimeSettings?.active_port);
  if (latestRuntimeSettings && configuredPort !== activePort) {
    summary.textContent = `当前使用 ${activePort}，重启后切换到 ${configuredPort}`;
  } else {
    summary.textContent = "数据源和检测地址保存后即时生效";
  }
}

function renderRuntimeSettings(settings) {
  latestRuntimeSettings = settings;
  runtimePortInput.value = String(settings.configured_port ?? settings.active_port ?? "");
  runtimeDatabaseInput.value = settings.database_path || "~/.cc-switch/cc-switch.db";
  runtimeHealthUrlInput.value = settings.health_status_url || "";
  runtimeConsoleUiInputs.forEach((input) => {
    input.checked = input.value === (settings.console_ui || "modern");
  });
  runtimeShowLaunchCommandInput.checked = settings.show_provider_launch_command !== false;
  runtimeShowStatusUploadInput.checked = settings.show_status_upload !== false;
  document.querySelector("#database-path").textContent = (
    uiConfig.features.provider_catalog === true
      ? settings.provider_catalog
      : runtimeDatabaseInput.value
  ) || runtimeDatabaseInput.value;
  document.querySelector("#runtime-data-directory").textContent = settings.data_directory || uiConfig.data_directory;
  document.querySelector("#runtime-config-location").textContent = settings.codex_config_file || settings.claude_config_file || uiConfig.config_location;
  const restartRequired = settings.restart_required === true;
  document.querySelector("#runtime-restart-notice").hidden = !restartRequired;
  const state = document.querySelector("#runtime-settings-state");
  state.classList.toggle("pending", restartRequired);
  state.lastChild.textContent = restartRequired ? "等待重启" : "配置已生效";
  renderRuntimeSettingsSummary();
  if (latestStatus) renderProviderList();
}

async function responseDetail(response, fallback) {
  try {
    const payload = await response.json();
    return typeof payload?.detail === "string" && payload.detail ? payload.detail : fallback;
  } catch (error) {
    return fallback;
  }
}

async function readRuntimeSettings({ quiet = false } = {}) {
  try {
    const response = await fetchControl(controlUrl("/api/runtime-settings"), { cache: "no-store" });
    if (!response.ok) throw new Error(await responseDetail(response, `HTTP ${response.status}`));
    renderRuntimeSettings(await response.json());
  } catch (error) {
    if (!quiet) showToast("读取设置失败", error?.message || "无法读取运行设置。", "error");
  }
}

function requestRecordKey(item) {
  return [
    item?.started_at,
    item?.finished_at,
    item?.provider_id,
    item?.session_key,
    item?.model,
    item?.reasoning_effort,
    item?.duration_ms,
    item?.outcome,
  ].join("|");
}

function formatRequestDuration(milliseconds) {
  const value = Math.max(0, Number(milliseconds || 0));
  if (value < 1000) return `${Math.round(value)}ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`;
  const minutes = Math.floor(value / 60000);
  const seconds = Math.floor((value % 60000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function requestReasoningEffortLabel(value) {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return "—";
  const labels = {
    none: "关闭",
    minimal: "极低",
    low: "低",
    medium: "中",
    high: "高",
    xhigh: "极高",
  };
  return labels[raw.toLowerCase()] || raw.slice(0, 40);
}

function requestResultLabel(item) {
  if (item?.state === "running") {
    return item.outcome === "retrying"
      ? `第 ${Number(item.retry_count || 0) + 1} 次尝试`
      : ({
        connecting: "连接上游",
        waiting_first_chunk: "等待首包",
        receiving: "接收中",
      }[item.phase] || (item.outcome === "receiving" ? "接收中" : "处理中"));
  }
  if (item?.succeeded === true) {
    return String(Number(item.status_code || 200));
  }
  if (item?.error_kind === "client_disconnected") return "客户端取消";
  if (item?.error_summary) return item.error_summary;
  const statusCode = Number(item?.status_code || 0);
  return statusCode > 0 ? `HTTP ${statusCode} 失败` : "请求失败";
}

function requestActualProviderLabel(item) {
  const provider = item?.provider_name || item?.provider_id || "未知供应商";
  return item?.state === "running" ? `正在使用 · ${provider}` : provider;
}

function positionSessionRoutePopover() {
  if (!sessionRouteSettingsButton || sessionRoutePopover.hidden) return;
  const anchorRect = sessionRouteSettingsButton.getBoundingClientRect();
  const popoverRect = sessionRoutePopover.getBoundingClientRect();
  const margin = 12;
  const left = Math.min(
    window.innerWidth - popoverRect.width - margin,
    Math.max(margin, anchorRect.right - popoverRect.width),
  );
  const below = anchorRect.bottom + 8;
  const above = anchorRect.top - popoverRect.height - 8;
  const top = below + popoverRect.height <= window.innerHeight - margin
    ? below
    : Math.max(margin, above);
  sessionRoutePopover.style.left = `${Math.round(left)}px`;
  sessionRoutePopover.style.top = `${Math.round(top)}px`;
}

function closeSessionRoutePopover() {
  sessionRoutePopover.hidden = true;
  sessionRoutePopover.classList.remove("show");
  sessionRouteSettingsButton?.setAttribute("aria-expanded", "false");
}

function selectedSessionRouteItem() {
  return sessionRouteItems.find((item) => item.session_key === sessionRouteSelectedKey) || null;
}

function sessionRouteOptionLabel(item, duplicateRank = null, duplicateCount = 0) {
  const name = item?.name || "未知会话";
  const suffix = duplicateCount > 1 ? ` · 会话 ${duplicateRank}/${duplicateCount}` : "";
  return `${item?.active ? "● " : ""}${name}${suffix} · ${formatRetryTime(item?.updated_at)}`;
}

function renderSessionRouteProviders() {
  if (!sessionRouteProviderSelect) return;
  const session = selectedSessionRouteItem();
  sessionRouteProviderSelect.replaceChildren();
  if (!session) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "请先选择会话";
    sessionRouteProviderSelect.append(empty);
    sessionRouteProviderSelect.disabled = true;
    return;
  }
  const current = currentProvider(latestStatus);
  const following = document.createElement("option");
  following.value = "";
  following.textContent = `跟随当前 · ${current?.name || "未选择"}`;
  sessionRouteProviderSelect.append(following);
  for (const provider of latestStatus?.providers || []) {
    const option = document.createElement("option");
    option.value = provider.provider_id;
    option.textContent = `固定 · ${provider.name}`;
    sessionRouteProviderSelect.append(option);
  }
  sessionRouteProviderSelect.value = session.route_provider_id || "";
  sessionRouteProviderSelect.disabled = false;
}

function renderSessionRouteSettings() {
  if (!sessionRouteSessionSelect) return;
  const previous = sessionRouteSelectedKey;
  sessionRouteSessionSelect.replaceChildren();
  if (sessionRouteItems.length === 0) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = sessionRouteLoading ? "正在读取会话…" : "近 7 天没有会话";
    sessionRouteSessionSelect.append(empty);
    sessionRouteSelectedKey = "";
  } else {
    if (!sessionRouteItems.some((item) => item.session_key === previous)) {
      sessionRouteSelectedKey = sessionRouteItems[0].session_key;
    }
    const duplicateGroups = new Map();
    for (const item of sessionRouteItems) {
      const key = String(item.name || "未知会话").trim().toLocaleLowerCase();
      const group = duplicateGroups.get(key) || [];
      group.push(item);
      duplicateGroups.set(key, group);
    }
    const duplicateRanks = new Map();
    for (const group of duplicateGroups.values()) {
      if (group.length < 2) continue;
      [...group]
        .sort((left, right) => String(left.session_key).localeCompare(String(right.session_key)))
        .forEach((item, index) => duplicateRanks.set(item.session_key, [index + 1, group.length]));
    }
    for (const item of sessionRouteItems) {
      const option = document.createElement("option");
      option.value = item.session_key;
      const [duplicateRank, duplicateCount] = duplicateRanks.get(item.session_key) || [null, 0];
      option.textContent = sessionRouteOptionLabel(item, duplicateRank, duplicateCount);
      option.title = item.name || "未知会话";
      sessionRouteSessionSelect.append(option);
    }
    sessionRouteSessionSelect.value = sessionRouteSelectedKey;
  }
  renderSessionRouteProviders();
  const initialLoading = sessionRouteLoading && sessionRouteItems.length === 0;
  sessionRouteMeta.textContent = sessionRouteError
    || (initialLoading
      ? "正在读取最近 7 天会话…"
      : `最近 7 天 ${sessionRouteItems.length} 个会话，活跃会话优先`);
}

async function readSessionRoutes({ quiet = false, force = false } = {}) {
  if (!sessionRouteSettingsButton || sessionRouteSettingsButton.hidden) return;
  if (sessionRouteLoading) return;
  if (
    !force
    && sessionRouteLastReadAt > 0
    && Date.now() - sessionRouteLastReadAt < SESSION_ROUTE_CACHE_MS
  ) return;
  const sequence = ++sessionRouteSequence;
  const hadItems = sessionRouteItems.length > 0;
  sessionRouteLoading = true;
  if (!hadItems) sessionRouteError = null;
  renderSessionRouteSettings();
  try {
    const response = await fetchControl(controlUrl("/api/sessions"), { cache: "no-store" });
    if (!response.ok) throw new Error(await responseDetail(response, `HTTP ${response.status}`));
    const payload = await response.json();
    if (sequence !== sessionRouteSequence) return;
    sessionRouteItems = Array.isArray(payload.items) ? payload.items : [];
    sessionRouteError = null;
    renderSessionRouteSettings();
  } catch (error) {
    if (sequence !== sessionRouteSequence) return;
    if (!hadItems) sessionRouteItems = [];
    sessionRouteError = hadItems && quiet
      ? null
      : error?.message || "无法读取最近 7 天会话";
    renderSessionRouteSettings();
    if (!quiet) showToast("会话列表读取失败", sessionRouteError, "error");
  } finally {
    if (sequence === sessionRouteSequence) {
      sessionRouteLastReadAt = Date.now();
      sessionRouteLoading = false;
      renderSessionRouteSettings();
    }
  }
}

function openSessionRoutePopover() {
  hideRecoveryDetails({ force: true });
  closeProviderHealthPopover();
  closeActiveSessionsPopover();
  closeUsageHistoryPopover();
  sessionRoutePopover.hidden = false;
  sessionRoutePopover.classList.add("show");
  sessionRouteSettingsButton.setAttribute("aria-expanded", "true");
  positionSessionRoutePopover();
  void readSessionRoutes();
}

async function updateSelectedSessionRoute() {
  const session = selectedSessionRouteItem();
  if (!session) return;
  const previous = session.route_provider_id || null;
  const selected = sessionRouteProviderSelect.value || null;
  sessionRouteProviderSelect.disabled = true;
  try {
    const response = await fetch(
      controlUrl(`/api/session-routes/${encodeURIComponent(session.session_key)}`),
      {
        method: "POST",
        headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
        body: JSON.stringify({ provider_id: selected }),
        cache: "no-store",
      },
    );
    if (!response.ok) throw new Error(await responseDetail(response, `HTTP ${response.status}`));
    session.route_provider_id = selected;
    renderSessionRouteSettings();
    showToast(
      "会话路由已保存",
      selected
        ? `后续请求固定使用 ${latestStatus?.providers?.find((item) => item.provider_id === selected)?.name || selected}。`
        : "后续请求恢复跟随当前供应商。",
    );
  } catch (error) {
    session.route_provider_id = previous;
    renderSessionRouteSettings();
    showToast("会话路由保存失败", error?.message || "本地中转没有接受这次修改。", "error");
  }
}

function populateRequestProviders() {
  if (!requestProvider || !latestStatus?.providers) return;
  const selected = requestProvider.value;
  const signature = latestStatus.providers
    .map((provider) => `${provider.provider_id}:${provider.name}`)
    .join("|");
  if (requestProvider.dataset.signature === signature) return;
  requestProvider.dataset.signature = signature;
  requestProvider.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部供应商";
  requestProvider.append(all);
  for (const provider of latestStatus.providers) {
    const option = document.createElement("option");
    option.value = provider.provider_id;
    option.textContent = provider.name;
    requestProvider.append(option);
  }
  requestProvider.value = latestStatus.providers.some(
    (provider) => provider.provider_id === selected,
  ) ? selected : "";
}

function createRequestProviderCell(item) {
  const cell = document.createElement("div");
  cell.className = "request-provider-cell";
  const actual = document.createElement("span");
  actual.className = "request-provider-actual";
  actual.textContent = requestActualProviderLabel(item);
  actual.title = actual.textContent;
  cell.append(actual);
  return cell;
}

function renderRequests() {
  if (!requestList) return;
  const previousScrollTop = requestTableShell?.scrollTop || 0;
  const combined = [...requestActiveItems, ...requestHistoryItems];
  requestList.replaceChildren();
  for (const item of combined) {
    const state = item.state === "running"
      ? "running"
      : item.error_kind === "client_disconnected" ? "cancelled"
      : item.succeeded === true ? "succeeded" : "failed";
    const row = document.createElement("div");
    row.className = `request-row ${state}`;

    const status = document.createElement("span");
    status.className = "request-state";
    const dot = document.createElement("span");
    dot.className = "request-state-dot";
    dot.setAttribute("aria-hidden", "true");
    const statusText = document.createElement("span");
    statusText.textContent = state === "running"
      ? "运行中"
      : state === "succeeded" ? "成功" : state === "cancelled" ? "取消" : "失败";
    status.append(dot, statusText);

    const startedAt = document.createElement("time");
    startedAt.className = "request-time";
    startedAt.dateTime = new Date(Number(item.started_at)).toISOString();
    startedAt.textContent = formatRetryTime(item.started_at);

    const session = document.createElement("strong");
    session.className = "request-session";
    session.textContent = item.session_name || "未知会话";
    session.title = session.textContent;

    const route = createRequestProviderCell(item);

    const model = document.createElement("span");
    model.className = "request-model";
    model.textContent = item.model || "unknown";
    model.title = model.textContent;

    const reasoning = document.createElement("span");
    reasoning.className = "request-reasoning";
    reasoning.textContent = requestReasoningEffortLabel(item.reasoning_effort);
    reasoning.title = item.reasoning_effort
      ? `推理强度：${reasoning.textContent}`
      : "未指定推理强度";

    const duration = document.createElement("span");
    duration.className = "request-duration";
    duration.textContent = state === "running"
      ? formatRequestDuration(Date.now() - Number(item.started_at))
      : item.duration_ms == null ? "—" : formatRequestDuration(item.duration_ms);

    const token = document.createElement("span");
    token.className = "request-token";
    token.textContent = item.usage_source == null && Number(item.total_tokens || 0) === 0
      ? "—"
      : formatTokenCount(item.total_tokens);
    token.title = item.usage_source === "estimated" ? "估算 Token" : "Token";

    const result = document.createElement("span");
    result.className = "request-result";
    result.textContent = requestResultLabel(item);
    result.title = state === "succeeded"
      ? `HTTP ${Number(item.status_code || 200)}`
      : state === "cancelled"
        ? "客户端在响应完成前结束连接"
        : result.textContent;
    row.append(status, startedAt, session, route, model, reasoning, duration, token, result);
    requestList.append(row);
  }
  if (requestTableShell) requestTableShell.scrollTop = previousScrollTop;
  requestsEmpty.hidden = combined.length > 0;
  requestsEmpty.textContent = requestError
    ? requestError
    : requestLoading ? "正在读取请求记录…" : "当前筛选范围内没有请求记录";
  const count = document.querySelector("#request-tab-count");
  const activeCount = requestActiveItems.length;
  const globalActiveCount = Number(latestStatus?.active_requests || 0);
  count.hidden = globalActiveCount === 0;
  count.textContent = String(globalActiveCount);
  const requestRange = appliedTimeWindows.requests === "custom"
    ? formatCustomRange(customTimeRanges.requests)
    : null;
  document.querySelector("#requests-meta").textContent = requestError
    ? requestError
    : `${requestTotalCount} 条记录 · 运行中 ${activeCount} 条 · ${requestRange || "最多保留 7 天"}`;
}

async function readRequests({ reset = false, loadMore = false, refresh = false, quiet = false } = {}) {
  if (!requestList || (requestLoading && !reset)) return;
  if (loadMore && !requestNextCursor) return;
  if (reset) {
    requestActiveItems = [];
    requestHistoryItems = [];
    requestNextCursor = null;
    requestTotalCount = 0;
    requestLoadedMore = false;
    requestError = null;
  }
  requestLoading = true;
  if (!quiet) renderRequests();
  const sequence = ++requestSequence;
  const params = timeRangeParams("requests");
  params.set("status", requestStatus.value);
  if (requestProvider.value) params.set("provider_id", requestProvider.value);
  if (requestQuery.value.trim()) params.set("query", requestQuery.value.trim());
  if (loadMore && requestNextCursor) params.set("cursor", requestNextCursor);
  try {
    const response = await fetchControl(`${controlUrl("/api/requests")}?${params}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await responseDetail(response, `HTTP ${response.status}`));
    const payload = await response.json();
    if (sequence !== requestSequence) return;
    requestActiveItems = Array.isArray(payload.active) ? payload.active : [];
    const incoming = Array.isArray(payload.items) ? payload.items : [];
    if (loadMore) {
      const seen = new Set(requestHistoryItems.map(requestRecordKey));
      requestHistoryItems.push(...incoming.filter((item) => !seen.has(requestRecordKey(item))));
      requestNextCursor = payload.next_cursor || null;
      requestLoadedMore = true;
    } else if (refresh && requestHistoryItems.length > 0) {
      const merged = [...incoming, ...requestHistoryItems];
      const seen = new Set();
      requestHistoryItems = merged.filter((item) => {
        const key = requestRecordKey(item);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).sort((left, right) => Number(right.finished_at) - Number(left.finished_at));
      if (!requestLoadedMore) requestNextCursor = payload.next_cursor || null;
    } else {
      requestHistoryItems = incoming;
      requestNextCursor = payload.next_cursor || null;
    }
    if (!loadMore) requestTotalCount = Number(payload.total_count || 0);
    requestError = null;
  } catch (error) {
    if (sequence !== requestSequence) return;
    requestError = error?.message || "无法读取请求记录";
    if (!quiet) showToast("请求记录读取失败", requestError, "error");
  } finally {
    if (sequence === requestSequence) {
      requestLoading = false;
      renderRequests();
    }
  }
}

function openAllRequests() {
  populateRequestProviders();
  requestProvider.value = "";
  requestStatus.value = "all";
  requestQuery.value = "";
  switchView("requests");
  void readRequests({ reset: true });
}

function switchView(viewName, { persist = true } = {}) {
  viewName = normalizeViewName(viewName, requestsViewEnabled());
  closeUsageHistoryPopover();
  if (viewName !== "requests") closeSessionRoutePopover();
  for (const button of document.querySelectorAll(".view-tab")) {
    const active = button.dataset.view === viewName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  document.querySelector("#providers-view").hidden = viewName !== "providers";
  document.querySelector("#requests-view").hidden = viewName !== "requests";
  document.querySelector("#settings-view").hidden = viewName !== "settings";
  document.querySelector("#runtime-view").hidden = viewName !== "runtime";
  document.querySelector("#monitor-view").hidden = viewName !== "monitor";
  if (viewName === "runtime" && latestRuntimeSettings === null) {
    readRuntimeSettings();
  }
  if (viewName === "requests" && requestHistoryItems.length === 0 && !requestLoading) {
    readRequests({ reset: true });
  }
  if (viewName === "monitor" && !monitorManagementLoading) void loadMonitorManagement();
  if (persist) persistView(viewName);
}

function monitorStateLabel(state) {
  return ({
    healthy: "正常",
    recovering: "恢复中",
    degraded: "波动",
    down: "不可用",
    probing: "检测中",
    unknown: "等待检测",
  })[state] || "等待检测";
}

function monitorProbeStateLabel(state) {
  return ({ queued: "排队中", running: "检测中" })[state] || "";
}

function monitorProviderId(provider) {
  return String(provider?.id || provider?.provider_id || "").trim();
}

function statusUrlForWindow(statusUrl, windowName = "24h") {
  if (!statusUrl) return null;
  try {
    const url = new URL(statusUrl, window.location.origin);
    url.searchParams.set("window", windowName);
    return url.toString();
  } catch (error) {
    return null;
  }
}

function alignMonitorProviders(configuredProviders, statusProviders) {
  const configured = Array.isArray(configuredProviders) ? configuredProviders : [];
  const statuses = Array.isArray(statusProviders) ? statusProviders : [];
  const configuredById = new Map(
    configured.map((provider) => [monitorProviderId(provider), provider]).filter(([id]) => id),
  );
  const statusById = new Map(
    statuses.map((provider) => [monitorProviderId(provider), provider]).filter(([id]) => id),
  );
  const ordered = [];
  for (const status of statuses) {
    const id = monitorProviderId(status);
    const provider = configuredById.get(id);
    if (!provider) continue;
    ordered.push({
      ...provider,
      id: provider.id || id,
      state: status.state || provider.state,
      health_status: status,
    });
  }
  for (const provider of configured) {
    if (!statusById.has(monitorProviderId(provider))) ordered.push(provider);
  }
  return ordered;
}

function movableMonitorIndexes() {
  return monitorProviders
    .map((provider, index) => provider.state === "down" ? index : -1)
    .filter((index) => index >= 0);
}

function canMoveMonitorProvider(index, offset) {
  const indexes = movableMonitorIndexes();
  const position = indexes.indexOf(index);
  return position >= 0 && position + offset >= 0 && position + offset < indexes.length;
}

function renderMonitorManagement(statusPayload = null) {
  const list = document.querySelector("#monitor-list");
  const empty = document.querySelector("#monitor-empty");
  if (!list) return;
  const statusById = new Map(
    (statusPayload?.providers || []).map((item) => [monitorProviderId(item), item]),
  );
  list.replaceChildren();
  empty.hidden = monitorProviders.length !== 0;
  for (const [index, provider] of monitorProviders.entries()) {
    const status = provider.health_status || statusById.get(monitorProviderId(provider)) || {};
    const row = document.createElement("div");
    row.className = "monitor-row";
    const main = document.createElement("div");
    main.className = "monitor-main";
    const name = document.createElement("strong");
    name.className = "monitor-name";
    name.textContent = provider.name || provider.id;
    const endpoint = document.createElement("code");
    endpoint.className = "monitor-endpoint";
    endpoint.textContent = provider.endpoint || provider.base_url || "—";
    const models = document.createElement("span");
    models.className = "monitor-models";
    models.textContent = `模型：${(provider.display_models || provider.models || []).join("、") || "—"}`;
    const state = document.createElement("span");
    const probeState = monitorProbeStateLabel(status.manual_probe?.status);
    state.className = `monitor-state ${probeState ? "probing" : status.state || "unknown"}`;
    state.textContent = probeState || `${monitorStateLabel(status.state)}${status.last_checked ? ` · ${status.last_checked}` : ""}`;
    main.append(name, endpoint, models, state);
    const controls = document.createElement("div");
    controls.className = "monitor-controls";
    for (const [label, offset] of [["上移", -1], ["下移", 1]]) {
      const button = document.createElement("button"); button.type = "button"; button.textContent = label;
      button.disabled = !canMoveMonitorProvider(index, offset);
      button.addEventListener("click", () => void moveMonitorProvider(index, offset)); controls.append(button);
    }
    const probe = document.createElement("button"); probe.type = "button"; probe.textContent = "立即检测";
    probe.disabled = Boolean(probeState);
    probe.addEventListener("click", () => void requestMonitorProbe(provider, probe)); controls.append(probe);
    if (provider.probe_mode === "automatic") {
      const disable = document.createElement("button"); disable.type = "button"; disable.textContent = "停止自动监控";
      disable.addEventListener("click", () => void disableAutomaticMonitor(provider)); controls.append(disable);
    }
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "删除";
    remove.addEventListener("click", () => void deleteMonitorProvider(provider)); controls.append(remove);
    row.append(main, controls); list.append(row);
  }
  text("#monitor-meta", `服务器监控供应商 ${monitorProviders.length} 个`);
}

async function monitorManagementRequest(payload) {
  const response = await fetchControl(controlUrl("/api/status-management"), {
    method: "POST", headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify(payload), cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function loadMonitorManagement() {
  monitorManagementLoading = true;
  const error = document.querySelector("#monitor-error"); error.hidden = true;
  try {
    const result = await monitorManagementRequest({ action: "list" });
    monitorProviders = alignMonitorProviders(
      result.providers,
      latestHealthStatus?.providers,
    );
    renderMonitorManagement(latestHealthStatus);
  } catch (err) {
    error.hidden = false; error.textContent = err?.message || "无法读取服务器监控配置";
  } finally { monitorManagementLoading = false; }
}

async function moveMonitorProvider(index, offset) {
  if (!canMoveMonitorProvider(index, offset)) return;
  const next = monitorProviders.slice();
  const indexes = movableMonitorIndexes();
  const target = indexes[indexes.indexOf(index) + offset];
  [next[index], next[target]] = [next[target], next[index]];
  try {
    await monitorManagementRequest({ action: "order", provider_ids: next.map(monitorProviderId) });
    await loadMonitorManagement();
    showToast("排序已保存", "不可用供应商之间的顺序已更新。");
  } catch (err) { showToast("排序失败", err?.message || "无法保存服务器监控顺序。", "error"); }
}

async function deleteMonitorProvider(provider) {
  if (!window.confirm(`确定删除服务器监控供应商“${provider.name || provider.id}”吗？`)) return;
  try {
    await monitorManagementRequest({ action: "delete", provider_id: provider.id });
    await loadMonitorManagement(); await readHealthStatus({ quiet: true });
    showToast("已删除", `${provider.name || provider.id} 已从服务器监控中移除。`);
  } catch (err) { showToast("删除失败", err?.message || "无法删除服务器监控供应商。", "error"); }
}

async function requestMonitorProbe(provider, button) {
  button.disabled = true;
  try {
    const response = await fetch(controlUrl(`/api/status-management/providers/${encodeURIComponent(provider.id)}/probe`), {
      method: "POST",
      headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
      body: JSON.stringify({ models: provider.display_models || provider.models || [] }),
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    showToast("检测已提交", "服务器正在检测该供应商，稍后刷新即可查看结果。");
    window.setTimeout(() => void readHealthStatus({ quiet: true }), 2500);
  } catch (err) { showToast("检测提交失败", err?.message || "无法提交立即检测。", "error"); }
  finally { button.disabled = false; }
}

function escapeText(value) {
  return String(value ?? "");
}

function showToast(title, message, tone = "success") {
  window.clearTimeout(toastTimer);
  const toast = document.createElement("div");
  toast.className = `toast${tone === "error" ? " error" : ""}`;
  toast.setAttribute("role", tone === "error" ? "alert" : "status");
  const icon = document.createElement("span");
  icon.className = "toast-icon";
  icon.textContent = tone === "error" ? "!" : "✓";
  const copy = document.createElement("div");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const detail = document.createElement("span");
  detail.textContent = message;
  copy.append(heading, detail);
  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.setAttribute("aria-label", "关闭提示");
  close.textContent = "×";
  close.addEventListener("click", () => toast.remove());
  toast.append(icon, copy, close);
  const region = document.querySelector("#toast-region");
  region.replaceChildren(toast);
  window.requestAnimationFrame(() => toast.classList.add("show"));
  toastTimer = window.setTimeout(() => toast.remove(), tone === "error" ? 6000 : 4000);
}

function currentProvider(status) {
  return status.providers.find((provider) => provider.current) || null;
}

function formatTokenCount(value) {
  const count = Number(value || 0);
  if (!Number.isFinite(count)) return "0";
  const absoluteCount = Math.abs(count);
  const units = [
    { threshold: 1_000_000_000, suffix: "B" },
    { threshold: 1_000_000, suffix: "M" },
    { threshold: 1_000, suffix: "K" },
  ];
  const unit = units.find(({ threshold }) => absoluteCount >= threshold);
  if (!unit) return String(Math.round(count));
  const compact = Number((count / unit.threshold).toFixed(2));
  return `${compact}${unit.suffix}`;
}

function usageWindowLabel(value) {
  return {
    today: "今日",
    "24h": "近 24 小时",
    "7d": "近 7 天",
    "30d": "近 30 天",
    all: "全部",
  }[value] || "今日";
}

function positionUsageHistoryPopover() {
  if (!usageHistoryButton || !usageHistoryPopover.classList.contains("show")) return;
  const anchor = usageHistoryButton.getBoundingClientRect();
  const rect = usageHistoryPopover.getBoundingClientRect();
  const margin = 12;
  const left = Math.min(
    window.innerWidth - rect.width - margin,
    Math.max(margin, anchor.right - rect.width),
  );
  const preferredTop = anchor.bottom + 8;
  const top = preferredTop + rect.height <= window.innerHeight - margin
    ? preferredTop
    : Math.max(margin, anchor.top - rect.height - 8);
  usageHistoryPopover.style.left = `${Math.round(left)}px`;
  usageHistoryPopover.style.top = `${Math.round(top)}px`;
}

function usageSourceLabel(item) {
  return item?.usage_source === "estimated" ? "估算" : "上游";
}

function usageStatusLabel(item) {
  if (item?.succeeded === true) return "成功";
  const statusCode = Number(item?.status_code || 0);
  if (statusCode >= 200 && statusCode < 300) return "流级失败";
  return statusCode > 0 ? `HTTP ${statusCode} 失败` : "失败";
}

function usageHistoryMetaText(windowValue, totalCount, usage) {
  const failedCount = Number(usage?.failed_requests || 0);
  return [
    usageWindowLabel(windowValue),
    `${Number(totalCount || 0)} 条`,
    failedCount > 0 ? `${failedCount} 条失败` : null,
  ].filter(Boolean).join(" · ");
}

function renderUsageHistoryPopover() {
  if (!usageHistoryProvider) return;
  const usage = usageHistoryTotals || providerUsage(usageHistoryProvider.provider_id);
  usageHistoryTitle.textContent = `${usageHistoryProvider.name} · 请求记录`;
  usageHistoryMeta.textContent = usageHistoryMetaText(
    usageHistoryWindow,
    usageHistoryTotalCount,
    usage,
  );
  usageHistorySummary.replaceChildren();
  for (const [label, value, tone, suffix] of [
    ["总计", usage.total_tokens, "total", " Token"],
    ["成功", usage.successful_tokens, "success", ""],
    ["失败/中断", usage.failed_tokens, "failed", ""],
  ]) {
    const summaryItem = document.createElement("span");
    summaryItem.className = `usage-history-summary-item ${tone}`;
    summaryItem.textContent = `${label} ${formatTokenCount(value)}${suffix}`;
    usageHistorySummary.append(summaryItem);
  }

  const previousScrollTop = usageHistoryList.scrollTop;
  usageHistoryList.replaceChildren();
  if (usageHistoryItems.length === 0) {
    const empty = document.createElement("li");
    empty.className = "usage-history-empty";
    empty.textContent = usageHistoryError
      ? usageHistoryError
      : usageHistoryLoading ? "正在读取请求记录…" : "当前时间范围内没有请求记录";
    usageHistoryList.append(empty);
  } else {
    for (const item of usageHistoryItems) {
      const succeeded = item.succeeded === true;
      const row = document.createElement("li");
      row.className = `usage-history-item ${succeeded ? "succeeded" : "failed"}`;
      const top = document.createElement("div");
      top.className = "usage-history-item-top";
      const recordedAt = document.createElement("time");
      recordedAt.className = "usage-history-time";
      recordedAt.dateTime = new Date(Number(item.recorded_at)).toISOString();
      recordedAt.textContent = formatRetryTime(item.recorded_at);
      const model = document.createElement("strong");
      model.className = "usage-history-model";
      model.textContent = item.model || "unknown";
      model.title = item.model || "unknown";
      const total = document.createElement("strong");
      total.className = "usage-history-total";
      total.textContent = `${formatTokenCount(item.total_tokens)} Token`;
      top.append(recordedAt, model, total);

      const detail = document.createElement("div");
      detail.className = "usage-history-detail";
      for (const text of [
        `输入 ${formatTokenCount(item.input_tokens)}`,
        `输出 ${formatTokenCount(item.output_tokens)}`,
        Number(item.cached_tokens || 0) > 0
          ? `缓存 ${formatTokenCount(item.cached_tokens)}`
          : null,
        Number(item.reasoning_tokens || 0) > 0
          ? `推理 ${formatTokenCount(item.reasoning_tokens)}`
          : null,
      ].filter(Boolean)) {
        detail.append(Object.assign(document.createElement("span"), { textContent: text }));
      }
      const status = document.createElement("span");
      status.className = `usage-status-badge ${succeeded ? "succeeded" : "failed"}`;
      status.textContent = usageStatusLabel(item);
      detail.append(status);
      const source = document.createElement("span");
      source.className = `usage-source-badge${item.usage_source === "estimated" ? " estimated" : ""}`;
      source.textContent = usageSourceLabel(item);
      if (item.usage_source === "estimated" && item.estimate_method) {
        source.title = `本地估算 · ${item.estimate_method}`;
      }
      detail.append(source);
      row.append(top, detail);
      usageHistoryList.append(row);
    }
  }
  if (previousScrollTop > 0) usageHistoryList.scrollTop = previousScrollTop;
  usageHistoryMore.hidden = usageHistoryItems.length === 0
    || (!usageHistoryNextCursor && !usageHistoryLoading);
  usageHistoryMore.disabled = usageHistoryLoading;
  usageHistoryMore.textContent = usageHistoryLoading ? "正在加载…" : "加载更早记录";
}

async function readUsageHistory({ reset = false } = {}) {
  if (!usageHistoryProvider || !usageHistoryPopover.classList.contains("show")) return;
  if (usageHistoryLoading && !reset) return;
  if (!reset && !usageHistoryNextCursor) return;
  const providerId = usageHistoryProvider.provider_id;
  const selectedWindow = usageHistoryWindow;
  const cursor = reset ? null : usageHistoryNextCursor;
  const requestSequence = ++usageHistoryRequestSequence;
  usageHistoryLoading = true;
  usageHistoryError = null;
  if (reset) {
    usageHistoryItems = [];
    usageHistoryTotals = null;
    usageHistoryNextCursor = null;
    usageHistoryTotalCount = 0;
  }
  renderUsageHistoryPopover();
  try {
    const params = timeRangeParams("usage");
    params.set("provider_id", providerId);
    if (cursor) params.set("cursor", cursor);
    const response = await fetchControl(`${controlUrl("/api/usage-history")}?${params}`, { cache: "no-store" });
    if (!response.ok) throw new Error(await responseDetail(response, "无法读取请求记录"));
    const result = await response.json();
    if (
      requestSequence !== usageHistoryRequestSequence
      || usageHistoryProvider?.provider_id !== providerId
      || usageHistoryWindow !== selectedWindow
    ) return;
    usageHistoryItems = reset
      ? Array.isArray(result.items) ? result.items : []
      : [...usageHistoryItems, ...(Array.isArray(result.items) ? result.items : [])];
    usageHistoryNextCursor = result.next_cursor || null;
    usageHistoryTotalCount = Number(result.total_count || 0);
    usageHistoryTotals = result.total && typeof result.total === "object"
      ? result.total
      : null;
  } catch (error) {
    if (requestSequence === usageHistoryRequestSequence) {
      usageHistoryError = error?.message || "无法读取请求记录";
    }
  } finally {
    if (requestSequence === usageHistoryRequestSequence) {
      usageHistoryLoading = false;
      renderUsageHistoryPopover();
      positionUsageHistoryPopover();
    }
  }
}

function closeUsageHistoryPopover() {
  if (usageHistoryButton) usageHistoryButton.setAttribute("aria-expanded", "false");
  usageHistoryButton = null;
  usageHistoryProvider = null;
  usageHistoryItems = [];
  usageHistoryTotals = null;
  usageHistoryNextCursor = null;
  usageHistoryTotalCount = 0;
  usageHistoryLoading = false;
  usageHistoryError = null;
  usageHistoryRequestSequence += 1;
  usageHistoryPopover.classList.remove("show");
}

function openUsageHistoryPopover(button, provider) {
  closeActiveSessionsPopover();
  if (
    usageHistoryProvider?.provider_id === provider.provider_id
    && usageHistoryPopover.classList.contains("show")
  ) {
    closeUsageHistoryPopover();
    return;
  }
  closeProviderHealthPopover();
  hideRecoveryDetails({ force: true });
  hideHistoryDetail({ force: true });
  if (usageHistoryButton) usageHistoryButton.setAttribute("aria-expanded", "false");
  usageHistoryButton = button;
  usageHistoryProvider = provider;
  usageHistoryWindow = appliedTimeWindows.usage;
  usageHistoryItems = [];
  usageHistoryTotals = null;
  usageHistoryNextCursor = null;
  usageHistoryTotalCount = 0;
  usageHistoryError = null;
  button.setAttribute("aria-expanded", "true");
  usageHistoryPopover.classList.add("show");
  renderUsageHistoryPopover();
  positionUsageHistoryPopover();
  void readUsageHistory({ reset: true });
}

function providerUsage(providerId) {
  return latestStatus?.usage?.by_provider?.[providerId] || {
    request_count: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cached_tokens: 0,
    estimated_requests: 0,
    successful_requests: 0,
    failed_requests: 0,
    successful_tokens: 0,
    failed_tokens: 0,
    last_request_at: null,
    last_success_at: null,
  };
}

function healthStatusUrl() {
  return typeof latestStatus?.health_status_url === "string"
    ? latestStatus.health_status_url.trim()
    : "";
}

function normalizeProviderEndpoint(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : "https://" + raw);
    const path = parsed.pathname.replace(/\/{2,}/g, "/").replace(/\/+$/, "");
    return parsed.host.toLocaleLowerCase() + (path === "/" ? "" : path);
  } catch (error) {
    return raw
      .toLocaleLowerCase()
      .replace(/^[a-z][a-z0-9+.-]*:\/\//i, "")
      .replace(/[?#].*$/, "")
      .replace(/\/+$/, "");
  }
}

function providerEndpointsMatch(left, right) {
  const normalizedLeft = normalizeProviderEndpoint(left);
  const normalizedRight = normalizeProviderEndpoint(right);
  if (!normalizedLeft || !normalizedRight) return false;
  if (normalizedLeft === normalizedRight) return true;
  const shorter = normalizedLeft.length < normalizedRight.length
    ? normalizedLeft
    : normalizedRight;
  const longer = shorter === normalizedLeft ? normalizedRight : normalizedLeft;
  return longer.startsWith(shorter) && longer.charAt(shorter.length) === "/";
}

function healthStatusForProvider(provider) {
  if (!Array.isArray(latestHealthStatus?.providers)) return null;
  const endpoint = normalizeProviderEndpoint(provider.endpoint);
  const exactMatch = latestHealthStatus.providers.find(
    (candidate) => normalizeProviderEndpoint(candidate?.base_url) === endpoint,
  );
  if (exactMatch) return exactMatch;
  const prefixMatches = latestHealthStatus.providers.filter(
    (candidate) => providerEndpointsMatch(candidate?.base_url, endpoint),
  );
  return prefixMatches.length === 1 ? prefixMatches[0] : null;
}

function normalizeHealthState(value) {
  return ["healthy", "degraded", "recovering", "down"].includes(value)
    ? value
    : "unknown";
}

function healthStateLabel(value) {
  return {
    healthy: "可用",
    degraded: "有波动",
    recovering: "恢复中",
    down: "暂不可用",
    unknown: "等待检测",
  }[normalizeHealthState(value)];
}

function healthStateTone(value) {
  return {
    healthy: "healthy",
    degraded: "warning",
    recovering: "warning",
    down: "down",
    unknown: "unknown",
  }[normalizeHealthState(value)];
}

function formatAvailability(value) {
  const availability = Number(value);
  if (!Number.isFinite(availability)) return "—";
  return Number(availability.toFixed(2)) + "%";
}

function formatLatency(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  if (milliseconds < 1000) return Math.round(milliseconds) + " ms";
  return Number((milliseconds / 1000).toFixed(1)) + " 秒";
}

function formatHealthTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatRelativeHealthTime(value) {
  const recordedAt = new Date(value);
  if (Number.isNaN(recordedAt.getTime())) return "更新时间未知";
  const seconds = Math.max(0, Math.round((Date.now() - recordedAt.getTime()) / 1000));
  if (seconds < 10) return "刚刚更新";
  if (seconds < 60) return seconds + " 秒前更新";
  if (seconds < 3600) return Math.floor(seconds / 60) + " 分钟前更新";
  if (seconds < 86400) return Math.floor(seconds / 3600) + " 小时前更新";
  return Math.floor(seconds / 86400) + " 天前更新";
}

function historyEntries(history, limit) {
  const items = Array.isArray(history) ? history.slice(0, limit).reverse() : [];
  while (items.length < limit) items.unshift(null);
  return items;
}

function createHistoryBars(history, limit, label) {
  const bars = document.createElement("span");
  bars.className = limit > 16 ? "provider-health-history full" : "provider-health-history";
  const entries = historyEntries(history, limit);
  const states = entries.map((item) => normalizeHealthState(item?.state));
  const healthyCount = states.filter((state) => state === "healthy").length;
  const failedCount = states.filter((state) => state === "down").length;
  bars.setAttribute(
    "aria-label",
    label + "：" + healthyCount + " 次可用，" + failedCount + " 次不可用",
  );
  for (const [index, state] of states.entries()) {
    const mark = document.createElement("span");
    mark.className = "provider-health-mark " + healthStateTone(state);
    mark.setAttribute("aria-hidden", "true");
    mark.dataset.historyIndex = String(index);
    bars.append(mark);
  }
  bars.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || historyDetailPinned) return;
    const rect = bars.getBoundingClientRect();
    const index = Math.max(0, Math.min(
      entries.length - 1,
      Math.floor(((event.clientX - rect.left) / rect.width) * entries.length),
    ));
    showHistoryDetail(entries[index], label, bars.children[index]);
  });
  bars.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse" && !historyDetailPinned) hideHistoryDetail();
  });
  bars.addEventListener("click", (event) => {
    event.stopPropagation();
    const rect = bars.getBoundingClientRect();
    const index = Math.max(0, Math.min(
      entries.length - 1,
      Math.floor(((event.clientX - rect.left) / rect.width) * entries.length),
    ));
    if (!entries[index]) return;
    historyDetailPinned = true;
    showHistoryDetail(entries[index], label, bars.children[index]);
  });
  return bars;
}

function showHistoryDetail(entry, label, anchor) {
  if (!entry || !anchor) {
    if (!historyDetailPinned) hideHistoryDetail();
    return;
  }
  const state = normalizeHealthState(entry.state);
  const time = entry.recorded_at || entry.finished_at;
  const latency = entry.latency_ms ?? entry.latency;
  const title = document.createElement("strong");
  title.textContent = label;
  const meta = document.createElement("span");
  meta.textContent = [formatHealthTime(time), healthStateLabel(state),
    latency == null ? "" : formatLatency(latency)].filter(Boolean).join(" · ");
  historyDetailPopover.replaceChildren(title, meta);
  if (entry.error_summary || entry.error_code) {
    const reason = document.createElement("span");
    reason.textContent = "原因：" + (entry.error_summary || entry.error_code);
    historyDetailPopover.append(reason);
  }
  historyDetailPopover.classList.add("show");
  const rect = anchor.getBoundingClientRect();
  const popoverRect = historyDetailPopover.getBoundingClientRect();
  const margin = 10;
  const left = Math.min(
    window.innerWidth - popoverRect.width - margin,
    Math.max(margin, rect.left + rect.width / 2 - popoverRect.width / 2),
  );
  const above = rect.top - popoverRect.height - 8;
  const top = above >= margin ? above : rect.bottom + 8;
  historyDetailPopover.style.left = `${Math.round(left)}px`;
  historyDetailPopover.style.top = `${Math.round(top)}px`;
}

function hideHistoryDetail({ force = false } = {}) {
  if (historyDetailPinned && !force) return;
  historyDetailPinned = false;
  historyDetailPopover.classList.remove("show");
}

function healthModels(providerHealth) {
  const automaticModels = Array.isArray(providerHealth?.models)
    ? providerHealth.models
    : [];
  const automaticByName = new Map(
    automaticModels.map((model) => [String(model?.model || ""), model]),
  );
  const manualHistory = providerHealth?.manual_history &&
    typeof providerHealth.manual_history === "object"
    ? providerHealth.manual_history
    : {};
  const configuredNames = Array.isArray(providerHealth?.display_models)
    ? providerHealth.display_models
    : [];
  const names = [...new Set([
    ...configuredNames,
    ...automaticModels.map((model) => model?.model),
    ...Object.keys(manualHistory),
  ].filter(Boolean))];
  return names.map((name) => {
    const automatic = automaticByName.get(name);
    if (automatic) {
      return {
        model: name,
        state: normalizeHealthState(automatic.state),
        availability: automatic.availability,
        latestLatency: automatic.latest_latency,
        history: automatic.history,
        source: "automatic",
      };
    }
    const manualItems = Array.isArray(manualHistory[name]) ? manualHistory[name] : [];
    const latest = manualItems[0] || null;
    return {
      model: name,
      state: latest ? latest.success ? "healthy" : "down" : "unknown",
      availability: null,
      latestLatency: latest?.latency_ms,
      history: manualItems.map((item) => ({
        ...item,
        state: item?.success ? "healthy" : "down",
      })),
      source: "manual",
    };
  });
}

function createProviderHealthSummary(providerHealth) {
  const summary = document.createElement("span");
  summary.className = "provider-health-summary";
  const top = document.createElement("span");
  top.className = "provider-health-top";
  const label = document.createElement("span");
  label.className = "provider-health-label";
  const dot = document.createElement("span");
  dot.className = "provider-health-dot";
  dot.setAttribute("aria-hidden", "true");
  const stateText = document.createElement("span");
  const latency = document.createElement("span");
  latency.className = "provider-health-latency";
  const meta = document.createElement("span");
  meta.className = "provider-health-meta";
  const modelName = document.createElement("span");
  const availability = document.createElement("span");

  if (providerHealth) {
    const state = normalizeHealthState(providerHealth.state);
    dot.classList.add(healthStateTone(state));
    stateText.textContent = healthStateLabel(state);
    latency.textContent = formatLatency(providerHealth.latest_latency);
    modelName.textContent = providerHealth.display_models?.[0] ||
      providerHealth.models?.[0]?.model ||
      "未配置模型";
    availability.textContent = "24h " + formatAvailability(providerHealth.availability);
    summary.append(top, meta, createHistoryBars(providerHealth.history, 16, "最近 16 次检测"));
  } else {
    dot.classList.add("unknown");
    stateText.textContent = latestHealthStatus ? "未纳入检测" : "检测数据暂不可用";
    latency.textContent = "";
    modelName.textContent = latestHealthStatus
      ? "未匹配服务器供应商"
      : "等待服务器检测数据";
    availability.textContent = "";
    summary.append(top, meta, createHistoryBars([], 16, "暂无检测历史"));
  }

  label.append(dot, stateText);
  top.append(label, latency);
  meta.append(modelName, availability);
  return summary;
}

function createProviderHealthDetail(provider, providerHealth) {
  const detail = document.createElement("div");
  detail.className = "provider-health-detail";
  detail.id = "provider-health-detail-" + provider.provider_id;

  const heading = document.createElement("div");
  heading.className = "provider-health-detail-head";
  const title = document.createElement("strong");
  title.textContent = "服务器检测详情";
  const checked = document.createElement("span");
  checked.textContent = "最后探测 " + formatHealthTime(providerHealth.last_checked);
  const headingMeta = document.createElement("span");
  headingMeta.className = "provider-health-detail-meta";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "provider-health-close";
  close.textContent = "×";
  close.title = "关闭检测详情";
  close.setAttribute("aria-label", "关闭检测详情");
  close.addEventListener("click", closeProviderHealthPopover);
  headingMeta.append(checked, close);
  heading.append(title, headingMeta);

  const models = healthModels(providerHealth);
  const metrics = document.createElement("div");
  metrics.className = "provider-health-metrics";
  for (const [label, value] of [
    ["24 小时可用率", formatAvailability(providerHealth.availability)],
    ["最近延迟", formatLatency(providerHealth.latest_latency)],
    ["检测模型", models.length + " 个"],
  ]) {
    const metric = document.createElement("span");
    const metricLabel = document.createElement("span");
    metricLabel.textContent = label;
    const metricValue = document.createElement("strong");
    metricValue.textContent = value;
    metric.append(metricLabel, metricValue);
    metrics.append(metric);
  }

  detail.append(heading, metrics);
  for (const model of models) {
    const row = document.createElement("div");
    row.className = "provider-health-model";
    const modelCopy = document.createElement("span");
    modelCopy.className = "provider-health-model-copy";
    const modelTitle = document.createElement("span");
    modelTitle.className = "provider-health-model-title";
    const modelDot = document.createElement("span");
    modelDot.className = "provider-health-dot " + healthStateTone(model.state);
    modelDot.setAttribute("aria-hidden", "true");
    const modelName = document.createElement("strong");
    modelName.textContent = model.model;
    const modelSummary = document.createElement("span");
    modelSummary.textContent = model.source === "manual"
      ? "手动检测" + healthStateLabel(model.state) + " · " + formatLatency(model.latestLatency)
      : healthStateLabel(model.state) + " · " + formatAvailability(model.availability);
    modelTitle.append(modelDot, modelName);
    modelCopy.append(modelTitle, modelSummary);

    const history = document.createElement("span");
    history.className = "provider-health-model-history";
    const historyLabel = document.createElement("span");
    historyLabel.className = "provider-health-history-label";
    const historyName = document.createElement("span");
    historyName.textContent = model.source === "manual" ? "最近点击检测" : "最近 60 次";
    const historyDirection = document.createElement("span");
    historyDirection.textContent = "较早 → 最近";
    historyLabel.append(historyName, historyDirection);
    history.append(
      historyLabel,
      createHistoryBars(model.history, 60, model.model + " 最近检测历史"),
    );
    row.append(modelCopy, history);
    detail.append(row);
  }
  return detail;
}

function positionProviderHealthPopover() {
  if (!healthDetailButton || !providerHealthPopover.classList.contains("show")) return;
  const anchor = healthDetailButton.getBoundingClientRect();
  const rect = providerHealthPopover.getBoundingClientRect();
  const margin = 12;
  const left = Math.min(
    window.innerWidth - rect.width - margin,
    Math.max(margin, anchor.right - rect.width),
  );
  const preferredTop = anchor.bottom + 8;
  const top = preferredTop + rect.height <= window.innerHeight - margin
    ? preferredTop
    : Math.max(margin, anchor.top - rect.height - 8);
  providerHealthPopover.style.left = `${Math.round(left)}px`;
  providerHealthPopover.style.top = `${Math.round(top)}px`;
}

function openProviderHealthPopover(button, provider, providerHealth) {
  closeActiveSessionsPopover();
  closeUsageHistoryPopover();
  hideHistoryDetail({ force: true });
  if (healthDetailButton === button && providerHealthPopover.classList.contains("show")) {
    closeProviderHealthPopover();
    return;
  }
  if (healthDetailButton) healthDetailButton.setAttribute("aria-expanded", "false");
  healthDetailButton = button;
  button.setAttribute("aria-expanded", "true");
  providerHealthPopover.replaceChildren(createProviderHealthDetail(provider, providerHealth));
  providerHealthPopover.classList.add("show");
  positionProviderHealthPopover();
}

function closeProviderHealthPopover() {
  if (healthDetailButton) healthDetailButton.setAttribute("aria-expanded", "false");
  healthDetailButton = null;
  providerHealthPopover.classList.remove("show");
  hideHistoryDetail({ force: true });
}

function renderHealthSourceStatus() {
  const configuredUrl = healthStatusUrl();
  if (!configuredUrl) {
    healthSource.dataset.state = "unknown";
    healthSourceDot.className = "health-source-dot unknown";
    healthSourceText.textContent = "未配置服务器检测地址";
    healthRefreshButton.disabled = true;
    return;
  }
  const freshnessValue = latestHealthStatus?.last_checked ||
    latestHealthStatus?.generated_at;
  const dataStatus = healthStatusError
    ? "error"
    : latestHealthStatus?.data_status || "unknown";
  healthSource.dataset.state = dataStatus;
  healthSourceDot.className = "health-source-dot " + dataStatus;
  if (!latestHealthStatus) {
    healthSourceText.textContent = healthStatusError
      ? "服务器检测数据暂不可用"
      : "正在读取服务器检测数据";
  } else if (healthStatusError) {
    healthSourceText.textContent = "服务器检测数据 · " +
      formatRelativeHealthTime(freshnessValue) +
      "（刷新失败）";
  } else if (dataStatus === "stale") {
    healthSourceText.textContent = "服务器检测数据已过期 · " +
      formatRelativeHealthTime(freshnessValue);
  } else {
    healthSourceText.textContent = "服务器检测数据 · " +
      formatRelativeHealthTime(freshnessValue);
  }
  healthRefreshButton.disabled = healthRequestActive;
}

function renderUsageSummary() {
  const total = latestStatus?.usage?.total || {};
  document.querySelector("#usage-total").textContent = formatTokenCount(total.total_tokens);
  document.querySelector("#usage-input").textContent = formatTokenCount(total.input_tokens);
  document.querySelector("#usage-output").textContent = formatTokenCount(total.output_tokens);
  document.querySelector("#usage-cached").textContent = formatTokenCount(total.cached_tokens);
  const estimatedNote = document.querySelector("#usage-estimated-note");
  const estimated = Number(total.estimated_requests || 0);
  estimatedNote.hidden = estimated === 0;
  estimatedNote.textContent = estimated > 0 ? `含 ${estimated} 个估算请求` : "";
}

function renderProviderList() {
  if (!latestStatus) return;
  const query = searchInput.value.trim().toLocaleLowerCase();
  const signature = JSON.stringify([
    query,
    manageProvidersMode,
    latestStatus.usage?.window,
    latestHealthStatus?.generated_at,
    latestHealthStatus?.data_status,
    healthStatusError,
    latestStatus.providers.map((provider) => [
      provider.provider_id,
      provider.name,
      provider.endpoint,
      provider.current,
      provider.has_credentials,
      provider.active_requests,
      (provider.active_sessions || []).map((session) => session.name),
      provider.hidden,
      providerUsage(provider.provider_id).request_count,
      providerUsage(provider.provider_id).total_tokens,
      providerUsage(provider.provider_id).estimated_requests,
      providerUsage(provider.provider_id).failed_requests,
      providerUsage(provider.provider_id).last_request_at,
      providerUsage(provider.provider_id).last_success_at,
      healthStatusForProvider(provider)?.last_checked,
    ]),
  ]);
  if (signature === renderedListSignature) return;
  renderedListSignature = signature;
  closeProviderHealthPopover();
  const openUsageProviderId = usageHistoryProvider?.provider_id || null;
  const openActiveProviderId = activeSessionsPopover.classList.contains("show")
    ? activeSessionsProviderId
    : null;
  usageHistoryButton = null;
  activeSessionsButton = null;
  const providers = latestStatus.providers.filter((provider) => {
    if (provider.hidden && !manageProvidersMode) return false;
    return `${provider.name} ${provider.endpoint}`.toLocaleLowerCase().includes(query);
  });
  providerList.replaceChildren();
  for (const provider of providers) {
    const row = document.createElement("div");
    row.className = `provider-row${provider.current ? " current" : ""}${provider.hidden ? " hidden-provider" : ""}${manageProvidersMode ? " managing" : ""}`;
    row.dataset.providerId = provider.provider_id;
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", "供应商 " + provider.name);
    row.draggable = manageProvidersMode && query === "";

    const state = document.createElement("span");
    state.className = "provider-state";
    if (manageProvidersMode) {
      const handle = document.createElement("span");
      handle.className = "drag-handle";
      handle.textContent = "⋮⋮";
      handle.title = query ? "搜索时不能拖动排序" : "拖动排序";
      state.append(handle);
    }
    state.append(Object.assign(document.createElement("span"), { className: "dot" }));
    state.append(provider.hidden ? "已隐藏" : provider.current ? "当前使用" : "可切换");

    const copy = document.createElement("div");
    copy.className = "provider-copy";
    const providerSelect = document.createElement("button");
    providerSelect.type = "button";
    providerSelect.className = "provider-select";
    providerSelect.disabled = manageProvidersMode;
    providerSelect.setAttribute(
      "aria-label",
      (provider.current ? "当前供应商 " : "切换到 ") + provider.name,
    );
    providerSelect.setAttribute("aria-pressed", String(provider.current));
    const title = document.createElement("span");
    title.className = "provider-title";
    const name = document.createElement("strong");
    name.textContent = escapeText(provider.name);
    providerSelect.append(name);
    title.append(providerSelect);
    if (
      uiConfig.features.provider_launch_command
      && latestRuntimeSettings !== null
      && latestRuntimeSettings?.show_provider_launch_command !== false
    ) {
      const launchButton = document.createElement("button");
      launchButton.type = "button";
      launchButton.className = "copy-command";
      launchButton.textContent = "复制临时启动命令";
      launchButton.disabled = !provider.has_credentials;
      launchButton.title = provider.has_credentials
        ? "复制使用此供应商临时启动 Codex CLI 的命令"
        : "该供应商没有可用 Key";
      launchButton.setAttribute("aria-label", `复制 ${provider.name} 的 Codex CLI 临时启动命令`);
      launchButton.addEventListener("click", (event) => {
        event.stopPropagation();
        if (provider.has_credentials) void copyProviderCommand(provider, launchButton);
      });
      title.append(launchButton);
    }
    if (
      uiConfig.features.status_upload === true
      && latestRuntimeSettings?.show_status_upload !== false
      && !manageProvidersMode
    ) {
      const uploadButton = document.createElement("button");
      uploadButton.type = "button";
      uploadButton.className = "copy-command status-upload-button";
      uploadButton.textContent = "上传检测";
      const uploadSupported = provider.status_upload_supported !== false && provider.has_credentials;
      uploadButton.disabled = !uploadSupported;
      uploadButton.title = uploadSupported
        ? "上传此供应商到服务器检测配置"
        : (provider.status_upload_reason || "该供应商没有可上传的标准凭据");
      uploadButton.setAttribute("aria-label", `上传 ${provider.name} 到状态服务`);
      uploadButton.addEventListener("click", (event) => {
        event.stopPropagation();
        if (uploadSupported) void openStatusUpload(provider);
      });
      title.append(uploadButton);
    }
    const endpoint = document.createElement("code");
    endpoint.textContent = escapeText(provider.endpoint);
    const usage = providerUsage(provider.provider_id);
    copy.append(title, endpoint);

    const requestCell = document.createElement("span");
    requestCell.className = "provider-request-cell";
    if (provider.active_requests > 0) {
      const active = document.createElement("button");
      active.type = "button";
      active.className = "active-badge";
      active.textContent = `${provider.active_requests} 个请求`;
      active.setAttribute("aria-label", `查看 ${provider.name} 正在运行的请求`);
      active.setAttribute("aria-controls", "active-sessions-popover");
      active.setAttribute("aria-expanded", String(openActiveProviderId === provider.provider_id));
      active.disabled = manageProvidersMode;
      if (!manageProvidersMode) {
        active.addEventListener("mouseenter", () => openActiveSessionsPopover(active, provider));
        active.addEventListener("mouseleave", scheduleActiveSessionsPopoverClose);
        active.addEventListener("focus", () => openActiveSessionsPopover(active, provider));
        active.addEventListener("blur", scheduleActiveSessionsPopoverClose);
        active.addEventListener("click", (event) => {
          event.stopPropagation();
          closeActiveSessionsPopover();
          openAllRequests();
        });
        if (openActiveProviderId === provider.provider_id) activeSessionsButton = active;
      }
      requestCell.append(active);
    } else {
      const emptyRequests = document.createElement("span");
      emptyRequests.className = "provider-request-empty";
      emptyRequests.textContent = "—";
      requestCell.append(emptyRequests);
    }

    const providerHealth = healthStatusForProvider(provider);
    const healthCell = document.createElement("span");
    healthCell.className = "provider-health-cell";
    healthCell.append(createProviderHealthSummary(providerHealth));
    if (providerHealth && !manageProvidersMode) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "provider-health-toggle";
      toggle.textContent = "详情";
      toggle.title = "查看检测详情";
      toggle.setAttribute("aria-label", "查看 " + provider.name + " 检测详情");
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-controls", "provider-health-popover");
      toggle.addEventListener("click", (event) => {
        event.stopPropagation();
        openProviderHealthPopover(toggle, provider, providerHealth);
      });
      healthCell.append(toggle);
    }

    const tokenCell = document.createElement(manageProvidersMode ? "span" : "button");
    tokenCell.className = "provider-token-cell";
    if (!manageProvidersMode) {
      tokenCell.type = "button";
      tokenCell.disabled = Number(usage.request_count || 0) === 0;
      tokenCell.setAttribute("aria-label", `查看 ${provider.name} 的请求记录`);
      tokenCell.setAttribute("aria-controls", "usage-history-popover");
      tokenCell.setAttribute("aria-expanded", String(openUsageProviderId === provider.provider_id));
      tokenCell.addEventListener("click", (event) => {
        event.stopPropagation();
        openUsageHistoryPopover(tokenCell, provider);
      });
      if (openUsageProviderId === provider.provider_id) {
        usageHistoryButton = tokenCell;
        usageHistoryProvider = provider;
      }
    }
    if (Number(usage.total_tokens || 0) > 0) {
      const tokenValue = document.createElement("strong");
      tokenValue.className = "provider-token-value";
      tokenValue.textContent = formatTokenCount(usage.total_tokens);
      const tokenUnit = document.createElement("span");
      tokenUnit.className = "provider-token-unit";
      const latestUsageAt = usage.last_request_at || usage.last_success_at;
      tokenUnit.textContent = latestUsageAt
        ? `最近 ${formatRetryTime(latestUsageAt)}`
        : "Token";
      tokenCell.append(tokenValue, tokenUnit);
    } else {
      const tokenZero = document.createElement("span");
      tokenZero.className = "provider-token-zero";
      tokenZero.textContent = "—";
      tokenCell.append(tokenZero);
      const latestUsageAt = usage.last_request_at || usage.last_success_at;
      if (latestUsageAt) {
        const tokenUnit = document.createElement("span");
        tokenUnit.className = "provider-token-unit";
        tokenUnit.textContent = `最近 ${formatRetryTime(latestUsageAt)}`;
        tokenCell.append(tokenUnit);
      }
    }
    if (!manageProvidersMode && Number(usage.request_count || 0) > 0) {
      const detailIcon = document.createElement("span");
      detailIcon.className = "provider-token-detail-icon";
      detailIcon.textContent = "i";
      detailIcon.setAttribute("aria-hidden", "true");
      tokenCell.append(detailIcon);
    }
    const tokenNotes = [];
    if (!manageProvidersMode && Number(usage.request_count || 0) > 0) {
      tokenNotes.push("点击查看请求记录");
    }
    if (usage.estimated_requests > 0) tokenNotes.push(`含 ${usage.estimated_requests} 个估算请求`);
    tokenCell.title = tokenNotes.join(" · ");

    const meta = document.createElement("span");
    meta.className = "provider-meta";
    const auth = document.createElement("span");
    auth.className = `auth-label${provider.has_credentials ? "" : " missing"}`;
    auth.textContent = provider.has_credentials ? "已配置" : "缺失";
    meta.append(auth);
    if (manageProvidersMode) {
      const actions = document.createElement("span");
      actions.className = "provider-row-actions";
      const visibility = document.createElement("button");
      visibility.type = "button";
      visibility.className = "provider-action-button visibility-button";
      visibility.textContent = provider.hidden ? "恢复" : "隐藏";
      visibility.disabled = provider.current;
      visibility.title = provider.current ? "请先切换供应商再隐藏" : visibility.textContent;
      visibility.setAttribute("aria-label", `${visibility.textContent}供应商 ${provider.name}`);
      visibility.addEventListener("click", (event) => {
        event.stopPropagation();
        setProviderHidden(provider, !provider.hidden);
      });
      actions.append(visibility);
      if (uiConfig.features.provider_catalog === true) {
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.className = "provider-action-button provider-edit-button";
        editButton.textContent = "编辑";
        editButton.title = "编辑本地供应商配置";
        editButton.setAttribute("aria-label", `编辑供应商 ${provider.name}`);
        editButton.addEventListener("click", (event) => {
          event.stopPropagation();
          void openProviderEditor(provider.provider_id);
        });
        actions.append(editButton);
      }
      meta.append(actions);
    }
    row.append(state, copy, requestCell, healthCell, tokenCell, meta);
    providerSelect.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!manageProvidersMode) selectProvider(provider);
    });
    row.addEventListener("click", (event) => {
      if (
        !manageProvidersMode &&
        !event.target.closest("button") &&
        !event.target.closest(".provider-health-detail")
      ) {
        selectProvider(provider);
      }
    });
    row.addEventListener("dragstart", (event) => {
      if (!row.draggable) return;
      draggedProviderId = provider.provider_id;
      row.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", provider.provider_id);
    });
    row.addEventListener("dragover", (event) => {
      if (!draggedProviderId || !row.draggable || draggedProviderId === provider.provider_id) return;
      event.preventDefault();
      const dragging = providerList.querySelector(".dragging");
      if (!dragging) return;
      const rect = row.getBoundingClientRect();
      providerList.insertBefore(dragging, event.clientY < rect.top + rect.height / 2 ? row : row.nextSibling);
    });
    row.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      if (!draggedProviderId) return;
      draggedProviderId = null;
      saveProviderOrder([...providerList.children].map((item) => item.dataset.providerId));
    });
    providerList.append(row);
  }
  if (activeSessionsPopover.classList.contains("show")) {
    const activeProvider = latestStatus.providers.find(
      (provider) => provider.provider_id === activeSessionsProviderId,
    );
    if (!activeSessionsButton || !activeProvider) {
      closeActiveSessionsPopover();
    } else {
      renderActiveSessionsPopover(activeProvider);
      activeSessionsButton.setAttribute("aria-expanded", "true");
      positionActiveSessionsPopover();
    }
  }
  if (usageHistoryPopover.classList.contains("show")) {
    if (!usageHistoryButton) {
      closeUsageHistoryPopover();
    } else {
      usageHistoryButton.setAttribute("aria-expanded", "true");
      positionUsageHistoryPopover();
      const latestRequest = Number(
        providerUsage(usageHistoryProvider.provider_id).last_request_at
          || providerUsage(usageHistoryProvider.provider_id).last_success_at
          || 0,
      );
      const loadedLatest = Number(usageHistoryItems[0]?.recorded_at || 0);
      if (!usageHistoryLoading && latestRequest > loadedLatest) {
        void readUsageHistory({ reset: true });
      }
    }
  }
  emptyState.hidden = providers.length > 0;
  const visibleCount = latestStatus.providers.filter((provider) => !provider.hidden).length;
  const hiddenCount = latestStatus.providers.length - visibleCount;
  document.querySelector("#provider-count").textContent = query
    ? `找到 ${providers.length} 个供应商`
    : manageProvidersMode
      ? `共 ${latestStatus.providers.length} 个供应商，已隐藏 ${hiddenCount} 个`
      : `已显示 ${visibleCount} 个 ${uiConfig.provider_label} 供应商`;
}

function renderStatus(status) {
  const previousHealthStatusUrl = healthStatusUrl();
  latestStatus = status;
  const configuredHealthStatusUrl = healthStatusUrl();
  if (configuredHealthStatusUrl !== previousHealthStatusUrl) {
    healthRequestSequence += 1;
    healthRequestActive = false;
    latestHealthStatus = null;
    healthStatusError = null;
    renderedListSignature = null;
    renderHealthSourceStatus();
    if (configuredHealthStatusUrl) readHealthStatus({ quiet: true });
  }
  renderUsageSummary();
  const current = currentProvider(status);
  document.querySelector("#current-name").textContent = current?.name || "尚未选择";
  document.querySelector("#current-endpoint").textContent = current?.endpoint || "—";
  document.querySelector("#active-requests").textContent = String(status.active_requests);
  const requestTabCount = document.querySelector("#request-tab-count");
  requestTabCount.hidden = Number(status.active_requests || 0) === 0;
  requestTabCount.textContent = String(status.active_requests || 0);
  populateRequestProviders();
  if (!sessionRoutePopover.hidden) renderSessionRouteProviders();
  document.querySelector("#auth-state").textContent = current?.has_credentials ? "已安全读取" : "缺失";
  document.querySelector("#wire-api").textContent = current?.wire_api
    ? (current.wire_api === "responses" ? "Responses · SSE" : current.wire_api === "anthropic_messages" ? "Messages · SSE" : escapeText(current.wire_api))
    : uiConfig.protocol_label;
  const last = status.last_status_code;
  document.querySelector("#last-request").textContent = last == null
    ? "尚无请求"
    : `${last >= 200 && last < 400 ? "成功" : "失败"} · HTTP ${last}`;

  const retry = status.retry || {};
  const recoveryHistory = retry.history && typeof retry.history === "object"
    ? retry.history
    : null;
  const recoveryHistoryItems = Array.isArray(recoveryHistory?.items)
    ? recoveryHistory.items
    : Array.isArray(retry.recent_errors) ? retry.recent_errors : [];
  const recoveryHistoryCount = Number.isFinite(Number(recoveryHistory?.total_count))
    ? Number(recoveryHistory.total_count)
    : recoveryHistoryItems.length;
  const activeRecoveries = retry.active || [];
  const activeRecovery = activeRecoveries[0];
  const openCircuit = retry.circuit_open?.[0];
  const recoveryTitle = document.querySelector("#recovery-title");
  const recoveryDetail = document.querySelector("#recovery-detail");
  recovery.classList.toggle("active", Boolean(activeRecovery));
  recovery.classList.toggle("blocked", Boolean(openCircuit));
  if (activeRecovery) {
    recoveryTitle.textContent = activeRecoveries.length > 1
      ? `${activeRecoveries.length} 个请求正在自动恢复`
      : `正在自动恢复 · 第 ${activeRecovery.attempt}/${attemptLabel(activeRecovery.max_attempts)} 次`;
    recoveryDetail.textContent = formatRecoverySummary(activeRecovery, { waiting: true });
  } else if (openCircuit) {
    recoveryTitle.textContent = "当前供应商短暂熔断";
    recoveryDetail.textContent = `约 ${Math.ceil(openCircuit.retry_after_seconds)} 秒后恢复接收新请求。`;
  } else if (recoveryHistoryCount > 0) {
    recoveryTitle.textContent = `自动恢复已就绪 · 近 24 小时 ${recoveryHistoryCount} 条`;
    const latestError = recoveryHistoryItems[0];
    recoveryDetail.textContent = latestError
      ? `最近一次 · ${formatRecoverySummary(latestError)}`
      : "只在内容输出前重试，不会重放已经开始的响应。";
  } else {
    recoveryTitle.textContent = "自动恢复已就绪";
    recoveryDetail.textContent = "临时故障会在输出开始前自动重试。";
  }
  renderRecoveryErrors(retry);
  document.querySelector("#retry-state").textContent = activeRecovery
    ? `${activeRecoveries.length} 个重试中`
    : retry.enabled === false ? "已关闭" : "已启用";
  if (!retryFormLoaded) populateRetryForm(retry);

  const drainingProviders = status.providers.filter(
    (provider) => Number(provider.draining_requests || 0) > 0,
  );
  const draining = document.querySelector("#draining");
  const drainingTitle = draining.querySelector("strong");
  const drainingDetail = draining.querySelector("span");
  if (drainingProviders.length > 0) {
    drainingTitle.textContent = "旧请求仍在完成";
    drainingDetail.textContent = drainingProviders
      .map((provider) => `${provider.name}：${provider.draining_requests} 个`)
      .join("，");
  } else {
    drainingTitle.textContent = "没有旧请求正在处理";
    drainingDetail.textContent = "切换不会中断已经开始的流式响应。";
  }
  renderProviderList();
}

async function readStatus({ quiet = false, force = false } = {}) {
  if (controlRequestActive || statusRequestActive || (!force && document.hidden)) return;
  statusRequestActive = true;
  const requestSequence = ++statusRequestSequence;
  try {
    const params = timeRangeParams("usage");
    const response = await fetchControl(
      `${controlUrl("/api/status")}?${params}`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (requestSequence === statusRequestSequence) {
      renderStatus(status);
      if (!document.querySelector("#requests-view").hidden) {
        void readRequests({ refresh: true, quiet: true });
        if (!sessionRoutePopover.hidden) void readSessionRoutes({ quiet: true });
      }
    }
  } catch (error) {
    footerMessage.textContent = "无法连接本地中转，服务可能已经退出";
    if (!quiet) showToast("连接失败", "无法读取本地中转状态。", "error");
  } finally {
    statusRequestActive = false;
  }
}

async function readHealthStatus({ quiet = false } = {}) {
  const configuredUrl = healthStatusUrl();
  if (!configuredUrl) {
    latestHealthStatus = null;
    healthStatusError = null;
    renderHealthSourceStatus();
    return;
  }
  if (healthRequestActive) return;
  healthRequestActive = true;
  const requestSequence = ++healthRequestSequence;
  renderHealthSourceStatus();
  try {
    const requestUrl = statusUrlForWindow(configuredUrl);
    if (!requestUrl) throw new Error("检测地址无效");
    const response = await fetchControl(requestUrl, {
      cache: "no-store",
      mode: "cors",
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.providers)) {
      throw new Error("检测数据格式无效");
    }
    if (requestSequence !== healthRequestSequence) return;
    latestHealthStatus = payload;
    healthStatusError = null;
    if (!document.querySelector("#monitor-view")?.hidden) {
      monitorProviders = alignMonitorProviders(monitorProviders, payload.providers);
      renderMonitorManagement(latestHealthStatus);
    }
  } catch (error) {
    if (requestSequence !== healthRequestSequence) return;
    healthStatusError = error?.message || "无法读取服务器检测数据";
    if (!quiet) {
      showToast(
        "检测数据刷新失败",
        latestHealthStatus
          ? "继续显示上次成功获取的检测结果。"
          : "暂时无法连接服务器检测接口。",
        "error",
      );
    }
  } finally {
    if (requestSequence === healthRequestSequence) {
      healthRequestActive = false;
      renderedListSignature = null;
      renderHealthSourceStatus();
      renderProviderList();
    }
  }
}

async function setProviderHidden(provider, hidden) {
  controlRequestActive = true;
  try {
    const params = timeRangeParams("usage");
    const response = await fetch(
      `${controlUrl(`/api/providers/${encodeURIComponent(provider.provider_id)}/visibility`)}?${params}`,
      {
        method: "POST",
        headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
        body: JSON.stringify({ hidden }),
        cache: "no-store",
      },
    );
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${response.status}`);
    }
    renderedListSignature = null;
    renderStatus(await response.json());
    showToast(hidden ? "供应商已隐藏" : "供应商已恢复", provider.name);
  } catch (error) {
    showToast("更新显示状态失败", error.message || "本地中转没有接受这次修改。", "error");
  } finally {
    controlRequestActive = false;
  }
}

async function saveProviderOrder(providerIds) {
  if (!Array.isArray(providerIds) || providerIds.length !== latestStatus?.providers?.length) return;
  controlRequestActive = true;
  try {
    const params = timeRangeParams("usage");
    const response = await fetch(
      `${controlUrl("/api/providers/order")}?${params}`,
      {
        method: "POST",
        headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
        body: JSON.stringify({ provider_ids: providerIds }),
        cache: "no-store",
      },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderedListSignature = null;
    renderStatus(await response.json());
    showToast("供应商顺序已保存", "后续启动仍会使用当前顺序。");
  } catch (error) {
    renderedListSignature = null;
    renderProviderList();
    showToast("保存排序失败", "已恢复服务器中的供应商顺序。", "error");
  } finally {
    controlRequestActive = false;
  }
}

function setProviderEditorError(message = "") {
  providerEditorError.textContent = message;
  providerEditorError.hidden = !message;
}

function parseProviderEditorObject(value, fieldName) {
  let parsed;
  try {
    parsed = JSON.parse(value || "{}");
  } catch (error) {
    throw new Error(`${fieldName} 必须是有效的 JSON 对象`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName} 必须是 JSON 对象`);
  }
  return parsed;
}

function closeProviderEditor() {
  setProviderEditorError();
  if (providerEditor.open && typeof providerEditor.close === "function") {
    providerEditor.close();
  } else {
    providerEditor.removeAttribute("open");
  }
}

async function openProviderEditor(providerId = "") {
  if (uiConfig.features.provider_catalog !== true) return;
  providerEditorId.value = providerId;
  providerEditorTitle.textContent = providerId ? "编辑供应商" : "新增供应商";
  providerEditorMode.textContent = providerId ? "修改本地副本" : "新增本地供应商";
  providerEditorName.value = "";
  providerEditorBaseUrl.value = "";
  providerEditorWireApi.value = "responses";
  providerEditorTransport.value = "httpx";
  providerEditorApiKey.value = "";
  providerEditorClearKey.checked = false;
  providerEditorClearKey.disabled = !providerId;
  providerEditorHeaders.value = "{}";
  providerEditorQuery.value = "{}";
  setProviderEditorError();
  providerEditorSave.disabled = true;
  providerEditorDelete.hidden = !providerId;
  providerEditorDelete.disabled = true;
  if (providerId) {
    try {
      const response = await fetch(
        controlUrl(`/api/providers/${encodeURIComponent(providerId)}`),
        { cache: "no-store" },
      );
      if (!response.ok) throw new Error(await responseDetail(response, "无法读取供应商"));
      const provider = await response.json();
      providerEditorName.value = provider.name || "";
      providerEditorBaseUrl.value = provider.base_url || "";
      providerEditorWireApi.value = provider.wire_api || "responses";
      providerEditorTransport.value = provider.transport || "httpx";
      providerEditorHeaders.value = JSON.stringify(provider.headers || {}, null, 2);
      providerEditorQuery.value = JSON.stringify(provider.query_params || {}, null, 2);
    } catch (error) {
      showToast("无法打开供应商", error?.message || "无法读取本地供应商配置", "error");
      return;
    }
  }
  providerEditorSave.disabled = false;
  providerEditorDelete.disabled = providerId === latestStatus.current_provider_id;
  providerEditorDelete.title = providerEditorDelete.disabled
    ? "当前供应商不能删除，请先切换"
    : "永久删除本地供应商";
  if (typeof providerEditor.showModal === "function") {
    providerEditor.showModal();
  } else {
    providerEditor.setAttribute("open", "");
  }
  providerEditorName.focus();
}

async function saveProviderEditor(event) {
  event.preventDefault();
  setProviderEditorError();
  let headers;
  let queryParams;
  try {
    headers = parseProviderEditorObject(providerEditorHeaders.value, "HTTP Headers");
    queryParams = parseProviderEditorObject(providerEditorQuery.value, "Query Params");
  } catch (error) {
    setProviderEditorError(error?.message || "配置格式无效");
    return;
  }
  const payload = {
    name: providerEditorName.value.trim(),
    base_url: providerEditorBaseUrl.value.trim(),
    wire_api: providerEditorWireApi.value,
    transport: providerEditorTransport.value,
    headers,
    query_params: queryParams,
    clear_api_key: providerEditorClearKey.checked,
  };
  const apiKey = providerEditorApiKey.value.trim();
  if (apiKey) payload.api_key = apiKey;
  if (!payload.name || !payload.base_url) {
    setProviderEditorError("名称和 Base URL 不能为空");
    return;
  }
  const providerId = providerEditorId.value;
  providerEditorSave.disabled = true;
  try {
    const response = await fetch(
      controlUrl(providerId ? `/api/providers/${encodeURIComponent(providerId)}` : "/api/providers"),
      {
        method: providerId ? "PUT" : "POST",
        headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        cache: "no-store",
      },
    );
    if (!response.ok) throw new Error(await responseDetail(response, "保存供应商失败"));
    const status = await response.json();
    renderedListSignature = null;
    renderStatus(status);
    closeProviderEditor();
    showToast(providerId ? "供应商已更新" : "供应商已新增", "本地供应商目录已保存，路由已立即刷新");
  } catch (error) {
    setProviderEditorError(error?.message || "保存供应商失败");
  } finally {
    providerEditorSave.disabled = false;
  }
}

async function deleteProviderEditor() {
  const providerId = providerEditorId.value;
  if (!providerId || uiConfig.features.provider_catalog !== true) return;
  const providerName = providerEditorName.value.trim() || "该供应商";
  if (!window.confirm(`删除“${providerName}”吗？此操作无法撤销。`)) return;
  setProviderEditorError();
  providerEditorDelete.disabled = true;
  providerEditorSave.disabled = true;
  try {
    const response = await fetch(
      controlUrl(`/api/providers/${encodeURIComponent(providerId)}`),
      {
        method: "DELETE",
        headers: CONTROL_HEADER,
        cache: "no-store",
      },
    );
    if (!response.ok) throw new Error(await responseDetail(response, "删除供应商失败"));
    const status = await response.json();
    renderedListSignature = null;
    renderStatus(status);
    closeProviderEditor();
    showToast("供应商已删除", `${providerName} 已从本地供应商目录移除`);
  } catch (error) {
    setProviderEditorError(error?.message || "删除供应商失败");
  } finally {
    providerEditorSave.disabled = false;
    providerEditorDelete.disabled = providerId === latestStatus.current_provider_id;
  }
}

async function importProvidersFromCcSwitch() {
  if (uiConfig.features.provider_catalog !== true) return;
  const overwrite = providerImportMode?.value === "overwrite";
  importProvidersButton.disabled = true;
  try {
    const response = await fetch(controlUrl("/api/providers/import/cc-switch"), {
      method: "POST",
      headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
      body: JSON.stringify({ overwrite }),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(await responseDetail(response, "CC Switch 导入失败"));
    const status = await response.json();
    renderedListSignature = null;
    renderStatus(status);
    const result = status.catalog || {};
    showToast(
      "CC Switch 导入完成",
      overwrite
        ? `新增 ${Number(result.added || 0)} 个，覆盖 ${Number(result.overwritten || 0)} 个`
        : `新增 ${Number(result.added || 0)} 个，跳过 ${Number(result.skipped || 0)} 个，未覆盖本地修改`,
    );
  } catch (error) {
    showToast("CC Switch 导入失败", error?.message || "无法读取 CCS 数据库", "error");
  } finally {
    importProvidersButton.disabled = false;
  }
}

function toggleProviderManagement() {
  manageProvidersMode = !manageProvidersMode;
  manageProvidersButton.setAttribute("aria-pressed", String(manageProvidersMode));
  manageProvidersButton.textContent = manageProvidersMode ? "完成" : "管理";
  document.querySelector("#manage-hint").hidden = !manageProvidersMode;
  renderProviderManagementState();
  renderedListSignature = null;
  renderProviderList();
}

async function selectProvider(provider) {
  if (provider.current) return;
  controlRequestActive = true;
  const requestSequence = ++statusRequestSequence;
  try {
    const params = timeRangeParams("usage");
    const response = await fetch(
      `${controlUrl(`/api/providers/${encodeURIComponent(provider.provider_id)}/select`)}?${params}`,
      { method: "POST", headers: CONTROL_HEADER, cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (requestSequence === statusRequestSequence) renderStatus(status);
    showToast(
      `已切换到 ${provider.name}`,
      "新请求立即生效；尚未输出且再次失败的旧请求将由新供应商接管。",
    );
  } catch (error) {
    showToast("切换失败", "本地中转没有接受这次切换。", "error");
  } finally {
    controlRequestActive = false;
  }
}

async function refreshProviders() {
  const button = document.querySelector("#refresh-button");
  button.disabled = true;
  controlRequestActive = true;
  const requestSequence = ++statusRequestSequence;
  try {
    const params = timeRangeParams("usage");
    const response = await fetch(`${controlUrl("/api/refresh")}?${params}`, {
      method: "POST",
      headers: CONTROL_HEADER,
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (requestSequence === statusRequestSequence) renderStatus(status);
    showToast("已重新读取本地目录", `供应商列表已更新，共 ${latestStatus.providers.length} 个。`);
  } catch (error) {
    showToast("刷新失败", "无法读取本地供应商目录。", "error");
  } finally {
    controlRequestActive = false;
    button.disabled = false;
  }
}

async function saveRetrySettings(event) {
  event.preventDefault();
  const button = document.querySelector("#save-retry-settings");
  const policy = retryPayloadFromForm();
  if (policy.max_delay_seconds < policy.delay_seconds) {
    showToast("设置无效", "最大等待不能小于首次等待。", "error");
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(controlUrl("/api/retry-policy"), {
      method: "POST",
      headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
      body: JSON.stringify(policy),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    retryFormLoaded = false;
    renderStatus(status);
    showToast("重试设置已保存", "新请求将使用更新后的策略。", "success");
  } catch (error) {
    showToast("保存失败", "本地中转没有接受这组设置。", "error");
  } finally {
    button.disabled = false;
  }
}

async function validateRuntimeDatabase() {
  const button = document.querySelector("#validate-database");
  const databasePath = runtimeDatabaseInput.value.trim();
  if (!databasePath) {
    showToast("数据来源无效", "请输入 CC Switch 数据库路径。", "error");
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(controlUrl("/api/runtime-settings/validate-database"), {
      method: "POST",
      headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
      body: JSON.stringify({ database_path: databasePath }),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(await responseDetail(response, "无法读取供应商数据库"));
    const result = await response.json();
    runtimeDatabaseInput.value = result.database_path;
    showToast(
      "数据来源可用",
      `已读取 ${result.provider_count} 个 ${uiConfig.provider_label} 供应商。`,
    );
  } catch (error) {
    showToast("数据来源不可用", error?.message || "无法读取供应商数据库。", "error");
  } finally {
    button.disabled = false;
  }
}

function normalizedHealthUrl(value) {
  const raw = value.trim();
  if (!raw) return "";
  const parsed = new URL(raw);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash) {
    throw new Error("检测地址必须是无凭据、无片段的 HTTP 或 HTTPS 地址");
  }
  return parsed.href;
}

async function testRuntimeHealthUrl() {
  const button = document.querySelector("#test-health-url");
  let url;
  try {
    url = normalizedHealthUrl(runtimeHealthUrlInput.value);
  } catch (error) {
    showToast("检测地址无效", error?.message || "请输入有效地址。", "error");
    return;
  }
  if (!url) {
    showToast("检测地址为空", "保存后将关闭服务器检测数据。", "success");
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(url, { cache: "no-store", mode: "cors" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.providers)) throw new Error("检测数据格式无效");
    runtimeHealthUrlInput.value = url;
    showToast("检测地址可用", `接口返回 ${payload.providers.length} 个供应商状态。`);
  } catch (error) {
    showToast("检测地址不可用", error?.message || "无法读取服务器检测接口。", "error");
  } finally {
    button.disabled = false;
  }
}

async function saveRuntimeSettings(event) {
  event.preventDefault();
  const button = document.querySelector("#save-runtime-settings");
  const payload = runtimePayloadFromForm();
  if (!Number.isInteger(payload.port) || payload.port < 1024 || payload.port > 65535) {
    showToast("端口无效", "端口必须是 1024 到 65535 之间的整数。", "error");
    return;
  }
  if (!payload.database_path) {
    showToast("数据来源无效", "请输入 CC Switch 数据库路径。", "error");
    return;
  }
  try {
    if (payload.health_status_url) payload.health_status_url = normalizedHealthUrl(payload.health_status_url);
  } catch (error) {
    showToast("检测地址无效", error?.message || "请输入有效地址。", "error");
    return;
  }
  button.disabled = true;
  try {
    const previousConsoleUi = latestRuntimeSettings?.console_ui || "modern";
    const response = await fetch(controlUrl("/api/runtime-settings"), {
      method: "POST",
      headers: { ...CONTROL_HEADER, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(await responseDetail(response, "本地中转没有接受这组设置"));
    const settings = await response.json();
    renderRuntimeSettings(settings);
    if (settings.console_ui !== previousConsoleUi) {
      showToast("控制台界面已保存", "正在刷新页面应用新界面。");
      window.setTimeout(() => {
        const nextUrl = new URL(window.location.href);
        nextUrl.searchParams.delete("ui");
        window.location.replace(nextUrl.toString());
      }, 350);
      return;
    }
    renderedListSignature = null;
    await readStatus();
    await readHealthStatus({ quiet: true });
    showToast(
      "运行设置已保存",
      settings.restart_required
        ? "数据源和检测地址已生效；端口将在重新启动后生效。"
        : "数据源和检测地址已即时生效。",
    );
  } catch (error) {
    showToast("保存失败", error?.message || "本地中转没有接受这组设置。", "error");
  } finally {
    button.disabled = false;
  }
}

async function copyDataDirectory() {
  const value = document.querySelector("#runtime-data-directory").textContent.trim();
  try {
    await navigator.clipboard.writeText(value);
    showToast("路径已复制", value);
  } catch (error) {
    showToast("复制失败", "浏览器没有允许写入剪贴板。", "error");
  }
}

function importToCcSwitch() {
  try {
    const deeplink = buildCcSwitchImportDeeplink({
      serviceId: uiConfig.service_id,
      endpoint: uiConfig.proxy_url,
      homepage: window.location.origin,
      providerName: uiConfig.display_name || "Codex Provider Hub",
    });
    window.location.assign(deeplink);
    window.setTimeout(() => {
      if (document.hasFocus()) {
        showToast("导入失败", "CC Switch 未安装或协议处理程序未注册。", "error");
      }
    }, 100);
  } catch (error) {
    showToast("导入失败", error?.message || "无法生成 CC Switch 导入地址。", "error");
  }
}

async function disableAutomaticMonitor(provider) {
  if (!window.confirm(`确定停止“${provider.name || provider.id}”的自动监控吗？之后仍可手动检测。`)) return;
  try {
    await monitorManagementRequest({
      action: "disable",
      provider_id: monitorProviderId(provider),
      probe_mode: "manual_only",
    });
    await loadMonitorManagement();
    showToast("已停止自动监控", `${provider.name || provider.id} 仍可手动检测。`);
  } catch (err) {
    showToast("停止失败", err?.message || "无法停止自动监控。", "error");
  }
}

async function copyProviderCommand(provider, button) {
  button.disabled = true;
  try {
    const response = await fetch(
      controlUrl(`/api/providers/${encodeURIComponent(provider.provider_id)}/launch-command`),
      { method: "POST", headers: CONTROL_HEADER, cache: "no-store" },
    );
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${response.status}`);
    }
    const payload = await response.json();
    if (!payload || typeof payload.command !== "string" || !payload.command.trim()) {
      throw new Error("命令格式无效");
    }
    await navigator.clipboard.writeText(payload.command);
    showToast("临时启动命令已复制", `已为 ${provider.name} 生成 Codex CLI 临时启动命令。`);
  } catch (error) {
    showToast("复制临时启动命令失败", error?.message || "浏览器没有允许写入剪贴板。", "error");
  } finally {
    button.disabled = !provider.has_credentials;
  }
}

async function shutdownProxy() {
  if (!window.confirm(`退出本地中转后，${uiConfig.shutdown_client_name} 新请求将无法连接。确定退出吗？`)) return;
  try {
    const response = await fetch(controlUrl("/api/shutdown"), {
      method: "POST",
      headers: CONTROL_HEADER,
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    window.clearInterval(pollTimer);
    window.clearInterval(healthPollTimer);
    footerMessage.textContent = "本地中转正在退出，可以关闭此页面";
    showToast("本地中转正在退出", "再次使用时从桌面快捷方式启动。", "success");
  } catch (error) {
    showToast("退出失败", "本地中转仍在运行。", "error");
  }
}

let updateState = { supported: false, has_update: false, release_url: "" };

function renderUpdateResult(message, tone) {
  const box = document.querySelector("#update-result");
  if (!box) return;
  if (!message) {
    box.hidden = true;
    box.textContent = "";
    return;
  }
  box.hidden = false;
  box.textContent = "";
  const span = document.createElement("span");
  span.textContent = message;
  box.appendChild(span);
  if (tone === "update" && updateState.release_url) {
    const link = document.createElement("a");
    link.href = updateState.release_url;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = "打开 Releases 页面";
    link.style.marginLeft = "8px";
    box.appendChild(link);
  }
}

function applyUpdateState(state) {
  updateState = { ...updateState, ...state };
  const versionCode = document.querySelector("#update-current-version");
  if (versionCode) {
    versionCode.textContent = state.current_version
      ? `v${state.current_version}`
      : "—";
  }
  const applyButton = document.querySelector("#update-apply-button");
  if (applyButton) {
    applyButton.hidden = !(updateState.has_update && updateState.supported);
  }
}

async function loadUpdateStatus() {
  try {
    const response = await fetch(controlUrl("/api/update"), { cache: "no-store" });
    if (!response.ok) return;
    applyUpdateState(await response.json());
  } catch (error) {
    /* 版本信息读取失败时静默降级 */
  }
}

async function checkForUpdate() {
  const button = document.querySelector("#update-check-button");
  if (button) button.disabled = true;
  renderUpdateResult("正在检查更新…", "info");
  try {
    const response = await fetch(controlUrl("/api/update/check"), {
      method: "POST",
      headers: CONTROL_HEADER,
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      updateState.release_url = payload.release_url || updateState.release_url;
      renderUpdateResult(payload.detail || `检查更新失败：HTTP ${response.status}`, "update");
      return;
    }
    applyUpdateState(payload);
    if (payload.has_update) {
      renderUpdateResult(`发现新版本 v${payload.latest_version}`, "update");
    } else {
      renderUpdateResult("当前已是最新版本。", "info");
    }
  } catch (error) {
    renderUpdateResult(error?.message || "无法连接更新服务。", "info");
  } finally {
    if (button) button.disabled = false;
  }
}

async function applyUpdate() {
  const button = document.querySelector("#update-apply-button");
  if (button) button.disabled = true;
  renderUpdateResult("正在下载并校验新版本…", "info");
  try {
    const response = await fetch(controlUrl("/api/update/apply"), {
      method: "POST",
      headers: CONTROL_HEADER,
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      updateState.release_url = payload.release_url || updateState.release_url;
      renderUpdateResult(payload.detail || `更新失败：HTTP ${response.status}`, "update");
      if (button) button.disabled = false;
      return;
    }
    window.clearInterval(pollTimer);
    window.clearInterval(healthPollTimer);
    renderUpdateResult("更新已就绪，正在重启到新版本…", "info");
    showToast("正在更新", "本地中转会自动重启到新版本。", "success");
  } catch (error) {
    renderUpdateResult(error?.message || "无法连接更新服务。", "info");
    if (button) button.disabled = false;
  }
}

searchInput.addEventListener("input", renderProviderList);
usageWindow.addEventListener("change", () => {
  if (usageWindow.value === "custom") {
    openTimeRangePopover("usage", usageWindow);
    return;
  }
  appliedTimeWindows.usage = usageWindow.value;
  persistTimeRange("usage");
  refreshForTimeRange("usage");
});
manageProvidersButton.addEventListener("click", toggleProviderManagement);
addProviderButton?.addEventListener("click", () => void openProviderEditor());
importProvidersButton?.addEventListener("click", () => void importProvidersFromCcSwitch());
providerEditorForm?.addEventListener("submit", saveProviderEditor);
providerEditorDelete?.addEventListener("click", () => void deleteProviderEditor());
providerEditorClose?.addEventListener("click", closeProviderEditor);
providerEditorCancel?.addEventListener("click", closeProviderEditor);
healthRefreshButton.addEventListener("click", () => readHealthStatus());
document.querySelector("#refresh-button").addEventListener("click", refreshProviders);
document.querySelector("#copy-config").addEventListener("click", importToCcSwitch);
document.querySelector("#shutdown-button").addEventListener("click", shutdownProxy);
document.querySelector("#update-check-button").addEventListener("click", checkForUpdate);
document.querySelector("#update-apply-button").addEventListener("click", applyUpdate);
for (const button of document.querySelectorAll(".view-tab")) {
  button.addEventListener("click", () => switchView(button.dataset.view));
}
for (const control of retryForm.querySelectorAll("input, select")) {
  control.addEventListener("change", renderSettingsSummary);
}
retryForm.addEventListener("submit", saveRetrySettings);
for (const control of runtimeForm.querySelectorAll("input")) {
  control.addEventListener("input", () => {
    renderRuntimeSettingsSummary();
  });
}
runtimeForm.addEventListener("submit", saveRuntimeSettings);
document.querySelector("#validate-database").addEventListener("click", validateRuntimeDatabase);
document.querySelector("#test-health-url").addEventListener("click", testRuntimeHealthUrl);
document.querySelector("#monitor-refresh")?.addEventListener("click", () => { void loadMonitorManagement(); void readHealthStatus({ quiet: true }); });
document.querySelector("#copy-data-directory").addEventListener("click", copyDataDirectory);
document.querySelector("#status-upload-close").addEventListener("click", closeStatusUploadModal);
document.querySelector("#status-upload-cancel").addEventListener("click", closeStatusUploadModal);
document.querySelector("#status-upload-submit").addEventListener("click", () => void submitStatusUpload());
document.querySelector("#status-upload-add-model").addEventListener("click", () => {
  const models = [...statusUploadModels.querySelectorAll("input")].map((input) => input.value);
  renderUploadModelFields([...models, ""]);
  statusUploadModels.lastElementChild?.querySelector("input")?.focus();
});
statusUploadChangeTarget.addEventListener("click", () => {
  statusUploadSsh.hidden = false;
  statusUploadChangeTarget.hidden = true;
  document.querySelector("#status-upload-password").focus();
});
usageHistoryClose.addEventListener("click", closeUsageHistoryPopover);
usageHistoryMore.addEventListener("click", () => void readUsageHistory());
usageHistoryList.addEventListener("scroll", () => {
  const remaining = usageHistoryList.scrollHeight
    - usageHistoryList.scrollTop
    - usageHistoryList.clientHeight;
  if (remaining < 48 && usageHistoryNextCursor && !usageHistoryLoading) {
    void readUsageHistory();
  }
});
requestWindow.addEventListener("change", () => {
  if (requestWindow.value === "custom") {
    openTimeRangePopover("requests", requestWindow);
    return;
  }
  appliedTimeWindows.requests = requestWindow.value;
  persistTimeRange("requests");
  refreshForTimeRange("requests");
});
for (const control of [requestStatus, requestProvider]) {
  control.addEventListener("change", () => void readRequests({ reset: true }));
}
for (const button of document.querySelectorAll("[data-time-range-edit]")) {
  button.addEventListener("click", () => {
    openTimeRangePopover(button.dataset.timeRangeEdit, button);
  });
}
timeRangeClose.addEventListener("click", () => closeTimeRangePopover({ restoreSelection: true }));
timeRangeCancel.addEventListener("click", () => closeTimeRangePopover({ restoreSelection: true }));
timeRangeApply.addEventListener("click", applyCustomTimeRange);
requestQuery.addEventListener("input", () => {
  window.clearTimeout(requestSearchTimer);
  requestSearchTimer = window.setTimeout(
    () => void readRequests({ reset: true }),
    240,
  );
});
document.querySelector("#requests-refresh").addEventListener(
  "click",
  () => {
    void readRequests({ reset: true });
    if (!sessionRoutePopover.hidden) void readSessionRoutes({ quiet: true, force: true });
  },
);
sessionRouteSettingsButton.addEventListener("click", () => {
  if (sessionRoutePopover.hidden) {
    openSessionRoutePopover();
  } else {
    closeSessionRoutePopover();
  }
});
document.querySelector("#session-route-close").addEventListener("click", closeSessionRoutePopover);
sessionRouteSessionSelect.addEventListener("change", () => {
  sessionRouteSelectedKey = sessionRouteSessionSelect.value;
  renderSessionRouteProviders();
});
sessionRouteProviderSelect.addEventListener("change", () => void updateSelectedSessionRoute());
requestTableShell.addEventListener("scroll", () => {
  const remaining = requestTableShell.scrollHeight
    - requestTableShell.scrollTop
    - requestTableShell.clientHeight;
  if (remaining < 64 && requestNextCursor && !requestLoading) {
    void readRequests({ loadMore: true });
  }
});
providerList.addEventListener("scroll", () => {
  if (activeSessionsPopover.classList.contains("show")) positionActiveSessionsPopover();
  if (usageHistoryPopover.classList.contains("show")) positionUsageHistoryPopover();
}, { passive: true });
activeSessionsPopover.addEventListener("mouseenter", () => {
  window.clearTimeout(activeSessionsHideTimer);
});
activeSessionsPopover.addEventListener("mouseleave", scheduleActiveSessionsPopoverClose);
recoveryDetailsButton.addEventListener("click", () => {
  if (recoveryDetailsPinned) {
    hideRecoveryDetails({ force: true });
  } else {
    showRecoveryDetails({ pinned: true });
  }
});
recoveryDetailsButton.addEventListener("mouseenter", () => showRecoveryDetails());
recoveryDetailsButton.addEventListener("mouseleave", scheduleRecoveryDetailsHide);
recoveryDetailsButton.addEventListener("focus", () => showRecoveryDetails());
recoveryDetailsButton.addEventListener("blur", scheduleRecoveryDetailsHide);
recoveryPopover.addEventListener("mouseenter", () => window.clearTimeout(recoveryHideTimer));
recoveryPopover.addEventListener("mouseleave", scheduleRecoveryDetailsHide);
recoveryErrorList.addEventListener("scroll", () => {
  const remaining = recoveryErrorList.scrollHeight
    - recoveryErrorList.scrollTop
    - recoveryErrorList.clientHeight;
  if (
    remaining < 48
    && latestRecoveryHistory?.next_cursor
    && !recoveryHistoryRequestActive
  ) {
    void readRecoveryHistory({ loadMore: true });
  }
});
themeButton.addEventListener("click", () => {
  setThemeMenuOpen(themeMenu.hidden);
});
for (const item of themeMenu.querySelectorAll("[data-theme-value]")) {
  item.addEventListener("click", () => {
    applyTheme(item.dataset.themeValue, { persist: true });
    setThemeMenuOpen(false);
    themeButton.focus();
  });
}
document.addEventListener("click", (event) => {
  if (
    !timeRangePopover.hidden
    && !event.target.closest("#time-range-popover")
    && !event.target.closest("[data-time-range-edit]")
    && event.target !== usageWindow
    && event.target !== requestWindow
  ) {
    closeTimeRangePopover({ restoreSelection: true });
  }
  if (!event.target.closest(".theme-control")) setThemeMenuOpen(false);
  if (!event.target.closest("#recovery") && !event.target.closest("#recovery-popover")) {
    hideRecoveryDetails({ force: true });
  }
  if (!event.target.closest("#provider-health-popover") && !event.target.closest(".provider-health-toggle")) {
    closeProviderHealthPopover();
  }
  if (!event.target.closest("#active-sessions-popover") && !event.target.closest(".active-badge")) {
    closeActiveSessionsPopover();
  }
  if (!event.target.closest("#session-route-popover") && !event.target.closest("#session-route-settings")) {
    closeSessionRoutePopover();
  }
  if (!event.target.closest("#usage-history-popover") && !event.target.closest(".provider-token-cell")) {
    closeUsageHistoryPopover();
  }
  if (!event.target.closest("#history-detail-popover") && !event.target.closest(".provider-health-history")) {
    hideHistoryDetail({ force: true });
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && statusUploadModal && !statusUploadModal.hidden) {
    closeStatusUploadModal();
    return;
  }
  if (event.key === "Escape" && !timeRangePopover.hidden) {
    closeTimeRangePopover({ restoreSelection: true });
    return;
  }
  if (event.key === "Escape" && !themeMenu.hidden) {
    setThemeMenuOpen(false);
    themeButton.focus();
  }
  if (event.key === "Escape" && recoveryPopover.classList.contains("show")) {
    hideRecoveryDetails({ force: true });
    recoveryDetailsButton.focus();
  }
  if (event.key === "Escape" && historyDetailPopover.classList.contains("show")) {
    hideHistoryDetail({ force: true });
  } else if (event.key === "Escape" && !sessionRoutePopover.hidden) {
    closeSessionRoutePopover();
    sessionRouteSettingsButton.focus();
  } else if (event.key === "Escape" && activeSessionsPopover.classList.contains("show")) {
    const button = activeSessionsButton;
    closeActiveSessionsPopover();
    button?.focus();
  } else if (event.key === "Escape" && usageHistoryPopover.classList.contains("show")) {
    const button = usageHistoryButton;
    closeUsageHistoryPopover();
    button?.focus();
  } else if (event.key === "Escape" && providerHealthPopover.classList.contains("show")) {
    const button = healthDetailButton;
    closeProviderHealthPopover();
    button?.focus();
  }
});
window.addEventListener("resize", () => {
  if (!timeRangePopover.hidden) positionTimeRangePopover();
  if (!sessionRoutePopover.hidden) positionSessionRoutePopover();
  if (activeSessionsPopover.classList.contains("show")) positionActiveSessionsPopover();
  if (recoveryPopover.classList.contains("show")) positionRecoveryPopover();
  if (providerHealthPopover.classList.contains("show")) positionProviderHealthPopover();
  if (usageHistoryPopover.classList.contains("show")) positionUsageHistoryPopover();
  hideHistoryDetail({ force: true });
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) void readStatus({ quiet: true, force: true });
});
themeMedia.addEventListener("change", () => {
  if (themePreference() === "system") applyTheme("system");
});
async function initialize() {
  await readUiConfig();
  restoreView();
  if (!uiConfig.proxy_url) text("#proxy-url", `${window.location.origin}/v1`);
  applyTheme(themePreference());
  renderHealthSourceStatus();
  readStatus({ force: true });
  readRuntimeSettings({ quiet: true });
  loadUpdateStatus();
  pollTimer = window.setInterval(() => readStatus({ quiet: true }), 1000);
  healthPollTimer = window.setInterval(
    () => readHealthStatus({ quiet: true }),
    30000,
  );
}

void initialize();
