# Claude curl_cffi Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Route production Claude upstream traffic through `curl_cffi` while leaving Codex on httpx.

**Architecture:** Add an httpx-compatible curl adapter selected by the Claude app through a shared client factory. Reuse all existing routing and response logic.

**Tech Stack:** Python 3.12+, FastAPI, httpx, curl_cffi, unittest, PyInstaller

---

### Task 1: Curl Client Adapter

**Files:**
- Create: `claude_curl_transport.py`
- Create: `tests/test_claude_curl_transport.py`

- [x] Write failing tests for request construction, query/header/body forwarding, streaming, close, and exception translation.
- [x] Run `python -m unittest tests.test_claude_curl_transport` and confirm the missing adapter fails.
- [x] Implement the minimal adapter around `curl_cffi.requests.AsyncSession`.
- [x] Re-run the focused tests and confirm they pass.

### Task 2: Production Claude Selection

**Files:**
- Modify: `codex_local_proxy.py`
- Modify: `claude_local_proxy.py`
- Modify: `tests/test_claude_local_proxy.py`

- [x] Write a failing test showing the Claude app selects the curl factory while an injected test client remains supported.
- [x] Add a shared `client_factory` boundary and configure Claude to use the curl adapter.
- [x] Add successful HTML detection to the Claude retry decision.
- [x] Run Claude and Codex proxy tests.

### Task 3: Packaging

**Files:**
- Modify: `requirements-status.txt`
- Modify: `codex_local_proxy_app.py`
- Modify: `tests/test_windows_release.py`

- [x] Add failing smoke assertions for curl transport availability.
- [x] Add `curl_cffi` to dependencies and packaged smoke checks.
- [x] Run the release tests and packaged `--smoke-test`.

### Task 4: Full And Real Verification

- [x] Run all Python and Node tests plus JavaScript syntax checks.
- [x] Build to a new output directory without replacing the running EXE.
- [x] Start the new EXE on temporary Codex and Claude ports.
- [x] Point an isolated Claude Code process at the temporary Claude port and verify AgentRouter returns `OK`.
- [x] Stop only the temporary verification instance and record the artifact hash.
