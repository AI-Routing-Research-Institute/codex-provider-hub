"use strict";

(() => {
  const STORAGE_KEY = "codex-status-theme";
  const VALID_THEMES = new Set(["light", "dark", "system"]);

  function normalize(value) {
    return VALID_THEMES.has(value) ? value : "system";
  }

  function read() {
    try {
      return normalize(window.localStorage.getItem(STORAGE_KEY));
    } catch (_error) {
      return "system";
    }
  }

  function apply(value) {
    const normalized = normalize(value);
    const root = document.documentElement;
    if (normalized === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", normalized);
    }
    return normalized;
  }

  function save(value) {
    const normalized = normalize(value);
    try {
      window.localStorage.setItem(STORAGE_KEY, normalized);
    } catch (_error) {
      // 存储不可用时，当前会话仍然可以切换主题。
    }
    return normalized;
  }

  const initialTheme = read();
  apply(initialTheme);
  window.codexTheme = { apply, normalize, read, save };
})();
