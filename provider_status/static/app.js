"use strict";

const REFRESH_SECONDS = 60;
const overallStateLabels = {
  healthy: "全部模型可用",
  recovering: "服务恢复中",
  degraded: "部分服务异常",
  down: "暂无可用模型",
  unknown: "等待探测结果",
};
const providerStateLabels = {
  healthy: "全部模型可用",
  recovering: "模型恢复中",
  degraded: "部分模型异常",
  down: "当前不可用",
  unknown: "等待首次探测",
};
const monitoredProviderStateLabels = {
  healthy: "已监测模型可用",
  recovering: "已监测模型恢复中",
  degraded: "已监测模型异常",
  down: "已监测模型不可用",
  unknown: "等待已监测模型",
};
const modelStateLabels = {
  healthy: "可用",
  recovering: "恢复中",
  degraded: "待确认",
  down: "暂不可用",
  unknown: "待探测",
  unmonitored: "未监测",
};
const errorCodeLabels = {
  auth_failed: "专用 Key 被拒绝",
  client_blocked: "当前客户端被拒绝",
  no_channel: "供应商无可用线路",
  rate_limited: "频率或额度受限",
  model_unavailable: "供应商未开放该模型",
  upstream_unavailable: "供应商上游服务异常",
  timeout: "请求超时",
  network_error: "无法连接供应商",
  invalid_output: "响应格式异常",
  unknown_error: "未识别的探测错误",
};
const windowLabels = {
  "3h": "3 小时",
  "24h": "24 小时",
  "7d": "7 天",
  "15d": "15 天",
  "30d": "30 天",
};
const PROBE_MODE_MANUAL_ONLY = "manual_only";
let selectedWindow = "24h";
let secondsUntilRefresh = REFRESH_SECONDS;
let refreshTimer = null;
let statusRequestSequence = 0;
let manualPollTimer = null;
let toastTimer = null;
let toastRemoveTimer = null;
let modelPickerLayer = null;

const providerList = document.querySelector("#provider-list");
const loadMessage = document.querySelector("#load-message");
const toastRegion = document.querySelector("#toast-region");
const overallState = document.querySelector("#overall-state");
const overallDot = document.querySelector("#overall-dot");
const overallDetail = document.querySelector("#overall-detail");
const providerCount = document.querySelector("#provider-count");
const updatedAt = document.querySelector("#updated-at");
const countdown = document.querySelector("#refresh-countdown");
const refreshButton = document.querySelector("#refresh-button");
const themeApi = window.codexTheme;
const themeButton = document.querySelector("#theme-button");
const themeMenu = document.querySelector("#theme-menu");
const themePicker = document.querySelector(".theme-picker");
const themeOptions = [...document.querySelectorAll(".theme-option")];
const themeTriggerIcons = [
  ...document.querySelectorAll("[data-theme-trigger-icon]"),
];
const systemThemeStatus = document.querySelector("#system-theme-status");
const systemThemeQuery = typeof window.matchMedia === "function"
  ? window.matchMedia("(prefers-color-scheme: dark)")
  : null;
const themeLabels = {
  light: "浅色模式",
  dark: "深色模式",
  system: "跟随系统",
};
let selectedTheme = themeApi ? themeApi.read() : "system";

let historyDetailLayer = null;
let selectedHistoryBars = null;
let selectedHistoryIndex = -1;
let historyDetailPinned = false;
let historyDetailAnchor = null;

function normalizeTheme(value) {
  if (themeApi) {
    return themeApi.normalize(value);
  }
  return Object.hasOwn(themeLabels, value) ? value : "system";
}

function systemTheme() {
  return systemThemeQuery?.matches ? "dark" : "light";
}

function setThemeOptionTabStop(option) {
  themeOptions.forEach((candidate) => {
    candidate.tabIndex = candidate === option ? 0 : -1;
  });
}

function focusThemeOption(option) {
  if (!option) {
    return;
  }
  setThemeOptionTabStop(option);
  option.focus();
}

function updateThemeUi() {
  const normalized = normalizeTheme(selectedTheme);
  themeTriggerIcons.forEach((icon) => {
    icon.toggleAttribute(
      "hidden",
      icon.dataset.themeTriggerIcon !== normalized,
    );
  });
  themeButton.setAttribute(
    "aria-label",
    `切换主题，当前${themeLabels[normalized]}`,
  );
  themeOptions.forEach((option) => {
    const active = option.dataset.themeValue === normalized;
    option.classList.toggle("is-active", active);
    option.setAttribute("aria-checked", String(active));
  });
  const selectedOption = themeOptions.find(
    (option) => option.dataset.themeValue === normalized,
  );
  const focusedOption = themeOptions.includes(document.activeElement)
    ? document.activeElement
    : null;
  setThemeOptionTabStop(
    !themeMenu.hidden && focusedOption
      ? focusedOption
      : selectedOption || themeOptions[0],
  );
  systemThemeStatus.textContent = `当前系统主题：${
    systemTheme() === "dark" ? "深色" : "浅色"
  }`;
}

function applyThemeSelection(value, { persist = false } = {}) {
  const normalized = normalizeTheme(value);
  selectedTheme = themeApi ? themeApi.apply(normalized) : normalized;
  if (!themeApi) {
    if (normalized === "system") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", normalized);
    }
  }
  if (persist && themeApi) {
    themeApi.save(normalized);
  }
  updateThemeUi();
}

function closeThemeMenu({ restoreFocus = false } = {}) {
  if (!themeMenu.hidden) {
    themeMenu.hidden = true;
    themeButton.setAttribute("aria-expanded", "false");
  }
  if (restoreFocus) {
    themeButton.focus();
  }
}

function openThemeMenu(targetOption = null) {
  closeHistoryDetail();
  themeMenu.hidden = false;
  themeButton.setAttribute("aria-expanded", "true");
  const current = themeOptions.find(
    (option) => option.dataset.themeValue === selectedTheme,
  );
  focusThemeOption(targetOption || current || themeOptions[0]);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function formatAvailability(value) {
  return value === null || value === undefined ? "暂无数据" : `${value.toFixed(2)}%`;
}

function formatLatency(value) {
  if (value === null || value === undefined) {
    return "暂无数据";
  }
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} 毫秒`;
}

function formatTime(value) {
  if (!value) {
    return "暂无数据";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "暂无数据";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function stateDot(state) {
  const dot = element("span", `status-dot status-${state || "unknown"}`);
  dot.setAttribute("aria-hidden", "true");
  return dot;
}

function summarizeModels(providers) {
  const summary = {
    total: 0,
    available: 0,
    recovering: 0,
    pending: 0,
    unavailable: 0,
    unknown: 0,
  };
  for (const provider of providers) {
    for (const model of provider.models || []) {
      summary.total += 1;
      if (model.state === "healthy") {
        summary.available += 1;
      } else if (model.state === "recovering") {
        summary.available += 1;
        summary.recovering += 1;
      } else if (model.state === "degraded") {
        summary.pending += 1;
      } else if (model.state === "down") {
        summary.unavailable += 1;
      } else {
        summary.unknown += 1;
      }
    }
  }
  return summary;
}

function formatModelSummary(summary) {
  if (summary.total === 0) {
    return "暂无已配置模型";
  }
  const details = [`${summary.available} / ${summary.total} 个模型可用`];
  if (summary.recovering > 0) {
    details.push(`${summary.recovering} 个恢复中`);
  }
  if (summary.pending > 0) {
    details.push(`${summary.pending} 个待确认`);
  }
  if (summary.unavailable > 0) {
    details.push(`${summary.unavailable} 个暂时不可用`);
  }
  if (summary.unknown > 0) {
    details.push(`${summary.unknown} 个待探测`);
  }
  return details.join("，");
}

function modelsForDisplay(provider) {
  const models = Array.isArray(provider.models) ? [...provider.models] : [];
  const modelsByName = new Map(models.map((model) => [model.model, model]));
  const manualResults = new Map(
    (provider.manual_probe?.results || []).map((result) => [result.model, result]),
  );
  const displayNames = Array.isArray(provider.display_models)
    && provider.display_models.length > 0
    ? provider.display_models
    : models.map((model) => model.model);
  const displayed = displayNames.map((modelName) => {
    const monitored = modelsByName.get(modelName);
    const manualHistory = Array.isArray(provider.manual_history?.[modelName])
      ? provider.manual_history[modelName].map((result) => ({
          ...result,
          recorded_at: result.finished_at,
          state: result.success ? "healthy" : "down",
        }))
      : [];
    if (monitored) {
      return {
        ...monitored,
        probe_mode: provider.probe_mode,
        manual_history: manualHistory,
      };
    }
    const manualResult = manualResults.get(modelName);
    const latestManual = manualResult || manualHistory[0];
    return {
        model: modelName,
        state: latestManual
          ? latestManual.success ? "healthy" : "down"
          : "unmonitored",
        monitored: false,
        manual: Boolean(latestManual),
        probe_mode: provider.probe_mode,
        availability: null,
        latest_latency: latestManual?.latency_ms ?? null,
        last_checked: latestManual?.finished_at ?? null,
        error_code: latestManual?.error_code ?? null,
        error_summary: manualResult?.error_summary ?? null,
        history: [],
        manual_history: manualHistory,
    };
  });
  const displayedNames = new Set(displayNames);
  for (const model of models) {
    if (!displayedNames.has(model.model)) {
      displayed.push(model);
    }
  }
  return displayed;
}

function metric(label, value) {
  const wrapper = element("div");
  wrapper.append(
    element("span", "metric-label", label),
    element("span", "metric-value", value),
  );
  return wrapper;
}

function historyIndexAtClientX(clientX, rect, count) {
  if (!count || rect.width <= 0) {
    return -1;
  }
  const ratio = Math.min(
    0.999999,
    Math.max(0, (clientX - rect.left) / rect.width),
  );
  return Math.min(count - 1, Math.floor(ratio * count));
}

function errorLabel(errorCode) {
  if (!errorCode) {
    return "";
  }
  return errorCodeLabels[errorCode] || errorCodeLabels.unknown_error;
}

function historyDetailContent(record) {
  const state = record?.state || "unknown";
  const time = record?.recorded_at ? formatTime(record.recorded_at) : "暂无记录";
  const reason = errorLabel(record?.error_code);
  return {
    status: `${time} · ${modelStateLabels[state] || state}`,
    message: reason ? `原因：${reason}` : "",
  };
}

function isCoarsePointer() {
  return window.matchMedia("(pointer: coarse)").matches || window.innerWidth <= 720;
}

function ensureHistoryDetailLayer() {
  if (historyDetailLayer) {
    return historyDetailLayer;
  }
  historyDetailLayer = element("div", "history-detail-layer");
  historyDetailLayer.hidden = true;
  historyDetailLayer.setAttribute("role", "dialog");
  historyDetailLayer.setAttribute("aria-modal", "false");
  historyDetailLayer.setAttribute("aria-label", "历史状态详情");

  const content = element("div", "history-detail-content");
  const title = element("strong", "history-detail-title");
  const status = element("span", "history-detail-status");
  const message = element("p", "history-detail-message");
  const balance = element("span", "history-detail-balance");
  balance.setAttribute("aria-hidden", "true");
  const close = element("button", "history-detail-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "关闭详情");
  close.addEventListener("click", () => closeHistoryDetail());
  content.append(title, status, message);
  historyDetailLayer.append(balance, content, close);
  document.body.append(historyDetailLayer);
  return historyDetailLayer;
}

function clearHistorySelection() {
  if (selectedHistoryBars) {
    selectedHistoryBars
      .querySelectorAll(".history-bar.is-selected")
      .forEach((bar) => bar.classList.remove("is-selected"));
  }
  selectedHistoryBars = null;
  selectedHistoryIndex = -1;
}

function positionHistoryDetail(anchor) {
  if (!historyDetailLayer || historyDetailLayer.hidden || !anchor) {
    return;
  }
  const anchorRect = anchor.getBoundingClientRect();
  const layerRect = historyDetailLayer.getBoundingClientRect();
  const margin = 12;
  const gap = 8;
  const fitsViewport = (top) => (
    top >= margin
    && top + layerRect.height <= window.innerHeight - margin
  );
  let left = anchorRect.left + (anchorRect.width / 2) - (layerRect.width / 2);
  const above = anchorRect.top - layerRect.height - gap;
  const below = anchorRect.bottom + gap;
  let top = isCoarsePointer() && fitsViewport(below) ? below : above;
  if (!fitsViewport(top) && fitsViewport(below)) {
    top = below;
  }
  if (!fitsViewport(top)) {
    historyDetailLayer.style.left = `${margin}px`;
    historyDetailLayer.style.right = `${margin}px`;
    historyDetailLayer.style.top = "auto";
    historyDetailLayer.style.bottom = `${margin}px`;
    return;
  }
  left = Math.min(
    Math.max(margin, left),
    Math.max(margin, window.innerWidth - layerRect.width - margin),
  );
  top = Math.min(
    Math.max(margin, top),
    Math.max(margin, window.innerHeight - layerRect.height - margin),
  );
  historyDetailLayer.style.left = `${left}px`;
  historyDetailLayer.style.right = "auto";
  historyDetailLayer.style.top = `${top}px`;
  historyDetailLayer.style.bottom = "auto";
}

function openHistoryDetail({
  title,
  status = "",
  message = "",
  anchor,
  pinned = false,
}) {
  closeThemeMenu();
  const layer = ensureHistoryDetailLayer();
  const statusNode = layer.querySelector(".history-detail-status");
  const messageNode = layer.querySelector(".history-detail-message");
  layer.querySelector(".history-detail-title").textContent = title;
  statusNode.textContent = status;
  statusNode.hidden = !status;
  messageNode.textContent = message;
  messageNode.hidden = !message;
  historyDetailPinned = pinned;
  historyDetailAnchor = anchor;
  layer.hidden = false;
  layer.classList.toggle("is-mobile", isCoarsePointer());
  positionHistoryDetail(anchor);
}

function closeHistoryDetail() {
  if (historyDetailLayer) {
    historyDetailLayer.hidden = true;
    historyDetailLayer.classList.remove("is-mobile");
  }
  historyDetailPinned = false;
  historyDetailAnchor = null;
  clearHistorySelection();
}

function selectHistoryEntry(bars, index, pinned) {
  if (!bars.historyEntries || index < 0 || index >= bars.historyEntries.length) {
    return;
  }
  clearHistorySelection();
  selectedHistoryBars = bars;
  selectedHistoryIndex = index;
  const selectedBar = bars.children[index];
  selectedBar.classList.add("is-selected");
  const record = bars.historyEntries[index];
  const detail = historyDetailContent(record);
  openHistoryDetail({
    title: `${bars.dataset.modelName} 历史记录`,
    status: detail.status,
    message: detail.message,
    anchor: selectedBar,
    pinned,
  });
}

function renderHistory(history, modelName) {
  const bars = element("div", "history-bars");
  const recent = Array.isArray(history) ? history.slice(0, 60).reverse() : [];
  const missing = Math.max(0, 60 - recent.length);
  bars.historyEntries = [
    ...Array.from({ length: missing }, () => null),
    ...recent,
  ];
  bars.dataset.modelName = modelName;
  bars.tabIndex = 0;
  bars.setAttribute("role", "button");
  bars.setAttribute("aria-haspopup", "dialog");
  bars.setAttribute("aria-label", `${modelName} 最近 60 次状态，点击查看详情`);

  for (const item of bars.historyEntries) {
    const state = item?.state || "unknown";
    bars.append(element("span", `history-bar state-bar-${state}`));
  }

  bars.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || historyDetailPinned) {
      return;
    }
    const index = historyIndexAtClientX(
      event.clientX,
      bars.getBoundingClientRect(),
      bars.historyEntries.length,
    );
    selectHistoryEntry(bars, index, false);
  });
  bars.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse" && !historyDetailPinned) {
      closeHistoryDetail();
    }
  });
  bars.addEventListener("click", (event) => {
    const index = historyIndexAtClientX(
      event.clientX,
      bars.getBoundingClientRect(),
      bars.historyEntries.length,
    );
    selectHistoryEntry(bars, index, true);
  });
  bars.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Enter", " ", "Escape"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    if (event.key === "Escape") {
      closeHistoryDetail();
      return;
    }
    const lastIndex = bars.historyEntries.length - 1;
    const currentIndex = selectedHistoryBars === bars
      ? selectedHistoryIndex
      : lastIndex;
    const nextIndex = event.key === "ArrowLeft"
      ? Math.max(0, currentIndex - 1)
      : event.key === "ArrowRight"
        ? Math.min(lastIndex, currentIndex + 1)
        : currentIndex;
    selectHistoryEntry(bars, nextIndex, true);
  });
  return bars;
}

function renderUnmonitoredHistory(modelName, description = "未监测，无历史记录") {
  const bars = element("div", "history-bars history-bars-static");
  bars.setAttribute("aria-label", `${modelName} ${description}`);
  for (let index = 0; index < 60; index += 1) {
    bars.append(element("span", "history-bar state-bar-unknown"));
  }
  return bars;
}

function modelStrip(model) {
  const monitored = model.monitored !== false;
  const manualMode = model.probe_mode === PROBE_MODE_MANUAL_ONLY;
  const manualResult = !monitored && model.manual === true;
  const manualHistory = Array.isArray(model.manual_history)
    ? model.manual_history
    : [];
  const strip = element("div", "model-strip");
  const summary = element("div", "model-strip-summary");
  const identity = element("div", "model-strip-identity");
  identity.append(stateDot(model.state), element("strong", null, model.model));

  const stateText = manualMode
    ? manualResult
      ? model.state === "healthy" ? "上次可用" : "上次不可用"
      : "尚未检测"
    : manualResult
      ? model.state === "healthy" ? "手动检测可用" : "手动检测不可用"
    : modelStateLabels[model.state] || model.state;
  const state = element(
    "span",
    "model-strip-state",
    stateText,
  );
  const reason = monitored
    ? errorLabel(model.error_code)
    : manualResult
      ? errorLabel(model.error_code) || (manualMode ? "" : "未加入定时监测")
      : manualMode ? "" : "本站未启用该模型探测";
  const stateCopy = element("div", "model-strip-status");
  stateCopy.append(state);
  if (reason) {
    stateCopy.append(element("span", "model-strip-reason", reason));
  }
  const stateActions = element("div", "model-strip-actions");
  stateActions.append(stateCopy);
  if ((monitored || manualResult) && (model.error_summary || reason)) {
    const detailButton = element("button", "model-detail-button", "详情");
    detailButton.type = "button";
    detailButton.setAttribute("aria-label", `查看 ${model.model} 当前错误详情`);
    detailButton.addEventListener("click", (event) => {
      event.stopPropagation();
      clearHistorySelection();
      openHistoryDetail({
        title: `${model.model} 当前详情`,
        status: `状态：${stateText}`,
        message: [
          `原因：${reason || errorCodeLabels.unknown_error}`,
          model.error_summary,
        ].filter(Boolean).join("\n"),
        anchor: detailButton,
        pinned: true,
      });
    });
    stateActions.append(detailButton);
  }
  const metrics = element("div", "model-strip-metrics");
  metrics.append(
    element(
      "span",
      null,
      manualMode
        ? `最近结果 ${manualResult ? model.state === "healthy" ? "可用" : "不可用" : "—"}`
        : `可用率 ${monitored ? formatAvailability(model.availability) : "—"}`,
    ),
    element(
      "span",
      null,
      `延迟 ${monitored || manualResult ? formatLatency(model.latest_latency) : "—"}`,
    ),
  );
  summary.append(identity, stateActions, metrics);

  const history = element("div", "model-strip-history");
  const historyHeader = element("div", "history-header model-history-header");
  const showsManualHistory = !monitored && (manualMode || manualHistory.length > 0);
  historyHeader.append(
    element("span", null, showsManualHistory ? "最近点击检测" : "最近 60 次"),
    element("span", null, "较早 → 最近"),
  );
  history.append(
    historyHeader,
    monitored
      ? renderHistory(model.history, model.model)
      : manualHistory.length > 0
        ? renderHistory(manualHistory, model.model)
        : renderUnmonitoredHistory(
            model.model,
            manualMode ? "尚无点击检测历史" : "未监测，无历史记录",
          ),
  );
  strip.append(summary, history);
  return strip;
}

function manualProbeButton(provider) {
  const button = element("button", "manual-probe-button", "立即检测");
  button.type = "button";
  button.setAttribute("aria-label", `立即检测 ${provider.name} 的全部展示模型`);
  const job = provider.manual_probe;
  if (job?.status === "queued") {
    button.textContent = "已排队";
    button.disabled = true;
  } else if (job?.status === "running") {
    const total = job.total_models || 2;
    const current = Math.min(total, (job.completed_models || 0) + 1);
    button.textContent = `检测 ${current}/${total}`;
    button.disabled = true;
  }
  button.addEventListener("click", () => {
    const models = Array.isArray(provider.display_models) ? provider.display_models : [];
    if (models.length <= 1) {
      submitManualProbe(provider, button, models);
      return;
    }
    showModelPicker(provider, button, models);
  });
  return button;
}

function closeModelPicker({ restoreFocus = false } = {}) {
  if (!modelPickerLayer) return;
  const trigger = modelPickerLayer.trigger;
  modelPickerLayer.remove();
  modelPickerLayer = null;
  if (restoreFocus) trigger.focus();
}

function showModelPicker(provider, button, models) {
  closeModelPicker();
  const layer = element("div", "model-picker-layer");
  const picker = element("div", "model-picker");
  picker.setAttribute("role", "menu");
  picker.setAttribute("aria-label", `选择要检测的 ${provider.name} 模型`);
  picker.append(element("strong", "model-picker-title", "选择检测模型"));
  const choices = [
    ...models.map((model) => ({ label: model, models: [model] })),
    { label: "全部模型", models },
  ];
  for (const choice of choices) {
    const option = element("button", "model-picker-option", choice.label);
    option.type = "button";
    option.setAttribute("role", "menuitem");
    option.addEventListener("click", () => {
      closeModelPicker();
      submitManualProbe(provider, button, choice.models);
    });
    picker.append(option);
  }
  layer.append(picker);
  layer.trigger = button;
  layer.addEventListener("pointerdown", (event) => {
    if (event.target === layer) closeModelPicker({ restoreFocus: true });
  });
  document.body.append(layer);
  modelPickerLayer = layer;
  const rect = button.getBoundingClientRect();
  picker.style.setProperty("--picker-left", `${Math.max(12, rect.right - 220)}px`);
  const pickerTop = Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - 170));
  picker.style.setProperty("--picker-top", `${pickerTop}px`);
  picker.querySelector("button")?.focus();
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && modelPickerLayer) {
    closeModelPicker({ restoreFocus: true });
  }
});

function dismissToast() {
  if (toastTimer !== null) {
    window.clearTimeout(toastTimer);
    toastTimer = null;
  }
  if (toastRemoveTimer !== null) {
    window.clearTimeout(toastRemoveTimer);
    toastRemoveTimer = null;
  }
  const toast = toastRegion.firstElementChild;
  if (!toast) {
    return;
  }
  toast.classList.remove("is-visible");
  toast.classList.add("is-leaving");
  toastRemoveTimer = window.setTimeout(() => {
    toastRemoveTimer = null;
    toast.remove();
  }, 180);
}

function showToast({ title, message, tone = "success", duration = 4000 }) {
  if (toastTimer !== null) {
    window.clearTimeout(toastTimer);
  }
  if (toastRemoveTimer !== null) {
    window.clearTimeout(toastRemoveTimer);
    toastRemoveTimer = null;
  }
  toastRegion.replaceChildren();

  const toast = element("div", `toast toast-${tone}`);
  toast.setAttribute("role", tone === "error" ? "alert" : "status");

  const icon = element(
    "span",
    "toast-icon",
    tone === "success" ? "✓" : tone === "warning" ? "!" : "×",
  );
  icon.setAttribute("aria-hidden", "true");

  const copy = element("div", "toast-copy");
  copy.append(
    element("strong", "toast-title", title),
    element("span", "toast-message", message),
  );

  const closeButton = element("button", "toast-close", "×");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "关闭提示");
  closeButton.addEventListener("click", dismissToast);

  toast.append(icon, copy, closeButton);
  toastRegion.append(toast);
  window.requestAnimationFrame(() => toast.classList.add("is-visible"));
  toastTimer = window.setTimeout(dismissToast, duration);
}

async function submitManualProbe(provider, button, models) {
  button.disabled = true;
  button.textContent = "正在提交";
  try {
    const response = await fetch(
      `api/manual-probes/${encodeURIComponent(provider.provider_id)}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ models }),
        cache: "no-store",
      },
    );
    if (!response.ok) {
      if (response.status === 403) {
        throw Object.assign(new Error("服务器拒绝了立即检测请求"), {
          toastTitle: "提交失败",
          toastTone: "error",
        });
      }
      if (response.status === 429) {
        const retryAfter = Number.parseInt(response.headers.get("Retry-After"), 10);
        const retryMessage = Number.isFinite(retryAfter)
          ? `该供应商刚刚检测过，请 ${retryAfter} 秒后再试`
          : "该供应商刚刚检测过，请稍后再试";
        throw Object.assign(new Error(retryMessage), {
          toastTitle: "请稍后再试",
          toastTone: "warning",
        });
      }
      throw new Error(`立即检测请求失败（HTTP ${response.status}）`);
    }
    button.textContent = "已排队";
    showToast({
      title: "已进入优先检测队列",
      message: models.length === 1
        ? `${provider.name} · ${models[0]} 正在等待检测`
        : `${provider.name} · ${models.length} 个模型正在等待检测`,
    });
    await loadStatus({ quiet: true });
  } catch (error) {
    button.disabled = false;
    button.textContent = "立即检测";
    showToast({
      title: error?.toastTitle || "提交失败",
      message: error instanceof Error ? error.message : "立即检测请求失败",
      tone: error?.toastTone || "error",
      duration: 6000,
    });
  }
}

function scheduleManualPoll(hasActiveJobs) {
  if (manualPollTimer !== null) {
    window.clearTimeout(manualPollTimer);
    manualPollTimer = null;
  }
  if (!hasActiveJobs) {
    return;
  }
  manualPollTimer = window.setTimeout(() => {
    manualPollTimer = null;
    loadStatus({ quiet: true });
  }, 1500);
}

function manualProviderSummary(provider) {
  const job = provider.manual_probe;
  if (job?.status === "queued") {
    return { state: "unknown", label: "等待检测", result: "等待中" };
  }
  if (job?.status === "running") {
    return { state: "recovering", label: "正在检测", result: "检测中" };
  }
  const results = Array.isArray(job?.results) ? job.results : [];
  if (results.length === 0) {
    return job?.status === "failed"
      ? { state: "down", label: "上次检测失败", result: "失败" }
      : { state: "unknown", label: "等待点击检测", result: "—" };
  }
  const successes = results.filter((result) => result.success).length;
  if (successes === results.length) {
    return { state: "healthy", label: "上次检测可用", result: "可用" };
  }
  if (successes > 0) {
    return { state: "degraded", label: "上次部分可用", result: "部分可用" };
  }
  return { state: "down", label: "上次检测不可用", result: "不可用" };
}

function latestManualResult(provider) {
  const results = Array.isArray(provider.manual_probe?.results)
    ? provider.manual_probe.results
    : [];
  return [...results].sort((left, right) => (
    String(right.finished_at || "").localeCompare(String(left.finished_at || ""))
  ))[0] || null;
}

function providerCard(provider, responseWindow) {
  const manualMode = provider.probe_mode === PROBE_MODE_MANUAL_ONLY;
  const manualSummary = manualMode ? manualProviderSummary(provider) : null;
  const displayModels = modelsForDisplay(provider);
  const hasUnmonitoredModels = displayModels.some(
    (model) => model.monitored === false,
  );
  const card = element("article", "provider-card");
  const main = element("div", "provider-card-main");
  const top = element("div", "provider-topline");
  const identity = element("div");
  const nameRow = element("div", "provider-name-row");
  const modeBadge = element(
    "span",
    "probe-mode-badge",
    manualMode ? "点击检测" : "自动检测",
  );
  nameRow.append(
    stateDot(manualMode ? manualSummary.state : provider.state),
    element("h3", null, provider.name),
    modeBadge,
  );
  identity.append(nameRow, element("p", "provider-url", provider.base_url));

  const stateLabel = element(
    "div",
    "state-label",
    manualMode
      ? manualSummary.label
      : hasUnmonitoredModels
      ? monitoredProviderStateLabels[provider.state] || provider.state
      : providerStateLabels[provider.state] || provider.state,
  );
  const providerActions = element("div", "provider-card-actions");
  providerActions.append(stateLabel, manualProbeButton(provider));
  top.append(identity, providerActions);

  const metrics = element("div", "metrics");
  if (manualMode) {
    const latestResult = latestManualResult(provider);
    metrics.append(
      metric("上次检测结果", manualSummary.result),
      metric("最近延迟", formatLatency(latestResult?.latency_ms)),
      metric("检测模型", `${displayModels.length} 个`),
    );
  } else {
    metrics.append(
      metric(
        `${windowLabels[responseWindow] || responseWindow}可用率`,
        formatAvailability(provider.availability),
      ),
      metric("最近延迟", formatLatency(provider.latest_latency)),
      metric("监测模型", `${provider.model_count} 个`),
    );
  }

  const modelStrips = element("div", "model-strip-list");
  modelStrips.append(...displayModels.map(modelStrip));
  main.append(top, metrics, modelStrips);

  const footer = element("div", "provider-card-footer");
  const lastChecked = manualMode
    ? provider.manual_probe?.finished_at
      || provider.manual_probe?.started_at
      || provider.manual_probe?.requested_at
    : provider.last_checked;
  footer.append(element("span", null, `最后探测：${formatTime(lastChecked)}`));
  card.append(main, footer);
  return card;
}

function renderProviders(providers, responseWindow) {
  closeHistoryDetail();
  if (!providers.length) {
    providerList.replaceChildren(element("p", "empty-state", "还没有可展示的供应商数据。"));
    return;
  }
  providerList.replaceChildren(
    ...providers.map((provider) => providerCard(provider, responseWindow)),
  );
}

async function loadStatus({ quiet = false } = {}) {
  const requestSequence = statusRequestSequence + 1;
  statusRequestSequence = requestSequence;
  const requestedWindow = selectedWindow;
  refreshButton.disabled = true;
  if (!quiet) {
    loadMessage.textContent = "正在刷新状态…";
  }
  try {
    const response = await fetch(`api/status?window=${requestedWindow}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    if (requestSequence !== statusRequestSequence) {
      return;
    }
    const state = payload.overall_state || "unknown";
    const modelSummary = summarizeModels(payload.providers);
    if (payload.data_status === "stale") {
      overallState.textContent = "数据已过期";
      overallDetail.textContent = "最近一次探测结果已超过更新时限";
      overallDot.className = "status-dot status-unknown";
    } else {
      overallState.textContent = overallStateLabels[state] || state;
      overallDetail.textContent = formatModelSummary(modelSummary);
      overallDot.className = `status-dot status-${state}`;
    }
    providerCount.textContent = `${payload.providers.length} 个供应商`;
    updatedAt.textContent = payload.last_checked
      ? `最后探测于 ${formatTime(payload.last_checked)}`
      : "等待首次探测";
    renderProviders(payload.providers, payload.window);
    const hasActiveJobs = payload.providers.some(
      (provider) => ["queued", "running"].includes(provider.manual_probe?.status),
    );
    scheduleManualPoll(hasActiveJobs);
    if (!quiet || !hasActiveJobs) {
      loadMessage.textContent = payload.data_status === "stale"
        ? "探测数据已过期，服务正在等待新结果"
        : "状态数据已更新";
    }
    secondsUntilRefresh = REFRESH_SECONDS;
  } catch (error) {
    if (requestSequence !== statusRequestSequence) {
      return;
    }
    overallState.textContent = "服务连接异常";
    overallDetail.textContent = "无法读取最新状态，将在下一次刷新时重试";
    overallDot.className = "status-dot status-down";
    loadMessage.textContent = "状态服务暂时无法访问";
    providerList.replaceChildren(element("p", "error-state", "读取失败，将在下一次自动刷新时重试。"));
  } finally {
    if (requestSequence === statusRequestSequence) {
      refreshButton.disabled = false;
      updateCountdown();
    }
  }
}

function updateCountdown() {
  countdown.textContent = `${secondsUntilRefresh} 秒后自动刷新`;
}

function startRefreshTimer() {
  if (refreshTimer !== null) {
    window.clearInterval(refreshTimer);
  }
  refreshTimer = window.setInterval(() => {
    secondsUntilRefresh -= 1;
    if (secondsUntilRefresh <= 0) {
      secondsUntilRefresh = REFRESH_SECONDS;
      loadStatus();
    }
    updateCountdown();
  }, 1000);
}

document.querySelectorAll(".window-button").forEach((button) => {
  button.addEventListener("click", () => {
    selectedWindow = button.dataset.window;
    document.querySelectorAll(".window-button").forEach((candidate) => {
      candidate.classList.toggle("is-active", candidate === button);
    });
    loadStatus();
  });
});

themeButton.addEventListener("click", () => {
  if (themeMenu.hidden) {
    openThemeMenu();
  } else {
    closeThemeMenu({ restoreFocus: true });
  }
});
themeButton.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const target = event.key === "ArrowUp"
      ? themeOptions.at(-1)
      : themeOptions[0];
    openThemeMenu(target);
  }
});
themeOptions.forEach((option) => {
  option.addEventListener("click", () => {
    applyThemeSelection(option.dataset.themeValue, { persist: true });
    closeThemeMenu({ restoreFocus: true });
  });
});
themeMenu.addEventListener("keydown", (event) => {
  const currentIndex = themeOptions.indexOf(document.activeElement);
  let nextIndex = currentIndex;
  if (event.key === "ArrowDown") {
    nextIndex = (currentIndex + 1) % themeOptions.length;
  } else if (event.key === "ArrowUp") {
    nextIndex = (currentIndex - 1 + themeOptions.length) % themeOptions.length;
  } else if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = themeOptions.length - 1;
  } else if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeThemeMenu({ restoreFocus: true });
    return;
  } else {
    return;
  }
  event.preventDefault();
  focusThemeOption(themeOptions[nextIndex]);
});
themePicker.addEventListener("focusout", (event) => {
  const nextTarget = event.relatedTarget;
  if (nextTarget instanceof Node && themeMenu.contains(nextTarget)) {
    return;
  }
  closeThemeMenu();
});
if (systemThemeQuery) {
  const handleSystemThemeChange = () => updateThemeUi();
  if (typeof systemThemeQuery.addEventListener === "function") {
    systemThemeQuery.addEventListener("change", handleSystemThemeChange);
  } else if (typeof systemThemeQuery.addListener === "function") {
    systemThemeQuery.addListener(handleSystemThemeChange);
  }
}

refreshButton.addEventListener("click", loadStatus);
document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!themeMenu.hidden && (!target || !themePicker.contains(target))) {
    closeThemeMenu();
  }
  if (!historyDetailLayer || historyDetailLayer.hidden) {
    return;
  }
  if (!target) {
    closeHistoryDetail();
    return;
  }
  if (
    historyDetailLayer.contains(target)
    || target.closest(".history-bars:not(.history-bars-static)")
    || target.closest(".model-detail-button")
  ) {
    return;
  }
  closeHistoryDetail();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!themeMenu.hidden) {
      closeThemeMenu({ restoreFocus: true });
      return;
    }
    if (historyDetailLayer && !historyDetailLayer.hidden) {
      closeHistoryDetail();
    }
  }
});
window.addEventListener("resize", () => {
  if (
    historyDetailLayer
    && !historyDetailLayer.hidden
    && historyDetailAnchor
  ) {
    historyDetailLayer.classList.toggle("is-mobile", isCoarsePointer());
    positionHistoryDetail(historyDetailAnchor);
  }
});
window.addEventListener("scroll", () => {
  if (
    historyDetailLayer
    && !historyDetailLayer.hidden
    && historyDetailAnchor
  ) {
    positionHistoryDetail(historyDetailAnchor);
  }
}, { passive: true });
applyThemeSelection(selectedTheme);
loadStatus();
startRefreshTimer();
