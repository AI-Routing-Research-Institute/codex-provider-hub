from __future__ import annotations

import asyncio
import email.utils
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from contextlib import asynccontextmanager, closing, suppress
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from local_proxy.control_ui import (
    CONTROL_ASSET_DIR,
    control_index_path,
    resolve_control_asset,
    select_control_ui,
)
from local_proxy.diagnostics import DiagnosticLog, EventLoopWatchdog


try:
    import tiktoken
except ImportError:  # The desktop installer installs it; keep diagnostics importable.
    tiktoken = None


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17890
DEFAULT_DATABASE = Path.home() / ".cc-switch" / "cc-switch.db"
MAX_REQUEST_BODY_BYTES = 64 * 1024 * 1024
RETRY_ERROR_BODY_BYTES = 4 * 1024
RETRY_ERROR_HISTORY_LIMIT = 5


RETRY_ERROR_MESSAGE_CHARS = 220
RETRY_ERROR_READ_TIMEOUT_SECONDS = 0.25
INPUT_ITEM_ID_COMPATIBILITY_TTL_SECONDS = 24 * 3600
INPUT_ITEM_ID_COMPATIBILITY_MAX_ENTRIES = 4096
MAX_INPUT_ITEM_ID_REPAIRS_PER_REQUEST = 8
HTML_ERROR_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
RECOVERY_HISTORY_HOURS = 24
RECOVERY_HISTORY_API_LIMIT = 500
RECOVERY_HISTORY_CLEANUP_INTERVAL_SECONDS = 3600
USAGE_HISTORY_PAGE_LIMIT = 50
REQUEST_HISTORY_HOURS = 7 * 24
REQUEST_HISTORY_CLEANUP_INTERVAL_SECONDS = 3600
REQUEST_HISTORY_PAGE_LIMIT = 50
REQUEST_HISTORY_WINDOWS = {"1h", "6h", "24h", "7d", "custom"}
_UNSET = object()
UPSTREAM_STREAM_IDLE_TIMEOUT_SECONDS = 120.0
UPSTREAM_RESPONSE_HEADERS_TIMEOUT_SECONDS = 120.0
SSE_RETRY_EVENT_PARSE_BYTES = 256 * 1024
SSE_RETRY_PREFLIGHT_BYTES = 8 * 1024 * 1024
SSE_RETRY_MARKER_TAIL_BYTES = 512
USAGE_RESPONSE_BUFFER_BYTES = 8 * 1024 * 1024
USAGE_WINDOWS = {"today", "24h", "7d", "30d", "all", "custom"}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
REQUEST_HEADERS_TO_REPLACE = HOP_BY_HOP_HEADERS | {
    "authorization",
    "content-length",
    "host",
}
CODEX_TURN_METADATA_HEADER = "x-codex-turn-metadata"
MAX_CODEX_TURN_METADATA_CHARS = 32 * 1024
RESPONSE_HEADERS_TO_DROP = HOP_BY_HOP_HEADERS | {"content-length"}
RETRYABLE_STATUS_CODES = {402, 403, 500, 502, 503, 504}
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|api[-_ ]?key|access[-_ ]?token|token|secret)\b"
    r"[\"']?(\s*[:=]\s*)[\"']?(?:bearer\s+)?[^\s,;\"'}]+[\"']?"
)
BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|key|secret)=)[^&\s]+"
)
OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")
SSE_FAILURE_MARKER_RE = re.compile(
    rb"(?:\bevent\s*:\s*(?:response\.)?failed\b|"
    rb'(?<!\\)"type"\s*:\s*"(?:response\.)?failed"|'
    rb'(?<!\\)"status"\s*:\s*"failed"|'
    rb'(?<!\\)"error"\s*:\s*(?:\{|"))',
    re.IGNORECASE,
)
INPUT_ITEM_ID_ERROR_PARAM_RE = re.compile(r"^input\[(0|[1-9][0-9]*)\]\.id$")
class UpstreamStreamIdleTimeout(TimeoutError):
    """Raised when an upstream response produces no bytes for too long."""


class UpstreamResponseHeadersTimeout(TimeoutError):
    """Raised when an upstream response does not arrive in time."""


class RuntimeDiagnostics:
    """Small thread-safe snapshot of event-loop and request pressure."""

    def __init__(
        self,
        *,
        heartbeat_interval_seconds: float = 0.5,
        diagnostic_log_path: Path | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self._last_heartbeat_at = 0.0
        self._last_heartbeat_monotonic = 0.0
        self._last_lag_ms = 0.0
        self._max_lag_ms = 0.0
        self._watchdog_event_count = 0
        self._last_watchdog_at = 0.0
        self._last_watchdog_stall_ms = 0.0
        self._diagnostic_log_path = (
            str(diagnostic_log_path.expanduser().resolve())
            if diagnostic_log_path is not None
            else None
        )

    def arm(self) -> None:
        with self._lock:
            self._last_heartbeat_at = time.time()
            self._last_heartbeat_monotonic = time.monotonic()

    def observe_heartbeat(self, lag_ms: float) -> None:
        with self._lock:
            self._last_heartbeat_at = time.time()
            self._last_heartbeat_monotonic = time.monotonic()
            self._last_lag_ms = max(0.0, float(lag_ms))
            self._max_lag_ms = max(self._max_lag_ms, self._last_lag_ms)

    def stalled_for_ms(self) -> float:
        with self._lock:
            if not self._last_heartbeat_monotonic:
                return 0.0
            return max(
                0.0,
                (time.monotonic() - self._last_heartbeat_monotonic) * 1000,
            )

    def observe_watchdog_event(self, stalled_ms: float) -> None:
        with self._lock:
            self._watchdog_event_count += 1
            self._last_watchdog_at = time.time()
            self._last_watchdog_stall_ms = max(0.0, float(stalled_ms))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "heartbeat_interval_ms": round(self.heartbeat_interval_seconds * 1000),
                "last_heartbeat_at": (
                    round(self._last_heartbeat_at * 1000)
                    if self._last_heartbeat_at
                    else None
                ),
                "event_loop_lag_ms": round(self._last_lag_ms),
                "event_loop_max_lag_ms": round(self._max_lag_ms),
                "watchdog_event_count": self._watchdog_event_count,
                "last_watchdog_at": (
                    round(self._last_watchdog_at * 1000)
                    if self._last_watchdog_at
                    else None
                ),
                "last_watchdog_stall_ms": (
                    round(self._last_watchdog_stall_ms)
                    if self._last_watchdog_at
                    else None
                ),
                "diagnostic_log_path": self._diagnostic_log_path,
            }


async def _event_loop_heartbeat(diagnostics: RuntimeDiagnostics) -> None:
    loop = asyncio.get_running_loop()
    interval = max(0.1, diagnostics.heartbeat_interval_seconds)
    expected = loop.time() + interval
    while True:
        await asyncio.sleep(max(0.0, expected - loop.time()))
        now = loop.time()
        diagnostics.observe_heartbeat(max(0.0, now - expected) * 1000)
        expected += interval


async def _next_stream_chunk(
    stream: AsyncIterator[bytes],
    *,
    timeout_seconds: float = UPSTREAM_STREAM_IDLE_TIMEOUT_SECONDS,
) -> bytes:
    try:
        return await asyncio.wait_for(anext(stream), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise UpstreamStreamIdleTimeout(
            f"上游响应连续 {timeout_seconds:g} 秒没有返回数据"
        ) from exc


async def _stream_with_idle_timeout(
    stream: AsyncIterator[bytes],
    *,
    timeout_seconds: float = UPSTREAM_STREAM_IDLE_TIMEOUT_SECONDS,
) -> AsyncIterator[bytes]:
    try:
        while True:
            try:
                chunk = await _next_stream_chunk(
                    stream,
                    timeout_seconds=timeout_seconds,
                )
            except StopAsyncIteration:
                return
            yield chunk
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


def _stream_idle_retry_summary(exc: UpstreamStreamIdleTimeout) -> str:
    return _sanitize_retry_summary(str(exc)) or _retry_kind_summary("stream_idle_timeout")


async def _send_upstream_response(
    client: Any,
    request: Any,
    *,
    timeout_seconds: float = UPSTREAM_RESPONSE_HEADERS_TIMEOUT_SECONDS,
) -> Any:
    try:
        return await asyncio.wait_for(
            client.send(request, stream=True),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise UpstreamResponseHeadersTimeout(
            f"上游响应头连续 {timeout_seconds:g} 秒没有返回"
        ) from exc


def _finalize_stream_captures(
    usage_capture: Any | None,
    failure_capture: Any | None,
    status_code: int,
) -> tuple[TokenUsage | None, tuple[str, str] | None]:
    """Finalize CPU-heavy response parsing away from the ASGI event loop."""
    embedded_failure = (
        failure_capture.finalize()
        if failure_capture is not None
        else None
    )
    usage = (
        usage_capture.finalize(status_code)
        if usage_capture is not None
        else None
    )
    return usage, embedded_failure


def _feed_stream_captures(
    usage_capture: Any | None,
    failure_capture: Any | None,
    terminal_capture: Any | None,
    chunk: bytes,
) -> str | None:
    if usage_capture is not None:
        usage_capture.feed(chunk)
    if failure_capture is not None:
        failure_capture.feed(chunk)
    return terminal_capture.feed(chunk) if terminal_capture is not None else None


class DisconnectAwareStreamingResponse(StreamingResponse):
    """Stop a stalled upstream iterator as soon as the ASGI client disconnects."""

    def __init__(
        self,
        *args: Any,
        session_request_lease: "SessionRequestLease | None" = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._session_request_lease = session_request_lease

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] == "websocket":
            await super().__call__(scope, receive, send)
            return

        stream_task = asyncio.create_task(self.stream_response(send))
        disconnect_task = asyncio.create_task(self.listen_for_disconnect(receive))
        tasks = {stream_task, disconnect_task}
        try:
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if stream_task in done:
                await stream_task
            else:
                await disconnect_task
        finally:
            try:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._close_body_iterator()
            finally:
                if self._session_request_lease is not None:
                    await self._session_request_lease.release()

        if self.background is not None:
            await self.background()

    async def _close_body_iterator(self) -> None:
        close = getattr(self.body_iterator, "aclose", None)
        if close is None:
            return
        close_task = asyncio.create_task(close())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            with suppress(asyncio.CancelledError, Exception):
                await close_task
            raise


class SessionRequestLease:
    def __init__(
        self,
        coordinator: "SessionRequestCoordinator",
        thread_id: str | None,
        task: asyncio.Task[Any],
    ) -> None:
        self._coordinator = coordinator
        self.thread_id = thread_id
        self.task = task
        self.superseded = False
        self._released = False
        self._superseded_cleanup: Callable[[], Awaitable[None]] | None = None

    def set_superseded_cleanup(
        self,
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        self._superseded_cleanup = cleanup

    async def cleanup_superseded(self) -> None:
        if self._superseded_cleanup is not None:
            await self._superseded_cleanup()

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._coordinator.release(self)


class SessionRequestCoordinator:
    """Keep one live ASGI request for each real Codex thread id."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: dict[str, SessionRequestLease] = {}

    async def acquire(self, thread_id: str | None) -> SessionRequestLease:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("同会话请求协调必须运行在 asyncio 任务中")
        lease = SessionRequestLease(self, thread_id, task)
        previous: SessionRequestLease | None = None
        if thread_id:
            async with self._lock:
                previous = self._active.get(thread_id)
                self._active[thread_id] = lease
                if previous is not None and previous.task is not task:
                    previous.superseded = True
        try:
            if previous is not None and previous.task is not task:
                if not previous.task.done():
                    previous.task.cancel()
                await asyncio.gather(previous.task, return_exceptions=True)
                await previous.cleanup_superseded()
            return lease
        except BaseException:
            await lease.release()
            raise

    async def release(self, lease: SessionRequestLease) -> None:
        if not lease.thread_id:
            return
        async with self._lock:
            if self._active.get(lease.thread_id) is lease:
                self._active.pop(lease.thread_id, None)


SSE_MODEL_CAPACITY_CODE_RE = re.compile(
    rb"\bmodel(?:_at)?_capacity(?:_error)?\b",
    re.IGNORECASE,
)
SSE_MODEL_CAPACITY_MESSAGE_RE = re.compile(
    rb"\b(?:selected|requested|this)\s+model\s+is\s+"
    rb"(?:currently\s+)?at\s+capacity\b",
    re.IGNORECASE,
)


class ProviderConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ProxyProvider:
    provider_id: str
    name: str
    base_url: str
    is_cc_switch_current: bool
    wire_api: str = "responses"
    transport: str = "httpx"
    api_key: str | None = field(default=None, repr=False)
    configured_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    default_query: Mapping[str, str] = field(default_factory=dict)
    model: str | None = None
    model_mappings: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key or self.configured_headers)

    @property
    def display_endpoint(self) -> str:
        parsed = urlsplit(self.base_url)
        path = parsed.path.rstrip("/")
        return f"{parsed.netloc}{path}"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    source: str = "upstream"
    estimate_method: str | None = None


class UsageStore:
    """Persist aggregate-safe request usage without request or response content."""

    def __init__(self, path: Path, *, run_id: str | None = None) -> None:
        self.path = path
        self.run_id = str(run_id or uuid.uuid4().hex)[:64]
        self._lock = threading.Lock()
        self._inflight_lock = threading.Lock()
        self._last_request_history_cleanup_at = 0.0
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _connect_inflight(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.1)
        connection.execute("PRAGMA busy_timeout = 100")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS request_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL,
                    provider_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cached_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    usage_source TEXT NOT NULL,
                    estimate_method TEXT,
                    status_code INTEGER NOT NULL,
                    succeeded INTEGER
                );
                CREATE INDEX IF NOT EXISTS request_usage_recorded_at
                    ON request_usage(recorded_at);
                CREATE INDEX IF NOT EXISTS request_usage_provider_time
                    ON request_usage(provider_id, recorded_at);
                CREATE TABLE IF NOT EXISTS request_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL,
                    provider_id TEXT NOT NULL,
                    thread_id TEXT,
                    session_key TEXT,
                    session_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    upstream_model TEXT,
                    reasoning_effort TEXT,
                    status_code INTEGER,
                    succeeded INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    error_kind TEXT,
                    error_summary TEXT,
                    usage_id INTEGER,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_source TEXT,
                    estimate_method TEXT
                );
                CREATE INDEX IF NOT EXISTS request_history_finished_at
                    ON request_history(finished_at);
                CREATE INDEX IF NOT EXISTS request_history_provider_time
                    ON request_history(provider_id, finished_at);
                CREATE INDEX IF NOT EXISTS request_history_session_key
                    ON request_history(session_key);
                CREATE INDEX IF NOT EXISTS request_history_usage_id
                    ON request_history(usage_id);
                CREATE TABLE IF NOT EXISTS inflight_requests (
                    run_id TEXT NOT NULL,
                    request_id INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    provider_id TEXT NOT NULL,
                    thread_id TEXT,
                    session_key TEXT,
                    session_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    upstream_model TEXT,
                    reasoning_effort TEXT,
                    phase TEXT NOT NULL,
                    request_body_bytes INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS inflight_requests_updated_at
                    ON inflight_requests(updated_at);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(request_usage)")
            }
            if "succeeded" not in columns:
                connection.execute("ALTER TABLE request_usage ADD COLUMN succeeded INTEGER")
            history_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(request_history)")
            }
            if "reasoning_effort" not in history_columns:
                connection.execute(
                    "ALTER TABLE request_history ADD COLUMN reasoning_effort TEXT"
                )
            if "upstream_model" not in history_columns:
                connection.execute(
                    "ALTER TABLE request_history ADD COLUMN upstream_model TEXT"
                )
            inflight_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(inflight_requests)")
            }
            if "upstream_model" not in inflight_columns:
                connection.execute(
                    "ALTER TABLE inflight_requests ADD COLUMN upstream_model TEXT"
                )
            connection.execute(
                """
                UPDATE request_usage
                SET succeeded = CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END
                WHERE succeeded IS NULL
                """
            )
            self._recover_interrupted_requests(connection)

    def _recover_interrupted_requests(self, connection: sqlite3.Connection) -> None:
        recovered_at = time.time()
        connection.execute(
            """
            INSERT INTO request_history (
                started_at, finished_at, provider_id, thread_id, session_key,
                session_name, model, upstream_model, reasoning_effort,
                status_code, succeeded,
                outcome, duration_ms, retry_count, error_kind, error_summary,
                usage_id, input_tokens, output_tokens, total_tokens,
                cached_tokens, reasoning_tokens, usage_source, estimate_method
            )
            SELECT
                started_at, ?, provider_id, thread_id, session_key,
                session_name, model, upstream_model, reasoning_effort, NULL, 0,
                'interrupted',
                CAST(MAX(0, (? - started_at) * 1000) AS INTEGER),
                retry_count, 'process_restarted',
                '本地中转在请求完成前退出或重启（阶段：' || phase ||
                    '，请求体：' || request_body_bytes || ' 字节）',
                NULL, 0, 0, 0, 0, 0, NULL, NULL
            FROM inflight_requests
            """,
            (recovered_at, recovered_at),
        )
        connection.execute("DELETE FROM inflight_requests")

    def start_inflight_request(
        self,
        *,
        request_id: int,
        started_at: float,
        provider_id: str,
        thread_id: str | None,
        session_name: str = "未知会话",
        model: str = "unknown",
        upstream_model: str | None = None,
        reasoning_effort: str | None = None,
        phase: str = "accepted",
        request_body_bytes: int = 0,
        retry_count: int = 0,
    ) -> None:
        timestamp = time.time()
        safe_thread_id = thread_id if isinstance(thread_id, str) and thread_id else None
        values = (
            self.run_id,
            int(request_id),
            min(float(started_at), timestamp),
            timestamp,
            str(provider_id)[:240],
            safe_thread_id,
            _session_key(safe_thread_id),
            str(session_name or "未知会话")[:240],
            str(model or "unknown")[:240],
            None if not upstream_model else str(upstream_model)[:240],
            _normalize_reasoning_effort(reasoning_effort),
            str(phase or "accepted")[:40],
            max(0, int(request_body_bytes)),
            max(0, int(retry_count)),
        )
        with (
            self._inflight_lock,
            closing(self._connect_inflight()) as connection,
            connection,
        ):
            connection.execute(
                """
                INSERT OR REPLACE INTO inflight_requests (
                    run_id, request_id, started_at, updated_at, provider_id,
                    thread_id, session_key, session_name, model,
                    upstream_model, reasoning_effort, phase, request_body_bytes,
                    retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def update_inflight_request(
        self,
        request_id: int,
        *,
        provider_id: str | None = None,
        model: str | None = None,
        upstream_model: str | None | object = _UNSET,
        reasoning_effort: str | None = None,
        phase: str | None = None,
        request_body_bytes: int | None = None,
        retry_count: int | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        optional_values = (
            ("provider_id", None if provider_id is None else str(provider_id)[:240]),
            ("model", None if model is None else str(model or "unknown")[:240]),
            (
                "reasoning_effort",
                None if reasoning_effort is None else _normalize_reasoning_effort(reasoning_effort),
            ),
            ("phase", None if phase is None else str(phase or "requesting")[:40]),
            (
                "request_body_bytes",
                None if request_body_bytes is None else max(0, int(request_body_bytes)),
            ),
            ("retry_count", None if retry_count is None else max(0, int(retry_count))),
        )
        for column, value in optional_values:
            if value is None:
                continue
            assignments.append(f"{column} = ?")
            values.append(value)
        if upstream_model is not _UNSET:
            assignments.append("upstream_model = ?")
            values.append(
                None if not upstream_model else str(upstream_model)[:240]
            )
        values.extend((self.run_id, int(request_id)))
        with (
            self._inflight_lock,
            closing(self._connect_inflight()) as connection,
            connection,
        ):
            connection.execute(
                f"UPDATE inflight_requests SET {', '.join(assignments)} "
                "WHERE run_id = ? AND request_id = ?",
                values,
            )

    def record(
        self,
        *,
        provider_id: str,
        model: str,
        usage: TokenUsage,
        status_code: int,
        recorded_at: float | None = None,
        successful: bool | None = None,
    ) -> int:
        timestamp = time.time() if recorded_at is None else float(recorded_at)
        status = int(status_code)
        values = (
            timestamp,
            provider_id,
            model,
            max(0, usage.input_tokens),
            max(0, usage.output_tokens),
            max(0, usage.total_tokens),
            max(0, usage.cached_tokens),
            max(0, usage.reasoning_tokens),
            usage.source,
            usage.estimate_method,
            status,
            int(200 <= status < 300 if successful is None else bool(successful)),
        )
        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO request_usage (
                    recorded_at, provider_id, model, input_tokens, output_tokens,
                    total_tokens, cached_tokens, reasoning_tokens, usage_source,
                    estimate_method, status_code, succeeded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            return int(cursor.lastrowid)

    def record_request(
        self,
        *,
        request_id: int | None = None,
        started_at: float,
        provider_id: str,
        thread_id: str | None,
        session_name: str,
        model: str,
        status_code: int | None,
        successful: bool,
        outcome: str,
        retry_count: int,
        upstream_model: str | None = None,
        error_kind: str | None = None,
        error_summary: str | None = None,
        usage: TokenUsage | None = None,
        usage_id: int | None = None,
        finished_at: float | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        completed_at = time.time() if finished_at is None else float(finished_at)
        started = min(float(started_at), completed_at)
        safe_thread_id = thread_id if isinstance(thread_id, str) and thread_id else None
        safe_session_name = str(session_name or "未知会话")[:240]
        safe_model = str(model or "unknown")[:240]
        safe_upstream_model = (
            None
            if not upstream_model or str(upstream_model) == safe_model
            else str(upstream_model)[:240]
        )
        safe_reasoning_effort = _normalize_reasoning_effort(reasoning_effort)
        safe_summary = (
            _sanitize_retry_summary(error_summary) if error_summary else None
        )
        token_usage = usage or TokenUsage(0, 0, 0, source="none")
        values = (
            started,
            completed_at,
            str(provider_id),
            safe_thread_id,
            _session_key(safe_thread_id),
            safe_session_name,
            safe_model,
            safe_upstream_model,
            safe_reasoning_effort,
            None if status_code is None else int(status_code),
            int(bool(successful)),
            str(outcome)[:64],
            max(0, round((completed_at - started) * 1000)),
            max(0, int(retry_count)),
            None if error_kind is None else str(error_kind)[:80],
            safe_summary,
            usage_id,
            max(0, token_usage.input_tokens),
            max(0, token_usage.output_tokens),
            max(0, token_usage.total_tokens),
            max(0, token_usage.cached_tokens),
            max(0, token_usage.reasoning_tokens),
            None if usage is None else token_usage.source,
            None if usage is None else token_usage.estimate_method,
        )
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO request_history (
                    started_at, finished_at, provider_id, thread_id, session_key,
                    session_name, model, upstream_model, reasoning_effort,
                    status_code, succeeded, outcome,
                    duration_ms, retry_count, error_kind, error_summary, usage_id,
                    input_tokens, output_tokens, total_tokens, cached_tokens,
                    reasoning_tokens, usage_source, estimate_method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            if request_id is not None:
                connection.execute(
                    "DELETE FROM inflight_requests WHERE run_id = ? AND request_id = ?",
                    (self.run_id, int(request_id)),
                )
            self._cleanup_request_history_if_due(connection, completed_at)

    def request_history(
        self,
        *,
        window: str = "24h",
        start_at: float | None = None,
        end_at: float | None = None,
        status: str = "all",
        provider_id: str | None = None,
        query: str = "",
        cursor: str | None = None,
        limit: int = REQUEST_HISTORY_PAGE_LIMIT,
        now: float | None = None,
    ) -> dict[str, Any]:
        normalized_window = window.strip().lower()
        if normalized_window not in REQUEST_HISTORY_WINDOWS:
            raise ValueError("请求记录时间范围无效")
        normalized_status = status.strip().lower()
        if normalized_status not in {"all", "succeeded", "failed"}:
            raise ValueError("请求记录状态无效")
        timestamp = time.time() if now is None else float(now)
        if normalized_window == "custom":
            cutoff, upper_bound = _custom_time_bounds(
                start_at,
                end_at,
                max_seconds=REQUEST_HISTORY_HOURS * 3600,
            )
            if cutoff < timestamp - REQUEST_HISTORY_HOURS * 3600:
                raise ValueError("自定义时间不能早于最近 7 天的保留范围")
        else:
            hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 7 * 24}[
                normalized_window
            ]
            cutoff = timestamp - hours * 3600
            upper_bound = None
        bounded_limit = max(1, min(int(limit), REQUEST_HISTORY_PAGE_LIMIT))
        clauses = ["sort_at >= ?"]
        params: list[Any] = [cutoff]
        if upper_bound is not None:
            clauses.append("sort_at < ?")
            params.append(upper_bound)
        if normalized_status == "succeeded":
            clauses.append("succeeded = 1")
        elif normalized_status == "failed":
            clauses.append("succeeded = 0")
        if provider_id:
            clauses.append("provider_id = ?")
            params.append(str(provider_id))
        normalized_query = query.strip()[:100]
        if normalized_query:
            clauses.append(
                "(session_name LIKE ? ESCAPE '\\' OR model LIKE ? ESCAPE '\\' "
                "OR COALESCE(upstream_model, '') LIKE ? ESCAPE '\\' "
                "OR provider_id LIKE ? ESCAPE '\\' OR COALESCE(error_summary, '') LIKE ? ESCAPE '\\')"
            )
            escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            params.extend((pattern, pattern, pattern, pattern, pattern))
        if cursor:
            try:
                cursor_time_hex, cursor_id_text = cursor.rsplit("@", 1)
                cursor_time = float.fromhex(cursor_time_hex)
                cursor_id = int(cursor_id_text)
                if not math.isfinite(cursor_time) or cursor_id < 0:
                    raise ValueError
            except (AttributeError, OverflowError, TypeError, ValueError) as exc:
                raise ValueError("请求记录游标无效") from exc
            clauses.append("(sort_at < ? OR (sort_at = ? AND cursor_id < ?))")
            params.extend((cursor_time, cursor_time, cursor_id))
        where = " AND ".join(clauses)
        common_table = """
            WITH items AS (
                SELECT finished_at AS sort_at, id * 2 AS cursor_id,
                       started_at, finished_at, provider_id, thread_id,
                       session_key, session_name, model, upstream_model,
                       reasoning_effort,
                       status_code, succeeded,
                       outcome, duration_ms, retry_count, error_kind, error_summary,
                       input_tokens, output_tokens, total_tokens, cached_tokens,
                       reasoning_tokens, usage_source, estimate_method
                FROM request_history
                UNION ALL
                SELECT recorded_at AS sort_at, id * 2 + 1 AS cursor_id,
                       recorded_at AS started_at, recorded_at AS finished_at,
                       provider_id, NULL AS thread_id, NULL AS session_key,
                       '未知会话' AS session_name, model,
                       NULL AS upstream_model, NULL AS reasoning_effort,
                       status_code, succeeded,
                       CASE WHEN succeeded = 1 THEN 'succeeded' ELSE 'failed' END AS outcome,
                       NULL AS duration_ms, 0 AS retry_count, NULL AS error_kind,
                       NULL AS error_summary, input_tokens, output_tokens,
                       total_tokens, cached_tokens, reasoning_tokens,
                       usage_source, estimate_method
                FROM request_usage AS legacy
                WHERE NOT EXISTS (
                    SELECT 1 FROM request_history AS history
                    WHERE history.usage_id = legacy.id
                )
            )
        """
        count_clauses = [clause for clause in clauses if not clause.startswith("(sort_at <")]
        count_params = params[:-3] if cursor else params
        with self._lock, closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            if self._request_history_cleanup_due(timestamp):
                with connection:
                    self._delete_expired_request_history(connection, timestamp)
            total_count = int(
                connection.execute(
                    f"{common_table} SELECT COUNT(*) FROM items WHERE {' AND '.join(count_clauses)}",
                    count_params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                {common_table}
                SELECT * FROM items
                WHERE {where}
                ORDER BY sort_at DESC, cursor_id DESC
                LIMIT ?
                """,
                (*params, bounded_limit + 1),
            ).fetchall()
        has_more = len(rows) > bounded_limit
        visible_rows = rows[:bounded_limit]
        last_row = visible_rows[-1] if has_more and visible_rows else None
        return {
            "window": normalized_window,
            "start_at": round(cutoff * 1000),
            "end_at": None if upper_bound is None else round(upper_bound * 1000) - 1,
            "total_count": total_count,
            "items": [
                {
                    "started_at": round(float(row["started_at"]) * 1000),
                    "finished_at": round(float(row["finished_at"]) * 1000),
                    "provider_id": str(row["provider_id"]),
                    "_thread_id": row["thread_id"],
                    "session_key": row["session_key"],
                    "session_name": str(row["session_name"]),
                    "model": str(row["model"]),
                    "upstream_model": row["upstream_model"],
                    "reasoning_effort": row["reasoning_effort"],
                    "status_code": None if row["status_code"] is None else int(row["status_code"]),
                    "succeeded": bool(row["succeeded"]),
                    "outcome": str(row["outcome"]),
                    "duration_ms": (
                        None
                        if row["duration_ms"] is None
                        else int(row["duration_ms"])
                    ),
                    "retry_count": int(row["retry_count"]),
                    "error_kind": row["error_kind"],
                    "error_summary": row["error_summary"],
                    "input_tokens": int(row["input_tokens"]),
                    "output_tokens": int(row["output_tokens"]),
                    "total_tokens": int(row["total_tokens"]),
                    "cached_tokens": int(row["cached_tokens"]),
                    "reasoning_tokens": int(row["reasoning_tokens"]),
                    "usage_source": row["usage_source"],
                    "estimate_method": row["estimate_method"],
                }
                for row in visible_rows
            ],
            "next_cursor": (
                None
                if last_row is None
                else f"{float(last_row['sort_at']).hex()}@{int(last_row['cursor_id'])}"
            ),
        }

    def _request_history_cleanup_due(self, now: float) -> bool:
        return (
            now < self._last_request_history_cleanup_at
            or now - self._last_request_history_cleanup_at
            >= REQUEST_HISTORY_CLEANUP_INTERVAL_SECONDS
        )

    def _cleanup_request_history_if_due(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> None:
        if self._request_history_cleanup_due(now):
            self._delete_expired_request_history(connection, now)

    def _delete_expired_request_history(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> None:
        connection.execute(
            "DELETE FROM request_history WHERE finished_at < ?",
            (now - REQUEST_HISTORY_HOURS * 3600,),
        )
        self._last_request_history_cleanup_at = now

    def thread_id_for_session_key(self, session_key: str) -> str | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT thread_id FROM request_history
                WHERE session_key = ? AND thread_id IS NOT NULL
                ORDER BY finished_at DESC LIMIT 1
                """,
                (session_key,),
            ).fetchone()
        return None if row is None else str(row[0])

    def recent_sessions(self, since: float) -> tuple[dict[str, Any], ...]:
        """Return sessions represented by recent request history.

        The Codex session index is authoritative for names when available, but
        requests can arrive before that index is updated. Keep this fallback
        small and internal: only thread IDs, names, and last activity are
        returned to the profile merger.
        """
        cutoff = float(since)
        with self._lock, closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT thread_id, session_name, finished_at
                FROM request_history
                WHERE finished_at >= ? AND thread_id IS NOT NULL
                ORDER BY finished_at DESC, id DESC
                """,
                (cutoff,),
            ).fetchall()

        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            thread_id = str(row["thread_id"])
            if thread_id in sessions:
                continue
            sessions[thread_id] = {
                "thread_id": thread_id,
                "name": str(row["session_name"] or "未知会话").strip() or "未知会话",
                "updated_at": float(row["finished_at"]),
            }
        return tuple(sessions.values())

    def summary(
        self,
        window: str,
        *,
        start_at: float | None = None,
        end_at: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        normalized = window.strip().lower()
        if normalized not in USAGE_WINDOWS:
            raise ValueError("不支持的 Token 统计时间范围")
        timestamp = time.time() if now is None else float(now)
        if normalized == "custom":
            cutoff, upper_bound = _custom_time_bounds(start_at, end_at)
        else:
            cutoff = _usage_window_cutoff(normalized, timestamp)
            upper_bound = None
        clauses: list[str] = []
        params: list[float] = []
        if cutoff is not None:
            clauses.append("recorded_at >= ?")
            params.append(cutoff)
        if upper_bound is not None:
            clauses.append("recorded_at < ?")
            params.append(upper_bound)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        aggregate = """
            COUNT(*) AS request_count,
            COALESCE(SUM(CASE WHEN succeeded = 1 THEN 1 ELSE 0 END), 0)
                AS successful_requests,
            COALESCE(SUM(CASE WHEN succeeded = 1 THEN 0 ELSE 1 END), 0)
                AS failed_requests,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(CASE WHEN succeeded = 1 THEN total_tokens ELSE 0 END), 0)
                AS successful_tokens,
            COALESCE(SUM(CASE WHEN succeeded = 1 THEN 0 ELSE total_tokens END), 0)
                AS failed_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
            COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
            COALESCE(SUM(CASE WHEN usage_source = 'estimated' THEN 1 ELSE 0 END), 0)
                AS estimated_requests,
            MAX(recorded_at) AS last_request_at,
            MAX(CASE WHEN succeeded = 1 THEN recorded_at END)
                AS last_success_at
        """
        with self._lock, closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(
                f"SELECT {aggregate} FROM request_usage {where}", params
            ).fetchone()
            rows = connection.execute(
                f"""
                SELECT provider_id, {aggregate}
                FROM request_usage {where}
                GROUP BY provider_id
                """,
                params,
            ).fetchall()
        return {
            "window": normalized,
            "cutoff": cutoff,
            "start_at": None if cutoff is None else round(cutoff * 1000),
            "end_at": None if upper_bound is None else round(upper_bound * 1000) - 1,
            "total": _usage_summary_row(total),
            "by_provider": {
                str(row["provider_id"]): _usage_summary_row(row) for row in rows
            },
        }

    def history(
        self,
        *,
        provider_id: str,
        window: str,
        start_at: float | None = None,
        end_at: float | None = None,
        cursor: str | None = None,
        limit: int = USAGE_HISTORY_PAGE_LIMIT,
        now: float | None = None,
    ) -> dict[str, Any]:
        normalized = window.strip().lower()
        if normalized not in USAGE_WINDOWS:
            raise ValueError("不支持的 Token 统计时间范围")
        timestamp = time.time() if now is None else float(now)
        if normalized == "custom":
            cutoff, upper_bound = _custom_time_bounds(start_at, end_at)
        else:
            cutoff = _usage_window_cutoff(normalized, timestamp)
            upper_bound = None
        bounded_limit = max(1, min(int(limit), USAGE_HISTORY_PAGE_LIMIT))
        clauses = ["provider_id = ?"]
        params: list[Any] = [str(provider_id)]
        if cutoff is not None:
            clauses.append("recorded_at >= ?")
            params.append(cutoff)
        if upper_bound is not None:
            clauses.append("recorded_at < ?")
            params.append(upper_bound)
        count_clauses = list(clauses)
        count_params = list(params)
        if cursor:
            try:
                cursor_time_hex, cursor_id_text = cursor.rsplit("@", 1)
                cursor_time = float.fromhex(cursor_time_hex)
                cursor_id = int(cursor_id_text)
                if not math.isfinite(cursor_time) or cursor_id < 0:
                    raise ValueError
            except (AttributeError, OverflowError, TypeError, ValueError) as exc:
                raise ValueError("请求记录游标无效") from exc
            clauses.append("(recorded_at < ? OR (recorded_at = ? AND id < ?))")
            params.extend((cursor_time, cursor_time, cursor_id))
        where = " AND ".join(clauses)
        count_where = " AND ".join(count_clauses)
        with self._lock, closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(
                f"""
                SELECT COUNT(*) AS request_count,
                       COALESCE(SUM(CASE WHEN succeeded = 1 THEN 1 ELSE 0 END), 0)
                           AS successful_requests,
                       COALESCE(SUM(CASE WHEN succeeded = 1 THEN 0 ELSE 1 END), 0)
                           AS failed_requests,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(CASE WHEN succeeded = 1 THEN total_tokens ELSE 0 END), 0)
                           AS successful_tokens,
                       COALESCE(SUM(CASE WHEN succeeded = 1 THEN 0 ELSE total_tokens END), 0)
                           AS failed_tokens,
                       COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                       COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                       COALESCE(SUM(CASE WHEN usage_source = 'estimated' THEN 1 ELSE 0 END), 0)
                           AS estimated_requests,
                       MAX(recorded_at) AS last_request_at,
                       MAX(CASE WHEN succeeded = 1 THEN recorded_at END)
                           AS last_success_at
                FROM request_usage
                WHERE {count_where}
                """,
                count_params,
            ).fetchone()
            total_count = int(total["request_count"])
            rows = connection.execute(
                f"""
                SELECT id, recorded_at, model, input_tokens, output_tokens,
                       total_tokens, cached_tokens, reasoning_tokens,
                       usage_source, estimate_method, status_code, succeeded
                FROM request_usage
                WHERE {where}
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
                """,
                (*params, bounded_limit + 1),
            ).fetchall()
        has_more = len(rows) > bounded_limit
        visible_rows = rows[:bounded_limit]
        last_row = visible_rows[-1] if has_more and visible_rows else None
        return {
            "window": normalized,
            "start_at": None if cutoff is None else round(cutoff * 1000),
            "end_at": None if upper_bound is None else round(upper_bound * 1000) - 1,
            "provider_id": str(provider_id),
            "total_count": total_count,
            "total": _usage_summary_row(total),
            "items": [
                {
                    "recorded_at": round(float(row["recorded_at"]) * 1000),
                    "model": str(row["model"]),
                    "input_tokens": int(row["input_tokens"]),
                    "output_tokens": int(row["output_tokens"]),
                    "total_tokens": int(row["total_tokens"]),
                    "cached_tokens": int(row["cached_tokens"]),
                    "reasoning_tokens": int(row["reasoning_tokens"]),
                    "usage_source": str(row["usage_source"]),
                    "estimate_method": (
                        None
                        if row["estimate_method"] is None
                        else str(row["estimate_method"])
                    ),
                    "status_code": int(row["status_code"]),
                    "succeeded": bool(row["succeeded"]),
                }
                for row in visible_rows
            ],
            "next_cursor": (
                None
                if last_row is None
                else f"{float(last_row['recorded_at']).hex()}@{int(last_row['id'])}"
            ),
        }


class RecoveryHistoryStore:
    """Persist sanitized recovery events without request or response content."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._last_cleanup_at = 0.0
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recovery_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL,
                    request_started_at REAL,
                    request_id INTEGER NOT NULL,
                    provider_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    delay_seconds REAL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS recovery_events_recorded_at
                    ON recovery_events(recorded_at);
                CREATE INDEX IF NOT EXISTS recovery_events_provider_time
                    ON recovery_events(provider_id, recorded_at);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(recovery_events)")
            }
            if "request_started_at" not in columns:
                connection.execute(
                    "ALTER TABLE recovery_events ADD COLUMN request_started_at REAL"
                )
            self._delete_expired(connection, time.time())

    def record(
        self,
        *,
        request_id: int,
        provider_id: str,
        attempt: int,
        max_attempts: int,
        delay_seconds: float | None,
        kind: str,
        summary: str,
        stage: str,
        outcome: str,
        recorded_at: float | None = None,
        request_started_at: float | None = None,
    ) -> None:
        timestamp = time.time() if recorded_at is None else float(recorded_at)
        values = (
            timestamp,
            None if request_started_at is None else float(request_started_at),
            max(0, int(request_id)),
            str(provider_id),
            max(1, int(attempt)),
            int(max_attempts),
            None if delay_seconds is None else max(0.0, float(delay_seconds)),
            str(kind),
            _sanitize_retry_summary(summary),
            str(stage),
            str(outcome),
        )
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO recovery_events (
                    recorded_at, request_started_at, request_id, provider_id,
                    attempt, max_attempts, delay_seconds, kind, summary, stage, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            self._cleanup_if_due(connection, timestamp)

    def history(
        self,
        *,
        now: float | None = None,
        limit: int = RECOVERY_HISTORY_API_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        cutoff = timestamp - RECOVERY_HISTORY_HOURS * 3600
        bounded_limit = max(1, min(int(limit), RECOVERY_HISTORY_API_LIMIT))
        clauses = ["recorded_at >= ?"]
        params: list[Any] = [cutoff]
        if cursor:
            try:
                cursor_time_hex, cursor_id_text = cursor.rsplit("@", 1)
                cursor_time = float.fromhex(cursor_time_hex)
                cursor_id = int(cursor_id_text)
                if not math.isfinite(cursor_time) or cursor_id < 0:
                    raise ValueError
            except (AttributeError, OverflowError, TypeError, ValueError) as exc:
                raise ValueError("恢复记录游标无效") from exc
            clauses.append("(recorded_at < ? OR (recorded_at = ? AND id < ?))")
            params.extend((cursor_time, cursor_time, cursor_id))
        where = " AND ".join(clauses)
        with self._lock, closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            if self._cleanup_due(timestamp):
                with connection:
                    self._delete_expired(connection, timestamp)
            total_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM recovery_events WHERE recorded_at >= ?",
                    (cutoff,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT id, recorded_at, request_started_at, request_id, provider_id,
                       attempt, max_attempts, delay_seconds, kind, summary, stage, outcome
                FROM recovery_events
                WHERE {where}
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
                """,
                (*params, bounded_limit + 1),
            ).fetchall()
        has_more = len(rows) > bounded_limit
        visible_rows = rows[:bounded_limit]
        last_row = visible_rows[-1] if has_more and visible_rows else None
        return {
            "window_hours": RECOVERY_HISTORY_HOURS,
            "total_count": total_count,
            "truncated": has_more,
            "items": [
                {
                    "request_id": int(row["request_id"]),
                    "provider_id": str(row["provider_id"]),
                    "attempt": int(row["attempt"]),
                    "max_attempts": int(row["max_attempts"]),
                    "delay_seconds": (
                        None
                        if row["delay_seconds"] is None
                        else round(float(row["delay_seconds"]), 1)
                    ),
                    "kind": str(row["kind"]),
                    "summary": _sanitize_retry_summary(str(row["summary"])),
                    "stage": str(row["stage"]),
                    "outcome": str(row["outcome"]),
                    "recorded_at": round(float(row["recorded_at"]) * 1000),
                    "request_started_at": (
                        None
                        if row["request_started_at"] is None
                        else round(float(row["request_started_at"]) * 1000)
                    ),
                }
                for row in visible_rows
            ],
            "next_cursor": (
                None
                if last_row is None
                else f"{float(last_row['recorded_at']).hex()}@{int(last_row['id'])}"
            ),
        }

    def _cleanup_due(self, now: float) -> bool:
        return (
            now < self._last_cleanup_at
            or now - self._last_cleanup_at
            >= RECOVERY_HISTORY_CLEANUP_INTERVAL_SECONDS
        )

    def _cleanup_if_due(self, connection: sqlite3.Connection, now: float) -> None:
        if self._cleanup_due(now):
            self._delete_expired(connection, now)

    def _delete_expired(self, connection: sqlite3.Connection, now: float) -> None:
        cutoff = now - RECOVERY_HISTORY_HOURS * 3600
        connection.execute(
            "DELETE FROM recovery_events WHERE recorded_at < ?",
            (cutoff,),
        )
        self._last_cleanup_at = now


def _usage_summary_row(row: sqlite3.Row | None) -> dict[str, Any]:
    fields = (
        "request_count",
        "successful_requests",
        "failed_requests",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "successful_tokens",
        "failed_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "estimated_requests",
    )
    summary: dict[str, Any] = {
        field: int(row[field] or 0) if row is not None else 0 for field in fields
    }
    summary["last_success_at"] = (
        None
        if row is None or row["last_success_at"] is None
        else round(float(row["last_success_at"]) * 1000)
    )
    summary["last_request_at"] = (
        None
        if row is None or row["last_request_at"] is None
        else round(float(row["last_request_at"]) * 1000)
    )
    return summary


def _usage_window_cutoff(window: str, now: float) -> float | None:
    if window == "all":
        return None
    if window == "today":
        local = time.localtime(now)
        return time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        )
    seconds = {"24h": 24 * 3600, "7d": 7 * 24 * 3600, "30d": 30 * 24 * 3600}
    return now - seconds[window]


def _custom_time_bounds(
    start_at: float | None,
    end_at: float | None,
    *,
    max_seconds: float | None = None,
) -> tuple[float, float]:
    try:
        start = float(start_at)
        end = float(end_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("自定义时间必须同时提供开始和结束时间") from exc
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("自定义时间格式无效")
    if start >= end:
        raise ValueError("开始时间必须早于结束时间")
    if max_seconds is not None and end - start > max_seconds:
        raise ValueError(f"自定义时间范围不能超过 {round(max_seconds / 86400)} 天")
    return start, end


def _query_time_range(
    request: Request,
    *,
    window_param: str,
    default_window: str,
    allowed_windows: set[str],
    max_custom_seconds: float | None = None,
) -> tuple[str, float | None, float | None]:
    window = request.query_params.get(window_param, default_window).strip().lower()
    if window not in allowed_windows:
        raise ValueError("时间范围无效")
    if window != "custom":
        return window, None, None
    try:
        start_ms = int(request.query_params.get("start_at", ""))
        end_ms = int(request.query_params.get("end_at", ""))
    except ValueError as exc:
        raise ValueError("自定义时间必须同时提供开始和结束时间") from exc
    start, end_inclusive = _custom_time_bounds(
        start_ms / 1000,
        end_ms / 1000,
        max_seconds=max_custom_seconds,
    )
    return window, start, end_inclusive + 0.001


class UsageCapture:
    """Observe a streamed upstream response and resolve one final usage record."""

    def __init__(self, request_body: bytes, upstream_path: str) -> None:
        self.request_body = request_body
        self.upstream_path = upstream_path
        self.model = _request_model(request_body)
        self._response = bytearray()
        self._line_buffer = bytearray()
        self._output_segments: list[str] = []
        self._saw_output_delta = False
        self._upstream_usage: TokenUsage | None = None
        self._saw_successful_terminal_event = False
        self._finalized = False

    @property
    def saw_successful_terminal_event(self) -> bool:
        return self._saw_successful_terminal_event

    def feed(self, chunk: bytes) -> None:
        if self._finalized or not chunk:
            return
        remaining = USAGE_RESPONSE_BUFFER_BYTES - len(self._response)
        if remaining > 0:
            self._response.extend(chunk[:remaining])
        self._line_buffer.extend(chunk)
        while True:
            newline = self._line_buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._line_buffer[:newline]).strip()
            del self._line_buffer[: newline + 1]
            self._observe_sse_line(line)
        if len(self._line_buffer) > USAGE_RESPONSE_BUFFER_BYTES:
            del self._line_buffer[:-USAGE_RESPONSE_BUFFER_BYTES]

    def finalize(self, status_code: int) -> TokenUsage | None:
        if self._finalized:
            return None
        self._finalized = True
        if self._line_buffer:
            self._observe_sse_line(bytes(self._line_buffer).strip())
            self._line_buffer.clear()
        root = _decode_json(bytes(self._response))
        if root is not None:
            self._observe_json(root)
        if self._upstream_usage is not None:
            return self._upstream_usage
        if not 200 <= status_code < 300 or not _is_generation_path(self.upstream_path):
            return None
        if root is not None and not self._saw_output_delta:
            self._output_segments.extend(_response_output_segments(root))
        input_text = "\n".join(_request_token_segments(self.request_body))
        output_text = "\n".join(self._output_segments)
        input_tokens, input_method = _estimate_text_tokens(input_text, self.model)
        output_tokens, output_method = _estimate_text_tokens(output_text, self.model)
        method = input_method if input_method == output_method else f"{input_method}+{output_method}"
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            source="estimated",
            estimate_method=method,
        )

    def _observe_sse_line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        root = _decode_json(payload)
        if root is not None:
            self._observe_json(root)

    def _observe_json(self, root: Any) -> None:
        if not isinstance(root, dict):
            return
        usage = _usage_from_payload(root)
        if usage is not None:
            self._upstream_usage = usage
        event_type = root.get("type")
        response = root.get("response") if isinstance(root.get("response"), dict) else {}
        if (
            event_type == "response.completed"
            and root.get("status") != "failed"
            and response.get("status") != "failed"
        ):
            self._saw_successful_terminal_event = True
        if event_type in {
            "response.output_text.delta",
            "response.function_call_arguments.delta",
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            delta = root.get("delta")
            if isinstance(delta, str) and delta:
                self._output_segments.append(delta)
                self._saw_output_delta = True


def _decode_json(payload: bytes) -> Any | None:
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _input_item_id_error_index(root: Any) -> int | None:
    """Return the rejected input index only for the exact upstream error shape."""
    if not isinstance(root, dict):
        return None
    response = root.get("response") if isinstance(root.get("response"), dict) else {}
    candidates = (root, root.get("error"), response, response.get("error"))
    for node in candidates:
        if not isinstance(node, dict):
            continue
        if (
            node.get("type") != "invalid_request_error"
            or node.get("code") != "invalid_value"
        ):
            continue
        match = INPUT_ITEM_ID_ERROR_PARAM_RE.fullmatch(str(node.get("param", "")))
        if match is not None:
            return int(match.group(1))
    return None


def _input_item_id_error_index_from_body(body: bytes) -> int | None:
    return _input_item_id_error_index(_decode_json(body))


def _strip_input_item_ids(
    payload: bytes,
    *,
    only_indexes: Iterable[int] | None = None,
) -> tuple[bytes | None, int]:
    """Remove safe top-level Responses input item IDs from a fresh JSON copy."""
    root = _decode_json(payload)
    if not isinstance(root, dict) or not isinstance(root.get("input"), list):
        return None, 0
    items = root["input"]
    changed = 0
    indexes = range(len(items)) if only_indexes is None else tuple(only_indexes)
    for index in indexes:
        if not isinstance(index, int) or index < 0 or index >= len(items):
            continue
        item = items[index]
        if not isinstance(item, dict):
            continue
        if item.get("type") == "item_reference":
            continue
        if isinstance(item.get("id"), str):
            item.pop("id", None)
            changed += 1
    if not changed:
        return None, 0
    try:
        return json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), changed
    except (TypeError, ValueError):
        return None, 0


def _rewrite_request_model(
    payload: bytes,
    target: str | Mapping[str, str],
) -> bytes | None:
    """Rewrite the top-level model using either a fixed name or an exact mapping."""
    if isinstance(target, str):
        if not target.strip():
            return None
        root = _decode_json(payload)
        if not isinstance(root, dict):
            return None
        current = root.get("model")
        if not isinstance(current, str) or not current.strip() or current == target:
            return None
        root["model"] = target
    else:
        if not target:
            return payload
        root = _decode_json(payload)
        if not isinstance(root, dict) or not isinstance(root.get("model"), str):
            return payload
        local_model = root["model"].strip()
        upstream_model = target.get(local_model)
        if not isinstance(upstream_model, str) or not upstream_model.strip():
            return payload
        upstream_model = upstream_model.strip()
        if local_model == upstream_model:
            return payload
        root["model"] = upstream_model
    try:
        return json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None if isinstance(target, str) else payload


def _normalize_reasoning_effort(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:40] if normalized else None


def _request_metadata(payload: bytes) -> tuple[str, str | None]:
    root = _decode_json(payload)
    if not isinstance(root, dict):
        return "unknown", None
    model = root["model"].strip() if isinstance(root.get("model"), str) else "unknown"
    reasoning = root.get("reasoning")
    if isinstance(reasoning, dict):
        nested_effort = _normalize_reasoning_effort(reasoning.get("effort"))
        if nested_effort is not None:
            return model, nested_effort
    return model, _normalize_reasoning_effort(root.get("reasoning_effort"))


def _request_model(payload: bytes) -> str:
    return _request_metadata(payload)[0]


def _request_reasoning_effort(payload: bytes) -> str | None:
    return _request_metadata(payload)[1]


def _usage_from_payload(root: dict[str, Any]) -> TokenUsage | None:
    candidates: list[Any] = []
    response = root.get("response")
    if isinstance(response, dict):
        candidates.append(response.get("usage"))
    candidates.append(root.get("usage"))
    for node in candidates:
        if not isinstance(node, dict):
            continue
        known = any(
            key in node
            for key in (
                "input_tokens",
                "prompt_tokens",
                "output_tokens",
                "completion_tokens",
                "total_tokens",
            )
        )
        if not known:
            continue
        input_tokens = _token_int(node.get("input_tokens", node.get("prompt_tokens", 0)))
        output_tokens = _token_int(node.get("output_tokens", node.get("completion_tokens", 0)))
        total_tokens = _token_int(node.get("total_tokens", input_tokens + output_tokens))
        input_details = node.get("input_tokens_details", node.get("prompt_tokens_details", {}))
        output_details = node.get("output_tokens_details", node.get("completion_tokens_details", {}))
        cached_tokens = _token_int(input_details.get("cached_tokens", 0)) if isinstance(input_details, dict) else 0
        reasoning_tokens = _token_int(output_details.get("reasoning_tokens", 0)) if isinstance(output_details, dict) else 0
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or input_tokens + output_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            source="upstream",
        )
    return None


def _token_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _request_token_segments(payload: bytes) -> list[str]:
    root = _decode_json(payload)
    if not isinstance(root, dict):
        return []
    segments: list[str] = []
    _append_text(segments, root.get("instructions"))
    request_input = root.get("input")
    if isinstance(request_input, str):
        _append_text(segments, request_input)
    elif isinstance(request_input, list):
        for item in request_input:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                _append_text(segments, item.get("name"))
                _append_text(segments, item.get("arguments"))
            elif item_type == "function_call_output":
                _append_text(segments, item.get("output"))
            else:
                _append_text(segments, item.get("text"))
                _append_content_segments(segments, item.get("content"))
    tools = root.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            _append_text(segments, function.get("name"))
            _append_text(segments, function.get("description"))
            _append_json(segments, function.get("parameters"))
    text_format = root.get("text")
    if isinstance(text_format, dict) and isinstance(text_format.get("format"), dict):
        text_format = text_format["format"]
        _append_text(segments, text_format.get("name"))
        _append_json(segments, text_format.get("schema"))
    return segments


def _append_content_segments(segments: list[str], content: Any) -> None:
    if isinstance(content, str):
        _append_text(segments, content)
        return
    if not isinstance(content, list):
        return
    for part in content:
        if isinstance(part, dict):
            _append_text(segments, part.get("text"))
            _append_text(segments, part.get("refusal"))


def _append_text(segments: list[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        segments.append(value.strip())


def _append_json(segments: list[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        _append_text(segments, value)
        return
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    _append_text(segments, encoded)


def _response_output_segments(root: dict[str, Any]) -> list[str]:
    response = root.get("response")
    if isinstance(response, dict):
        root = response
    segments: list[str] = []
    output = root.get("output")
    if not isinstance(output, list):
        return segments
    for item in output:
        if not isinstance(item, dict):
            continue
        _append_text(segments, item.get("name"))
        _append_text(segments, item.get("arguments"))
        _append_content_segments(segments, item.get("content"))
        summary = item.get("summary")
        if isinstance(summary, list):
            _append_content_segments(segments, summary)
    return segments


def _estimate_text_tokens(text: str, model: str) -> tuple[int, str]:
    if not text:
        return 0, "tiktoken" if tiktoken is not None else "utf8_heuristic"
    if tiktoken is not None:
        lowered = model.strip().lower()
        encoding_name = "o200k_base" if lowered.startswith(("gpt-5", "gpt-4.1", "gpt-4o", "o1", "o3", "o4")) else "cl100k_base"
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(text)), "tiktoken"
    return max(1, (len(text.encode("utf-8")) + 3) // 4), "utf8_heuristic"


def _is_generation_path(upstream_path: str) -> bool:
    path = upstream_path.strip("/").lower()
    return path.endswith("responses") or path.endswith("chat/completions")


def order_proxy_providers(
    providers: Iterable[ProxyProvider], provider_order: Iterable[str]
) -> tuple[ProxyProvider, ...]:
    ordered = tuple(providers)
    positions = {
        provider_id: index
        for index, provider_id in enumerate(provider_order)
        if isinstance(provider_id, str)
    }
    original = {provider.provider_id: index for index, provider in enumerate(ordered)}
    return tuple(
        sorted(
            ordered,
            key=lambda provider: (
                positions.get(provider.provider_id, len(positions) + original[provider.provider_id]),
                original[provider.provider_id],
            ),
        )
    )


def filter_self_referencing_providers(
    providers: Iterable[ProxyProvider], port: int
) -> tuple[ProxyProvider, ...]:
    loopback_names = {"127.0.0.1", "localhost", "::1"}
    result: list[ProxyProvider] = []
    for provider in providers:
        parsed = urlsplit(provider.base_url)
        parsed_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.hostname in loopback_names and parsed_port == port:
            continue
        result.append(provider)
    return tuple(result)


@dataclass(frozen=True)
class RouteSnapshot:
    request_id: int
    provider: ProxyProvider
    started_at: float
    started_wall_at: float
    session_provider_id: str | None = None


@dataclass(frozen=True)
class ActiveRequest:
    request_id: int
    provider_id: str
    thread_id: str | None
    started_wall_at: float
    model: str = "unknown"
    upstream_model: str | None = None
    reasoning_effort: str | None = None
    request_body_bytes: int = 0
    phase: str = "requesting"
    last_activity_wall_at: float | None = None


@dataclass
class ForwardRequestLifecycle:
    router: "ProviderRouter"
    usage_store: UsageStore | None
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None
    thread_id: str | None
    snapshot: RouteSnapshot | None = None
    model: str = "unknown"
    upstream_model: str | None = None
    reasoning_effort: str | None = None
    retry_count: int = 0
    upstream_response: httpx.Response | None = None
    stream: AsyncIterator[bytes] | None = None
    cleaned: bool = False

    async def record_superseded(self) -> None:
        if self.cleaned:
            return
        self.cleaned = True
        response = self.upstream_response
        try:
            close_stream = getattr(self.stream, "aclose", None)
            if close_stream is not None:
                with suppress(Exception):
                    await close_stream()
        finally:
            if response is not None:
                with suppress(Exception):
                    await response.aclose()
        if self.snapshot is None:
            return
        status_code = None if response is None else response.status_code
        self.router.finish_request(
            self.snapshot,
            status_code=status_code,
            error="session_superseded",
        )
        await _record_request_event_async(
            self.usage_store,
            snapshot=self.snapshot,
            thread_id=self.thread_id,
            session_name_resolver=self.session_name_resolver,
            model=self.model,
            upstream_model=self.upstream_model,
            reasoning_effort=self.reasoning_effort,
            status_code=status_code,
            successful=False,
            outcome="cancelled",
            retry_count=self.retry_count,
            error_kind="session_superseded",
            error_summary="已由同会话新请求接管",
        )


@dataclass(frozen=True)
class ProxyStatus:
    current_provider_id: str | None
    active_by_provider: dict[str, int]
    active_request_details: tuple[ActiveRequest, ...]
    total_requests: int
    last_provider_id: str | None
    last_status_code: int | None
    last_error: str | None
    retrying_by_request: dict[int, "RetryProgress"]
    total_retries: int
    last_retry_kind: str | None
    last_retry_attempt: int | None
    recent_retry_errors: tuple["RetryErrorRecord", ...]
    circuit_open_by_provider: dict[str, float]


@dataclass(frozen=True)
class RetryProgress:
    provider_id: str
    attempt: int
    max_attempts: int
    delay_seconds: float
    kind: str
    summary: str


@dataclass(frozen=True)
class RetryErrorRecord:
    request_id: int
    provider_id: str
    attempt: int
    kind: str
    summary: str
    recorded_at: float
    request_started_at: float


@dataclass(frozen=True)
class RetryPolicy:
    enabled: bool = True
    max_attempts: int = 4
    delay_seconds: float = 1.0
    strategy: str = "exponential"
    max_delay_seconds: float = 30.0
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 30.0

    def backoff(self, retry_index: int) -> float:
        multiplier = 2 ** min(retry_index, 32) if self.strategy == "exponential" else 1
        return min(self.max_delay_seconds, self.delay_seconds * multiplier)

    def allows_attempt(self, attempt: int) -> bool:
        return self.enabled and (self.max_attempts == -1 or attempt <= self.max_attempts)

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_attempts": self.max_attempts,
            "delay_seconds": self.delay_seconds,
            "strategy": self.strategy,
            "max_delay_seconds": self.max_delay_seconds,
            "circuit_failure_threshold": self.circuit_failure_threshold,
            "circuit_cooldown_seconds": self.circuit_cooldown_seconds,
        }


class RetryPolicyStore:
    def __init__(self, policy: RetryPolicy | None = None) -> None:
        self._lock = threading.RLock()
        self._policy = policy or RetryPolicy()

    def get(self) -> RetryPolicy:
        with self._lock:
            return self._policy

    def replace(self, policy: RetryPolicy) -> None:
        with self._lock:
            self._policy = policy


class HealthStatusUrlStore:
    def __init__(self, url: str | None = None) -> None:
        self._lock = threading.RLock()
        self._url = normalize_health_status_url(url)

    def get(self) -> str | None:
        with self._lock:
            return self._url

    def replace(self, url: str | None) -> None:
        normalized = normalize_health_status_url(url)
        with self._lock:
            self._url = normalized


class InputItemIdCompatibilityStore:
    """Remember providers that reject replayed Responses input item IDs."""

    def __init__(
        self,
        *,
        ttl_seconds: float = INPUT_ITEM_ID_COMPATIBILITY_TTL_SECONDS,
        max_entries: int = INPUT_ITEM_ID_COMPATIBILITY_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._expires_at: dict[tuple[str, str], float] = {}

    def should_strip(self, thread_id: str | None, provider_id: str) -> bool:
        key = self._key(thread_id, provider_id)
        if key is None:
            return False
        now = self._clock()
        with self._lock:
            expires_at = self._expires_at.get(key)
            if expires_at is None:
                return False
            if expires_at <= now:
                self._expires_at.pop(key, None)
                return False
            return True

    def remember(self, thread_id: str | None, provider_id: str) -> None:
        key = self._key(thread_id, provider_id)
        if key is None:
            return
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            if key not in self._expires_at and len(self._expires_at) >= self._max_entries:
                oldest = min(self._expires_at, key=self._expires_at.__getitem__)
                self._expires_at.pop(oldest, None)
            self._expires_at[key] = now + self._ttl_seconds

    def _remove_expired(self, now: float) -> None:
        for key, expires_at in tuple(self._expires_at.items()):
            if expires_at <= now:
                self._expires_at.pop(key, None)

    @staticmethod
    def _key(thread_id: str | None, provider_id: str) -> tuple[str, str] | None:
        session_key = _session_key(thread_id)
        if session_key is None or not provider_id:
            return None
        return session_key, provider_id


def retry_policy_from_mapping(payload: Any) -> RetryPolicy:
    if not isinstance(payload, dict):
        raise ValueError("retry policy must be an object")
    enabled = payload.get("enabled", True)
    max_attempts = payload.get("max_attempts", 4)
    delay_seconds = payload.get("delay_seconds", 1.0)
    strategy = payload.get("strategy", "exponential")
    max_delay_seconds = payload.get("max_delay_seconds", 30.0)
    failure_threshold = payload.get("circuit_failure_threshold", 3)
    cooldown_seconds = payload.get("circuit_cooldown_seconds", 30.0)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or (max_attempts != -1 and not 1 <= max_attempts <= 100)
    ):
        raise ValueError("max_attempts is out of range")
    if strategy not in {"fixed", "exponential"}:
        raise ValueError("strategy is invalid")
    delay = _bounded_number(delay_seconds, minimum=0.1, maximum=300.0)
    max_delay = _bounded_number(max_delay_seconds, minimum=delay, maximum=3600.0)
    if (
        isinstance(failure_threshold, bool)
        or not isinstance(failure_threshold, int)
        or not 1 <= failure_threshold <= 100
    ):
        raise ValueError("circuit_failure_threshold is out of range")
    cooldown = _bounded_number(cooldown_seconds, minimum=1.0, maximum=3600.0)
    return RetryPolicy(
        enabled=enabled,
        max_attempts=max_attempts,
        delay_seconds=delay,
        strategy=strategy,
        max_delay_seconds=max_delay,
        circuit_failure_threshold=failure_threshold,
        circuit_cooldown_seconds=cooldown,
    )


def _bounded_number(value: Any, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a number")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError("number is out of range")
    return number


class ProviderRouter:
    def __init__(
        self,
        providers: Iterable[ProxyProvider],
        current_provider_id: str | None = None,
        session_provider_overrides: Mapping[str, str] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, ProxyProvider] = {}
        self._current_provider_id: str | None = None
        self._active: dict[str, int] = {}
        self._active_request_details: dict[int, ActiveRequest] = {}
        self._total_requests = 0
        self._last_provider_id: str | None = None
        self._last_status_code: int | None = None
        self._last_error: str | None = None
        self._retrying: dict[int, RetryProgress] = {}
        self._request_sequence = 0
        self._total_retries = 0
        self._last_retry_kind: str | None = None
        self._last_retry_attempt: int | None = None
        self._recent_retry_errors: deque[RetryErrorRecord] = deque(
            maxlen=RETRY_ERROR_HISTORY_LIMIT
        )
        self._consecutive_failures: dict[str, int] = {}
        self._circuit_open_until: dict[str, float] = {}
        self._session_provider_overrides = {
            str(thread_id): str(provider_id)
            for thread_id, provider_id in (session_provider_overrides or {}).items()
            if isinstance(thread_id, str)
            and thread_id
            and isinstance(provider_id, str)
            and provider_id
        }
        self.replace_providers(providers, preferred_id=current_provider_id)

    def providers(self) -> tuple[ProxyProvider, ...]:
        with self._lock:
            return tuple(self._providers.values())

    def current_provider(self) -> ProxyProvider | None:
        with self._lock:
            if self._current_provider_id is None:
                return None
            return self._providers.get(self._current_provider_id)

    def replace_providers(
        self,
        providers: Iterable[ProxyProvider],
        *,
        preferred_id: str | None = None,
    ) -> ProxyProvider | None:
        ordered = tuple(providers)
        by_id = {provider.provider_id: provider for provider in ordered}
        if len(by_id) != len(ordered):
            raise ProviderConfigurationError("CC Switch 包含重复的供应商 ID")
        with self._lock:
            previous_id = self._current_provider_id
            self._providers = by_id
            candidates = (
                preferred_id,
                previous_id,
                next(
                    (provider.provider_id for provider in ordered if provider.is_cc_switch_current),
                    None,
                ),
                ordered[0].provider_id if ordered else None,
            )
            self._current_provider_id = next(
                (provider_id for provider_id in candidates if provider_id in by_id),
                None,
            )
            return self.current_provider()

    def select(self, provider_id: str) -> ProxyProvider:
        with self._lock:
            provider = self._providers.get(provider_id)
            if provider is None:
                raise KeyError(provider_id)
            self._current_provider_id = provider_id
            return provider

    def begin_request(self, *, thread_id: str | None = None) -> RouteSnapshot:
        with self._lock:
            override_id = self._session_provider_overrides.get(thread_id or "")
            provider = (
                self._providers.get(override_id)
                if override_id is not None
                else self.current_provider()
            )
            if provider is None:
                if override_id is not None:
                    raise ProviderConfigurationError("会话指定的供应商当前不可用")
                raise ProviderConfigurationError("没有可用于转发的 CC Switch 供应商")
            open_until = self._circuit_open_until.get(provider.provider_id, 0.0)
            if open_until > time.monotonic():
                raise ProviderCircuitOpenError(open_until - time.monotonic())
            self._active[provider.provider_id] = self._active.get(provider.provider_id, 0) + 1
            self._total_requests += 1
            self._request_sequence += 1
            snapshot = RouteSnapshot(
                request_id=self._request_sequence,
                provider=provider,
                started_at=time.monotonic(),
                started_wall_at=time.time(),
                session_provider_id=override_id,
            )
            self._active_request_details[snapshot.request_id] = ActiveRequest(
                request_id=snapshot.request_id,
                provider_id=provider.provider_id,
                thread_id=thread_id,
                started_wall_at=snapshot.started_wall_at,
                last_activity_wall_at=snapshot.started_wall_at,
            )
            return snapshot

    def update_request_model(
        self,
        snapshot: RouteSnapshot,
        model: str,
        reasoning_effort: str | None = None,
        request_body_bytes: int | None = None,
    ) -> None:
        with self._lock:
            detail = self._active_request_details.get(snapshot.request_id)
            if detail is None:
                return
            self._active_request_details[snapshot.request_id] = ActiveRequest(
                request_id=detail.request_id,
                provider_id=detail.provider_id,
                thread_id=detail.thread_id,
                started_wall_at=detail.started_wall_at,
                model=str(model or "unknown")[:240],
                upstream_model=detail.upstream_model,
                reasoning_effort=_normalize_reasoning_effort(reasoning_effort),
                request_body_bytes=(
                    detail.request_body_bytes
                    if request_body_bytes is None
                    else max(0, int(request_body_bytes))
                ),
                phase=detail.phase,
                last_activity_wall_at=detail.last_activity_wall_at,
            )

    def update_request_upstream_model(
        self,
        snapshot: RouteSnapshot,
        upstream_model: str | None,
    ) -> None:
        with self._lock:
            detail = self._active_request_details.get(snapshot.request_id)
            if detail is None:
                return
            normalized = (
                str(upstream_model)[:240]
                if isinstance(upstream_model, str) and upstream_model
                else None
            )
            self._active_request_details[snapshot.request_id] = ActiveRequest(
                request_id=detail.request_id,
                provider_id=detail.provider_id,
                thread_id=detail.thread_id,
                started_wall_at=detail.started_wall_at,
                model=detail.model,
                upstream_model=normalized,
                reasoning_effort=detail.reasoning_effort,
                request_body_bytes=detail.request_body_bytes,
                phase=detail.phase,
                last_activity_wall_at=detail.last_activity_wall_at,
            )

    def update_request_phase(
        self,
        snapshot: RouteSnapshot,
        phase: str,
        *,
        activity: bool = False,
    ) -> None:
        with self._lock:
            detail = self._active_request_details.get(snapshot.request_id)
            if detail is None:
                return
            self._active_request_details[snapshot.request_id] = ActiveRequest(
                request_id=detail.request_id,
                provider_id=detail.provider_id,
                thread_id=detail.thread_id,
                started_wall_at=detail.started_wall_at,
                model=detail.model,
                upstream_model=detail.upstream_model,
                reasoning_effort=detail.reasoning_effort,
                request_body_bytes=detail.request_body_bytes,
                phase=str(phase or "requesting")[:40],
                last_activity_wall_at=(
                    time.time() if activity else detail.last_activity_wall_at
                ),
            )

    def session_provider_override(self, thread_id: str | None) -> str | None:
        if not thread_id:
            return None
        with self._lock:
            return self._session_provider_overrides.get(thread_id)

    def set_session_provider_override(
        self,
        thread_id: str,
        provider_id: str | None,
    ) -> None:
        with self._lock:
            if provider_id is None:
                self._session_provider_overrides.pop(thread_id, None)
                return
            if provider_id not in self._providers:
                raise KeyError(provider_id)
            self._session_provider_overrides[thread_id] = provider_id

    def session_provider_overrides(self) -> dict[str, str]:
        with self._lock:
            return dict(self._session_provider_overrides)

    def thread_id_for_session_key(self, session_key: str) -> str | None:
        with self._lock:
            for detail in self._active_request_details.values():
                if _session_key(detail.thread_id) == session_key:
                    return detail.thread_id
        return None

    def route_retry_to_current(
        self,
        snapshot: RouteSnapshot,
    ) -> tuple[RouteSnapshot, bool]:
        with self._lock:
            detail = self._active_request_details.get(snapshot.request_id)
            override_id = (
                self._session_provider_overrides.get(detail.thread_id or "")
                if detail is not None
                else snapshot.session_provider_id
            )
            provider = (
                self._providers.get(override_id)
                if override_id is not None
                else self.current_provider()
            )
            if provider is None:
                return snapshot, False

            if provider.provider_id == snapshot.provider.provider_id:
                return (
                    RouteSnapshot(
                        request_id=snapshot.request_id,
                        provider=provider,
                        started_at=snapshot.started_at,
                        started_wall_at=snapshot.started_wall_at,
                        session_provider_id=override_id,
                    ),
                    False,
                )

            previous_id = snapshot.provider.provider_id
            remaining = max(0, self._active.get(previous_id, 1) - 1)
            if remaining:
                self._active[previous_id] = remaining
            else:
                self._active.pop(previous_id, None)
            self._active[provider.provider_id] = self._active.get(provider.provider_id, 0) + 1
            detail = self._active_request_details.get(snapshot.request_id)
            if detail is not None:
                self._active_request_details[snapshot.request_id] = ActiveRequest(
                    request_id=detail.request_id,
                    provider_id=provider.provider_id,
                    thread_id=detail.thread_id,
                    started_wall_at=detail.started_wall_at,
                    model=detail.model,
                    upstream_model=detail.upstream_model,
                    reasoning_effort=detail.reasoning_effort,
                    request_body_bytes=detail.request_body_bytes,
                    phase=detail.phase,
                    last_activity_wall_at=detail.last_activity_wall_at,
                )

            progress = self._retrying.get(snapshot.request_id)
            if progress is not None:
                self._retrying[snapshot.request_id] = RetryProgress(
                    provider_id=provider.provider_id,
                    attempt=progress.attempt,
                    max_attempts=progress.max_attempts,
                    delay_seconds=progress.delay_seconds,
                    kind=progress.kind,
                    summary=progress.summary,
                )

            return (
                RouteSnapshot(
                    request_id=snapshot.request_id,
                    provider=provider,
                    started_at=snapshot.started_at,
                    started_wall_at=snapshot.started_wall_at,
                    session_provider_id=override_id,
                ),
                True,
            )

    def finish_request(
        self,
        snapshot: RouteSnapshot,
        *,
        status_code: int | None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            detail = self._active_request_details.pop(snapshot.request_id, None)
            if detail is None:
                return
            provider_id = detail.provider_id
            remaining = max(0, self._active.get(provider_id, 1) - 1)
            if remaining:
                self._active[provider_id] = remaining
            else:
                self._active.pop(provider_id, None)
            self._last_provider_id = provider_id
            self._last_status_code = status_code
            self._last_error = error
            self._retrying.pop(snapshot.request_id, None)

    def record_retry(
        self,
        snapshot: RouteSnapshot,
        *,
        attempt: int,
        max_attempts: int,
        delay_seconds: float,
        kind: str,
        error_summary: str | None = None,
        error_provider_id: str | None = None,
    ) -> None:
        summary = _sanitize_retry_summary(error_summary or _retry_kind_summary(kind))
        with self._lock:
            self._retrying[snapshot.request_id] = RetryProgress(
                provider_id=snapshot.provider.provider_id,
                attempt=attempt,
                max_attempts=max_attempts,
                delay_seconds=delay_seconds,
                kind=kind,
                summary=summary,
            )
            detail = self._active_request_details.get(snapshot.request_id)
            if detail is not None:
                self._active_request_details[snapshot.request_id] = ActiveRequest(
                    request_id=detail.request_id,
                    provider_id=detail.provider_id,
                    thread_id=detail.thread_id,
                    started_wall_at=detail.started_wall_at,
                    model=detail.model,
                    upstream_model=detail.upstream_model,
                    reasoning_effort=detail.reasoning_effort,
                    request_body_bytes=detail.request_body_bytes,
                    phase="retrying",
                    last_activity_wall_at=detail.last_activity_wall_at,
                )
            self._recent_retry_errors.appendleft(
                RetryErrorRecord(
                    request_id=snapshot.request_id,
                    provider_id=error_provider_id or snapshot.provider.provider_id,
                    attempt=max(1, attempt - 1),
                    kind=kind,
                    summary=summary,
                    recorded_at=time.time(),
                    request_started_at=snapshot.started_wall_at,
                )
            )
            self._total_retries += 1
            self._last_retry_kind = kind
            self._last_retry_attempt = attempt

    def record_outcome(
        self,
        snapshot: RouteSnapshot,
        *,
        transient_failure: bool,
        policy: RetryPolicy,
    ) -> None:
        with self._lock:
            provider_id = snapshot.provider.provider_id
            if not transient_failure:
                self._consecutive_failures.pop(provider_id, None)
                self._circuit_open_until.pop(provider_id, None)
                return
            failures = self._consecutive_failures.get(provider_id, 0) + 1
            self._consecutive_failures[provider_id] = failures
            if failures >= policy.circuit_failure_threshold:
                self._circuit_open_until[provider_id] = (
                    time.monotonic() + policy.circuit_cooldown_seconds
                )
                self._consecutive_failures[provider_id] = 0

    def status(self) -> ProxyStatus:
        with self._lock:
            return ProxyStatus(
                current_provider_id=self._current_provider_id,
                active_by_provider=dict(self._active),
                active_request_details=tuple(self._active_request_details.values()),
                total_requests=self._total_requests,
                last_provider_id=self._last_provider_id,
                last_status_code=self._last_status_code,
                last_error=self._last_error,
                retrying_by_request=dict(self._retrying),
                total_retries=self._total_retries,
                last_retry_kind=self._last_retry_kind,
                last_retry_attempt=self._last_retry_attempt,
                recent_retry_errors=tuple(self._recent_retry_errors),
                circuit_open_by_provider={
                    provider_id: max(0.0, open_until - time.monotonic())
                    for provider_id, open_until in self._circuit_open_until.items()
                    if open_until > time.monotonic()
                },
            )


class ProviderCircuitOpenError(ProviderConfigurationError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("当前供应商正在短暂熔断")
        self.retry_after_seconds = retry_after_seconds


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderConfigurationError("base_url 必须是 HTTP 或 HTTPS 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderConfigurationError("base_url 不能包含凭据、查询参数或片段")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalize_health_status_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("health_status_url must be an HTTP or HTTPS URL")
    raw = value.strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("health_status_url must be an HTTP or HTTPS URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("health_status_url cannot contain credentials or a fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _default_ui_config(service_name: str) -> dict[str, Any]:
    claude = service_name == "claude-local-proxy"
    client_name = "Claude Code" if claude else "Codex"
    return {
        "service_id": "claude" if claude else "codex",
        "display_name": f"{client_name} 本地中转",
        "brand_mark": "CC" if claude else "CX",
        "client_name": client_name,
        "protocol_label": "Messages · SSE" if claude else "Responses · SSE",
        "proxy_url": f"http://127.0.0.1:{DEFAULT_PORT}" if claude else f"http://127.0.0.1:{DEFAULT_PORT}/v1",
        "peer_console_label": "Codex 控制台" if claude else "Claude Code 控制台",
        "peer_console_url": f"http://127.0.0.1:{DEFAULT_PORT}/control/{'codex' if claude else 'claude'}/",
        "config_endpoint": f"/control/api/{'claude' if claude else 'codex'}-config",
        "config_button_label": "导入到 CCS",
        "config_location_label": "Claude Code 配置位置" if claude else "Codex 配置文件",
        "config_location_hint": (
            "“导入到 CCS”会将本地中转注册为 Claude Code 供应商"
            if claude else
            "“导入到 CCS”会将本地中转注册为 Codex 供应商"
        ),
        "data_directory": "~/.codex-local-proxy",
        "config_location": "~/.claude/settings.json" if claude else "~/.codex/config.toml",
        "restart_config_text": "端口将在退出并重新启动本地中转后生效；届时需要重新导入到 CCS。",
        "copy_config_success_title": f"{client_name} 配置已复制",
        "copy_config_success_detail": "在当前终端运行配置后启动 Claude Code。" if claude else "首次配置后重启一次 Codex，后续切换不再需要重启。",
        "shutdown_client_name": client_name,
        "provider_label": "Claude Code" if claude else "Codex API",
        "theme_storage_key": "local-proxy-theme",
        "features": {
            "usage_history": True,
            "session_routing": not claude,
            "provider_launch_command": False,
        },
    }


def _diagnostic_active_requests(
    services: Iterable[tuple[str, ProviderRouter]],
) -> tuple[dict[str, Any], ...]:
    now = time.time()
    return tuple(
        {
            "service": service_name,
            "request_id": detail.request_id,
            "provider_id": detail.provider_id,
            "model": detail.model,
            "upstream_model": detail.upstream_model,
            "phase": detail.phase,
            "request_body_bytes": detail.request_body_bytes,
            "age_ms": max(0, round((now - detail.started_wall_at) * 1000)),
        }
        for service_name, router in services
        for detail in router.status().active_request_details
    )


def create_proxy_app(
    router: ProviderRouter | None = None,
    *,
    client: Any | None = None,
    client_factory: Callable[[], Any] | None = None,
    client_selector: Callable[[ProxyProvider], Any] | None = None,
    protocol_adapter: Any | None = None,
    reload_providers: Callable[[], tuple[ProxyProvider, ...]] | None = None,
    on_provider_selected: Callable[[str], None] | None = None,
    on_session_provider_override_changed: Callable[[str, str | None], None] | None = None,
    hidden_provider_ids: Iterable[str] = (),
    provider_order: Iterable[str] = (),
    on_hidden_provider_ids_changed: Callable[[tuple[str, ...]], None] | None = None,
    on_provider_order_changed: Callable[[tuple[str, ...]], None] | None = None,
    on_shutdown_requested: Callable[[], None] | None = None,
    config_fragment: Callable[[], str] | None = None,
    retry_policy: RetryPolicy | None = None,
    retry_policy_store: RetryPolicyStore | None = None,
    on_retry_policy_changed: Callable[[RetryPolicy], None] | None = None,
    usage_store: UsageStore | None = None,
    recovery_history_store: RecoveryHistoryStore | None = None,
    health_status_url: str | None = None,
    health_status_url_store: HealthStatusUrlStore | None = None,
    runtime_settings_snapshot: Callable[[], dict[str, Any]] | None = None,
    on_runtime_settings_changed: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    validate_runtime_database: Callable[[str], dict[str, Any]] | None = None,
    ui_config: Callable[[], Mapping[str, Any]] | None = None,
    retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    stream_idle_timeout_seconds: float = UPSTREAM_STREAM_IDLE_TIMEOUT_SECONDS,
    upstream_response_headers_timeout_seconds: float = UPSTREAM_RESPONSE_HEADERS_TIMEOUT_SECONDS,
    service_name: str = "codex-local-proxy",
    control_asset_dir: Path = CONTROL_ASSET_DIR,
    allowed_proxy_paths: frozenset[str] | None = None,
    provider_selectable: Callable[[ProxyProvider], bool] | None = None,
    provider_public_fields: Callable[[ProxyProvider], Mapping[str, Any]] | None = None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None = None,
    session_catalog: Callable[[float], Iterable[Mapping[str, Any]]] | None = None,
    session_key_resolver: Callable[[str], str | None] | None = None,
    input_item_id_compatibility_store: InputItemIdCompatibilityStore | None = None,
    config_endpoint_name: str = "codex-config",
    codex_profile: Any | None = None,
    claude_profile: Any | None = None,
    update_controller: Any | None = None,
    diagnostic_log: DiagnosticLog | None = None,
) -> FastAPI:
    if codex_profile is not None or claude_profile is not None:
        if codex_profile is None or claude_profile is None:
            raise ValueError("统一中转必须同时提供 Codex 和 Claude 协议配置")
        from local_proxy.server import create_unified_proxy_app

        return create_unified_proxy_app(
            codex_profile,
            claude_profile,
            control_asset_dir=control_asset_dir,
            on_shutdown_requested=on_shutdown_requested,
            update_controller=update_controller,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
            upstream_response_headers_timeout_seconds=upstream_response_headers_timeout_seconds,
            diagnostic_log=diagnostic_log,
        )
    if router is None:
        raise ValueError("必须提供供应商路由器")
    active_retry_policy_store = retry_policy_store or RetryPolicyStore(retry_policy)
    preferences_lock = threading.RLock()
    active_hidden_provider_ids = {
        provider_id for provider_id in hidden_provider_ids if isinstance(provider_id, str)
    }
    active_provider_order = [
        provider_id for provider_id in provider_order if isinstance(provider_id, str)
    ]
    upstream_client = client
    if upstream_client is None:
        upstream_client = (
            client_factory()
            if client_factory is not None
            else httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=None,
                    write=120.0,
                    pool=30.0,
                ),
                follow_redirects=False,
            )
        )
    owns_client = client is None
    active_health_status_url_store = health_status_url_store or HealthStatusUrlStore(
        health_status_url
    )
    active_input_item_id_compatibility_store = input_item_id_compatibility_store
    session_request_coordinator = SessionRequestCoordinator()
    runtime_diagnostics = RuntimeDiagnostics(
        diagnostic_log_path=(diagnostic_log.path if diagnostic_log is not None else None)
    )
    if (
        active_input_item_id_compatibility_store is None
        and service_name == "codex-local-proxy"
        and protocol_adapter is None
    ):
        active_input_item_id_compatibility_store = InputItemIdCompatibilityStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime_diagnostics.arm()
        heartbeat_task = asyncio.create_task(_event_loop_heartbeat(runtime_diagnostics))
        watchdog = (
            EventLoopWatchdog(
                runtime_diagnostics,
                diagnostic_log,
                active_requests=lambda: _diagnostic_active_requests(
                    ((service_name, router),)
                ),
            )
            if diagnostic_log is not None
            else None
        )
        if diagnostic_log is not None:
            diagnostic_log.write_event("service_started", service=service_name)
        if watchdog is not None:
            watchdog.start()
        try:
            yield
        finally:
            if watchdog is not None:
                watchdog.stop()
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if owns_client:
                await upstream_client.aclose()
            if diagnostic_log is not None:
                diagnostic_log.write_event("service_stopped", service=service_name)
                diagnostic_log.close()

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        current = router.current_provider()
        status = router.status()
        return {
            "service": service_name,
            "status": "ok" if current is not None else "not_configured",
            "current_provider": current.name if current else None,
            "active_requests": sum(status.active_by_provider.values()),
            "diagnostics": runtime_diagnostics.snapshot(),
        }

    @app.get("/control", include_in_schema=False)
    async def control_redirect() -> RedirectResponse:
        return RedirectResponse("/control/", status_code=307)

    @app.get("/control/", include_in_schema=False)
    async def control_page(request: Request) -> FileResponse:
        settings = runtime_settings_snapshot() if runtime_settings_snapshot is not None else None
        console_ui = select_control_ui(settings, request.query_params.get("ui"))
        return FileResponse(control_index_path(control_asset_dir, console_ui))

    @app.get("/control/api/ui-config", include_in_schema=False)
    async def control_ui_config():
        payload = dict(ui_config() if ui_config is not None else _default_ui_config(service_name))
        return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})

    @app.get("/control/static/{asset_name:path}", include_in_schema=False)
    async def control_asset(asset_name: str):
        asset_path = resolve_control_asset(control_asset_dir, asset_name)
        if asset_path is None:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        response = FileResponse(asset_path)
        response.headers["Cache-Control"] = "no-store"
        return response

    def public_status(
        window: str = "today",
        start_at: float | None = None,
        end_at: float | None = None,
    ) -> dict[str, Any]:
        with preferences_lock:
            hidden = set(active_hidden_provider_ids)
        usage = (
            usage_store.summary(window, start_at=start_at, end_at=end_at)
            if usage_store is not None
            else _empty_usage_summary(window)
        )
        recovery_history = None
        if recovery_history_store is not None:
            try:
                recovery_history = recovery_history_store.history(limit=1)
            except (OSError, sqlite3.Error):
                recovery_history = None
        return _public_control_status(
            router,
            active_retry_policy_store.get(),
            hidden_provider_ids=hidden,
            usage_summary=usage,
            recovery_history=recovery_history,
            health_status_url=active_health_status_url_store.get(),
            service_name=service_name,
            provider_public_fields=provider_public_fields,
            session_name_resolver=session_name_resolver,
            runtime_diagnostics=runtime_diagnostics,
        )

    def public_status_for_request(request: Request) -> dict[str, Any]:
        try:
            window, start_at, end_at = _query_time_range(
                request,
                window_param="usage_window",
                default_window="today",
                allowed_windows=USAGE_WINDOWS,
            )
        except ValueError:
            return public_status("today")
        return public_status(window, start_at, end_at)

    @app.get("/control/api/status", include_in_schema=False)
    async def control_status(request: Request):
        try:
            window, start_at, end_at = _query_time_range(
                request,
                window_param="usage_window",
                default_window="today",
                allowed_windows=USAGE_WINDOWS,
            )
            return await asyncio.to_thread(public_status, window, start_at, end_at)
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/control/api/recovery-history", include_in_schema=False)
    async def control_recovery_history(request: Request):
        if recovery_history_store is None:
            status = await asyncio.to_thread(public_status)
            history = status["retry"]["history"]
        else:
            try:
                limit = int(
                    request.query_params.get("limit", str(RECOVERY_HISTORY_API_LIMIT))
                )
                history = await asyncio.to_thread(
                    recovery_history_store.history,
                    limit=limit,
                    cursor=request.query_params.get("cursor"),
                )
            except ValueError as exc:
                return JSONResponse(status_code=422, content={"detail": str(exc)})
            except (OSError, sqlite3.Error):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "无法读取本地恢复记录"},
                )
        return JSONResponse(content=history, headers={"Cache-Control": "no-store"})

    @app.get("/control/api/usage-history", include_in_schema=False)
    async def control_usage_history(request: Request):
        provider_id = request.query_params.get("provider_id", "").strip()
        cursor = request.query_params.get("cursor")
        try:
            window, start_at, end_at = _query_time_range(
                request,
                window_param="usage_window",
                default_window="today",
                allowed_windows=USAGE_WINDOWS,
            )
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})
        if not provider_id or not any(
            provider.provider_id == provider_id for provider in router.providers()
        ):
            return JSONResponse(status_code=404, content={"detail": "供应商不存在"})
        if usage_store is None:
            return JSONResponse(status_code=503, content={"detail": "Token 记录功能不可用"})
        try:
            history = await asyncio.to_thread(
                usage_store.history,
                provider_id=provider_id,
                window=window,
                start_at=start_at,
                end_at=end_at,
                cursor=cursor,
            )
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"detail": "无法读取请求记录"})
        return JSONResponse(content=history, headers={"Cache-Control": "no-store"})

    @app.get("/control/api/requests", include_in_schema=False)
    async def control_requests(request: Request):
        if usage_store is None:
            return JSONResponse(status_code=503, content={"detail": "请求记录功能不可用"})
        try:
            window, start_at, end_at = _query_time_range(
                request,
                window_param="window",
                default_window="24h",
                allowed_windows=REQUEST_HISTORY_WINDOWS,
                max_custom_seconds=REQUEST_HISTORY_HOURS * 3600,
            )
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})
        status_filter = request.query_params.get("status", "all").strip().lower()
        provider_id = request.query_params.get("provider_id", "").strip() or None
        query = request.query_params.get("query", "")
        if provider_id and not any(
            provider.provider_id == provider_id for provider in router.providers()
        ):
            return JSONResponse(status_code=404, content={"detail": "供应商不存在"})
        try:
            payload = await asyncio.to_thread(
                _public_requests,
                router,
                usage_store,
                window=window,
                start_at=start_at,
                end_at=end_at,
                status_filter=status_filter,
                provider_id=provider_id,
                query=query,
                cursor=request.query_params.get("cursor"),
                session_name_resolver=session_name_resolver,
            )
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"detail": "无法读取本地请求记录"})
        return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})

    @app.get("/control/api/sessions", include_in_schema=False)
    async def control_sessions():
        if session_catalog is None:
            return JSONResponse(status_code=503, content={"detail": "会话路由功能不可用"})
        try:
            sessions = await asyncio.to_thread(
                session_catalog,
                time.time() - 7 * 24 * 3600,
            )
            payload = await asyncio.to_thread(
                _public_sessions,
                router,
                sessions,
                session_name_resolver=session_name_resolver,
            )
        except (OSError, TypeError, ValueError):
            return JSONResponse(status_code=503, content={"detail": "无法读取 Codex 会话列表"})
        return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})

    @app.post("/control/api/session-routes/{session_key}", include_in_schema=False)
    async def control_session_route(session_key: str, request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        if usage_store is None or not re.fullmatch(r"[0-9a-f]{24}", session_key):
            return JSONResponse(status_code=404, content={"detail": "未找到该会话"})
        try:
            payload = await request.json()
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=422, content={"detail": "会话路由格式无效"})
        provider_id = payload.get("provider_id") if isinstance(payload, dict) else ...
        if provider_id is not None and not isinstance(provider_id, str):
            return JSONResponse(status_code=422, content={"detail": "provider_id 必须是字符串或 null"})
        thread_id = router.thread_id_for_session_key(session_key)
        if thread_id is None:
            thread_id = await asyncio.to_thread(
                usage_store.thread_id_for_session_key,
                session_key,
            )
        if thread_id is None and session_key_resolver is not None:
            thread_id = await asyncio.to_thread(session_key_resolver, session_key)
        if thread_id is None:
            return JSONResponse(status_code=404, content={"detail": "未找到该会话"})
        if provider_id is not None:
            candidate = next(
                (provider for provider in router.providers() if provider.provider_id == provider_id),
                None,
            )
            if candidate is None:
                return JSONResponse(status_code=404, content={"detail": "供应商不存在"})
            if provider_selectable is not None and not provider_selectable(candidate):
                return JSONResponse(status_code=409, content={"detail": "该供应商与当前协议不兼容"})
        previous = router.session_provider_override(thread_id)
        try:
            router.set_session_provider_override(thread_id, provider_id)
            if on_session_provider_override_changed is not None:
                on_session_provider_override_changed(thread_id, provider_id)
        except (OSError, ValueError, sqlite3.Error):
            router.set_session_provider_override(thread_id, previous)
            return JSONResponse(status_code=503, content={"detail": "无法保存会话路由"})
        return JSONResponse(
            content={"session_key": session_key, "provider_id": provider_id},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/control/api/retry-policy", include_in_schema=False)
    async def control_retry_policy(request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        try:
            payload = await request.json()
            policy = retry_policy_from_mapping(payload)
        except (ValueError, TypeError):
            return JSONResponse(status_code=422, content={"detail": "重试设置格式无效"})
        active_retry_policy_store.replace(policy)
        if on_retry_policy_changed is not None:
            on_retry_policy_changed(policy)
        return public_status_for_request(request)

    @app.get("/control/api/runtime-settings", include_in_schema=False)
    async def control_runtime_settings():
        if runtime_settings_snapshot is None:
            return JSONResponse(status_code=503, content={"detail": "运行设置功能不可用"})
        return JSONResponse(
            content=runtime_settings_snapshot(),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/control/api/runtime-settings", include_in_schema=False)
    async def control_update_runtime_settings(request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        if on_runtime_settings_changed is None:
            return JSONResponse(status_code=503, content={"detail": "运行设置功能不可用"})
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("运行设置必须是对象")
            updated = on_runtime_settings_changed(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc) or "运行设置格式无效"},
            )
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"detail": "无法保存运行设置"})
        return JSONResponse(content=updated, headers={"Cache-Control": "no-store"})

    @app.post("/control/api/runtime-settings/validate-database", include_in_schema=False)
    async def control_validate_runtime_database(request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        if validate_runtime_database is None:
            return JSONResponse(status_code=503, content={"detail": "数据源验证功能不可用"})
        try:
            payload = await request.json()
            database_path = payload.get("database_path") if isinstance(payload, dict) else None
            if not isinstance(database_path, str) or not database_path.strip():
                raise ValueError("数据来源不能为空")
            result = validate_runtime_database(database_path)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc) or "数据来源无效"},
            )
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=422, content={"detail": "无法读取供应商数据库"})
        return JSONResponse(content=result, headers={"Cache-Control": "no-store"})

    @app.post("/control/api/providers/{provider_id}/select", include_in_schema=False)
    async def control_select(provider_id: str, request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        candidate = next(
            (provider for provider in router.providers() if provider.provider_id == provider_id),
            None,
        )
        if candidate is None:
            return JSONResponse(status_code=404, content={"detail": "未找到该供应商"})
        if provider_selectable is not None and not provider_selectable(candidate):
            return JSONResponse(status_code=409, content={"detail": "该供应商与当前协议不兼容"})
        try:
            selected = router.select(provider_id)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "未找到该供应商"})
        if on_provider_selected is not None:
            on_provider_selected(selected.provider_id)
        return public_status_for_request(request)

    @app.post("/control/api/providers/{provider_id}/visibility", include_in_schema=False)
    async def control_visibility(provider_id: str, request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        provider_ids = {provider.provider_id for provider in router.providers()}
        if provider_id not in provider_ids:
            return JSONResponse(status_code=404, content={"detail": "未找到该供应商"})
        try:
            payload = await request.json()
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=422, content={"detail": "显示设置格式无效"})
        hidden = payload.get("hidden") if isinstance(payload, dict) else None
        if not isinstance(hidden, bool):
            return JSONResponse(status_code=422, content={"detail": "hidden 必须是布尔值"})
        current = router.current_provider()
        if hidden and current is not None and current.provider_id == provider_id:
            return JSONResponse(status_code=409, content={"detail": "当前供应商不能隐藏，请先切换"})
        with preferences_lock:
            if hidden:
                active_hidden_provider_ids.add(provider_id)
            else:
                active_hidden_provider_ids.discard(provider_id)
            saved_hidden = tuple(
                item.provider_id
                for item in router.providers()
                if item.provider_id in active_hidden_provider_ids
            )
        if on_hidden_provider_ids_changed is not None:
            on_hidden_provider_ids_changed(saved_hidden)
        return public_status_for_request(request)

    @app.post("/control/api/providers/order", include_in_schema=False)
    async def control_provider_order(request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        try:
            payload = await request.json()
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(status_code=422, content={"detail": "排序设置格式无效"})
        requested = payload.get("provider_ids") if isinstance(payload, dict) else None
        current_ids = [provider.provider_id for provider in router.providers()]
        if (
            not isinstance(requested, list)
            or any(not isinstance(item, str) for item in requested)
            or len(set(requested)) != len(requested)
            or set(requested) != set(current_ids)
        ):
            return JSONResponse(status_code=422, content={"detail": "供应商排序必须完整且不能重复"})
        current = router.current_provider()
        by_id = {provider.provider_id: provider for provider in router.providers()}
        router.replace_providers(
            tuple(by_id[provider_id] for provider_id in requested),
            preferred_id=current.provider_id if current else None,
        )
        with preferences_lock:
            active_provider_order[:] = requested
            saved_order = tuple(active_provider_order)
        if on_provider_order_changed is not None:
            on_provider_order_changed(saved_order)
        return public_status_for_request(request)

    @app.post("/control/api/refresh", include_in_schema=False)
    async def control_refresh(request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        if reload_providers is None:
            return JSONResponse(status_code=503, content={"detail": "刷新功能不可用"})
        current = router.current_provider()
        try:
            providers = reload_providers()
            with preferences_lock:
                providers = order_proxy_providers(providers, active_provider_order)
            router.replace_providers(
                providers,
                preferred_id=current.provider_id if current else None,
            )
        except (OSError, ValueError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"detail": "无法读取 CC Switch 数据库"})
        return public_status_for_request(request)

    @app.get(f"/control/api/{config_endpoint_name}", include_in_schema=False)
    async def control_config():
        if config_fragment is None:
            return JSONResponse(status_code=503, content={"detail": "配置生成功能不可用"})
        return PlainTextResponse(config_fragment(), headers={"Cache-Control": "no-store"})

    @app.post("/control/api/shutdown", include_in_schema=False)
    async def control_shutdown(request: Request):
        if not _valid_control_request(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        if on_shutdown_requested is None:
            return JSONResponse(status_code=503, content={"detail": "退出功能不可用"})
        return JSONResponse(
            content={"status": "stopping"},
            background=BackgroundTask(on_shutdown_requested),
        )

    @app.api_route(
        "/v1/{upstream_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy_v1(upstream_path: str, request: Request):
        if allowed_proxy_paths is not None and upstream_path.strip("/") not in allowed_proxy_paths:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        current_provider = router.current_provider()
        if (
            current_provider is not None
            and provider_selectable is not None
            and not provider_selectable(current_provider)
        ):
            return JSONResponse(
                status_code=503,
                content={"error": {"message": "当前供应商与请求协议不兼容"}},
            )
        return await _forward_request(
            router,
            upstream_client,
            request,
            upstream_path,
            client_selector=client_selector,
            retry_policy=active_retry_policy_store.get(),
            retry_sleep=retry_sleep,
            usage_store=usage_store,
            recovery_history_store=recovery_history_store,
            protocol_adapter=protocol_adapter,
            session_name_resolver=session_name_resolver,
            input_item_id_compatibility_store=active_input_item_id_compatibility_store,
            session_request_coordinator=session_request_coordinator,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
            upstream_response_headers_timeout_seconds=upstream_response_headers_timeout_seconds,
        )

    return app


def _valid_control_request(request: Request) -> bool:
    return request.headers.get("X-Local-Proxy-Control") == "1"


def _codex_thread_id(headers: Mapping[str, str]) -> str | None:
    raw = headers.get(CODEX_TURN_METADATA_HEADER)
    if not isinstance(raw, str) or not raw or len(raw) > MAX_CODEX_TURN_METADATA_CHARS:
        return None
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(metadata, dict):
        return None
    thread_id = metadata.get("thread_id")
    if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 256:
        return None
    if any(ord(character) < 32 for character in thread_id):
        return None
    return thread_id


def _session_key(thread_id: str | None) -> str | None:
    if not thread_id:
        return None
    return hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:24]


def _public_control_status(
    router: ProviderRouter,
    retry_policy: RetryPolicy | None = None,
    *,
    hidden_provider_ids: Iterable[str] = (),
    usage_summary: dict[str, Any] | None = None,
    recovery_history: dict[str, Any] | None = None,
    health_status_url: str | None = None,
    service_name: str = "codex-local-proxy",
    provider_public_fields: Callable[[ProxyProvider], Mapping[str, Any]] | None = None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None = None,
    runtime_diagnostics: RuntimeDiagnostics | None = None,
) -> dict[str, Any]:
    status = router.status()
    policy = retry_policy or RetryPolicy()
    current_id = status.current_provider_id
    session_overrides = router.session_provider_overrides()
    draining_by_provider: dict[str, int] = {}
    for detail in status.active_request_details:
        expected_provider_id = session_overrides.get(detail.thread_id or "", current_id)
        if expected_provider_id == detail.provider_id:
            continue
        draining_by_provider[detail.provider_id] = (
            draining_by_provider.get(detail.provider_id, 0) + 1
        )
    hidden = set(hidden_provider_ids)
    recent_errors = [
        {
            "request_id": error.request_id,
            "provider_id": error.provider_id,
            "attempt": error.attempt,
            "max_attempts": policy.max_attempts,
            "delay_seconds": None,
            "kind": error.kind,
            "summary": error.summary,
            "stage": "before_output",
            "outcome": "retrying",
            "recorded_at": round(error.recorded_at * 1000),
            "request_started_at": round(error.request_started_at * 1000),
        }
        for error in status.recent_retry_errors
    ]
    history = recovery_history or {
        "window_hours": RECOVERY_HISTORY_HOURS,
        "total_count": len(recent_errors),
        "truncated": False,
        "items": recent_errors,
    }
    active_thread_ids = {
        detail.thread_id
        for detail in status.active_request_details
        if detail.thread_id is not None
    }
    session_names: Mapping[str, str] = {}
    if session_name_resolver is not None and active_thread_ids:
        try:
            session_names = session_name_resolver(active_thread_ids)
        except (OSError, ValueError, TypeError):
            session_names = {}

    def active_sessions(provider_id: str) -> list[dict[str, str]]:
        return [
            {
                "name": session_names.get(detail.thread_id, "未知会话")
                if detail.thread_id is not None
                else "未知会话"
            }
            for detail in status.active_request_details
            if detail.provider_id == provider_id
        ]

    now = time.time()
    active_details = [
        {
            "request_id": detail.request_id,
            "provider_id": detail.provider_id,
            "model": detail.model,
            "upstream_model": detail.upstream_model,
            "phase": detail.phase,
            "request_body_bytes": detail.request_body_bytes,
            "started_at": round(detail.started_wall_at * 1000),
            "age_ms": max(0, round((now - detail.started_wall_at) * 1000)),
            "last_activity_at": (
                round(detail.last_activity_wall_at * 1000)
                if detail.last_activity_wall_at is not None
                else None
            ),
        }
        for detail in status.active_request_details
    ]

    return {
        "service": service_name,
        "current_provider_id": current_id,
        "active_requests": sum(status.active_by_provider.values()),
        "active_by_provider": status.active_by_provider,
        "total_requests": status.total_requests,
        "last_provider_id": status.last_provider_id,
        "last_status_code": status.last_status_code,
        "last_error": status.last_error,
        "active_request_details": active_details,
        "diagnostics": (
            runtime_diagnostics.snapshot()
            if runtime_diagnostics is not None
            else None
        ),
        "health_status_url": health_status_url,
        "usage": usage_summary or _empty_usage_summary("today"),
        "retry": {
            **policy.as_public_dict(),
            "total_retries": status.total_retries,
            "last_kind": status.last_retry_kind,
            "last_attempt": status.last_retry_attempt,
            "active": [
                {
                    "request_id": request_id,
                    "provider_id": progress.provider_id,
                    "attempt": progress.attempt,
                    "max_attempts": progress.max_attempts,
                    "delay_seconds": progress.delay_seconds,
                    "kind": progress.kind,
                    "summary": progress.summary,
                }
                for request_id, progress in status.retrying_by_request.items()
            ],
            "recent_errors": recent_errors,
            "history": history,
            "circuit_open": [
                {
                    "provider_id": provider_id,
                    "retry_after_seconds": round(seconds, 1),
                }
                for provider_id, seconds in status.circuit_open_by_provider.items()
            ],
        },
        "providers": [
            {
                "provider_id": provider.provider_id,
                "name": provider.name,
                "endpoint": provider.display_endpoint,
                "current": provider.provider_id == current_id,
                "has_credentials": provider.has_credentials,
                "wire_api": provider.wire_api,
                "active_requests": status.active_by_provider.get(provider.provider_id, 0),
                "draining_requests": draining_by_provider.get(provider.provider_id, 0),
                "active_sessions": active_sessions(provider.provider_id),
                "hidden": provider.provider_id in hidden,
                **(
                    dict(provider_public_fields(provider))
                    if provider_public_fields is not None
                    else {}
                ),
            }
            for provider in router.providers()
        ],
    }


def _public_requests(
    router: ProviderRouter,
    usage_store: UsageStore,
    *,
    window: str = "24h",
    start_at: float | None = None,
    end_at: float | None = None,
    status_filter: str = "all",
    provider_id: str | None = None,
    query: str = "",
    cursor: str | None = None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    normalized_status = status_filter.strip().lower()
    if normalized_status not in {"all", "running", "succeeded", "failed"}:
        raise ValueError("请求记录状态无效")
    providers = {provider.provider_id: provider for provider in router.providers()}
    router_status = router.status()
    active_details = list(router_status.active_request_details)
    thread_ids = {
        detail.thread_id for detail in active_details if detail.thread_id is not None
    }
    resolved_names: Mapping[str, str] = {}
    if session_name_resolver is not None and thread_ids:
        try:
            resolved_names = session_name_resolver(thread_ids)
        except (OSError, TypeError, ValueError):
            resolved_names = {}
    normalized_query = query.strip().casefold()[:100]
    active: list[dict[str, Any]] = []
    if normalized_status in {"all", "running"} and cursor is None:
        for detail in active_details:
            if provider_id and detail.provider_id != provider_id:
                continue
            session_name = (
                resolved_names.get(detail.thread_id, "未知会话")
                if detail.thread_id is not None
                else "未知会话"
            )
            provider = providers.get(detail.provider_id)
            searchable = " ".join(
                (
                    session_name,
                    detail.model,
                    detail.upstream_model or "",
                    detail.provider_id,
                    provider.name if provider else "",
                )
            ).casefold()
            if normalized_query and normalized_query not in searchable:
                continue
            retry = router_status.retrying_by_request.get(detail.request_id)
            route_provider_id = router.session_provider_override(detail.thread_id)
            active.append(
                {
                    "state": "running",
                    "started_at": round(detail.started_wall_at * 1000),
                    "finished_at": None,
                    "provider_id": detail.provider_id,
                    "provider_name": provider.name if provider else detail.provider_id,
                    "session_key": _session_key(detail.thread_id),
                    "session_name": session_name,
                    "route_provider_id": route_provider_id,
                    "model": detail.model,
                    "upstream_model": detail.upstream_model,
                    "reasoning_effort": detail.reasoning_effort,
                    "phase": detail.phase,
                    "last_activity_at": (
                        round(detail.last_activity_wall_at * 1000)
                        if detail.last_activity_wall_at is not None
                        else None
                    ),
                    "status_code": None,
                    "succeeded": None,
                    "outcome": "retrying" if retry is not None else "receiving",
                    "duration_ms": None,
                    "retry_count": max(0, (retry.attempt - 1) if retry else 0),
                    "error_kind": retry.kind if retry else None,
                    "error_summary": retry.summary if retry else None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cached_tokens": 0,
                    "reasoning_tokens": 0,
                    "usage_source": None,
                    "estimate_method": None,
                }
            )
        active.sort(key=lambda item: item["started_at"], reverse=True)

    if normalized_status == "running":
        history = {
            "window": window,
            "total_count": 0,
            "items": [],
            "next_cursor": None,
        }
    else:
        history = usage_store.request_history(
            window=window,
            start_at=start_at,
            end_at=end_at,
            status=normalized_status,
            provider_id=provider_id,
            query=query,
            cursor=cursor,
        )
    history_thread_ids = {
        raw.get("_thread_id")
        for raw in history["items"]
        if raw.get("_thread_id") is not None
    }
    history_session_names: Mapping[str, str] = {}
    if session_name_resolver is not None and history_thread_ids:
        try:
            history_session_names = session_name_resolver(history_thread_ids)
        except (OSError, TypeError, ValueError):
            history_session_names = {}

    items: list[dict[str, Any]] = []
    for raw in history["items"]:
        item = dict(raw)
        thread_id = item.pop("_thread_id", None)
        current_name = history_session_names.get(thread_id)
        if current_name:
            item["session_name"] = current_name
        provider = providers.get(item["provider_id"])
        item["provider_name"] = provider.name if provider else item["provider_id"]
        item["route_provider_id"] = router.session_provider_override(thread_id)
        item["state"] = "succeeded" if item["succeeded"] else "failed"
        items.append(item)
    return {
        "window": history["window"],
        "active": active,
        "items": items,
        "next_cursor": history["next_cursor"],
        "total_count": len(active) + int(history["total_count"]),
    }


def _public_sessions(
    router: ProviderRouter,
    sessions: Iterable[Mapping[str, Any]],
    *,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    indexed: dict[str, dict[str, Any]] = {}
    for raw in sessions:
        if not isinstance(raw, Mapping):
            continue
        thread_id = raw.get("thread_id")
        name = raw.get("name")
        updated_at = raw.get("updated_at")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        if not isinstance(name, str) or not name.strip():
            name = "未知会话"
        try:
            timestamp = float(updated_at)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(timestamp):
            continue
        indexed[thread_id] = {
            "thread_id": thread_id,
            "name": name.strip(),
            "updated_at": timestamp,
            "active_requests": 0,
        }

    status = router.status()
    active_thread_ids = {
        detail.thread_id
        for detail in status.active_request_details
        if detail.thread_id is not None
    }
    resolved_names: Mapping[str, str] = {}
    if session_name_resolver is not None and active_thread_ids:
        try:
            resolved_names = session_name_resolver(active_thread_ids)
        except (OSError, TypeError, ValueError):
            resolved_names = {}
    for detail in status.active_request_details:
        if detail.thread_id is None:
            continue
        entry = indexed.setdefault(
            detail.thread_id,
            {
                "thread_id": detail.thread_id,
                "name": resolved_names.get(detail.thread_id, "未知会话"),
                "updated_at": detail.started_wall_at,
                "active_requests": 0,
            },
        )
        entry["updated_at"] = max(float(entry["updated_at"]), detail.started_wall_at)
        entry["active_requests"] = int(entry["active_requests"]) + 1

    items = [
        {
            "session_key": _session_key(entry["thread_id"]),
            "name": entry["name"],
            "updated_at": round(float(entry["updated_at"]) * 1000),
            "active": int(entry["active_requests"]) > 0,
            "active_requests": int(entry["active_requests"]),
            "route_provider_id": router.session_provider_override(entry["thread_id"]),
        }
        for entry in indexed.values()
    ]
    items.sort(
        key=lambda item: (
            not item["active"],
            -int(item["updated_at"]),
            str(item["name"]).casefold(),
            str(item["session_key"]),
        )
    )
    return {
        "window_days": 7,
        "total_count": len(items),
        "items": items[:500],
    }


def _empty_usage_summary(window: str) -> dict[str, Any]:
    return {
        "window": window,
        "cutoff": None,
        "total": _usage_summary_row(None),
        "by_provider": {},
    }


def _record_recovery_event(
    store: RecoveryHistoryStore | None,
    *,
    snapshot: RouteSnapshot,
    provider_id: str,
    attempt: int,
    max_attempts: int,
    delay_seconds: float | None,
    kind: str,
    summary: str | None,
    stage: str,
    outcome: str,
) -> None:
    if store is None:
        return
    try:
        store.record(
            request_id=snapshot.request_id,
            request_started_at=snapshot.started_wall_at,
            provider_id=provider_id,
            attempt=attempt,
            max_attempts=max_attempts,
            delay_seconds=delay_seconds,
            kind=kind,
            summary=summary or _retry_kind_summary(kind),
            stage=stage,
            outcome=outcome,
        )
    except (OSError, sqlite3.Error):
        pass


def _start_inflight_request(
    store: UsageStore | None,
    *,
    snapshot: RouteSnapshot,
    thread_id: str | None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None,
) -> None:
    if store is None:
        return
    session_name = "未知会话"
    if thread_id is not None and session_name_resolver is not None:
        try:
            session_name = session_name_resolver((thread_id,)).get(
                thread_id,
                session_name,
            )
        except (OSError, TypeError, ValueError):
            pass
    try:
        store.start_inflight_request(
            request_id=snapshot.request_id,
            started_at=snapshot.started_wall_at,
            provider_id=snapshot.provider.provider_id,
            thread_id=thread_id,
            session_name=session_name,
        )
    except (OSError, sqlite3.Error):
        pass


def _update_inflight_request(
    store: UsageStore | None,
    request_id: int,
    **changes: Any,
) -> None:
    if store is None:
        return
    try:
        store.update_inflight_request(request_id, **changes)
    except (OSError, sqlite3.Error):
        pass


def _record_request_event(
    store: UsageStore | None,
    *,
    snapshot: RouteSnapshot,
    thread_id: str | None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None,
    model: str,
    status_code: int | None,
    successful: bool,
    outcome: str,
    retry_count: int,
    upstream_model: str | None = None,
    error_kind: str | None = None,
    error_summary: str | None = None,
    usage: TokenUsage | None = None,
    usage_id: int | None = None,
    reasoning_effort: str | None = None,
) -> None:
    if store is None:
        return
    session_name = "未知会话"
    if thread_id is not None and session_name_resolver is not None:
        try:
            session_name = session_name_resolver((thread_id,)).get(
                thread_id,
                session_name,
            )
        except (OSError, TypeError, ValueError):
            pass
    try:
        store.record_request(
            request_id=snapshot.request_id,
            started_at=snapshot.started_wall_at,
            provider_id=snapshot.provider.provider_id,
            thread_id=thread_id,
            session_name=session_name,
            model=model,
            upstream_model=upstream_model,
            reasoning_effort=reasoning_effort,
            status_code=status_code,
            successful=successful,
            outcome=outcome,
            retry_count=retry_count,
            error_kind=error_kind,
            error_summary=error_summary,
            usage=usage,
            usage_id=usage_id,
        )
    except (OSError, sqlite3.Error):
        pass


async def _record_recovery_event_async(
    store: RecoveryHistoryStore | None,
    **event: Any,
) -> None:
    if store is None:
        return
    await asyncio.to_thread(_record_recovery_event, store, **event)


async def _start_inflight_request_async(
    store: UsageStore | None,
    **event: Any,
) -> None:
    if store is None:
        return
    task = asyncio.create_task(
        asyncio.to_thread(_start_inflight_request, store, **event)
    )
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise


async def _update_inflight_request_async(
    store: UsageStore | None,
    request_id: int,
    **changes: Any,
) -> None:
    if store is None:
        return
    await asyncio.to_thread(
        _update_inflight_request,
        store,
        request_id,
        **changes,
    )


async def _record_request_event_async(
    store: UsageStore | None,
    **event: Any,
) -> None:
    if store is None:
        return
    await asyncio.to_thread(_record_request_event, store, **event)


def _record_stream_completion(
    usage_store: UsageStore | None,
    recovery_history_store: RecoveryHistoryStore | None,
    *,
    snapshot: RouteSnapshot,
    thread_id: str | None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None,
    model: str,
    upstream_model: str | None,
    reasoning_effort: str | None,
    usage: TokenUsage | None,
    status_code: int,
    successful: bool,
    retry_count: int,
    error_kind: str | None,
    error_summary: str | None,
    stream_failure: tuple[str, str] | None,
    attempt: int,
    max_attempts: int,
) -> None:
    if stream_failure is not None:
        kind, summary = stream_failure
        _record_recovery_event(
            recovery_history_store,
            snapshot=snapshot,
            provider_id=snapshot.provider.provider_id,
            attempt=attempt,
            max_attempts=max_attempts,
            delay_seconds=None,
            kind=kind,
            summary=summary,
            stage="after_output",
            outcome="passed_through",
        )
    usage_id: int | None = None
    if usage_store is not None and usage is not None:
        try:
            usage_id = usage_store.record(
                provider_id=snapshot.provider.provider_id,
                model=model,
                usage=usage,
                status_code=status_code,
                successful=successful,
            )
        except (OSError, sqlite3.Error):
            pass
    _record_request_event(
        usage_store,
        snapshot=snapshot,
        thread_id=thread_id,
        session_name_resolver=session_name_resolver,
        model=model,
        upstream_model=upstream_model,
        reasoning_effort=reasoning_effort,
        status_code=status_code,
        successful=successful,
        outcome=(
            "succeeded"
            if successful
            else "cancelled"
            if error_kind in {"client_disconnected", "session_superseded"}
            else "failed"
        ),
        retry_count=retry_count,
        error_kind=error_kind,
        error_summary=error_summary,
        usage=usage,
        usage_id=usage_id,
    )


async def _record_stream_completion_async(
    usage_store: UsageStore | None,
    recovery_history_store: RecoveryHistoryStore | None,
    **event: Any,
) -> None:
    if usage_store is None and recovery_history_store is None:
        return
    await asyncio.to_thread(
        _record_stream_completion,
        usage_store,
        recovery_history_store,
        **event,
    )


async def _forward_request(
    router: ProviderRouter,
    client: Any,
    request: Request,
    upstream_path: str,
    *,
    client_selector: Callable[[ProxyProvider], Any] | None = None,
    retry_policy: RetryPolicy,
    retry_sleep: Callable[[float], Awaitable[None]],
    usage_store: UsageStore | None = None,
    recovery_history_store: RecoveryHistoryStore | None = None,
    protocol_adapter: Any | None = None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None = None,
    input_item_id_compatibility_store: InputItemIdCompatibilityStore | None = None,
    session_request_coordinator: SessionRequestCoordinator | None = None,
    stream_idle_timeout_seconds: float = UPSTREAM_STREAM_IDLE_TIMEOUT_SECONDS,
    upstream_response_headers_timeout_seconds: float = UPSTREAM_RESPONSE_HEADERS_TIMEOUT_SECONDS,
):
    thread_id = _codex_thread_id(request.headers)
    coordinator = session_request_coordinator or SessionRequestCoordinator()
    lease = await coordinator.acquire(thread_id)
    lifecycle = ForwardRequestLifecycle(
        router=router,
        usage_store=usage_store,
        session_name_resolver=session_name_resolver,
        thread_id=thread_id,
    )
    lease.set_superseded_cleanup(lifecycle.record_superseded)
    try:
        response = await _forward_request_with_lease(
            router,
            client,
            request,
            upstream_path,
            thread_id=thread_id,
            lease=lease,
            lifecycle=lifecycle,
            client_selector=client_selector,
            retry_policy=retry_policy,
            retry_sleep=retry_sleep,
            usage_store=usage_store,
            recovery_history_store=recovery_history_store,
            protocol_adapter=protocol_adapter,
            session_name_resolver=session_name_resolver,
            input_item_id_compatibility_store=input_item_id_compatibility_store,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
            upstream_response_headers_timeout_seconds=upstream_response_headers_timeout_seconds,
        )
    except asyncio.CancelledError:
        if lease.superseded:
            await lifecycle.record_superseded()
        await lease.release()
        raise
    except BaseException:
        await lease.release()
        raise
    if not isinstance(response, DisconnectAwareStreamingResponse):
        await lease.release()
    return response


async def _forward_request_with_lease(
    router: ProviderRouter,
    client: Any,
    request: Request,
    upstream_path: str,
    *,
    thread_id: str | None,
    lease: SessionRequestLease,
    lifecycle: ForwardRequestLifecycle,
    client_selector: Callable[[ProxyProvider], Any] | None = None,
    retry_policy: RetryPolicy,
    retry_sleep: Callable[[float], Awaitable[None]],
    usage_store: UsageStore | None = None,
    recovery_history_store: RecoveryHistoryStore | None = None,
    protocol_adapter: Any | None = None,
    session_name_resolver: Callable[[Iterable[str]], Mapping[str, str]] | None = None,
    input_item_id_compatibility_store: InputItemIdCompatibilityStore | None = None,
    stream_idle_timeout_seconds: float = UPSTREAM_STREAM_IDLE_TIMEOUT_SECONDS,
    upstream_response_headers_timeout_seconds: float = UPSTREAM_RESPONSE_HEADERS_TIMEOUT_SECONDS,
):
    try:
        snapshot = router.begin_request(thread_id=thread_id)
    except ProviderCircuitOpenError as exc:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "当前供应商暂时不可用，请稍后重试"}},
            headers={"Retry-After": str(max(1, int(exc.retry_after_seconds)))},
        )
    except ProviderConfigurationError:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "本地中转尚未配置可用供应商"}},
        )
    lifecycle.snapshot = snapshot
    provider = snapshot.provider
    await _start_inflight_request_async(
        usage_store,
        snapshot=snapshot,
        thread_id=thread_id,
        session_name_resolver=session_name_resolver,
    )
    if not provider.has_credentials:
        router.finish_request(snapshot, status_code=503, error="credential_missing")
        await _record_request_event_async(
            usage_store,
            snapshot=snapshot,
            thread_id=thread_id,
            session_name_resolver=session_name_resolver,
            model="unknown",
            status_code=503,
            successful=False,
            outcome="rejected",
            retry_count=0,
            error_kind="credential_missing",
            error_summary="当前供应商没有可用认证配置",
        )
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "当前供应商没有可用认证配置"}},
        )

    if _would_proxy_to_itself(provider, request):
        router.finish_request(snapshot, status_code=508, error="proxy_loop")
        await _record_request_event_async(
            usage_store,
            snapshot=snapshot,
            thread_id=thread_id,
            session_name_resolver=session_name_resolver,
            model="unknown",
            status_code=508,
            successful=False,
            outcome="rejected",
            retry_count=0,
            error_kind="proxy_loop",
            error_summary="当前供应商地址指向本地中转自身",
        )
        return JSONResponse(
            status_code=508,
            content={"error": {"message": "当前供应商地址指向本地中转自身"}},
        )

    try:
        request_body = await _read_request_body(request)
    except ValueError:
        router.finish_request(snapshot, status_code=413, error="request_too_large")
        await _record_request_event_async(
            usage_store,
            snapshot=snapshot,
            thread_id=thread_id,
            session_name_resolver=session_name_resolver,
            model="unknown",
            status_code=413,
            successful=False,
            outcome="rejected",
            retry_count=0,
            error_kind="request_too_large",
            error_summary="请求体超过本地中转允许的大小",
        )
        return JSONResponse(
            status_code=413,
            content={"error": {"message": "请求体超过本地中转允许的大小"}},
        )
    model, reasoning_effort = await asyncio.to_thread(_request_metadata, request_body)
    lifecycle.model = model
    lifecycle.reasoning_effort = reasoning_effort
    router.update_request_model(
        snapshot,
        model,
        reasoning_effort,
        request_body_bytes=len(request_body),
    )
    await _update_inflight_request_async(
        usage_store,
        snapshot.request_id,
        model=model,
        reasoning_effort=reasoning_effort,
        phase="connecting",
        request_body_bytes=len(request_body),
    )
    upstream_response: httpx.Response | None = None
    first_chunk: bytes | None = None
    stream: AsyncIterator[bytes] | None = None
    final_error = "upstream_unavailable"
    final_summary: str | None = None
    upstream_model: str | None = None
    response_body_decoded = False
    attempt = 1
    item_id_repairs = 0
    repaired_item_indexes_by_provider: dict[str, set[int]] = {}
    responses_request = upstream_path.strip("/").casefold() == "responses"
    reroute_before_attempt = True
    recorded_model = model
    while True:
        lifecycle.retry_count = max(0, attempt - 1)
        response_body_decoded = False
        if attempt > 1 and reroute_before_attempt:
            snapshot, _ = router.route_retry_to_current(snapshot)
            lifecycle.snapshot = snapshot
        reroute_before_attempt = True
        provider = snapshot.provider
        router.update_request_phase(snapshot, "connecting")
        await _update_inflight_request_async(
            usage_store,
            snapshot.request_id,
            provider_id=provider.provider_id,
            phase="connecting",
            retry_count=max(0, attempt - 1),
        )
        attempt_client = (
            client_selector(provider) if client_selector is not None else client
        )
        if not provider.has_credentials:
            final_error = "credential_missing"
            break
        if _would_proxy_to_itself(provider, request):
            final_error = "proxy_loop"
            break

        url = (
            protocol_adapter.upstream_url(provider, upstream_path)
            if protocol_adapter is not None
            and hasattr(protocol_adapter, "upstream_url")
            else _upstream_url(provider, upstream_path)
        )
        headers = (
            protocol_adapter.request_headers(request.headers, provider)
            if protocol_adapter is not None
            else _upstream_request_headers(request.headers, provider)
        )
        query_items = list(request.query_params.multi_items())
        existing_keys = {key for key, _ in query_items}
        query_items.extend(
            (key, value)
            for key, value in provider.default_query.items()
            if key not in existing_keys
        )
        request_body_for_attempt = request_body
        attempt_model = model
        mapping_applied = False
        if responses_request and provider.model_mappings:
            mapped_body = await asyncio.to_thread(
                _rewrite_request_model,
                request_body,
                provider.model_mappings,
            )
            if mapped_body is not None and mapped_body != request_body:
                request_body_for_attempt = mapped_body
                attempt_model, _ = await asyncio.to_thread(
                    _request_metadata,
                    mapped_body,
                )
                mapping_applied = True
        if not mapping_applied and provider.model:
            rewritten_body = await asyncio.to_thread(
                _rewrite_request_model,
                request_body,
                provider.model,
            )
            if rewritten_body is not None:
                request_body_for_attempt = rewritten_body
                attempt_model = provider.model
        if mapping_applied:
            upstream_model = attempt_model if attempt_model != model else None
            lifecycle.upstream_model = upstream_model
            router.update_request_upstream_model(snapshot, upstream_model)
            if recorded_model != model:
                recorded_model = model
                router.update_request_model(snapshot, model, reasoning_effort)
                await _update_inflight_request_async(
                    usage_store,
                    snapshot.request_id,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
            await _update_inflight_request_async(
                usage_store,
                snapshot.request_id,
                upstream_model=upstream_model,
            )
        else:
            upstream_model = None
            lifecycle.upstream_model = None
            router.update_request_upstream_model(snapshot, None)
            if attempt_model != recorded_model:
                recorded_model = attempt_model
                router.update_request_model(
                    snapshot,
                    attempt_model,
                    reasoning_effort,
                )
                await _update_inflight_request_async(
                    usage_store,
                    snapshot.request_id,
                    model=attempt_model,
                    reasoning_effort=reasoning_effort,
                )
        repaired_indexes = repaired_item_indexes_by_provider.get(provider.provider_id, set())
        strip_all_input_item_ids = (
            responses_request
            and not repaired_indexes
            and input_item_id_compatibility_store is not None
            and input_item_id_compatibility_store.should_strip(
                thread_id,
                provider.provider_id,
            )
        )
        if strip_all_input_item_ids or repaired_indexes:
            transformed_body, _ = await asyncio.to_thread(
                _strip_input_item_ids,
                request_body_for_attempt,
                only_indexes=None if strip_all_input_item_ids else repaired_indexes,
            )
            if transformed_body is not None:
                request_body_for_attempt = transformed_body
        retry_kind: str | None = None
        retry_summary: str | None = None
        retry_delay = retry_policy.backoff(attempt - 1)
        try:
            upstream_request = attempt_client.build_request(
                request.method,
                url,
                params=query_items,
                headers=headers,
                content=request_body_for_attempt,
            )
            upstream_response = await _send_upstream_response(
                attempt_client,
                upstream_request,
                timeout_seconds=upstream_response_headers_timeout_seconds,
            )
            lifecycle.upstream_response = upstream_response
            router.update_request_phase(snapshot, "waiting_first_chunk")
            await _update_inflight_request_async(
                usage_store,
                snapshot.request_id,
                phase="waiting_first_chunk",
            )
            retry_kind = None
            if retry_policy.enabled:
                retry_kind = (
                    protocol_adapter.retry_kind(upstream_response)
                    if protocol_adapter is not None
                    else _retry_kind(upstream_response)
                )
            if retry_kind is None:
                if upstream_response.is_stream_consumed:
                    first_chunk = upstream_response.content or None
                    stream = _empty_async_iterator()
                    lifecycle.stream = stream
                    response_body_decoded = "content-encoding" in upstream_response.headers
                else:
                    response_body_decoded = "content-encoding" in upstream_response.headers
                    if response_body_decoded:
                        stream = upstream_response.aiter_bytes()
                    else:
                        stream = upstream_response.aiter_raw()
                    stream = _stream_with_idle_timeout(
                        stream,
                        timeout_seconds=stream_idle_timeout_seconds,
                    )
                    lifecycle.stream = stream
                    try:
                        first_chunk = await anext(stream)
                    except StopAsyncIteration:
                        first_chunk = None
                    except httpx.HTTPError as exc:
                        retry_kind = "stream_start"
                        final_error = "stream_start_failed"
                        retry_summary = _exception_retry_summary(retry_kind, exc)
                if (
                    retry_kind is None
                    and first_chunk is None
                    and retry_policy.enabled
                    and protocol_adapter is not None
                    and hasattr(protocol_adapter, "empty_response_decision")
                ):
                    action, retry_kind, retry_summary = (
                        protocol_adapter.empty_response_decision(upstream_response)
                    )
                    if action != "retry":
                        retry_kind = None
                    else:
                        final_error = retry_kind or "malformed_response"
                if (
                    retry_kind is None
                    and first_chunk is not None
                    and upstream_response.status_code == 400
                    and (
                        retry_policy.enabled
                        or (
                            responses_request
                            and input_item_id_compatibility_store is not None
                        )
                    )
                ):
                    assert stream is not None
                    first_chunk, stream, retry_kind, retry_summary, repair_index = (
                        await _inspect_http_400_before_output(
                            upstream_response,
                            first_chunk,
                            stream,
                            detect_retryable=retry_policy.enabled,
                        )
                    )
                    lifecycle.stream = stream
                    repair_applied = False
                    if (
                        responses_request
                        and repair_index is not None
                        and item_id_repairs < MAX_INPUT_ITEM_ID_REPAIRS_PER_REQUEST
                        and repair_index not in repaired_indexes
                    ):
                        transformed_body, changed = await asyncio.to_thread(
                            _strip_input_item_ids,
                            request_body_for_attempt,
                            only_indexes=(repair_index,),
                        )
                        if transformed_body is not None and changed == 1:
                            repaired_item_indexes_by_provider.setdefault(
                                provider.provider_id,
                                set(),
                            ).add(repair_index)
                            item_id_repairs += 1
                            if input_item_id_compatibility_store is not None:
                                input_item_id_compatibility_store.remember(
                                    thread_id,
                                    provider.provider_id,
                                )
                            retry_kind = "request_item_id_repair"
                            retry_summary = (
                                f"请求体兼容修复：已移除 input[{repair_index}].id"
                            )
                            final_error = retry_kind
                            retry_delay = 0.0
                            repair_applied = True
                    if repair_index is not None and not repair_applied:
                        retry_kind = None
                        retry_summary = None
                    if retry_kind is not None:
                        final_error = retry_kind
                if (
                    retry_kind is None
                    and first_chunk is not None
                    and _is_event_stream(upstream_response)
                    and retry_policy.enabled
                ):
                    assert stream is not None
                    router.update_request_phase(snapshot, "preflighting_sse")
                    await _update_inflight_request_async(
                        usage_store,
                        snapshot.request_id,
                        phase="preflighting_sse",
                    )
                    first_chunk, retry_kind, retry_summary = (
                        await _inspect_sse_before_output(
                            first_chunk,
                            stream,
                            decision=(
                                protocol_adapter.sse_preflight_decision
                                if protocol_adapter is not None
                                else _sse_preflight_decision
                            ),
                        )
                    )
                    lifecycle.stream = stream
                    if retry_kind is not None:
                        final_error = retry_kind
                if (
                    retry_kind is None
                    and first_chunk is not None
                    and upstream_response.status_code == 404
                    and retry_policy.enabled
                ):
                    assert stream is not None
                    first_chunk, retry_kind, retry_summary = (
                        await _inspect_html_404_before_output(
                            upstream_response,
                            first_chunk,
                            stream,
                        )
                    )
                    lifecycle.stream = stream
                    if retry_kind is not None:
                        final_error = retry_kind
                if retry_kind is None:
                    break
            else:
                final_error = retry_kind
                if upstream_response.status_code == 429:
                    parsed_delay = _retry_after_seconds(upstream_response)
                    if parsed_delay is not None:
                        retry_delay = min(
                            parsed_delay,
                            retry_policy.max_delay_seconds,
                        )
        except UpstreamResponseHeadersTimeout as exc:
            retry_kind = "connection_timeout"
            final_error = "upstream_unavailable"
            retry_summary = _sanitize_retry_summary(str(exc)) or _retry_kind_summary(retry_kind)
        except UpstreamStreamIdleTimeout as exc:
            retry_kind = "stream_idle_timeout"
            final_error = retry_kind
            retry_summary = _stream_idle_retry_summary(exc)
        except httpx.HTTPError as exc:
            retry_kind = "connection"
            final_error = "upstream_unavailable"
            retry_summary = _exception_retry_summary(retry_kind, exc)

        request_disconnected = (
            await request.is_disconnected() if retry_kind is not None else False
        )
        can_retry = (
            retry_kind is not None
            and (
                retry_kind == "request_item_id_repair"
                or retry_policy.allows_attempt(attempt + 1)
            )
            and not request_disconnected
        )
        if retry_kind is not None and retry_summary is None and upstream_response is not None:
            retry_summary = await _response_retry_summary(upstream_response)
        if retry_summary is not None:
            final_summary = retry_summary
        if upstream_response is not None:
            await upstream_response.aclose()
            upstream_response = None
            lifecycle.upstream_response = None
            lifecycle.stream = None
        if not can_retry:
            if retry_kind is not None:
                await _record_recovery_event_async(
                    recovery_history_store,
                    snapshot=snapshot,
                    provider_id=snapshot.provider.provider_id,
                    attempt=attempt,
                    max_attempts=retry_policy.max_attempts,
                    delay_seconds=None,
                    kind=retry_kind,
                    summary=retry_summary,
                    stage="before_output",
                    outcome=(
                        "client_disconnected"
                        if request_disconnected
                        else "exhausted"
                    ),
                )
            break
        failed_provider_id = snapshot.provider.provider_id
        rerouted = False
        if retry_kind != "request_item_id_repair":
            snapshot, rerouted = router.route_retry_to_current(snapshot)
            lifecycle.snapshot = snapshot
            if rerouted:
                retry_delay = 0.0
            router.record_retry(
                snapshot,
                attempt=attempt + 1,
                max_attempts=retry_policy.max_attempts,
                delay_seconds=retry_delay,
                kind=retry_kind,
                error_summary=retry_summary,
                error_provider_id=failed_provider_id,
            )
            await _update_inflight_request_async(
                usage_store,
                snapshot.request_id,
                provider_id=snapshot.provider.provider_id,
                phase="retrying",
                retry_count=attempt,
            )
        await _record_recovery_event_async(
            recovery_history_store,
            snapshot=snapshot,
            provider_id=failed_provider_id,
            attempt=attempt,
            max_attempts=(
                MAX_INPUT_ITEM_ID_REPAIRS_PER_REQUEST + 1
                if retry_kind == "request_item_id_repair"
                else retry_policy.max_attempts
            ),
            delay_seconds=retry_delay,
            kind=retry_kind,
            summary=retry_summary,
            stage="before_output",
            outcome="retrying",
        )
        if retry_kind == "request_item_id_repair":
            reroute_before_attempt = False
        else:
            await retry_sleep(retry_delay)
            attempt += 1

    if upstream_response is None or stream is None:
        router.record_outcome(snapshot, transient_failure=True, policy=retry_policy)
        router.finish_request(snapshot, status_code=502, error=final_error)
        await _record_request_event_async(
            usage_store,
            snapshot=snapshot,
            thread_id=thread_id,
            session_name_resolver=session_name_resolver,
            model=model,
            upstream_model=upstream_model,
            reasoning_effort=reasoning_effort,
            status_code=502,
            successful=False,
            outcome="exhausted",
            retry_count=max(0, attempt - 1),
            error_kind=final_error,
            error_summary=final_summary or _retry_kind_summary(final_error),
        )
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "当前供应商临时不可用，自动重试后仍未恢复"}},
        )

    router.record_outcome(snapshot, transient_failure=False, policy=retry_policy)

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.casefold() not in RESPONSE_HEADERS_TO_DROP
        and not (
            response_body_decoded and key.casefold() == "content-encoding"
        )
    }

    usage_capture = None
    if usage_store is not None:
        usage_capture = await asyncio.to_thread(
            protocol_adapter.usage_capture
            if protocol_adapter is not None
            else UsageCapture,
            request_body,
            upstream_path,
        )
    failure_capture = None
    if (
        (recovery_history_store is not None or usage_store is not None)
        and _is_event_stream(upstream_response)
    ):
        failure_capture = (
            protocol_adapter.failure_capture()
            if protocol_adapter is not None
            and hasattr(protocol_adapter, "failure_capture")
            else SSEFailureCapture()
        )
    terminal_capture = (
        SSETerminalCapture() if _is_event_stream(upstream_response) else None
    )

    async def response_body() -> AsyncIterator[bytes]:
        stream_failure: tuple[str, str] | None = None
        stream_completed = False
        history_kind: str | None = None
        history_summary: str | None = None
        persistence_ready = False
        try:
            if first_chunk is not None:
                terminal_event = await asyncio.to_thread(
                    _feed_stream_captures,
                    usage_capture,
                    failure_capture,
                    terminal_capture,
                    first_chunk,
                )
                router.update_request_phase(snapshot, "receiving", activity=True)
                await _update_inflight_request_async(
                    usage_store,
                    snapshot.request_id,
                    phase="receiving",
                )
                if terminal_event is not None:
                    stream_completed = True
                yield first_chunk
                if terminal_event is not None:
                    return
            assert stream is not None
            async for chunk in stream:
                terminal_event = await asyncio.to_thread(
                    _feed_stream_captures,
                    usage_capture,
                    failure_capture,
                    terminal_capture,
                    chunk,
                )
                router.update_request_phase(snapshot, "receiving", activity=True)
                if terminal_event is not None:
                    stream_completed = True
                yield chunk
                if terminal_event is not None:
                    return
            stream_completed = True
        except UpstreamStreamIdleTimeout as exc:
            stream_failure = (
                "stream_idle_timeout",
                _stream_idle_retry_summary(exc),
            )
            raise
        except httpx.HTTPError as exc:
            stream_failure = (
                "stream_interrupted",
                _exception_retry_summary("stream_interrupted", exc),
            )
            raise
        finally:
            usage: TokenUsage | None = None
            try:
                if usage_capture is not None or failure_capture is not None:
                    usage, embedded_failure = await asyncio.to_thread(
                        _finalize_stream_captures,
                        usage_capture,
                        failure_capture,
                        upstream_response.status_code,
                    )
                    if embedded_failure is not None:
                        stream_failure = embedded_failure
                successful = (
                    (
                        stream_completed
                        or bool(
                            getattr(
                                usage_capture,
                                "saw_successful_terminal_event",
                                False,
                            )
                        )
                    )
                    and stream_failure is None
                    and 200 <= upstream_response.status_code < 300
                )
                history_kind = stream_failure[0] if stream_failure else None
                history_summary = stream_failure[1] if stream_failure else None
                if not successful and history_kind is None:
                    if not stream_completed:
                        if lease.superseded:
                            history_kind = "session_superseded"
                            history_summary = "已由同会话新请求接管"
                        else:
                            history_kind = "client_disconnected"
                            history_summary = "客户端取消"
                    else:
                        history_kind = f"http_{upstream_response.status_code}"
                        history_summary = f"HTTP {upstream_response.status_code}"
                persistence_ready = True
            finally:
                try:
                    close_stream = getattr(stream, "aclose", None)
                    if close_stream is not None:
                        await close_stream()
                finally:
                    try:
                        await upstream_response.aclose()
                    finally:
                        router.finish_request(
                            snapshot,
                            status_code=upstream_response.status_code,
                            error=history_kind,
                        )
                        if persistence_ready:
                            try:
                                await _record_stream_completion_async(
                                    usage_store,
                                    recovery_history_store,
                                    snapshot=snapshot,
                                    thread_id=thread_id,
                                    session_name_resolver=session_name_resolver,
                                    model=(
                                        usage_capture.model
                                        if usage_capture is not None
                                        else model
                                    ),
                                    upstream_model=upstream_model,
                                    reasoning_effort=reasoning_effort,
                                    usage=usage,
                                    status_code=upstream_response.status_code,
                                    successful=successful,
                                    retry_count=max(0, attempt - 1),
                                    error_kind=history_kind,
                                    error_summary=history_summary,
                                    stream_failure=stream_failure,
                                    attempt=attempt,
                                    max_attempts=retry_policy.max_attempts,
                                )
                            finally:
                                lifecycle.cleaned = True

    return DisconnectAwareStreamingResponse(
        response_body(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        session_request_lease=lease,
    )


def _retry_kind(response: httpx.Response) -> str | None:
    if _is_html_404_response(response):
        return "http_404"
    if response.status_code in RETRYABLE_STATUS_CODES:
        return f"http_{response.status_code}"
    if response.status_code == 429:
        return "rate_limited"
    return None


def _is_html_404_response(response: httpx.Response) -> bool:
    if response.status_code != 404:
        return False
    content_type = response.headers.get("content-type", "")
    media_type = content_type.partition(";")[0].strip().casefold()
    return media_type in HTML_ERROR_CONTENT_TYPES


async def _inspect_http_400_before_output(
    response: httpx.Response,
    first_chunk: bytes,
    stream: AsyncIterator[bytes],
    *,
    detect_retryable: bool = True,
) -> tuple[
    bytes | None,
    AsyncIterator[bytes],
    str | None,
    str | None,
    int | None,
]:
    buffered = bytearray(first_chunk[:RETRY_ERROR_BODY_BYTES])
    first_remainder = first_chunk[RETRY_ERROR_BODY_BYTES:]
    if first_remainder:
        repair_index = _input_item_id_error_index_from_body(bytes(buffered))
        if repair_index is not None:
            return (
                bytes(buffered),
                _resume_async_iterator(stream, prefix=first_remainder),
                "request_item_id_repair",
                f"请求体兼容修复：上游拒绝 input[{repair_index}].id",
                repair_index,
            )
        return (
            bytes(buffered),
            _resume_async_iterator(stream, prefix=first_remainder),
            None,
            None,
            None,
        )
    while len(buffered) < RETRY_ERROR_BODY_BYTES:
        next_chunk = asyncio.create_task(anext(stream))
        try:
            done, _ = await asyncio.wait(
                {next_chunk},
                timeout=RETRY_ERROR_READ_TIMEOUT_SECONDS,
            )
            if not done:
                return (
                    bytes(buffered),
                    _resume_async_iterator(stream, pending=next_chunk),
                    None,
                    None,
                    None,
                )
            chunk = next_chunk.result()
        except StopAsyncIteration:
            repair_index = _input_item_id_error_index_from_body(bytes(buffered))
            if repair_index is not None:
                return (
                    bytes(buffered),
                    stream,
                    "request_item_id_repair",
                    f"请求体兼容修复：上游拒绝 input[{repair_index}].id",
                    repair_index,
                )
            if detect_retryable:
                retry_kind, retry_summary = _http_400_retry_failure(
                    response,
                    bytes(buffered),
                )
                if retry_kind is not None:
                    return None, stream, retry_kind, retry_summary, None
            return bytes(buffered), stream, None, None, None
        except httpx.HTTPError as exc:
            return (
                None,
                stream,
                "stream_start",
                _exception_retry_summary("stream_start", exc),
                None,
            )
        remaining = RETRY_ERROR_BODY_BYTES - len(buffered)
        buffered.extend(chunk[:remaining])
        chunk_remainder = chunk[remaining:]
        if chunk_remainder:
            stream = _resume_async_iterator(stream, prefix=chunk_remainder)
    repair_index = _input_item_id_error_index_from_body(bytes(buffered))
    if repair_index is not None:
        return (
            bytes(buffered),
            stream,
            "request_item_id_repair",
            f"请求体兼容修复：上游拒绝 input[{repair_index}].id",
            repair_index,
        )
    return bytes(buffered), stream, None, None, None


async def _resume_async_iterator(
    stream: AsyncIterator[bytes],
    *,
    prefix: bytes = b"",
    pending: asyncio.Task[bytes] | None = None,
) -> AsyncIterator[bytes]:
    try:
        if prefix:
            yield prefix
        if pending is not None:
            try:
                chunk = await pending
            except StopAsyncIteration:
                return
            if chunk:
                yield chunk
        async for chunk in stream:
            yield chunk
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pending


def _http_400_retry_failure(
    response: httpx.Response,
    body_prefix: bytes,
) -> tuple[str | None, str | None]:
    if response.status_code != 400 or not body_prefix:
        return None, None
    root = _decode_json(body_prefix)
    if isinstance(root, dict):
        response_node = root.get("response")
        has_explicit_failure = (
            root.get("error") is not None
            or root.get("status") == "failed"
            or root.get("type") in {"error", "response.failed"}
            or (
                isinstance(response_node, dict)
                and (
                    response_node.get("error") is not None
                    or response_node.get("status") == "failed"
                )
            )
        )
        if not has_explicit_failure:
            root = {"error": root}
    else:
        content_type = response.headers.get("content-type", "")
        media_type = content_type.partition(";")[0].strip().casefold()
        if media_type == "application/json" or media_type.endswith("+json"):
            return None, None
        root = {
            "error": {
                "message": _decode_response_prefix(response, body_prefix),
            }
        }
    return _embedded_retry_failure(root, "")


async def _inspect_html_404_before_output(
    response: httpx.Response,
    first_chunk: bytes,
    stream: AsyncIterator[bytes],
) -> tuple[bytes | None, str | None, str | None]:
    buffered = bytearray(first_chunk)
    while True:
        retry_kind, retry_summary = _sniff_html_404_retry(
            response,
            bytes(buffered),
        )
        if retry_kind is not None:
            return None, retry_kind, retry_summary
        if (
            len(buffered) >= RETRY_ERROR_BODY_BYTES
            or not _could_be_nginx_html_prefix(response, bytes(buffered))
        ):
            return bytes(buffered), None, None
        try:
            chunk = await asyncio.wait_for(
                anext(stream),
                timeout=RETRY_ERROR_READ_TIMEOUT_SECONDS,
            )
        except StopAsyncIteration:
            return bytes(buffered), None, None
        except TimeoutError:
            return (
                None,
                "stream_start",
                "HTTP 404 response body stalled before output",
            )
        except httpx.HTTPError as exc:
            return (
                None,
                "stream_start",
                _exception_retry_summary("stream_start", exc),
            )
        buffered.extend(chunk)


def _sniff_html_404_retry(
    response: httpx.Response,
    body_prefix: bytes,
) -> tuple[str | None, str | None]:
    if response.status_code != 404 or not body_prefix:
        return None, None
    text = _decode_response_prefix(response, body_prefix)
    normalized = text.casefold()
    looks_like_nginx_404 = (
        ("<html" in normalized or "<!doctype html" in normalized)
        and ("404 not found" in normalized or "<h1>404" in normalized)
        and "nginx" in normalized
    )
    if not looks_like_nginx_404:
        return None, None
    detail = _extract_retry_error_message(text)
    summary = _sanitize_retry_summary(
        f"HTTP 404: {detail or 'Nginx upstream route not found'}"
    )
    return "http_404", summary


def _could_be_nginx_html_prefix(
    response: httpx.Response,
    body_prefix: bytes,
) -> bool:
    stripped = _decode_response_prefix(response, body_prefix).lstrip(
        "\ufeff \t\r\n"
    ).casefold()
    markers = ("<html", "<!doctype html")
    return not stripped or any(
        stripped.startswith(marker) or marker.startswith(stripped)
        for marker in markers
    )


def _decode_response_prefix(
    response: httpx.Response,
    body_prefix: bytes,
) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return body_prefix[:RETRY_ERROR_BODY_BYTES].decode(
            encoding,
            errors="replace",
        )
    except LookupError:
        return body_prefix[:RETRY_ERROR_BODY_BYTES].decode(
            "utf-8",
            errors="replace",
        )


def _is_event_stream(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").casefold().startswith(
        "text/event-stream"
    )


async def _inspect_sse_before_output(
    first_chunk: bytes,
    stream: AsyncIterator[bytes],
    *,
    decision: Callable[..., tuple[str, str | None, str | None]] = None,
) -> tuple[bytes | None, str | None, str | None]:
    decide = decision or _sse_preflight_decision
    buffered = bytearray(first_chunk)
    event_parser = SSEPreflightEventParser()
    marker_capture = SSECapacityFailureCapture()
    raw_failure = marker_capture.feed(first_chunk)
    pending_events = event_parser.feed(first_chunk)
    while True:
        for event in pending_events:
            action, retry_kind, retry_summary = decide(event + b"\n\n")
            if action == "retry":
                return None, retry_kind, retry_summary
            if action == "commit":
                return bytes(buffered), None, None
        if raw_failure is not None:
            return None, raw_failure[0], raw_failure[1]
        if len(buffered) >= SSE_RETRY_PREFLIGHT_BYTES:
            return bytes(buffered), None, None
        try:
            chunk = await anext(stream)
            raw_failure = marker_capture.feed(chunk)
            pending_events = event_parser.feed(chunk)
            buffered.extend(chunk)
        except StopAsyncIteration:
            finalized_events, remaining = event_parser.finalize()
            for event in finalized_events:
                action, retry_kind, retry_summary = decide(event + b"\n\n")
                if action == "retry":
                    return None, retry_kind, retry_summary
                if action == "commit":
                    return bytes(buffered), None, None
            action, retry_kind, retry_summary = decide(
                remaining,
                end_of_stream=True,
            )
            if action == "retry":
                return None, retry_kind, retry_summary
            return bytes(buffered) or None, None, None


class SSEPreflightEventParser:
    """Split SSE events incrementally while preserving an unfinished event."""

    def __init__(self) -> None:
        self._line = bytearray()
        self._event_lines: list[bytes] = []
        self._pending_cr = False

    def feed(self, chunk: bytes) -> tuple[bytes, ...]:
        completed: list[bytes] = []
        for value in chunk:
            if self._pending_cr:
                self._finish_line(completed)
                self._pending_cr = False
                if value == 0x0A:
                    continue
            if value == 0x0D:
                self._pending_cr = True
            elif value == 0x0A:
                self._finish_line(completed)
            else:
                self._line.append(value)
        return tuple(completed)

    def finalize(self) -> tuple[tuple[bytes, ...], bytes]:
        completed: list[bytes] = []
        if self._pending_cr:
            self._finish_line(completed)
            self._pending_cr = False
        if self._line:
            self._event_lines.append(bytes(self._line))
            self._line.clear()
        remaining = b"\n".join(self._event_lines)
        self._event_lines.clear()
        return tuple(completed), remaining

    def _finish_line(self, completed: list[bytes]) -> None:
        line = bytes(self._line)
        self._line.clear()
        if line:
            self._event_lines.append(line)
            return
        completed.append(b"\n".join(self._event_lines))
        self._event_lines.clear()


class SSETerminalCapture:
    """Recognize complete protocol terminal events across arbitrary chunks."""

    _TERMINAL_EVENTS = frozenset(
        {
            "error",
            "response.completed",
            "response.failed",
            "response.incomplete",
        }
    )

    def __init__(self) -> None:
        self._parser = SSEPreflightEventParser()
        self._terminal_event: str | None = None

    @property
    def terminal_event(self) -> str | None:
        return self._terminal_event

    def feed(self, chunk: bytes) -> str | None:
        if self._terminal_event is not None or not chunk:
            return self._terminal_event
        for event in self._parser.feed(chunk):
            event_name, payload = _sse_event_payload(event)
            if payload == b"[DONE]":
                self._terminal_event = "[DONE]"
                break
            root = _decode_json(payload) if payload is not None else None
            if not isinstance(root, dict):
                continue
            event_type = (
                root.get("type")
                if isinstance(root.get("type"), str)
                else event_name
            )
            if event_type in self._TERMINAL_EVENTS:
                self._terminal_event = event_type
                break
        return self._terminal_event


def _sse_preflight_decision(
    buffered: bytes,
    *,
    end_of_stream: bool = False,
) -> tuple[str, str | None, str | None]:
    normalized = buffered.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    events = normalized.split(b"\n\n")
    complete_events = events if end_of_stream else events[:-1]
    for event in complete_events:
        raw_failure = _raw_model_capacity_failure(event)
        if raw_failure is not None:
            return "retry", raw_failure[0], raw_failure[1]
        event_name, payload = _sse_event_payload(event)
        if payload is None:
            continue
        if payload == b"[DONE]":
            return "commit", None, None
        root = _decode_json(payload)
        if not isinstance(root, dict):
            return "commit", None, None
        retry_kind, retry_summary = _embedded_retry_failure(root, event_name)
        if retry_kind is not None:
            return "retry", retry_kind, retry_summary
        if _sse_event_commits_response(root, event_name):
            return "commit", None, None
    return "wait", None, None


class SSECapacityFailureCapture:
    """Recognize model-capacity failures without buffering a complete SSE event."""

    def __init__(self) -> None:
        self._tail = b""
        self._line_has_content = False
        self._skip_lf = False
        self._failed_event = False
        self._capacity_code = False
        self._capacity_message = False
        self._failure: tuple[str, str] | None = None

    def feed(self, chunk: bytes) -> tuple[str, str] | None:
        if self._failure is not None or not chunk:
            return self._failure
        offset = 0
        while offset < len(chunk):
            if self._skip_lf:
                self._skip_lf = False
                if chunk[offset] == 0x0A:
                    offset += 1
                    continue
            carriage_return = chunk.find(b"\r", offset)
            line_feed = chunk.find(b"\n", offset)
            line_end = min(
                value
                for value in (carriage_return, line_feed, len(chunk))
                if value >= 0
            )
            segment = chunk[offset:line_end]
            self._scan_segment(segment)
            if segment:
                self._line_has_content = True
            if self._failed_event and (
                self._capacity_code or self._capacity_message
            ):
                summary = (
                    "模型容量已满：Selected model is at capacity. "
                    "Please try a different model."
                    if self._capacity_message
                    else _retry_kind_summary("model_capacity")
                )
                self._failure = (
                    "model_capacity",
                    _sanitize_retry_summary(summary),
                )
                return self._failure
            if line_end == len(chunk):
                break
            self._finish_line()
            self._skip_lf = chunk[line_end] == 0x0D
            offset = line_end + 1
        return self._failure

    def _finish_line(self) -> None:
        if not self._line_has_content:
            self._reset_event()
        self._line_has_content = False
        self._tail = b""

    def _scan_segment(self, segment: bytes) -> None:
        if not segment:
            return
        window = self._tail + segment
        if SSE_FAILURE_MARKER_RE.search(window):
            self._failed_event = True
        if SSE_MODEL_CAPACITY_CODE_RE.search(window):
            self._capacity_code = True
        if SSE_MODEL_CAPACITY_MESSAGE_RE.search(window):
            self._capacity_message = True
        self._tail = window[-SSE_RETRY_MARKER_TAIL_BYTES:]

    def _reset_event(self) -> None:
        self._tail = b""
        self._failed_event = False
        self._capacity_code = False
        self._capacity_message = False


def _raw_model_capacity_failure(event: bytes) -> tuple[str, str] | None:
    return SSECapacityFailureCapture().feed(event)


class SSEFailureCapture:
    """Capture one retryable failure after a streamed response is committed."""

    def __init__(self) -> None:
        self._line_buffer = bytearray()
        self._event_lines: list[bytes] = []
        self._event_size = 0
        self._discard_event = False
        self._failure: tuple[str, str] | None = None
        self._capacity_capture = SSECapacityFailureCapture()

    def feed(self, chunk: bytes) -> None:
        if self._failure is not None or not chunk:
            return
        raw_failure = self._capacity_capture.feed(chunk)
        if raw_failure is not None:
            self._failure = raw_failure
            return
        self._line_buffer.extend(chunk)
        while True:
            newline = self._line_buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._line_buffer[:newline]).removesuffix(b"\r")
            del self._line_buffer[: newline + 1]
            self._feed_line(line)
            if self._failure is not None:
                return
        if len(self._line_buffer) > SSE_RETRY_EVENT_PARSE_BYTES:
            self._line_buffer.clear()
            self._event_lines.clear()
            self._event_size = 0
            self._discard_event = True

    def finalize(self) -> tuple[str, str] | None:
        if self._failure is not None:
            return self._failure
        if self._line_buffer:
            self._feed_line(bytes(self._line_buffer).removesuffix(b"\r"))
            self._line_buffer.clear()
        if self._event_lines and not self._discard_event:
            self._inspect_event(b"\n".join(self._event_lines))
        self._reset_event()
        return self._failure

    def _feed_line(self, line: bytes) -> None:
        if not line:
            if self._event_lines and not self._discard_event:
                self._inspect_event(b"\n".join(self._event_lines))
            self._reset_event()
            return
        if self._discard_event:
            return
        self._event_size += len(line) + 1
        if self._event_size > SSE_RETRY_EVENT_PARSE_BYTES:
            self._event_lines.clear()
            self._discard_event = True
            return
        self._event_lines.append(line)

    def _inspect_event(self, event: bytes) -> None:
        event_name, payload = _sse_event_payload(event)
        if payload is None or payload == b"[DONE]":
            return
        root = _decode_json(payload)
        if not isinstance(root, dict):
            return
        kind, summary = _embedded_retry_failure(root, event_name)
        if kind is not None:
            self._failure = (kind, summary or _retry_kind_summary(kind))

    def _reset_event(self) -> None:
        self._event_lines.clear()
        self._event_size = 0
        self._discard_event = False


def _sse_event_payload(event: bytes) -> tuple[str, bytes | None]:
    event_name = ""
    data_lines: list[bytes] = []
    for line in event.split(b"\n"):
        if line.startswith(b"event:"):
            event_name = line[6:].strip().decode("utf-8", errors="replace")
        elif line.startswith(b"data:"):
            data_lines.append(line[5:].lstrip())
    return event_name, b"\n".join(data_lines) if data_lines else None


def _embedded_retry_failure(
    root: dict[str, Any],
    event_name: str,
) -> tuple[str | None, str | None]:
    event_type = root.get("type") if isinstance(root.get("type"), str) else event_name
    response = root.get("response") if isinstance(root.get("response"), dict) else {}
    error_nodes = [root.get("error"), response.get("error")]
    failed = (
        event_type in {"error", "response.failed"}
        or any(node is not None for node in error_nodes)
        or root.get("status") == "failed"
        or response.get("status") == "failed"
    )
    if not failed:
        return None, None

    details: list[str] = []
    for node in (root, response, *error_nodes):
        if isinstance(node, dict):
            for key in ("message", "detail", "code", "type", "status_code"):
                value = node.get(key)
                if isinstance(value, (str, int, float)):
                    details.append(str(value))
        elif isinstance(node, (str, int, float)):
            details.append(str(node))
    error_codes = {
        str(node.get(key)).strip().casefold()
        for node in (root, response, *error_nodes)
        if isinstance(node, dict)
        for key in ("code", "type")
        if isinstance(node.get(key), (str, int, float))
    }
    combined = " ".join(details)
    message = next(
        (
            str(node.get(key))
            for node in (*error_nodes, root, response)
            if isinstance(node, dict)
            for key in ("message", "detail")
            if isinstance(node.get(key), (str, int, float)) and str(node.get(key)).strip()
        ),
        "上游临时错误",
    )

    permanent_codes = {
        "authentication_error",
        "billing_error",
        "content_policy_violation",
        "context_length_exceeded",
        "context_window_exceeded",
        "insufficient_quota",
        "invalid_argument",
        "invalid_parameter",
        "invalid_request_error",
        "malformed_request",
        "model_not_found",
        "not_found_error",
        "permission_denied",
        "request_too_large",
        "unsupported_parameter",
        "unsupported_value",
        "validation_error",
    }
    permanent_message = re.search(
        r"(?i)(?:"
        r"\b(?:invalid|unsupported|malformed)\s+"
        r"(?:request|parameter|argument|payload|field|value|json)\b|"
        r"\bcontext(?: length| window)?\b.{0,80}"
        r"(?:exceed(?:ed|s)?|too long|maximum)|"
        r"\bmaximum context length\b|"
        r"\bmodel\b.{0,80}(?:not found|does not exist|unsupported)|"
        r"(?:请求参数|请求格式|参数).{0,20}(?:错误|无效|非法|不支持)|"
        r"上下文.{0,30}(?:过长|超长|超限|超过.{0,12}(?:限制|最大))|"
        r"模型.{0,30}(?:不存在|未找到|不支持)|"
        r"不支持.{0,12}(?:参数|字段|取值)|"
        r"(?:请求体|json).{0,12}(?:格式错误|无效|非法)"
        r")",
        combined,
    )
    if (error_codes & permanent_codes) or permanent_message is not None:
        return None, None

    if re.search(r"(?i)(?:\b429\b|too many requests|rate[_ -]?limit)", combined):
        rate_message = message if message != "上游临时错误" else "请求频率受限"
        return "rate_limited", _sanitize_retry_summary(f"HTTP 429：{rate_message}")

    capacity_codes = {
        "model_busy",
        "model_at_capacity",
        "model_capacity",
        "model_capacity_error",
        "model_overloaded",
    }
    capacity_message = re.search(
        r"(?i)(?:"
        r"\b(?:selected|requested|this) model is (?:currently )?at capacity\b|"
        r"\bmodel\b.{0,120}\b(?:at capacity|busy|overloaded)\b|"
        r"模型.{0,120}(?:负载.{0,20}(?:达到|已达|已经达到)?上限|"
        r"容量.{0,20}(?:已满|达到上限)|繁忙|过载)"
        r")",
        combined,
    )
    if (error_codes & capacity_codes) or capacity_message is not None:
        return "model_capacity", _sanitize_retry_summary(
            f"模型容量已满：{message}"
        )

    transient_codes = {
        "internal_server_error",
        "no_available_channel",
        "no_channel_available",
        "server_error",
        "upstream_error",
    }
    transient_message = re.search(
        r"(?i)(?:\b(?:500|502|503|504)\b|bad gateway|gateway timeout|"
        r"service unavailable|temporar(?:y|ily) unavailable|"
        r"upstream (?:request )?(?:failed|unavailable)|"
        r"no available channels?|no channels? available|"
        r"(?:无|没有).{0,12}可用渠道|渠道.{0,12}(?:不足|不可用|繁忙)|"
        r"上游.{0,12}(?:暂时)?(?:不可用|失败|繁忙)|"
        r"please try again later|请稍后重试)",
        combined,
    )
    if (error_codes & transient_codes) or transient_message is not None:
        return "upstream_error", _sanitize_retry_summary(
            f"上游请求失败：{message}"
        )
    return None, None


def _sse_event_commits_response(root: dict[str, Any], event_name: str) -> bool:
    event_type = root.get("type") if isinstance(root.get("type"), str) else event_name
    visible_output_events = {
        "response.output_text.delta",
        "response.refusal.delta",
    }
    terminal_events = {
        "error",
        "response.completed",
        "response.failed",
        "response.incomplete",
    }
    return event_type in visible_output_events or event_type in terminal_events


def _retry_kind_summary(kind: str) -> str:
    if kind.startswith("http_"):
        return f"HTTP {kind.removeprefix('http_')} 上游临时错误"
    return {
        "rate_limited": "HTTP 429 请求频率受限",
        "model_capacity": "模型容量已满",
        "connection": "连接上游失败",
        "connection_timeout": "等待上游响应超时",
        "stream_start": "响应开始前连接中断",
        "stream_idle_timeout": "上游响应长时间没有新数据",
        "stream_interrupted": "输出后响应流中断",
        "upstream_error": "上游请求失败",
        "malformed_response": "HTTP 200 上游响应为空或格式错误",
    }.get(kind, "上游临时错误")


def _exception_retry_summary(kind: str, exc: httpx.HTTPError) -> str:
    detail = _sanitize_retry_summary(str(exc))
    label = _retry_kind_summary(kind)
    return f"{label}：{detail}" if detail and detail != label else label


async def _response_retry_summary(response: httpx.Response) -> str:
    label = f"HTTP {response.status_code}"
    reason = _sanitize_retry_summary(response.reason_phrase)
    try:
        excerpt = await asyncio.wait_for(
            _retry_response_excerpt(response),
            timeout=RETRY_ERROR_READ_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        excerpt = ""
    detail = _extract_retry_error_message(excerpt)
    if detail:
        return _sanitize_retry_summary(f"{label}：{detail}")
    if reason:
        return _sanitize_retry_summary(f"{label} {reason}")
    return label


async def _retry_response_excerpt(response: httpx.Response) -> str:
    content = bytearray()
    try:
        if response.is_stream_consumed:
            content.extend(response.content[:RETRY_ERROR_BODY_BYTES])
        else:
            async for chunk in response.aiter_bytes():
                remaining = RETRY_ERROR_BODY_BYTES - len(content)
                if remaining <= 0:
                    break
                content.extend(chunk[:remaining])
                if len(content) >= RETRY_ERROR_BODY_BYTES:
                    break
    except httpx.HTTPError:
        return ""
    if not content:
        return ""
    return bytes(content).decode(response.encoding or "utf-8", errors="replace")


def _extract_retry_error_message(raw_body: str) -> str:
    raw = raw_body.strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = None
    candidates: list[Any] = []
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            candidates.extend((error.get("message"), error.get("detail")))
        else:
            candidates.append(error)
        candidates.extend((payload.get("message"), payload.get("detail")))
    for candidate in candidates:
        if isinstance(candidate, (str, int, float)) and str(candidate).strip():
            return _sanitize_retry_summary(str(candidate))
    return _sanitize_retry_summary(raw)


def _sanitize_retry_summary(value: str) -> str:
    text = re.sub(r"<[^>]{1,120}>", " ", str(value))
    text = SECRET_QUERY_RE.sub(r"\1[已隐藏]", text)
    text = SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[已隐藏]",
        text,
    )
    text = BEARER_TOKEN_RE.sub("Bearer [已隐藏]", text)
    text = OPENAI_STYLE_KEY_RE.sub("[已隐藏]", text)
    text = " ".join(text.split())
    if len(text) > RETRY_ERROR_MESSAGE_CHARS:
        return text[: RETRY_ERROR_MESSAGE_CHARS - 1].rstrip() + "…"
    return text


async def _empty_async_iterator() -> AsyncIterator[bytes]:
    if False:
        yield b""


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _upstream_url(provider: ProxyProvider, upstream_path: str) -> str:
    return f"{provider.base_url}/{upstream_path.lstrip('/')}"


def _would_proxy_to_itself(provider: ProxyProvider, request: Request) -> bool:
    upstream = urlsplit(provider.base_url)
    incoming_host = request.url.hostname
    incoming_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    upstream_port = upstream.port or (443 if upstream.scheme == "https" else 80)
    loopback_names = {"127.0.0.1", "::1", "localhost"}
    return (
        upstream.hostname in loopback_names
        and incoming_host in loopback_names
        and upstream_port == incoming_port
    )


async def _read_request_body(
    request: Request,
    limit: int = MAX_REQUEST_BODY_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise ValueError("request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _upstream_request_headers(
    incoming: Mapping[str, str],
    provider: ProxyProvider,
) -> dict[str, str]:
    headers = {
        key: value
        for key, value in incoming.items()
        if key.casefold() not in REQUEST_HEADERS_TO_REPLACE
    }
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    for key, value in provider.configured_headers.items():
        for existing in tuple(headers):
            if existing.casefold() == key.casefold():
                headers.pop(existing)
        headers[key] = value
    return headers


class LocalProxyServer:
    def __init__(
        self,
        router: ProviderRouter | None = None,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        reload_providers: Callable[[], tuple[ProxyProvider, ...]] | None = None,
        on_provider_selected: Callable[[str], None] | None = None,
        hidden_provider_ids: Iterable[str] = (),
        provider_order: Iterable[str] = (),
        on_hidden_provider_ids_changed: Callable[[tuple[str, ...]], None] | None = None,
        on_provider_order_changed: Callable[[tuple[str, ...]], None] | None = None,
        config_fragment: Callable[[], str] | None = None,
        retry_policy_store: RetryPolicyStore | None = None,
        on_retry_policy_changed: Callable[[RetryPolicy], None] | None = None,
        on_shutdown_requested: Callable[[], None] | None = None,
        usage_store: UsageStore | None = None,
        recovery_history_store: RecoveryHistoryStore | None = None,
        health_status_url: str | None = None,
        health_status_url_store: HealthStatusUrlStore | None = None,
        runtime_settings_snapshot: Callable[[], dict[str, Any]] | None = None,
        on_runtime_settings_changed: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        validate_runtime_database: Callable[[str], dict[str, Any]] | None = None,
        ui_config: Callable[[], Mapping[str, Any]] | None = None,
        application: FastAPI | None = None,
        app_factory: Callable[..., FastAPI] = create_proxy_app,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("本地中转只允许监听回环地址")
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        def request_shutdown() -> None:
            self.request_stop()
            if on_shutdown_requested is not None:
                on_shutdown_requested()

        if application is None and router is None:
            raise ValueError("必须提供供应商路由器或完整应用")
        self.app = application if application is not None else app_factory(
            router,
            reload_providers=reload_providers,
            on_provider_selected=on_provider_selected,
            hidden_provider_ids=hidden_provider_ids,
            provider_order=provider_order,
            on_hidden_provider_ids_changed=on_hidden_provider_ids_changed,
            on_provider_order_changed=on_provider_order_changed,
            on_shutdown_requested=request_shutdown,
            config_fragment=config_fragment,
            retry_policy_store=retry_policy_store,
            on_retry_policy_changed=on_retry_policy_changed,
            usage_store=usage_store,
            recovery_history_store=recovery_history_store,
            health_status_url=health_status_url,
            health_status_url_store=health_status_url_store,
            runtime_settings_snapshot=runtime_settings_snapshot,
            on_runtime_settings_changed=on_runtime_settings_changed,
            validate_runtime_database=validate_runtime_database,
            ui_config=ui_config,
        )

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server)

    def start(self, timeout: float = 5.0) -> None:
        if self.running:
            return
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="codex-provider-hub",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.03)
        self.stop()
        raise RuntimeError(f"无法在 {self.host}:{self.port} 启动本地中转")

    def stop(self, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._server = None
        self._thread = None

    def request_stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
