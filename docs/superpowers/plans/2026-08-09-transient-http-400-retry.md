# Transient HTTP 400 Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse HTTP 400 error bodies before output and retry only recognized transient provider failures while preserving permanent and unknown 400 responses.

**Architecture:** Add a bounded pre-output HTTP 400 inspector beside the existing SSE and HTML 404 inspectors. Reuse the centralized embedded-error classifier for JSON and plain-text bodies, with permanent error codes taking precedence over explicit transient message patterns; return buffered bytes to the existing response stream whenever the response must be passed through.

**Tech Stack:** Python 3.13+, FastAPI, httpx async streaming, SQLite recovery history, unittest

---

### Task 1: Add HTTP 400 Classification Regression Tests

**Files:**
- Modify: `tests/test_proxy_core.py`

- [ ] **Step 1: Add a failing transient JSON HTTP 400 test**

Create an async test whose first upstream response is HTTP 400 with:

```python
{"error": {"message": "当前模型 gpt-5.6-sol 负载已经达到上限，请稍后重试"}}
```

Return HTTP 200 on the second attempt. Assert two attempts, successful client output, one retry, `model_capacity` classification, and a recovery-history entry at `before_output`.

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
.venv/Scripts/python.exe -m unittest tests.test_proxy_core.ProxyAppTests.test_transient_http_400_model_capacity_is_retried -v
```

Expected: FAIL because HTTP 400 is currently returned without retry.

- [ ] **Step 3: Add failing channel/upstream and permanent-response tests**

Cover JSON or split plain-text bodies containing `No available channel for model` and `upstream temporarily unavailable`, followed by success. Add an `invalid_request_error` HTTP 400 and an unknown HTTP 400 that assert one attempt and byte-for-byte response preservation.

- [ ] **Step 4: Run all new tests and confirm each fails for missing classification**

Run:

```bash
.venv/Scripts/python.exe -m unittest tests.test_proxy_core.ProxyAppTests.test_transient_http_400_model_capacity_is_retried tests.test_proxy_core.ProxyAppTests.test_transient_http_400_channel_error_is_retried tests.test_proxy_core.ProxyAppTests.test_permanent_http_400_is_passed_through_unchanged -v
```

Expected: transient cases FAIL with one attempt; permanent case may pass and remains as a regression guard.

### Task 2: Implement Bounded HTTP 400 Inspection

**Files:**
- Modify: `local_proxy/core.py`
- Test: `tests/test_proxy_core.py`

- [ ] **Step 1: Add a body classifier**

Add a helper with this contract:

```python
def _http_400_retry_failure(
    response: httpx.Response,
    body_prefix: bytes,
) -> tuple[str | None, str | None]:
    ...
```

Only inspect status 400. Decode JSON when possible; otherwise wrap decoded text as an error message. Call `_embedded_retry_failure()` so permanent codes remain authoritative.

- [ ] **Step 2: Extend explicit transient semantics**

Extend the centralized classifier with narrow patterns for model busy/capacity, `No available channel`, Chinese channel-unavailable wording, upstream unavailable, and `请稍后重试`. Keep `invalid_request_error`, `model_not_found`, authentication, permission, quota, billing, and policy codes ahead of message-based transient matching.

- [ ] **Step 3: Add a bounded pre-output inspector**

Add:

```python
async def _inspect_http_400_before_output(
    response: httpx.Response,
    first_chunk: bytes,
    stream: AsyncIterator[bytes],
) -> tuple[bytes | None, str | None, str | None]:
    ...
```

Read at most `RETRY_ERROR_BODY_BYTES` before classification. Return `(None, kind, summary)` for recognized transient errors. Return `(buffered_bytes, None, None)` for permanent, unknown, oversized, timed-out, or malformed bodies so the existing stream can continue without byte loss.

- [ ] **Step 4: Wire inspection into the retry loop**

After obtaining `first_chunk` and before SSE/HTML output commitment, invoke the HTTP 400 inspector when retries are enabled. Set `final_error` from the returned retry kind and let the existing `max_attempts`, delay, manual reroute, recovery history, and disconnect checks run unchanged.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run the three focused test names from Task 1. Expected: PASS with transient cases retried and permanent bodies unchanged.

- [ ] **Step 6: Run the full proxy-core test module**

Run:

```bash
.venv/Scripts/python.exe -m unittest tests.test_proxy_core -v
```

Expected: PASS.

### Task 3: Verify And Deliver

**Files:**
- Modify: `docs/changes/2026-08-09-transient-http-400-retry.md`

- [ ] **Step 1: Update the change record to implemented**

List exact helpers, message categories, stream-preservation behavior, and tests in `实际改动`.

- [ ] **Step 2: Run complete local verification**

Run:

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.js
node --check proxy_static/app.js
.venv/Scripts/python.exe -m compileall -q local_proxy tests
git diff --check
```

Expected: all Python and Node tests pass; syntax, compileall, and diff checks exit 0.

- [ ] **Step 3: Mark the change record verified**

Record exact command results and counts. Keep `PR` pending until the pull request exists.

- [ ] **Step 4: Rebase and rerun verification**

Fetch `origin/main`, rebase the feature branch, and rerun the complete verification against the exact rebased HEAD.

- [ ] **Step 5: Commit, push, create PR, and enable auto-merge**

Use `.agents/skills/git-commit-helper/SKILL.md`. Do not restart the local proxy. Follow required `policy`, `tests-windows`, and `tests-macos` checks and the repository auto-release workflow.
