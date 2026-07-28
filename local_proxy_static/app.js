"use strict";

const CONTROL_HEADER = { "X-Local-Proxy-Control": "1" };
let latestStatus = null;
let latestHealthStatus = null;
let healthStatusError = null;
let pollTimer = null;
let healthPollTimer = null;
let toastTimer = null;
let renderedListSignature = null;
let statusRequestSequence = 0;
let healthRequestSequence = 0;
let controlRequestActive = false;
let healthRequestActive = false;
let retryFormLoaded = false;
let recoveryDetailsPinned = false;
let recoveryHideTimer = null;
let manageProvidersMode = false;
let draggedProviderId = null;
let healthDetailButton = null;
let historyDetailPinned = false;

const providerList = document.querySelector("#provider-list");
const emptyState = document.querySelector("#empty-state");
const searchInput = document.querySelector("#search");
const usageWindow = document.querySelector("#usage-window");
const manageProvidersButton = document.querySelector("#manage-providers");
const healthSource = document.querySelector("#health-source");
const healthSourceDot = document.querySelector("#health-source-dot");
const healthSourceText = document.querySelector("#health-source-text");
const healthRefreshButton = document.querySelector("#health-refresh-button");
const footerMessage = document.querySelector("#footer-message");
const retryForm = document.querySelector("#retry-form");
const themeButton = document.querySelector("#theme-button");
const themeMenu = document.querySelector("#theme-menu");
const recovery = document.querySelector("#recovery");
const recoveryDetailsButton = document.querySelector("#recovery-details-button");
const recoveryPopover = document.querySelector("#recovery-popover");
const recoveryErrorList = document.querySelector("#recovery-error-list");
const providerHealthPopover = document.querySelector("#provider-health-popover");
const historyDetailPopover = document.querySelector("#history-detail-popover");
const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
const THEME_STORAGE_KEY = "codex-local-proxy-theme";

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
      window.localStorage.setItem(THEME_STORAGE_KEY, preference);
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
  window.clearTimeout(recoveryHideTimer);
  if (pinned) recoveryDetailsPinned = true;
  recoveryPopover.classList.add("show");
  recoveryDetailsButton.setAttribute("aria-expanded", "true");
  positionRecoveryPopover();
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

function formatRetryTime(value) {
  const recordedAt = new Date(Number(value));
  return Number.isNaN(recordedAt.getTime())
    ? "刚刚"
    : recordedAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderRecoveryErrors(retry) {
  const recentErrors = Array.isArray(retry.recent_errors)
    ? retry.recent_errors.slice(0, 5)
    : [];
  const hasDetails = recentErrors.length > 0;
  recovery.classList.toggle("has-details", hasDetails);
  recoveryDetailsButton.hidden = !hasDetails;
  if (!hasDetails) hideRecoveryDetails({ force: true });
  recoveryErrorList.replaceChildren();
  for (const error of recentErrors) {
    const providerName = latestStatus?.providers?.find(
      (provider) => provider.provider_id === error.provider_id,
    )?.name;
    const item = document.createElement("li");
    const meta = document.createElement("span");
    meta.className = "recovery-error-meta";
    meta.textContent = [
      formatRetryTime(error.recorded_at),
      providerName,
      `第 ${error.attempt} 次请求`,
    ].filter(Boolean).join(" · ");
    const summary = document.createElement("span");
    summary.className = "recovery-error-summary";
    summary.textContent = error.summary || "上游临时错误";
    item.append(meta, summary);
    recoveryErrorList.append(item);
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
    : "临时错误将直接返回 Codex";
}

function switchView(viewName) {
  for (const button of document.querySelectorAll(".view-tab")) {
    const active = button.dataset.view === viewName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  document.querySelector("#providers-view").hidden = viewName !== "providers";
  document.querySelector("#settings-view").hidden = viewName !== "settings";
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

function providerUsage(providerId) {
  return latestStatus?.usage?.by_provider?.[providerId] || {
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cached_tokens: 0,
    estimated_requests: 0,
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

function healthStatusForProvider(provider) {
  if (!Array.isArray(latestHealthStatus?.providers)) return null;
  const endpoint = normalizeProviderEndpoint(provider.endpoint);
  return latestHealthStatus.providers.find(
    (candidate) => normalizeProviderEndpoint(candidate?.base_url) === endpoint,
  ) || null;
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
      provider.hidden,
      providerUsage(provider.provider_id).total_tokens,
      providerUsage(provider.provider_id).estimated_requests,
      healthStatusForProvider(provider)?.last_checked,
    ]),
  ]);
  if (signature === renderedListSignature) return;
  renderedListSignature = signature;
  closeProviderHealthPopover();
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

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "provider-copy";
    copy.disabled = manageProvidersMode;
    copy.setAttribute(
      "aria-label",
      (provider.current ? "当前供应商 " : "切换到 ") + provider.name,
    );
    copy.setAttribute("aria-pressed", String(provider.current));
    const title = document.createElement("span");
    title.className = "provider-title";
    const name = document.createElement("strong");
    name.textContent = escapeText(provider.name);
    title.append(name);
    if (provider.active_requests > 0) {
      const active = document.createElement("span");
      active.className = "active-badge";
      active.textContent = `${provider.active_requests} 个请求`;
      title.append(active);
    }
    const endpoint = document.createElement("code");
    endpoint.textContent = escapeText(provider.endpoint);
    const usage = providerUsage(provider.provider_id);
    copy.append(title, endpoint);

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

    const tokenCell = document.createElement("span");
    tokenCell.className = "provider-token-cell";
    if (Number(usage.total_tokens || 0) > 0) {
      const tokenValue = document.createElement("strong");
      tokenValue.className = "provider-token-value";
      tokenValue.textContent = formatTokenCount(usage.total_tokens);
      const tokenUnit = document.createElement("span");
      tokenUnit.className = "provider-token-unit";
      tokenUnit.textContent = "Token";
      tokenCell.append(tokenValue, tokenUnit);
    } else {
      const tokenZero = document.createElement("span");
      tokenZero.className = "provider-token-zero";
      tokenZero.textContent = "—";
      tokenCell.append(tokenZero);
    }
    if (usage.estimated_requests > 0) tokenCell.title = `含 ${usage.estimated_requests} 个估算请求`;

    const meta = document.createElement("span");
    meta.className = "provider-meta";
    const auth = document.createElement("span");
    auth.className = `auth-label${provider.has_credentials ? "" : " missing"}`;
    auth.textContent = provider.has_credentials ? "已配置" : "缺失";
    meta.append(auth);
    if (manageProvidersMode) {
      const visibility = document.createElement("button");
      visibility.type = "button";
      visibility.className = "visibility-button";
      visibility.textContent = provider.hidden ? "恢复" : "隐藏";
      visibility.disabled = provider.current;
      visibility.title = provider.current ? "请先切换供应商再隐藏" : visibility.textContent;
      visibility.addEventListener("click", (event) => {
        event.stopPropagation();
        setProviderHidden(provider, !provider.hidden);
      });
      meta.append(visibility);
    }
    row.append(state, copy, healthCell, tokenCell, meta);
    copy.addEventListener("click", (event) => {
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
  emptyState.hidden = providers.length > 0;
  const visibleCount = latestStatus.providers.filter((provider) => !provider.hidden).length;
  const hiddenCount = latestStatus.providers.length - visibleCount;
  document.querySelector("#provider-count").textContent = query
    ? `找到 ${providers.length} 个供应商`
    : manageProvidersMode
      ? `共 ${latestStatus.providers.length} 个供应商，已隐藏 ${hiddenCount} 个`
      : `已显示 ${visibleCount} 个 Codex API 供应商`;
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
  document.querySelector("#auth-state").textContent = current?.has_credentials ? "已安全读取" : "缺失";
  document.querySelector("#wire-api").textContent = current?.wire_api === "responses" ? "Responses · SSE" : escapeText(current?.wire_api || "—");
  const last = status.last_status_code;
  document.querySelector("#last-request").textContent = last == null
    ? "尚无请求"
    : `${last >= 200 && last < 400 ? "成功" : "失败"} · HTTP ${last}`;

  const retry = status.retry || {};
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
    recoveryDetail.textContent = `已拦截：${activeRecovery.summary || "上游临时错误"}；等待 ${activeRecovery.delay_seconds} 秒后重试。`;
  } else if (openCircuit) {
    recoveryTitle.textContent = "当前供应商短暂熔断";
    recoveryDetail.textContent = `约 ${Math.ceil(openCircuit.retry_after_seconds)} 秒后恢复接收新请求。`;
  } else if (retry.total_retries > 0) {
    recoveryTitle.textContent = `自动恢复已就绪 · 已拦截 ${retry.total_retries} 次`;
    const latestError = retry.recent_errors?.[0]?.summary;
    recoveryDetail.textContent = latestError
      ? `最近一次：${latestError}`
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
    (provider) => !provider.current && provider.active_requests > 0,
  );
  const draining = document.querySelector("#draining");
  const drainingTitle = draining.querySelector("strong");
  const drainingDetail = draining.querySelector("span");
  if (drainingProviders.length > 0) {
    drainingTitle.textContent = "旧请求仍在完成";
    drainingDetail.textContent = drainingProviders
      .map((provider) => `${provider.name}：${provider.active_requests} 个`)
      .join("，");
  } else {
    drainingTitle.textContent = "没有旧请求正在处理";
    drainingDetail.textContent = "切换不会中断已经开始的流式响应。";
  }
  renderProviderList();
}

async function readStatus({ quiet = false } = {}) {
  if (controlRequestActive) return;
  const requestSequence = ++statusRequestSequence;
  try {
    const response = await fetch(
      `/control/api/status?usage_window=${encodeURIComponent(usageWindow.value)}`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (requestSequence === statusRequestSequence) renderStatus(status);
  } catch (error) {
    footerMessage.textContent = "无法连接本地中转，服务可能已经退出";
    if (!quiet) showToast("连接失败", "无法读取本地中转状态。", "error");
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
    const response = await fetch(configuredUrl, {
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
    const response = await fetch(
      `/control/api/providers/${encodeURIComponent(provider.provider_id)}/visibility?usage_window=${encodeURIComponent(usageWindow.value)}`,
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
    const response = await fetch(
      `/control/api/providers/order?usage_window=${encodeURIComponent(usageWindow.value)}`,
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

function toggleProviderManagement() {
  manageProvidersMode = !manageProvidersMode;
  manageProvidersButton.setAttribute("aria-pressed", String(manageProvidersMode));
  manageProvidersButton.textContent = manageProvidersMode ? "完成管理" : "管理列表";
  document.querySelector("#manage-hint").hidden = !manageProvidersMode;
  renderedListSignature = null;
  renderProviderList();
}

async function selectProvider(provider) {
  if (provider.current) return;
  controlRequestActive = true;
  const requestSequence = ++statusRequestSequence;
  try {
    const response = await fetch(
      `/control/api/providers/${encodeURIComponent(provider.provider_id)}/select?usage_window=${encodeURIComponent(usageWindow.value)}`,
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
    const response = await fetch(`/control/api/refresh?usage_window=${encodeURIComponent(usageWindow.value)}`, {
      method: "POST",
      headers: CONTROL_HEADER,
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (requestSequence === statusRequestSequence) renderStatus(status);
    showToast("已重新读取 CC Switch", `供应商列表已更新，共 ${latestStatus.providers.length} 个。`);
  } catch (error) {
    showToast("刷新失败", "无法读取 CC Switch 数据库。", "error");
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
    const response = await fetch("/control/api/retry-policy", {
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

async function copyConfig() {
  try {
    const response = await fetch("/control/api/codex-config", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await navigator.clipboard.writeText(await response.text());
    showToast("Codex 配置已复制", "首次配置后重启一次 Codex，后续切换不再需要重启。");
  } catch (error) {
    showToast("复制失败", "浏览器没有允许写入剪贴板。", "error");
  }
}

async function shutdownProxy() {
  if (!window.confirm("退出本地中转后，Codex 新请求将无法连接。确定退出吗？")) return;
  try {
    const response = await fetch("/control/api/shutdown", {
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

searchInput.addEventListener("input", renderProviderList);
usageWindow.addEventListener("change", () => {
  renderedListSignature = null;
  readStatus();
});
manageProvidersButton.addEventListener("click", toggleProviderManagement);
healthRefreshButton.addEventListener("click", () => readHealthStatus());
document.querySelector("#refresh-button").addEventListener("click", refreshProviders);
document.querySelector("#copy-config").addEventListener("click", copyConfig);
document.querySelector("#shutdown-button").addEventListener("click", shutdownProxy);
for (const button of document.querySelectorAll(".view-tab")) {
  button.addEventListener("click", () => switchView(button.dataset.view));
}
for (const control of retryForm.querySelectorAll("input, select")) {
  control.addEventListener("change", renderSettingsSummary);
}
retryForm.addEventListener("submit", saveRetrySettings);
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
  if (!event.target.closest(".theme-control")) setThemeMenuOpen(false);
  if (!event.target.closest("#recovery") && !event.target.closest("#recovery-popover")) {
    hideRecoveryDetails({ force: true });
  }
  if (!event.target.closest("#provider-health-popover") && !event.target.closest(".provider-health-toggle")) {
    closeProviderHealthPopover();
  }
  if (!event.target.closest("#history-detail-popover") && !event.target.closest(".provider-health-history")) {
    hideHistoryDetail({ force: true });
  }
});
document.addEventListener("keydown", (event) => {
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
  } else if (event.key === "Escape" && providerHealthPopover.classList.contains("show")) {
    const button = healthDetailButton;
    closeProviderHealthPopover();
    button?.focus();
  }
});
window.addEventListener("resize", () => {
  if (recoveryPopover.classList.contains("show")) positionRecoveryPopover();
  if (providerHealthPopover.classList.contains("show")) positionProviderHealthPopover();
  hideHistoryDetail({ force: true });
});
themeMedia.addEventListener("change", () => {
  if (themePreference() === "system") applyTheme("system");
});
document.querySelector("#proxy-url").textContent = `${window.location.origin}/v1`;

applyTheme(themePreference());
renderHealthSourceStatus();
readStatus();
pollTimer = window.setInterval(() => readStatus({ quiet: true }), 1000);
healthPollTimer = window.setInterval(
  () => readHealthStatus({ quiet: true }),
  30000,
);
