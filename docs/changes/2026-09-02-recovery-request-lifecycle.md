+++
id = "2026-09-02-recovery-request-lifecycle"
type = "bugfix"
release_bump = "patch"
status = "verified"
+++

# Recovery Request Lifecycle Display

## Goal

Make a long-running retry request show its actual lifecycle clearly after a
process restart. The request history must identify the restart boundary, while
the recovery panel must avoid presenting every retry attempt as a separate
request.

## Current State

The proxy persists one recovery event for each retry attempt. The recovery API
returns those raw events without grouping. Startup recovery converts every row
left in `inflight_requests` into a `process_restarted` history row and computes
the duration from `started_at` to startup time, even when the last activity was
earlier. The UI labels retrying events as failures and calls the attempt count a
request count.

## Design Scope

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

## Non-Goals

- Do not delete existing recovery or request history.
- Do not add a request-level retry deadline in this change.
- Do not change upstream error classification or provider failover rules.
- Do not change the `thread_id` same-session takeover behavior.

## Compatibility

The recovery API remains backward-compatible for existing raw event fields.
New grouped fields are additive. Existing databases migrate through the normal
SQLite initialization path; no manual schema reset is required.

## Risks

Grouping can hide individual retry events in the default view if the summary
contract is ambiguous. The raw event list must remain available through the
detail path. Using `updated_at` can under-report the lifetime of a request that
was actively waiting without updating its phase, so the UI must describe it as
last known activity rather than exact upstream execution time.

## Test Plan

- Add SQLite tests for restart recovery duration and grouped recovery summaries.
- Add JavaScript tests for retrying/interrupted labels and grouped rendering.
- Run Python unit tests, JavaScript tests, Vue build, Python compilation, JS
  syntax checks, and `git diff --check`.

## Self-Review

- The change is limited to persisted local lifecycle metadata and presentation.
- Existing raw records and retry policy semantics remain available.
- The restart timestamp remains the ordering timestamp; `updated_at` is used
  only for the recovered request's measured active duration.
- The implementation must not assume `request_id` is globally unique across
  process runs; grouping will include the request start timestamp.

## Actual Changes

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
- `tests/test_proxy_core.py`, `tests/local_proxy_recovery_history.test.js`,
  and `tests/local_proxy_vue_ui.test.js` cover migration, recovery duration,
  grouped pagination, lifecycle labels, and summary API usage.

## Verification Results

- `python -m unittest discover -s tests -p "test_*.py"` -> 534 tests passed.
- All `tests/*.test.js` files run with `node --test` -> passed.
- `npm run build --prefix proxy_static` -> production build passed.
- `python -m compileall -q local_proxy provider_status` -> passed.
- `node --check proxy_static/classic/app.js` -> passed.
- `git diff --check` -> passed.

## PR

pending
