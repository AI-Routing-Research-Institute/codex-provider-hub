"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const root = path.join(__dirname, "..", "proxy_static");
const entry = fs.readFileSync(path.join(root, "src", "classic-features.js"), "utf8");
const source = fs.readFileSync(path.join(root, "classic", "app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "classic", "index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "src", "classic-features.css"), "utf8");

function eventTarget(properties = {}) {
  const listeners = new Map();
  return {
    ...properties,
    listeners,
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); },
    fire(name) { listeners.get(name)?.(); },
  };
}

function fixture({ enabled = true, active = false, hostExists = true } = {}) {
  const nodes = {
    '[data-view="usage"]': { hidden: !enabled },
    "#classic-usage-view": { hidden: !active },
    "#providers-view": { hidden: active },
    "#classic-features-host": hostExists ? { dataset: {} } : null,
    "#usage-share-button": eventTarget({ hidden: !enabled, disabled: true, isConnected: true, focus() { this.focused = true; } }),
    ".classic-feature-loading": { remove() { this.removed = true; } },
  };
  const document = { querySelector: selector => nodes[selector] || null };
  const events = eventTarget();
  const mounted = [];
  const unmounted = [];
  let render;
  const context = vm.createContext({
    document, window: events,
    createApp(component) { return { mount() { render = component.setup(); for (const callback of mounted) callback(); }, unmount() { for (const callback of unmounted) callback(); } }; },
    h: (type, props, children) => ({ type, props, children }),
    ref: value => ({ value }),
    onMounted: callback => mounted.push(callback),
    onBeforeUnmount: callback => unmounted.push(callback),
    Teleport: "Teleport", UsageTrendView: "UsageTrendView", ShareCardDialog: "ShareCardDialog",
  });
  vm.runInContext(entry.slice(entry.indexOf("export function")).replaceAll("export function", "function").replace(/\nmountClassicFeatures\(\)\s*$/, ""), context);
  return { nodes, document, events, context, getRender: () => render() };
}

test("classic adds a usage tab after requests and a fixed today share action", () => {
  assert.ok(html.indexOf('data-view="requests"') < html.indexOf('data-view="usage"'));
  assert.ok(html.indexOf('data-view="usage"') < html.indexOf('data-view="settings"'));
  assert.match(html, /id="usage-share-button"[^>]+disabled>今日战报/);
  assert.match(html, /今日 00:00 至现在[^\n]+不跟随当前统计范围/);
  assert.match(html, /type="module" src="\.\/static\/assets\/classic-features\.js"/);
  assert.match(html, /href="\.\/static\/assets\/classic-features\.css"/);
  assert.match(entry, /import UsageTrendView from '\.\/components\/UsageTrendView\.vue'/);
  assert.match(entry, /import ShareCardDialog from '\.\/components\/ShareCardDialog\.vue'/);
  assert.doesNotMatch(entry, /from '\.\/App\.vue'|import '\.\/styles\.css'/);
});

test("usage mounts only while enabled and active, and unmounts when leaving", () => {
  const f = fixture();
  const app = f.context.mountClassicFeatures(f.document, f.events);
  assert.equal(f.getRender()[0], null);
  f.nodes["#classic-usage-view"].hidden = false;
  f.events.fire("local-proxy:classic-view-change");
  assert.equal(f.getRender()[0].children[0].type, "UsageTrendView");
  f.nodes["#classic-usage-view"].hidden = true;
  f.events.fire("local-proxy:classic-view-change");
  assert.equal(f.getRender()[0], null);
  assert.equal(f.nodes[".classic-feature-loading"].removed, true);
  app.unmount();
  assert.equal(f.events.listeners.size, 0);
  assert.equal(f.nodes["#usage-share-button"].listeners.size, 0);
  assert.equal(f.nodes["#usage-share-button"].disabled, true);
});

test("restored active view mounts immediately and disabled history blocks both features", () => {
  const restored = fixture({ active: true });
  restored.context.mountClassicFeatures(restored.document, restored.events);
  assert.equal(restored.getRender()[0].children[0].type, "UsageTrendView");
  const disabled = fixture({ enabled: false, active: true });
  disabled.context.mountClassicFeatures(disabled.document, disabled.events);
  disabled.nodes["#usage-share-button"].fire("click");
  assert.equal(disabled.getRender()[0], null);
  assert.equal(disabled.getRender()[1], null);
});

test("share opens, closes with focus return, and closes when leaving providers", () => {
  const f = fixture();
  f.context.mountClassicFeatures(f.document, f.events);
  f.nodes["#usage-share-button"].fire("click");
  assert.equal(f.getRender()[1].type, "ShareCardDialog");
  f.getRender()[1].props.onClose();
  assert.equal(f.getRender()[1], null);
  assert.equal(f.nodes["#usage-share-button"].focused, true);
  f.nodes["#usage-share-button"].fire("click");
  f.nodes["#providers-view"].hidden = true;
  f.events.fire("local-proxy:classic-view-change");
  assert.equal(f.getRender()[1], null);
});

test("missing mount container is harmless", () => {
  const f = fixture({ hostExists: false });
  assert.equal(f.context.mountClassicFeatures(f.document, f.events), null);
});

test("request count follows the selected usage range without changing active request actions", () => {
  const start = source.indexOf('const requestCell = document.createElement("span");');
  const end = source.indexOf("const providerHealth = healthStatusForProvider(provider);", start);
  const block = source.slice(start, end);
  function renderCell(count, active, enabled = true) {
    const context = vm.createContext({
      document: { createElement() { return { children: [], append(child) { this.children.push(child); }, setAttribute() {}, addEventListener() {} }; } },
      uiConfig: { features: { usage_history: enabled } },
      usage: { request_count: count },
      provider: { provider_id: "a", name: "A", active_requests: active },
      manageProvidersMode: false, openActiveProviderId: null,
      scheduleActiveSessionsPopoverClose() {},
      appliedTimeWindows: { usage: "7d" }, usageWindowLabel: value => value,
    });
    vm.runInContext(`${block}\nthis.cell = requestCell;`, context);
    return context.cell.children;
  }
  const active = renderCell(1234, 2);
  assert.equal(active[0].textContent, "1,234 次");
  assert.match(active[0].title, /7d/);
  assert.equal(active[1].textContent, "2 个请求");
  assert.equal(renderCell(0, 0)[0].textContent, "0 次");
  assert.equal(renderCell(12, 0, false).length, 1);
  assert.match(block, /mouseenter[^\n]+openActiveSessionsPopover/);
  assert.match(block, /openAllRequests\(\)/);
  assert.match(source, /\[data-view="usage"\][^\n]+usage_history === false/);
});

test("feature CSS is isolated and built as an explicit packaged entry", () => {
  const config = fs.readFileSync(path.join(root, "vite.config.js"), "utf8");
  const application = fs.readFileSync(path.join(root, "..", "local_proxy", "application.py"), "utf8");
  assert.match(config, /'classic-features': resolve\(__dirname, 'src\/classic-features\.js'\)/);
  assert.match(application, /dist\/static\/assets\/classic-features\.js/);
  assert.match(application, /dist\/static\/assets\/classic-features\.css/);
  assert.match(css, /\.classic-feature-surface \.usage-summary \{/);
  assert.match(css, /\.classic-feature-surface \.share-card-modal \{/);
  assert.doesNotMatch(css, /(?:^|\n)\.usage-summary \{|(?:^|\n):root/);
  const trend = fs.readFileSync(path.join(root, "src", "components", "UsageTrendView.vue"), "utf8");
  const aux = trend.slice(trend.indexOf("async function loadAuxData"), trend.indexOf("async function loadAll"));
  assert.doesNotMatch(aux, /error\.value = ''/);
});

test("trend custom range survives unmount and rejects invalid stored dates", () => {
  const trend = fs.readFileSync(path.join(root, "src", "components", "UsageTrendView.vue"), "utf8");
  const values = new Map();
  const context = vm.createContext({
    localStorage: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    customRange: { value: null }, windowName: { value: "24h" }, loadAll() {},
  });
  vm.runInContext(trend.slice(trend.indexOf("function readCustomRange()"), trend.indexOf("function timelineParams()")), context);
  assert.equal(context.readCustomRange(), null);
  context.applyCustomRange({ startAt: 100, endAt: 200 });
  assert.equal(context.windowName.value, "custom");
  assert.equal(context.readCustomRange().startAt, 100);
  assert.equal(context.readCustomRange().endAt, 200);
  context.changeWindow("7d");
  assert.equal(context.customRange.value, null);
  assert.equal(context.readCustomRange(), null);
  for (const value of ['invalid json', '{"startAt":200,"endAt":100}', '{"startAt":"100","endAt":200}', '{"endAt":200}']) {
    values.set("local-proxy-usage-trend-range", value);
    assert.equal(context.readCustomRange(), null);
  }
  assert.match(trend, /const customRange = ref\(readCustomRange\(\)\)/);
});

test("leaving trend during a pending request cancels queued and auxiliary reloads", async () => {
  const trend = fs.readFileSync(path.join(root, "src", "components", "UsageTrendView.vue"), "utf8");
  let releaseTimeline;
  let timelines = 0;
  let auxiliary = 0;
  let unmount;
  const context = vm.createContext({
    document: { hidden: false }, disposed: false, reloadAfterCurrent: false,
    loading: { value: false }, error: { value: "old error" },
    windowName: { value: "24h" }, customRange: { value: null },
    hoverIndex: { value: -1 }, hoverCell: { value: null },
    timer: 1, carouselTimer: 2, rafId: 3,
    window: { clearInterval() {} }, cancelAnimationFrame() {},
    loadTimeline() { timelines++; return new Promise(resolve => { releaseTimeline = resolve; }); },
    loadAuxData() { auxiliary++; },
    onMounted() {}, onBeforeUnmount(callback) { unmount = callback; },
  });
  vm.runInContext(trend.slice(trend.indexOf("async function loadAll()"), trend.indexOf("</script>")), context);
  const pending = context.loadAll();
  await context.loadAll();
  assert.equal(context.reloadAfterCurrent, true);
  assert.equal(context.error.value, "");
  unmount();
  releaseTimeline();
  await pending;
  await context.loadAll();
  assert.equal(context.reloadAfterCurrent, false);
  assert.equal(timelines, 1);
  assert.equal(auxiliary, 0);
});
