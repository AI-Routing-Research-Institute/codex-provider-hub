# Time Range Persistence Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the selected fixed or custom time window across reloads and allow custom ranges to end at the final second of the current local day.

**Architecture:** Keep the existing per-service storage keys but normalize their values to `{window, range}` through pure JavaScript helpers. Reuse those helpers in initialization, preset changes, and custom validation; keep backend APIs unchanged because they already accept a future upper bound within a valid range.

**Tech Stack:** Browser JavaScript, localStorage, Node.js test runner, FastAPI/Python regression suite

---

### Task 1: Add Time Preference Regression Tests

**Files:**
- Modify: `tests/local_proxy_time_range.test.js`

- [x] **Step 1: Add failing local-day boundary tests**

Evaluate the pure time helper from `proxy_static/app.js` and assert that a timestamp on 2026-08-10 resolves to local `23:59:59.999`. Assert that a range ending at that value is valid while a range ending at the following local midnight is invalid.

- [x] **Step 2: Run the boundary tests and confirm RED**

Run:

```bash
node --test tests/local_proxy_time_range.test.js
```

Expected: FAIL because the current code only compares the end against `Date.now()` and has no local-day-end helper.

- [x] **Step 3: Add failing preference restoration tests**

Cover these inputs through a pure normalization helper:

```js
{ window: "today", range: validRange }
{ window: "custom", range: validRange }
{ start: validRange.start, end: validRange.end }
{ window: "custom", range: invalidRange }
```

Assert that they restore to `today`, `custom`, legacy `custom`, and the target default respectively. Add a storage-payload assertion that a fixed window retains the latest custom range.

- [x] **Step 4: Run the focused test and confirm RED**

Run the same Node command. Expected: FAIL because stored ranges currently force `custom` and fixed selections are not represented in storage.

### Task 2: Implement Persistent Window State And Today-End Validation

**Files:**
- Modify: `proxy_static/app.js`
- Test: `tests/local_proxy_time_range.test.js`

- [x] **Step 1: Add pure normalization helpers**

Implement helpers with these contracts:

```js
function localDayEnd(milliseconds) {
  const value = new Date(milliseconds);
  value.setHours(23, 59, 59, 999);
  return value.getTime();
}

function defaultTimeWindow(target) {
  return target === "usage" ? "today" : "24h";
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
  const allowed = target === "usage"
    ? new Set(["today", "24h", "7d", "30d", "all", "custom"])
    : new Set(["1h", "6h", "24h", "7d", "custom"]);
  const candidateWindow = legacy ? "custom" : stored?.window;
  const windowName = allowed.has(candidateWindow) ? candidateWindow : defaultTimeWindow(target);
  return windowName === "custom" && !range
    ? { window: defaultTimeWindow(target), range: null }
    : { window: windowName, range };
}

function timeRangeStoragePayload(target) {
  return { window: appliedTimeWindows[target], range: customTimeRanges[target] };
}
```

Support the legacy raw `{start, end}` value, explicit fixed windows, valid custom windows, and target defaults.

- [x] **Step 2: Persist and restore the normalized state**

Change `persistTimeRange()` to serialize `timeRangeStoragePayload(target)`. Change `restoreTimeRanges()` to apply the normalized window and range instead of forcing `custom` whenever a stored range exists.

- [x] **Step 3: Persist fixed preset changes**

After assigning `appliedTimeWindows.usage` or `.requests` in each select change handler, call `persistTimeRange(target)` before refreshing data. Do not clear `customTimeRanges[target]`.

- [x] **Step 4: Allow the current local day end**

Replace the `end > now + 1000` rejection with `end > localDayEnd(now)`, and explicitly reject `start > now`. Keep the existing start-before-end and request seven-day checks. Update the validation messages to state that the start cannot be later than the current time and the end cannot be later than today `23:59:59`.

- [x] **Step 5: Run focused tests and confirm GREEN**

Run:

```bash
node --test tests/local_proxy_time_range.test.js
node --check proxy_static/app.js
```

Expected: all time-range tests pass and JavaScript syntax validation exits 0.

### Task 3: Verify And Deliver

**Files:**
- Modify: `docs/changes/2026-08-10-time-range-persistence.md`

- [x] **Step 1: Update the change record to implemented**

Record the exact storage migration, preset persistence, current-day boundary, and regression tests.

- [x] **Step 2: Run complete verification**

Run:

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/*.test.js
node --check proxy_static/app.js
node --check provider_status/static/app.js
.venv/Scripts/python.exe -m compileall -q local_proxy tests
git diff --check
```

Expected: Python and Node tests pass; both JavaScript checks, compileall, and diff check exit 0.

- [x] **Step 3: Mark the change record verified**

Record exact command results and keep the PR field pending until GitHub returns the final URL.

- [ ] **Step 4: Rebase, rerun verification, and deliver**

Fetch and rebase onto the latest `origin/main`, rerun the full commands against the exact HEAD, then use `.agents/skills/git-commit-helper/SKILL.md` to push the feature branch, create a PR, enable auto-merge, and track required checks and release workflows. Do not restart the local proxy.
