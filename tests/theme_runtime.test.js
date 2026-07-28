"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.join(
  __dirname,
  "..",
  "provider_status",
  "static",
  "theme-init.js",
);
const source = fs.readFileSync(sourcePath, "utf8");
const appSourcePath = path.join(
  __dirname,
  "..",
  "provider_status",
  "static",
  "app.js",
);
const appSource = fs.readFileSync(appSourcePath, "utf8");

function createThemeRuntime({ storedValue = null, throwOnRead = false } = {}) {
  const attributes = new Map();
  const savedValues = [];
  const root = {
    getAttribute(name) {
      return attributes.has(name) ? attributes.get(name) : null;
    },
    removeAttribute(name) {
      attributes.delete(name);
    },
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
  };
  const localStorage = {
    getItem(key) {
      assert.equal(key, "codex-status-theme");
      if (throwOnRead) {
        throw new Error("storage disabled");
      }
      return storedValue;
    },
    setItem(key, value) {
      savedValues.push([key, value]);
    },
  };
  const window = { localStorage };
  vm.runInNewContext(
    source,
    { document: { documentElement: root }, window },
    { filename: sourcePath },
  );
  return { api: window.codexTheme, root, savedValues };
}

class FakeClassList {
  constructor(value = "") {
    this.values = new Set(value.split(/\s+/).filter(Boolean));
  }

  toggle(name, force) {
    const shouldHave = force === undefined ? !this.values.has(name) : force;
    if (shouldHave) {
      this.values.add(name);
    } else {
      this.values.delete(name);
    }
    return shouldHave;
  }
}

class FakeElement {
  constructor({ id = "", className = "", dataset = {}, hidden = false } = {}) {
    this.id = id;
    this.className = className;
    this.classList = new FakeClassList(className);
    this.dataset = { ...dataset };
    this.hidden = hidden;
    this.tabIndex = -1;
    this.disabled = false;
    this.textContent = "";
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.ownerDocument = null;
  }

  addEventListener(type, listener) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(listener);
    this.listeners.set(type, callbacks);
  }

  dispatch(type, init = {}) {
    const event = {
      target: this,
      preventDefault() {},
      stopPropagation() {},
      ...init,
    };
    for (const listener of this.listeners.get(type) || []) {
      listener(event);
    }
    return event;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  toggleAttribute(name, force) {
    const shouldHave = force === undefined
      ? !this.attributes.has(name)
      : force;
    if (shouldHave) {
      this.attributes.set(name, "");
    } else {
      this.attributes.delete(name);
    }
    return shouldHave;
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = nodes;
  }

  contains(node) {
    return node === this || this.children.some((child) => child.contains(node));
  }
}

class FakeDocument {
  constructor(elements) {
    this.elements = elements;
    this.documentElement = elements.root;
    this.activeElement = elements.themeButton;
    this.listeners = new Map();
    for (const element of Object.values(elements).flat()) {
      if (element instanceof FakeElement) {
        element.ownerDocument = this;
      }
    }
  }

  querySelector(selector) {
    if (selector.startsWith("#")) {
      return Object.values(this.elements).find(
        (element) => element.id === selector.slice(1),
      ) || null;
    }
    if (selector ===(".theme-picker")) {
      return this.elements.themePicker;
    }
    if (selector ===("#theme-menu")) {
      return this.elements.themeMenu;
    }
    return null;
  }

  querySelectorAll(selector) {
    if (selector === ".theme-option") {
      return this.elements.themeOptions;
    }
    if (selector === "[data-theme-trigger-icon]") {
      return this.elements.themeIcons;
    }
    return [];
  }

  addEventListener(type, listener) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(listener);
    this.listeners.set(type, callbacks);
  }
}

function createAppThemeRuntime() {
  const root = new FakeElement();
  const themeButton = new FakeElement({ id: "theme-button" });
  const themeMenu = new FakeElement({ id: "theme-menu", hidden: true });
  const themePicker = new FakeElement({ className: "theme-picker" });
  const themeOptions = ["light", "dark", "system"].map(
    (value) => new FakeElement({
      className: "theme-option",
      dataset: { themeValue: value },
    }),
  );
  const themeIcons = ["light", "dark", "system"].map(
    (value) => new FakeElement({ dataset: { themeTriggerIcon: value } }),
  );
  const elements = {
    root,
    providerList: new FakeElement({ id: "provider-list" }),
    loadMessage: new FakeElement({ id: "load-message" }),
    overallState: new FakeElement({ id: "overall-state" }),
    overallDot: new FakeElement({ id: "overall-dot" }),
    overallDetail: new FakeElement({ id: "overall-detail" }),
    providerCount: new FakeElement({ id: "provider-count" }),
    updatedAt: new FakeElement({ id: "updated-at" }),
    countdown: new FakeElement({ id: "refresh-countdown" }),
    refreshButton: new FakeElement({ id: "refresh-button" }),
    themeButton,
    themeMenu,
    themePicker,
    systemThemeStatus: new FakeElement({ id: "system-theme-status" }),
    themeOptions,
    themeIcons,
  };
  themePicker.append(themeButton, themeMenu);
  themeMenu.append(...themeOptions);
  const document = new FakeDocument(elements);
  const savedThemes = [];
  const mediaQuery = {
    matches: false,
    listeners: [],
    addEventListener(type, listener) {
      assert.equal(type, "change");
      this.listeners.push(listener);
    },
    addListener(listener) {
      this.listeners.push(listener);
    },
    emit(matches) {
      this.matches = matches;
      for (const listener of this.listeners) {
        listener({ matches });
      }
    },
  };
  const window = {
    codexTheme: {
      read: () => "system",
      normalize: (value) => ["light", "dark", "system"].includes(value)
        ? value
        : "system",
      apply(value) {
        if (value === "system") {
          root.toggleAttribute("data-theme", false);
        } else {
          root.setAttribute("data-theme", value);
        }
        return value;
      },
      save(value) {
        savedThemes.push(value);
        return value;
      },
    },
    matchMedia: () => mediaQuery,
    addEventListener() {},
    setInterval: () => 1,
    clearInterval() {},
  };
  const context = {
    document,
    window,
    Element: FakeElement,
    Node: FakeElement,
    fetch: () => new Promise(() => {}),
    console,
  };
  vm.runInNewContext(appSource, context, { filename: appSourcePath });
  return { elements, document, mediaQuery, root, savedThemes };
}

test("defaults to system mode without forcing a theme attribute", () => {
  const runtime = createThemeRuntime();

  assert.equal(runtime.api.read(), "system");
  assert.equal(runtime.root.getAttribute("data-theme"), null);
});

test("restores explicit light and dark modes", () => {
  for (const mode of ["light", "dark"]) {
    const runtime = createThemeRuntime({ storedValue: mode });

    assert.equal(runtime.api.read(), mode);
    assert.equal(runtime.root.getAttribute("data-theme"), mode);
  }
});

test("normalizes invalid or inaccessible storage to system mode", () => {
  for (const options of [
    { storedValue: "invalid" },
    { throwOnRead: true },
  ]) {
    const runtime = createThemeRuntime(options);

    assert.equal(runtime.api.read(), "system");
    assert.equal(runtime.root.getAttribute("data-theme"), null);
  }
});

test("applies and persists an explicit selection", () => {
  const runtime = createThemeRuntime();

  assert.equal(runtime.api.apply("dark"), "dark");
  assert.equal(runtime.root.getAttribute("data-theme"), "dark");
  assert.equal(runtime.api.save("dark"), "dark");
  assert.deepEqual(runtime.savedValues, [["codex-status-theme", "dark"]]);
  assert.equal(runtime.api.apply("system"), "system");
  assert.equal(runtime.root.getAttribute("data-theme"), null);
});

test("runs the theme menu state machine for keyboard, persistence, and system changes", () => {
  const runtime = createAppThemeRuntime();
  const {
    document,
    elements,
    mediaQuery,
    root,
    savedThemes,
  } = runtime;
  const [light, dark, system] = elements.themeOptions;

  assert.equal(elements.themeMenu.hidden, true);
  assert.equal(elements.themeIcons[2].getAttribute("hidden"), null);
  assert.equal(elements.themeIcons[0].getAttribute("hidden"), "");

  elements.themeButton.dispatch("click");
  assert.equal(elements.themeMenu.hidden, false);
  assert.equal(elements.themeButton.getAttribute("aria-expanded"), "true");
  assert.equal(document.activeElement, system);
  assert.equal(system.tabIndex, 0);

  elements.themeMenu.dispatch("keydown", { key: "ArrowUp" });
  assert.equal(document.activeElement, dark);
  assert.equal(dark.tabIndex, 0);
  assert.equal(system.tabIndex, -1);

  elements.themePicker.dispatch("focusout", { relatedTarget: elements.themeButton });
  assert.equal(elements.themeMenu.hidden, true);
  assert.equal(elements.themeButton.getAttribute("aria-expanded"), "false");

  elements.themeButton.dispatch("click");
  light.dispatch("click");
  assert.equal(root.getAttribute("data-theme"), "light");
  assert.deepEqual(savedThemes, ["light"]);
  assert.equal(elements.themeButton.getAttribute("aria-label"), "切换主题，当前浅色模式");
  assert.equal(elements.themeIcons[0].getAttribute("hidden"), null);
  assert.equal(elements.themeMenu.hidden, true);

  elements.themeButton.dispatch("click");
  system.dispatch("click");
  mediaQuery.emit(true);
  assert.equal(root.getAttribute("data-theme"), null);
  assert.equal(elements.systemThemeStatus.textContent, "当前系统主题：深色");
});
