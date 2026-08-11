# View Tab Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the last active console tab after reload while safely falling back when the saved tab is invalid or unavailable.

**Architecture:** Add pure tab normalization and storage-key helpers near the existing UI configuration helpers. Route both user switching and startup restoration through `switchView()`, with a persistence option that prevents initialization from rewriting stored state.

**Tech Stack:** Browser JavaScript, localStorage, Node.js test runner, FastAPI/Python regression suite

---

### Task 1: Add View Persistence Regression Tests

**Files:**
- Create: `tests/local_proxy_view_state.test.js`

- [x] **Step 1: Add failing normalization tests**

Extract the planned pure helpers from `proxy_static/app.js` and assert:

```js
normalizeViewName("requests", true) === "requests"
normalizeViewName("runtime", true) === "runtime"
normalizeViewName("invalid", true) === "providers"
normalizeViewName("requests", false) === "providers"
viewStorageKey("codex") === "local-proxy-view-codex"
```

- [x] **Step 2: Run the focused test and confirm RED**

Run:

```bash
node --test tests/local_proxy_view_state.test.js
```

Expected: FAIL because the helpers and persistence wiring do not exist.

- [x] **Step 3: Add failing wiring assertions**

Assert that `switchView()` accepts a `{persist}` option and invokes `persistView()` with the normalized value, while `initialize()` invokes `restoreView()` after `readUiConfig()`.

### Task 2: Implement View Persistence

**Files:**
- Modify: `proxy_static/app.js`
- Test: `tests/local_proxy_view_state.test.js`

- [x] **Step 1: Add pure helpers**

Implement:

```js
function viewStorageKey(serviceId = uiConfig.service_id || "local") {
  return `local-proxy-view-${serviceId || "local"}`;
}

function normalizeViewName(viewName, requestsEnabled = true) {
  const allowed = ["providers", "requests", "settings", "runtime"];
  if (!allowed.includes(viewName)) return "providers";
  return viewName === "requests" && !requestsEnabled ? "providers" : viewName;
}
```

- [x] **Step 2: Add guarded persistence and restoration**

`persistView()` writes only the normalized string and catches storage failures. `restoreView()` reads after UI config, derives request availability from the requests tab `hidden` state, and calls `switchView(saved, {persist: false})`; read failures call the same fallback path with `providers`.

- [x] **Step 3: Normalize and persist in switchView**

Change the signature to `switchView(viewName, {persist = true} = {})`. Normalize before updating buttons and panels; after the existing lazy loads, persist the normalized value when requested.

- [x] **Step 4: Restore during initialization**

Call `restoreView()` immediately after `await readUiConfig()` so feature visibility is already resolved. Keep existing status, runtime-settings, and polling startup behavior.

- [x] **Step 5: Run focused verification**

Run:

```bash
node --test tests/local_proxy_view_state.test.js
node --test tests/*.test.js
node --check proxy_static/app.js
```

Expected: all Node tests and JavaScript syntax checks pass.

### Task 3: Verify, Scan, And Deliver

**Files:**
- Modify: `docs/changes/2026-08-11-view-tab-persistence.md`

- [x] **Step 1: Update the change record to implemented**

Record the storage key, fallback rules, initialization point, tests, and absence of backend changes.

- [ ] **Step 2: Run sensitive information scans**

Scan the complete branch diff against `origin/main` for private-key headers, OpenAI-style keys, GitHub tokens, bearer tokens, password assignments, API-key assignments, and authorization headers. The scan must exit successfully with no matched secret values before push.

- [ ] **Step 3: Run complete verification**

Run:

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/*.test.js
node --check proxy_static/app.js
node --check provider_status/static/app.js
.venv/Scripts/python.exe -m compileall -q local_proxy tests
git diff --check
```

Expected: Python and Node tests pass; both JavaScript checks, compileall, diff check, and sensitive-information scans exit 0.

- [ ] **Step 4: Mark verified and deliver through main**

Mark the change record `verified`, record exact results, commit on the feature branch, fetch/rebase latest `origin/main`, rerun full verification and the sensitive scan, then use `.agents/skills/git-commit-helper/SKILL.md` to create a PR, enable auto-merge, and track required checks and release workflows. Never push directly to protected `main` and do not restart the local proxy.
