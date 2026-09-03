+++
id = "2026-09-03-configurable-upstream-timeouts"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# Configurable Upstream Timeouts

## Goal

Allow users to configure the upstream response-header timeout and SSE stream
idle timeout from the control console. Support minute-based values, one hour,
custom long values, and an explicit no-timeout option.

## Current State

Both upstream protections are fixed at 120 seconds in the proxy core. The
shared runtime settings file and both control consoles do not expose these
values. The curl transport also has an independent 300 second idle timeout.

## Design Scope

- Store both settings in shared runtime settings as seconds, with `null`
  meaning no application-level timeout.
- Preserve the existing 120 second behavior when old settings omit either
  field.
- Validate positive integer minute values and reject invalid JSON types.
- Apply the settings to new upstream attempts without requiring a restart.
- Make the standard HTTP and curl transports honor the configured stream and
  response-header wait behavior while retaining a finite 30 second connect
  timeout.
- Add matching controls to the modern and classic runtime settings pages.

## Non-Goals

- Do not change retry policy, retry count, or provider selection behavior.
- Do not make connection establishment wait forever.
- Do not remove or rewrite existing user settings or request history.
- Do not alter unrelated uncommitted recovery or model-mapping changes.

## Compatibility

Missing timeout fields migrate to 120 seconds in memory and on the next save.
Existing shared-settings files remain readable. The runtime settings API adds
the timeout fields without removing existing fields.

## Risks

Very long waits can retain request and provider resources. The no-timeout
option must disable only the application-level response-header and stream-idle
guards; the connect timeout remains finite. The curl transport must not keep a
shorter internal idle timeout than the configured outer guard.

## Test Plan

- Test shared-settings defaults, migration, persistence, and validation.
- Test response-header and SSE idle timeout behavior for finite and null
  values.
- Test dynamic settings are used by new proxy requests.
- Test curl transport timeout updates and the retained connect timeout.
- Test modern and classic runtime settings markup and payload handling.
- Run Python unit tests, JavaScript tests, Vue build, Python compilation, JS
  syntax checks, and `git diff --check`.

## Self-Review

- Timeout values are shared by Codex and Claude because both protocols use the
  same runtime settings coordinator.
- `null` is distinct from zero and is the only representation for no timeout.
- Existing tests that construct proxy apps directly retain fixed default
  arguments unless they opt into a runtime timeout snapshot.
- The implementation must not claim no timeout while curl's internal idle
  timeout can still fire sooner.

## Actual Changes

- `local_proxy/shared_settings.py` adds shared response-header and SSE idle
  timeout fields, version 3 migration defaults, minute-granularity validation,
  and runtime API persistence. `null` is the explicit no-timeout value.
- `local_proxy/core.py` reads the shared values for every new request and
  skips the corresponding application-level `wait_for` when the value is
  `null`.
- `local_proxy/server.py` passes the latest shared timeout snapshot to both
  Codex and Claude request routes.
- `local_proxy/transports/curl.py` keeps a finite 30 second connect timeout
  while disabling curl's independent low-speed timer for proxy requests, so
  the configured outer SSE idle timeout remains authoritative.
- `proxy_static/src/components/RuntimeView.vue` and
  `proxy_static/classic/index.html` add preset, custom-minute, and no-timeout
  controls. Their styles and request payloads are updated in the matching CSS
  and JavaScript files.
- `tests/test_shared_settings.py`, `tests/test_proxy_core.py`,
  `tests/test_claude_transport.py`, `tests/local_proxy_vue_ui.test.js`, and
  `tests/local_proxy_requests.test.js` cover migration, validation, runtime
  behavior, curl options, and both control consoles.

## Verification Results

- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`
  -> 539 tests passed.
- `node --test tests/*.test.js` -> 87 tests passed.
- `npm run build --prefix proxy_static` -> production build passed.
- `python -m compileall -q local_proxy provider_status` -> passed.
- `node --check proxy_static/classic/app.js` -> passed.
- `git diff --check` -> passed.

## PR

pending
