from __future__ import annotations

import argparse
import asyncio
import email.utils
import json
import re
import sqlite3
import threading
import time
import tomllib
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

import probe_codex_cc_switch as cc_switch

try:
    import tiktoken
except ImportError:  # The desktop installer installs it; keep diagnostics importable.
    tiktoken = None


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17890
DEFAULT_DATABASE = Path.home() / ".cc-switch" / "cc-switch.db"
CONTROL_ASSET_DIR = Path(__file__).resolve().parent / "local_proxy_static"
MAX_REQUEST_BODY_BYTES = 64 * 1024 * 1024
RETRY_ERROR_BODY_BYTES = 4 * 1024
RETRY_ERROR_HISTORY_LIMIT = 5
RETRY_ERROR_MESSAGE_CHARS = 220
RETRY_ERROR_READ_TIMEOUT_SECONDS = 0.25
HTML_ERROR_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
RECOVERY_HISTORY_HOURS = 24
RECOVERY_HISTORY_API_LIMIT = 500
RECOVERY_HISTORY_CLEANUP_INTERVAL_SECONDS = 3600
SSE_RETRY_PREFLIGHT_BYTES = 256 * 1024
USAGE_RESPONSE_BUFFER_BYTES = 8 * 1024 * 1024
USAGE_WINDOWS = {"today", "24h", "7d", "30d", "all"}
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
RESPONSE_HEADERS_TO_DROP = HOP_BY_HOP_HEADERS | {"content-length"}
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|api[-_ ]?key|access[-_ ]?token|token|secret)\b"
    r"[\"']?(\s*[:=]\s*)[\"']?(?:bearer\s+)?[^\s,;\"'}]+[\"']?"
)
BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|key|secret)=)[^&\s]+"
)
OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")


class ProviderConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ProxyProvider:
    provider_id: str
    name: str
    base_url: str
    is_cc_switch_current: bool
    wire_api: str = "responses"
    api_key: str | None = field(default=None, repr=False)
    configured_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    default_query: Mapping[str, str] = field(default_factory=dict)

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

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
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
                    status_code INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS request_usage_recorded_at
                    ON request_usage(recorded_at);
                CREATE INDEX IF NOT EXISTS request_usage_provider_time
                    ON request_usage(provider_id, recorded_at);
                """
            )

    def record(
        self,
        *,
        provider_id: str,
        model: str,
        usage: TokenUsage,
        status_code: int,
        recorded_at: float | None = None,
    ) -> None:
        timestamp = time.time() if recorded_at is None else float(recorded_at)
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
            int(status_code),
        )
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO request_usage (
                    recorded_at, provider_id, model, input_tokens, output_tokens,
                    total_tokens, cached_tokens, reasoning_tokens, usage_source,
                    estimate_method, status_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def summary(self, window: str, *, now: float | None = None) -> dict[str, Any]:
        normalized = window.strip().lower()
        if normalized not in USAGE_WINDOWS:
            raise ValueError("不支持的 Token 统计时间范围")
        timestamp = time.time() if now is None else float(now)
        cutoff = _usage_window_cutoff(normalized, timestamp)
        where = "" if cutoff is None else "WHERE recorded_at >= ?"
        params: tuple[float, ...] = () if cutoff is None else (cutoff,)
        aggregate = """
            COUNT(*) AS request_count,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
            COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
            COALESCE(SUM(CASE WHEN usage_source = 'estimated' THEN 1 ELSE 0 END), 0)
                AS estimated_requests
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
            "total": _usage_summary_row(total),
            "by_provider": {
                str(row["provider_id"]): _usage_summary_row(row) for row in rows
            },
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
    ) -> None:
        timestamp = time.time() if recorded_at is None else float(recorded_at)
        values = (
            timestamp,
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
                    recorded_at, request_id, provider_id, attempt, max_attempts,
                    delay_seconds, kind, summary, stage, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            self._cleanup_if_due(connection, timestamp)

    def history(
        self,
        *,
        now: float | None = None,
        limit: int = RECOVERY_HISTORY_API_LIMIT,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        cutoff = timestamp - RECOVERY_HISTORY_HOURS * 3600
        bounded_limit = max(1, min(int(limit), RECOVERY_HISTORY_API_LIMIT))
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
                """
                SELECT recorded_at, request_id, provider_id, attempt, max_attempts,
                       delay_seconds, kind, summary, stage, outcome
                FROM recovery_events
                WHERE recorded_at >= ?
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
                """,
                (cutoff, bounded_limit),
            ).fetchall()
        return {
            "window_hours": RECOVERY_HISTORY_HOURS,
            "total_count": total_count,
            "truncated": total_count > len(rows),
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
                }
                for row in rows
            ],
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


def _usage_summary_row(row: sqlite3.Row | None) -> dict[str, int]:
    fields = (
        "request_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "estimated_requests",
    )
    return {field: int(row[field] or 0) if row is not None else 0 for field in fields}


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
        self._finalized = False

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


def _request_model(payload: bytes) -> str:
    root = _decode_json(payload)
    if isinstance(root, dict) and isinstance(root.get("model"), str):
        return root["model"].strip()
    return "unknown"


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


@dataclass(frozen=True)
class ProxyStatus:
    current_provider_id: str | None
    active_by_provider: dict[str, int]
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
    ) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, ProxyProvider] = {}
        self._current_provider_id: str | None = None
        self._active: dict[str, int] = {}
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

    def begin_request(self) -> RouteSnapshot:
        with self._lock:
            provider = self.current_provider()
            if provider is None:
                raise ProviderConfigurationError("没有可用于转发的 CC Switch 供应商")
            open_until = self._circuit_open_until.get(provider.provider_id, 0.0)
            if open_until > time.monotonic():
                raise ProviderCircuitOpenError(open_until - time.monotonic())
            self._active[provider.provider_id] = self._active.get(provider.provider_id, 0) + 1
            self._total_requests += 1
            self._request_sequence += 1
            return RouteSnapshot(
                request_id=self._request_sequence,
                provider=provider,
                started_at=time.monotonic(),
            )

    def route_retry_to_current(
        self,
        snapshot: RouteSnapshot,
    ) -> tuple[RouteSnapshot, bool]:
        with self._lock:
            provider = self.current_provider()
            if provider is None or provider.provider_id == snapshot.provider.provider_id:
                return snapshot, False

            previous_id = snapshot.provider.provider_id
            remaining = max(0, self._active.get(previous_id, 1) - 1)
            if remaining:
                self._active[previous_id] = remaining
            else:
                self._active.pop(previous_id, None)
            self._active[provider.provider_id] = self._active.get(provider.provider_id, 0) + 1

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
            provider_id = snapshot.provider.provider_id
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
            self._recent_retry_errors.appendleft(
                RetryErrorRecord(
                    request_id=snapshot.request_id,
                    provider_id=error_provider_id or snapshot.provider.provider_id,
                    attempt=max(1, attempt - 1),
                    kind=kind,
                    summary=summary,
                    recorded_at=time.time(),
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


def load_proxy_providers(db_path: Path = DEFAULT_DATABASE) -> tuple[ProxyProvider, ...]:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到 CC Switch 数据库：{path}")
    uri = path.as_uri() + "?mode=ro"
    query = """
        SELECT p.id, p.name, p.is_current, p.settings_config, p.meta, pe.url
        FROM providers AS p
        LEFT JOIN provider_endpoints AS pe
          ON pe.provider_id = p.id AND pe.app_type = p.app_type
        WHERE p.app_type = 'codex'
        ORDER BY p.sort_index IS NULL, p.sort_index, p.created_at, p.name
    """
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query).fetchall()
        common_row = connection.execute(
            "SELECT value FROM settings WHERE key = 'common_config_codex'"
        ).fetchone()
    common_config = (common_row["value"] if common_row else "") or ""
    providers: list[ProxyProvider] = []
    errors: list[str] = []
    for row in rows:
        try:
            record = _record_from_row(row)
            if not record.is_api_provider:
                continue
            providers.append(_proxy_provider(record, common_config))
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, ProviderConfigurationError) as exc:
            errors.append(f"{row['name']}: {exc}")
    if not providers and errors:
        raise ProviderConfigurationError("；".join(errors))
    return tuple(providers)


def _record_from_row(row: sqlite3.Row) -> cc_switch.ProviderRecord:
    payload = json.loads(row["settings_config"] or "{}")
    auth = payload.get("auth") or {}
    if not isinstance(auth, dict):
        raise ProviderConfigurationError("认证配置格式无效")
    meta = json.loads(row["meta"]) if row["meta"] else {}
    if not isinstance(meta, dict):
        raise ProviderConfigurationError("元数据格式无效")
    return cc_switch.ProviderRecord(
        provider_id=str(row["id"]),
        name=str(row["name"]),
        is_current=bool(row["is_current"]),
        endpoint_url=row["url"],
        common_config_enabled=bool(meta.get("commonConfigEnabled")),
        raw_config=str(payload.get("config") or "").strip(),
        auth=auth,
        meta=meta,
    )


def _proxy_provider(
    record: cc_switch.ProviderRecord,
    common_config: str,
) -> ProxyProvider:
    effective_text = cc_switch.build_effective_config(record, common_config)
    config = tomllib.loads(effective_text) if effective_text.strip() else {}
    provider_config = _selected_provider_config(config)
    base_url = provider_config.get("base_url") or record.endpoint_url
    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderConfigurationError("没有配置 base_url")
    normalized_url = _normalize_base_url(base_url)
    env_key = provider_config.get("env_key", "OPENAI_API_KEY")
    if not isinstance(env_key, str) or not env_key.strip():
        raise ProviderConfigurationError("env_key 格式无效")
    api_key = _nonempty_string(record.auth.get(env_key))
    if api_key is None and env_key != "OPENAI_API_KEY":
        api_key = _nonempty_string(record.auth.get("OPENAI_API_KEY"))

    headers = _string_mapping(provider_config.get("http_headers"), "http_headers")
    env_headers = _string_mapping(
        provider_config.get("env_http_headers"),
        "env_http_headers",
    )
    resolved_headers = dict(headers)
    for header_name, auth_name in env_headers.items():
        value = _nonempty_string(record.auth.get(auth_name))
        if value is not None:
            resolved_headers[header_name] = value
    query = _string_mapping(provider_config.get("query_params"), "query_params")
    wire_api = provider_config.get("wire_api", "responses")
    if not isinstance(wire_api, str):
        wire_api = "responses"
    return ProxyProvider(
        provider_id=record.provider_id,
        name=record.name,
        base_url=normalized_url,
        is_cc_switch_current=record.is_current,
        wire_api=wire_api,
        api_key=api_key,
        configured_headers=resolved_headers,
        default_query=query,
    )


def _selected_provider_config(config: Mapping[str, Any]) -> dict[str, Any]:
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return {}
    selected = config.get("model_provider")
    if isinstance(selected, str) and isinstance(providers.get(selected), dict):
        return dict(providers[selected])
    if len(providers) == 1:
        only = next(iter(providers.values()))
        return dict(only) if isinstance(only, dict) else {}
    return {}


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


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_mapping(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderConfigurationError(f"{field_name} 格式无效")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ProviderConfigurationError(f"{field_name} 必须只包含字符串")
        result[key] = item
    return result


def create_proxy_app(
    router: ProviderRouter,
    *,
    client: httpx.AsyncClient | None = None,
    protocol_adapter: Any | None = None,
    reload_providers: Callable[[], tuple[ProxyProvider, ...]] | None = None,
    on_provider_selected: Callable[[str], None] | None = None,
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
    retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    service_name: str = "codex-local-proxy",
    control_asset_dir: Path = CONTROL_ASSET_DIR,
    allowed_proxy_paths: frozenset[str] | None = None,
    provider_selectable: Callable[[ProxyProvider], bool] | None = None,
    provider_public_fields: Callable[[ProxyProvider], Mapping[str, Any]] | None = None,
) -> FastAPI:
    active_retry_policy_store = retry_policy_store or RetryPolicyStore(retry_policy)
    preferences_lock = threading.RLock()
    active_hidden_provider_ids = {
        provider_id for provider_id in hidden_provider_ids if isinstance(provider_id, str)
    }
    active_provider_order = [
        provider_id for provider_id in provider_order if isinstance(provider_id, str)
    ]
    upstream_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(connect=30.0, read=None, write=120.0, pool=30.0),
        follow_redirects=False,
    )
    owns_client = client is None
    active_health_status_url_store = health_status_url_store or HealthStatusUrlStore(
        health_status_url
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if owns_client:
                await upstream_client.aclose()

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
        }

    @app.get("/control", include_in_schema=False)
    async def control_redirect() -> RedirectResponse:
        return RedirectResponse("/control/", status_code=307)

    @app.get("/control/", include_in_schema=False)
    async def control_page() -> FileResponse:
        return FileResponse(control_asset_dir / "index.html")

    @app.get("/control/static/{asset_name}", include_in_schema=False)
    async def control_asset(asset_name: str):
        if asset_name not in {"app.js", "styles.css"}:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        response = FileResponse(control_asset_dir / asset_name)
        response.headers["Cache-Control"] = "no-store"
        return response

    def public_status(window: str = "today") -> dict[str, Any]:
        with preferences_lock:
            hidden = set(active_hidden_provider_ids)
        usage = usage_store.summary(window) if usage_store is not None else _empty_usage_summary(window)
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
        )

    def public_status_for_request(request: Request) -> dict[str, Any]:
        window = request.query_params.get("usage_window", "today").strip().lower()
        return public_status(window if window in USAGE_WINDOWS else "today")

    @app.get("/control/api/status", include_in_schema=False)
    async def control_status(request: Request):
        window = request.query_params.get("usage_window", "today").strip().lower()
        if window not in USAGE_WINDOWS:
            return JSONResponse(status_code=422, content={"detail": "Token 统计时间范围无效"})
        return public_status(window)

    @app.get("/control/api/recovery-history", include_in_schema=False)
    async def control_recovery_history():
        if recovery_history_store is None:
            history = public_status()["retry"]["history"]
        else:
            try:
                history = recovery_history_store.history()
            except (OSError, sqlite3.Error):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "无法读取本地恢复记录"},
                )
        return JSONResponse(content=history, headers={"Cache-Control": "no-store"})

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

    @app.get("/control/api/codex-config", include_in_schema=False)
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
        return await _forward_request(
            router,
            upstream_client,
            request,
            upstream_path,
            retry_policy=active_retry_policy_store.get(),
            retry_sleep=retry_sleep,
            usage_store=usage_store,
            recovery_history_store=recovery_history_store,
            protocol_adapter=protocol_adapter,
        )

    return app


def _valid_control_request(request: Request) -> bool:
    return request.headers.get("X-Local-Proxy-Control") == "1"


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
) -> dict[str, Any]:
    status = router.status()
    policy = retry_policy or RetryPolicy()
    current_id = status.current_provider_id
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
        }
        for error in status.recent_retry_errors
    ]
    history = recovery_history or {
        "window_hours": RECOVERY_HISTORY_HOURS,
        "total_count": len(recent_errors),
        "truncated": False,
        "items": recent_errors,
    }
    return {
        "service": service_name,
        "current_provider_id": current_id,
        "active_requests": sum(status.active_by_provider.values()),
        "active_by_provider": status.active_by_provider,
        "total_requests": status.total_requests,
        "last_provider_id": status.last_provider_id,
        "last_status_code": status.last_status_code,
        "last_error": status.last_error,
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


async def _forward_request(
    router: ProviderRouter,
    client: httpx.AsyncClient,
    request: Request,
    upstream_path: str,
    *,
    retry_policy: RetryPolicy,
    retry_sleep: Callable[[float], Awaitable[None]],
    usage_store: UsageStore | None = None,
    recovery_history_store: RecoveryHistoryStore | None = None,
    protocol_adapter: Any | None = None,
):
    try:
        snapshot = router.begin_request()
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
    provider = snapshot.provider
    if not provider.has_credentials:
        router.finish_request(snapshot, status_code=503, error="credential_missing")
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "当前供应商没有可用认证配置"}},
        )

    if _would_proxy_to_itself(provider, request):
        router.finish_request(snapshot, status_code=508, error="proxy_loop")
        return JSONResponse(
            status_code=508,
            content={"error": {"message": "当前供应商地址指向本地中转自身"}},
        )

    try:
        request_body = await _read_request_body(request)
    except ValueError:
        router.finish_request(snapshot, status_code=413, error="request_too_large")
        return JSONResponse(
            status_code=413,
            content={"error": {"message": "请求体超过本地中转允许的大小"}},
        )
    upstream_response: httpx.Response | None = None
    first_chunk: bytes | None = None
    stream: AsyncIterator[bytes] | None = None
    final_error = "upstream_unavailable"
    attempt = 1
    while True:
        if attempt > 1:
            snapshot, _ = router.route_retry_to_current(snapshot)
        provider = snapshot.provider
        if not provider.has_credentials:
            final_error = "credential_missing"
            break
        if _would_proxy_to_itself(provider, request):
            final_error = "proxy_loop"
            break

        url = _upstream_url(provider, upstream_path)
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
        retry_kind: str | None = None
        retry_summary: str | None = None
        retry_delay = retry_policy.backoff(attempt - 1)
        try:
            upstream_request = client.build_request(
                request.method,
                url,
                params=query_items,
                headers=headers,
                content=request_body,
            )
            upstream_response = await client.send(upstream_request, stream=True)
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
                else:
                    stream = upstream_response.aiter_raw()
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
                    and first_chunk is not None
                    and _is_event_stream(upstream_response)
                    and retry_policy.enabled
                ):
                    assert stream is not None
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
        except httpx.HTTPError as exc:
            retry_kind = "connection"
            final_error = "upstream_unavailable"
            retry_summary = _exception_retry_summary(retry_kind, exc)

        request_disconnected = (
            await request.is_disconnected() if retry_kind is not None else False
        )
        can_retry = (
            retry_kind is not None
            and retry_policy.allows_attempt(attempt + 1)
            and not request_disconnected
        )
        if retry_kind is not None and retry_summary is None and upstream_response is not None:
            retry_summary = await _response_retry_summary(upstream_response)
        if upstream_response is not None:
            await upstream_response.aclose()
            upstream_response = None
        if not can_retry:
            if retry_kind is not None:
                _record_recovery_event(
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
        snapshot, rerouted = router.route_retry_to_current(snapshot)
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
        _record_recovery_event(
            recovery_history_store,
            snapshot=snapshot,
            provider_id=failed_provider_id,
            attempt=attempt,
            max_attempts=retry_policy.max_attempts,
            delay_seconds=retry_delay,
            kind=retry_kind,
            summary=retry_summary,
            stage="before_output",
            outcome="retrying",
        )
        await retry_sleep(retry_delay)
        attempt += 1

    if upstream_response is None or stream is None:
        router.record_outcome(snapshot, transient_failure=True, policy=retry_policy)
        router.finish_request(snapshot, status_code=502, error=final_error)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "当前供应商临时不可用，自动重试后仍未恢复"}},
        )

    router.record_outcome(snapshot, transient_failure=False, policy=retry_policy)

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.casefold() not in RESPONSE_HEADERS_TO_DROP
    }

    usage_capture = None
    if usage_store is not None:
        usage_capture = (
            protocol_adapter.usage_capture(request_body, upstream_path)
            if protocol_adapter is not None
            else UsageCapture(request_body, upstream_path)
        )
    failure_capture = (
        SSEFailureCapture()
        if recovery_history_store is not None
        and retry_policy.enabled
        and _is_event_stream(upstream_response)
        else None
    )

    async def response_body() -> AsyncIterator[bytes]:
        stream_failure: tuple[str, str] | None = None
        try:
            if first_chunk is not None:
                if usage_capture is not None:
                    usage_capture.feed(first_chunk)
                if failure_capture is not None:
                    failure_capture.feed(first_chunk)
                yield first_chunk
            assert stream is not None
            async for chunk in stream:
                if usage_capture is not None:
                    usage_capture.feed(chunk)
                if failure_capture is not None:
                    failure_capture.feed(chunk)
                yield chunk
        except httpx.HTTPError as exc:
            stream_failure = (
                "stream_interrupted",
                _exception_retry_summary("stream_interrupted", exc),
            )
            raise
        finally:
            try:
                if failure_capture is not None:
                    embedded_failure = failure_capture.finalize()
                    if embedded_failure is not None:
                        stream_failure = embedded_failure
                if stream_failure is not None:
                    kind, summary = stream_failure
                    _record_recovery_event(
                        recovery_history_store,
                        snapshot=snapshot,
                        provider_id=snapshot.provider.provider_id,
                        attempt=attempt,
                        max_attempts=retry_policy.max_attempts,
                        delay_seconds=None,
                        kind=kind,
                        summary=summary,
                        stage="after_output",
                        outcome="passed_through",
                    )
                if usage_capture is not None and usage_store is not None:
                    usage = usage_capture.finalize(upstream_response.status_code)
                    if usage is not None:
                        try:
                            usage_store.record(
                                provider_id=snapshot.provider.provider_id,
                                model=usage_capture.model,
                                usage=usage,
                                status_code=upstream_response.status_code,
                            )
                        except (OSError, sqlite3.Error):
                            pass
            finally:
                try:
                    await upstream_response.aclose()
                finally:
                    router.finish_request(
                        snapshot,
                        status_code=upstream_response.status_code,
                    )

    return StreamingResponse(
        response_body(),
        status_code=upstream_response.status_code,
        headers=response_headers,
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
    while True:
        action, retry_kind, retry_summary = decide(bytes(buffered))
        if action == "retry":
            return None, retry_kind, retry_summary
        if action == "commit" or len(buffered) >= SSE_RETRY_PREFLIGHT_BYTES:
            return bytes(buffered), None, None
        try:
            buffered.extend(await anext(stream))
        except StopAsyncIteration:
            action, retry_kind, retry_summary = decide(
                bytes(buffered),
                end_of_stream=True,
            )
            if action == "retry":
                return None, retry_kind, retry_summary
            return bytes(buffered) or None, None, None


def _sse_preflight_decision(
    buffered: bytes,
    *,
    end_of_stream: bool = False,
) -> tuple[str, str | None, str | None]:
    normalized = buffered.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    events = normalized.split(b"\n\n")
    complete_events = events if end_of_stream else events[:-1]
    for event in complete_events:
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


class SSEFailureCapture:
    """Capture one retryable failure after a streamed response is committed."""

    def __init__(self) -> None:
        self._line_buffer = bytearray()
        self._event_lines: list[bytes] = []
        self._event_size = 0
        self._discard_event = False
        self._failure: tuple[str, str] | None = None

    def feed(self, chunk: bytes) -> None:
        if self._failure is not None or not chunk:
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
        if len(self._line_buffer) > SSE_RETRY_PREFLIGHT_BYTES:
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
        if self._event_size > SSE_RETRY_PREFLIGHT_BYTES:
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
        for node in error_nodes
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
    if re.search(r"(?i)(?:\b429\b|too many requests|rate[_ -]?limit)", combined):
        rate_message = message if message != "上游临时错误" else "请求频率受限"
        return "rate_limited", _sanitize_retry_summary(f"HTTP 429：{rate_message}")

    capacity_codes = {
        "model_at_capacity",
        "model_capacity",
        "model_capacity_error",
    }
    capacity_message = re.search(
        r"(?i)\b(?:selected|requested|this) model is (?:currently )?at capacity\b",
        combined,
    )
    if (error_codes & capacity_codes) or capacity_message is not None:
        return "model_capacity", _sanitize_retry_summary(
            f"模型容量已满：{message}"
        )

    permanent_codes = {
        "authentication_error",
        "billing_error",
        "content_policy_violation",
        "insufficient_quota",
        "invalid_request_error",
        "model_not_found",
        "not_found_error",
        "permission_denied",
    }
    if error_codes & permanent_codes:
        return None, None

    transient_codes = {
        "internal_server_error",
        "server_error",
        "upstream_error",
    }
    transient_message = re.search(
        r"(?i)(?:\b(?:500|502|503|504)\b|bad gateway|gateway timeout|"
        r"service unavailable|temporar(?:y|ily) unavailable|upstream (?:request )?failed)",
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
        "stream_start": "响应开始前连接中断",
        "stream_interrupted": "输出后响应流中断",
        "upstream_error": "上游请求失败",
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
        router: ProviderRouter,
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

        self.app = create_proxy_app(
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
            name="codex-local-proxy",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex CC Switch local forwarding proxy")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--provider")
    parser.add_argument("--health-status-url")
    args = parser.parse_args(argv)
    providers = load_proxy_providers(args.database)
    router = ProviderRouter(providers, current_provider_id=args.provider)
    uvicorn.run(
        create_proxy_app(router, health_status_url=args.health_status_url),
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
