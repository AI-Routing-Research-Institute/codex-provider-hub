from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit

from provider_status.error_semantics import has_usage_limit_semantics


_URL_RE = re.compile(r"https?://[^\s\"'<>\\)]+", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"(?:unexpected\s+status|http(?:[_\s-]+status)?|status(?:[_\s-]+code)?)"
    r"[^0-9]{0,16}([1-5][0-9]{2})\b",
    re.IGNORECASE,
)
_TEXT_TYPES = ("CHAR", "CLOB", "TEXT")
_MAX_LOG_ROWS = 1024
_MAX_LOG_TEXT = 8192
_MAX_CONTEXT_ROWS = 32
_MODEL_ERROR_PHRASES = (
    "model_not_found",
    "model not found",
    "unsupported model",
    "unknown model",
    "invalid model",
    "model is not available",
    "model unavailable",
    "model does not exist",
    "model not supported",
    "does not support the model",
    "does not support this model",
)
_NETWORK_ERROR_PHRASES = (
    "stream disconnected",
    "connection reset",
    "connection closed",
    "connection refused",
    "failed to connect",
    "connect error",
    "error sending request",
    "dns lookup",
    "tls handshake",
)
_PRIORITY = {
    "model_unavailable": 0,
    "auth_failed": 1,
    "rate_limited": 2,
    "client_blocked": 3,
    "no_channel": 4,
    "upstream_unavailable": 5,
    "network_error": 6,
}
_MESSAGES = {
    "model_unavailable": "供应商未开放该模型。",
    "auth_failed": "供应商鉴权失败。",
    "client_blocked": "供应商拒绝当前客户端。",
    "rate_limited": "供应商触发限流。",
    "no_channel": "供应商当前无可用通道。",
    "network_error": "响应流中断。",
}


@dataclass(frozen=True)
class CodexDiagnostic:
    kind: str | None = None
    message: str = ""
    http_status_code: int | None = None
    occurrences: int = 0
    retryable: bool = False


@dataclass(frozen=True)
class _DiagnosticEvent:
    kind: str
    http_status_code: int | None


def read_codex_diagnostic(
    codex_home: str | Path,
    *,
    base_url: str,
    model: str,
    started_at: float | None = None,
) -> CodexDiagnostic | None:
    """读取当前供应商最近的 Codex SQLite 诊断，失败时静默回退。"""
    try:
        database = _latest_database(Path(codex_home))
        if database is None:
            return None
        base = urlsplit(base_url)
        if not base.scheme or not base.hostname or not model.strip():
            return None
        events = _read_events(database, base, started_at)
        if not events:
            return None
        selected_kind = min(
            (event.kind for event in events),
            key=_PRIORITY.__getitem__,
        )
        matching = [event for event in events if event.kind == selected_kind]
        status_code = next(
            (
                event.http_status_code
                for event in matching
                if event.http_status_code is not None
            ),
            None,
        )
        message = _MESSAGES.get(selected_kind, "")
        if selected_kind == "auth_failed" and status_code == 401:
            message = "HTTP 401 Unauthorized；供应商鉴权失败。"
        elif selected_kind == "upstream_unavailable":
            message = (
                f"HTTP {status_code}；上游服务暂时不可用。"
                if status_code is not None
                else "上游服务暂时不可用。"
            )
        return CodexDiagnostic(
            kind=selected_kind,
            message=message,
            http_status_code=status_code,
            occurrences=len(matching),
            retryable=selected_kind in {"upstream_unavailable", "network_error"},
        )
    except (OSError, ValueError, sqlite3.Error):
        return None


def _latest_database(codex_home: Path) -> Path | None:
    databases = [path for path in codex_home.glob("logs_*.sqlite") if path.is_file()]
    if not databases:
        return None
    return max(databases, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _read_events(
    database: Path,
    base: SplitResult,
    started_at: float | None,
) -> list[_DiagnosticEvent]:
    uri = f"{database.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.05)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 50")
        columns = connection.execute('PRAGMA table_info("logs")').fetchall()
        if not columns:
            return []
        text_columns = [
            str(column[1])
            for column in columns
            if not str(column[2]).strip()
            or any(token in str(column[2]).upper() for token in _TEXT_TYPES)
        ]
        if not text_columns:
            return []
        names = [str(column[1]) for column in columns]
        timestamp_column = _timestamp_column(names) if started_at is not None else None
        selected_columns = list(text_columns)
        if timestamp_column and timestamp_column not in selected_columns:
            selected_columns.append(timestamp_column)
        selected_values = ", ".join(
            _select_expression(name, timestamp_column)
            for name in selected_columns
        )
        id_column = next((name for name in names if name.casefold() == "id"), None)
        order_by = f" ORDER BY {_quote_identifier(id_column)} DESC" if id_column else ""
        rows = connection.execute(
            f'SELECT {selected_values} FROM "logs"{order_by} LIMIT {_MAX_LOG_ROWS}'
        ).fetchall()
    finally:
        connection.close()

    if id_column:
        rows.reverse()

    timestamp_index = (
        selected_columns.index(timestamp_column) if timestamp_column is not None else None
    )
    thread_column = next(
        (name for name in selected_columns if name.casefold() == "thread_id"),
        None,
    )
    thread_index = (
        selected_columns.index(thread_column) if thread_column is not None else None
    )
    log_rows: list[tuple[str, list[SplitResult], str | None]] = []
    for row in rows:
        if timestamp_index is not None and not _is_recent(row[timestamp_index], started_at):
            continue
        text = "\n".join(
            _as_text(row[index])
            for index, name in enumerate(selected_columns)
            if name in text_columns and row[index] is not None
        )
        if not text:
            continue
        urls = _extract_urls(text)
        thread_id = (
            _as_text(row[thread_index]).strip()
            if thread_index is not None and row[thread_index] is not None
            else None
        )
        log_rows.append((text, urls, thread_id or None))

    if not any(
        _is_provider_request(base, url)
        for _, urls, _ in log_rows
        for url in urls
    ):
        return []

    events: list[_DiagnosticEvent] = []
    target_context = False
    target_thread_id: str | None = None
    rows_since_target = 0
    for text, urls, thread_id in log_rows:
        is_provider_request = any(_is_provider_request(base, url) for url in urls)
        if is_provider_request:
            target_context = True
            target_thread_id = thread_id
            rows_since_target = 0
        elif target_context and target_thread_id and thread_id != target_thread_id:
            continue
        elif urls:
            if any(_is_telemetry_host(url.hostname) for url in urls):
                target_context = False
                target_thread_id = None
                continue
            target_context = False
            target_thread_id = None
            rows_since_target = 0
        elif target_context:
            rows_since_target += 1
            if rows_since_target > _MAX_CONTEXT_ROWS:
                target_context = False
                target_thread_id = None
        if not target_context:
            continue
        event = _classify(text)
        if event is not None:
            events.append(event)
    return events


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _select_expression(name: str, timestamp_column: str | None) -> str:
    quoted = _quote_identifier(name)
    if name == timestamp_column:
        return quoted
    return f"substr({quoted}, 1, {_MAX_LOG_TEXT})"


def _timestamp_column(names: list[str]) -> str | None:
    preferred = ("ts", "timestamp", "created_at", "time", "ts_nanos")
    by_normalized = {name.casefold(): name for name in names}
    return next((by_normalized[name] for name in preferred if name in by_normalized), None)


def _is_recent(raw_value: Any, started_at: float | None) -> bool:
    if started_at is None:
        return True
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return True
    if value > 10_000_000_000:
        value /= 1_000_000_000
    return value >= started_at


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[:65536]
    return str(value)[:65536]


def _extract_urls(text: str) -> list[SplitResult]:
    urls: list[SplitResult] = []
    for match in _URL_RE.finditer(text):
        candidate = match.group(0).rstrip(".,;:]}")
        try:
            parsed = urlsplit(candidate)
            _effective_port(parsed)
        except ValueError:
            continue
        urls.append(parsed)
    return urls


def _is_telemetry_host(hostname: str | None) -> bool:
    host = (hostname or "").casefold().rstrip(".")
    return host == "ab.chatgpt.com" or host.endswith(".ab.chatgpt.com")


def _is_provider_request(base: SplitResult, candidate: SplitResult) -> bool:
    try:
        same_origin = (
            candidate.scheme.casefold() == base.scheme.casefold()
            and (candidate.hostname or "").casefold()
            == (base.hostname or "").casefold()
            and _effective_port(candidate) == _effective_port(base)
        )
    except ValueError:
        return False
    if not same_origin:
        return False
    base_path = (base.path or "/").rstrip("/") or "/"
    candidate_path = candidate.path or "/"
    if base_path == "/":
        return candidate_path != "/"
    return candidate_path.startswith(base_path + "/")


def _effective_port(url: SplitResult) -> int | None:
    if url.port is not None:
        return url.port
    if url.scheme.casefold() == "https":
        return 443
    if url.scheme.casefold() == "http":
        return 80
    return None


def _classify(text: str) -> _DiagnosticEvent | None:
    normalized = _semantic_text(text).casefold()
    status_code = _extract_status_code(text)
    if any(phrase in normalized for phrase in _MODEL_ERROR_PHRASES) or (
        "does not exist" in normalized and "model" in normalized
    ):
        return _DiagnosticEvent("model_unavailable", status_code)
    if status_code == 401 or any(
        phrase in normalized
        for phrase in (
            "invalid api key",
            "incorrect api key",
            "authentication failed",
            "authentication required",
            "401 unauthorized",
        )
    ):
        return _DiagnosticEvent("auth_failed", status_code)
    if status_code == 429 or has_usage_limit_semantics(normalized):
        return _DiagnosticEvent("rate_limited", status_code)
    if status_code == 403 or "does not allow the current client" in normalized:
        return _DiagnosticEvent("client_blocked", status_code)
    if "no available channel" in normalized:
        return _DiagnosticEvent("no_channel", status_code)
    if status_code in {502, 503, 504}:
        return _DiagnosticEvent("upstream_unavailable", status_code)
    if any(phrase in normalized for phrase in _NETWORK_ERROR_PHRASES):
        return _DiagnosticEvent("network_error", status_code)
    return None


def _semantic_text(text: str) -> str:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    return f"{text}\n{json.dumps(payload, ensure_ascii=False)}"


def _extract_status_code(text: str) -> int | None:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    status = _status_from_json(payload)
    if status is not None:
        return status
    match = _STATUS_RE.search(text)
    return int(match.group(1)) if match else None


def _status_from_json(value: Any) -> int | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if normalized_key in {"status", "status_code", "http_status_code"}:
                try:
                    status = int(item)
                except (TypeError, ValueError):
                    pass
                else:
                    if 100 <= status <= 599:
                        return status
        for item in value.values():
            status = _status_from_json(item)
            if status is not None:
                return status
    elif isinstance(value, list):
        for item in value:
            status = _status_from_json(item)
            if status is not None:
                return status
    return None
