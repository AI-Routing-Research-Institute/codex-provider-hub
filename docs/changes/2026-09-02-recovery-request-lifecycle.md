+++
id = "2026-09-02-recovery-request-lifecycle"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# Recovery Request Lifecycle Display

## 目标

Make a long-running retry request show its actual lifecycle clearly after a
process restart. The request history must identify the restart boundary, while
the recovery panel must avoid presenting every retry attempt as a separate
request.

## 现状

The proxy persists one recovery event for each retry attempt. The recovery API
returns those raw events without grouping. Startup recovery converts every row
left in `inflight_requests` into a `process_restarted` history row and computes
the duration from `started_at` to startup time, even when the last activity was
earlier. The UI labels retrying events as failures and calls the attempt count a
request count.

## 设计范围

- Keep raw recovery events for audit and pagination.
- Expose a grouped recovery view with one summary per request lifecycle,
  including the latest error, highest attempt, first/last event times, and
  event count.
- Use the persisted inflight `updated_at` as the last known activity when
  recovering an interrupted request; keep the recovery timestamp for ordering.
- Render retrying, interrupted, and terminal failure states with distinct
  wording and neutral/appropriate visual treatment in modern and classic UIs.
- Keep `max_attempts = -1`, provider routing, and retry timing semantics
  unchanged.

## 非目标

- Do not delete existing recovery or request history.
- Do not add a request-level retry deadline in this change.
- Do not change upstream error classification or provider failover rules.
- Do not change the `thread_id` same-session takeover behavior.

## 兼容性

The recovery API remains backward-compatible for existing raw event fields.
New grouped fields are additive. Existing databases migrate through the normal
SQLite initialization path; no manual schema reset is required.

## 风险

Grouping can hide individual retry events in the default view if the summary
contract is ambiguous. The raw event list must remain available through the
detail path. Using `updated_at` can under-report the lifetime of a request that
was actively waiting without updating its phase, so the UI must describe it as
last known activity rather than exact upstream execution time.

## 测试计划

- Add SQLite tests for restart recovery duration and grouped recovery summaries.
- Add JavaScript tests for retrying/interrupted labels and grouped rendering.
- Run Python unit tests, JavaScript tests, Vue build, Python compilation, JS
  syntax checks, and `git diff --check`.

## 自审

- The change is limited to persisted local lifecycle metadata and presentation.
- Existing raw records and retry policy semantics remain available.
- The restart timestamp remains the ordering timestamp; `updated_at` is used
  only for the recovered request's measured active duration.
- The implementation must not assume `request_id` is globally unique across
  process runs; grouping will include the request start timestamp.

## 实际改动

- `local_proxy/core.py` adds the migratable `request_history.last_activity_at`
  field and uses persisted `inflight_requests.updated_at` when creating a
  `process_restarted` record, while retaining the restart time for ordering.
- `local_proxy/core.py` adds grouped recovery history keyed by
  `(request_id, request_started_at)`. The summary exposes the latest error,
  maximum attempt, first/last event times, and event count; the raw endpoint
  remains available without `view=summary`.
- `local_proxy/core.py` makes the status payload use the grouped summary and
  adds `view=summary` to the recovery-history control API.
- `proxy_static/classic/app.js` and
  `proxy_static/src/components/ProvidersView.vue` display one lifecycle per
  request, label attempts as attempts, and distinguish retrying state from a
  final failure.
- `proxy_static/dist` is regenerated from the merged Vue sources so the
  production assets include the recovery lifecycle changes.
- `tests/test_proxy_core.py`, `tests/local_proxy_recovery_history.test.js`,
  and `tests/local_proxy_vue_ui.test.js` cover migration, recovery duration,
  grouped pagination, lifecycle labels, and summary API usage.

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"` -> 541 tests passed in 84.500s.
- `Get-ChildItem -Path tests -File -Filter *.test.js | Sort-Object Name | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }` -> 18 files and 89 tests passed.
- `npm run build --prefix proxy_static` -> Vite production build passed with 29 modules; final assets are `index-CFci3PoS.js` and `index-CJ1v9bgC.css` after syncing main.
- `.venv\\Scripts\\python.exe -m compileall -q provider_status local_proxy scripts tests` -> passed.
- `node --check proxy_static/classic/app.js` and all JavaScript files under `proxy_static/src` and `provider_status/static` -> passed.
- `cscript.exe //nologo scripts\\start_local_proxy_hidden.vbs <pythonw> <local_proxy_app.py> --smoke-test` -> passed.
- `git diff --check` -> passed.

## PR

pending
