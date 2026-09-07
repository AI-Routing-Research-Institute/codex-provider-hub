"""Bounded local persistence for request-level proxy debugging."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping


DEFAULT_REQUEST_DEBUG_RETENTION = 300
DEFAULT_REQUEST_DEBUG_BODY_BYTES = 16 * 1024 * 1024
DEFAULT_REQUEST_DEBUG_TOTAL_BYTES = 512 * 1024 * 1024
REQUEST_DEBUG_FLUSH_BYTES = 64 * 1024
REQUEST_DEBUG_FLUSH_INTERVAL_SECONDS = 0.25
_SENSITIVE_HEADER_RE = re.compile(
    r"(?i)(authorization|api[-_ ]?key|access[-_ ]?token|token|secret|cookie|set-cookie)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)((?:^|[?&])(?:api[-_]?key|access[-_]?token|token|key|secret)=)[^&\s]+"
)


def _safe_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    if not headers:
        return {}
    result: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key)
        result[name] = "<redacted>" if _SENSITIVE_HEADER_RE.search(name) else str(value)
    return result


def _safe_url(url: str) -> str:
    return _QUERY_SECRET_RE.sub(r"\1<redacted>", str(url))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _bounded_body(body: bytes | bytearray | memoryview | None, limit: int) -> tuple[bytes, bool]:
    raw = bytes(body or b"")
    bounded = max(1, int(limit))
    return raw[:bounded], len(raw) > bounded


def _body_payload(body: bytes | None, truncated: bool) -> dict[str, Any]:
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
    payload: dict[str, Any] = {
        "encoding": "base64",
        "base64": base64.b64encode(raw).decode("ascii"),
        "bytes": len(raw),
        "truncated": bool(truncated),
    }
    try:
        payload["text"] = raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return payload


class RequestDebugStore:
    """Keep bounded, local-only request evidence without affecting request flow."""

    def __init__(
        self,
        path: Path,
        *,
        service_id: str = "proxy",
        retention: int = DEFAULT_REQUEST_DEBUG_RETENTION,
        max_body_bytes: int = DEFAULT_REQUEST_DEBUG_BODY_BYTES,
        max_total_bytes: int = DEFAULT_REQUEST_DEBUG_TOTAL_BYTES,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.service_id = str(service_id)[:32]
        self.run_id = uuid.uuid4().hex[:64]
        self.retention = max(1, int(retention))
        self.max_body_bytes = max(1024, int(max_body_bytes))
        self.max_total_bytes = max(1024 * 1024, int(max_total_bytes))
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _key(run_id: str, request_id: int) -> str:
        return f"{str(run_id)[:64]}:{int(request_id)}"

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS debug_requests (
                    request_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    request_id INTEGER NOT NULL,
                    service_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    query TEXT,
                    thread_id TEXT,
                    session_name TEXT,
                    model TEXT,
                    upstream_model TEXT,
                    reasoning_effort TEXT,
                    provider_id TEXT,
                    phase TEXT NOT NULL,
                    state TEXT NOT NULL,
                    status_code INTEGER,
                    outcome TEXT,
                    error_kind TEXT,
                    error_summary TEXT,
                    request_headers_json TEXT NOT NULL,
                    request_body BLOB,
                    request_body_truncated INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS debug_requests_started_at
                    ON debug_requests(started_at DESC);
                CREATE INDEX IF NOT EXISTS debug_requests_thread_id
                    ON debug_requests(thread_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS debug_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_key TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL,
                    provider_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    request_headers_json TEXT NOT NULL,
                    request_body BLOB,
                    request_body_truncated INTEGER NOT NULL DEFAULT 0,
                    response_status_code INTEGER,
                    response_headers_json TEXT,
                    response_body BLOB,
                    response_body_truncated INTEGER NOT NULL DEFAULT 0,
                    forwarded_body BLOB,
                    forwarded_body_truncated INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    error_kind TEXT,
                    error_summary TEXT,
                    UNIQUE(request_key, attempt)
                );
                CREATE INDEX IF NOT EXISTS debug_attempts_request_key
                    ON debug_attempts(request_key, attempt);
                """
            )
            attempt_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(debug_attempts)")
            }
            if "forwarded_body" not in attempt_columns:
                connection.execute("ALTER TABLE debug_attempts ADD COLUMN forwarded_body BLOB")
            if "forwarded_body_truncated" not in attempt_columns:
                connection.execute(
                    "ALTER TABLE debug_attempts ADD COLUMN forwarded_body_truncated INTEGER NOT NULL DEFAULT 0"
                )
            stale = connection.execute(
                "SELECT request_key FROM debug_requests WHERE state = 'running'"
            ).fetchall()
            if stale:
                now = time.time()
                connection.execute(
                    """
                    UPDATE debug_requests
                    SET updated_at = ?, finished_at = COALESCE(finished_at, ?),
                        state = 'interrupted', outcome = 'interrupted',
                        error_kind = 'process_restarted',
                        error_summary = '本地中转在调试记录完成前退出或重启'
                    WHERE state = 'running'
                    """,
                    (now, now),
                )
                connection.execute(
                    """
                    UPDATE debug_attempts
                    SET updated_at = ?, finished_at = COALESCE(finished_at, ?),
                        state = CASE WHEN state = 'running' THEN 'interrupted' ELSE state END,
                        error_kind = CASE WHEN state = 'running' THEN 'process_restarted' ELSE error_kind END,
                        error_summary = CASE WHEN state = 'running' THEN '本地中转在调试记录完成前退出或重启' ELSE error_summary END
                    WHERE request_key IN (SELECT request_key FROM debug_requests WHERE state = 'interrupted')
                    """,
                    (now, now),
                )
            self._prune(connection)

    def _prune(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT request_key FROM debug_requests ORDER BY started_at DESC, request_key DESC LIMIT -1 OFFSET ?",
            (self.retention,),
        ).fetchall()
        keys = [str(row[0]) for row in rows]
        if keys:
            placeholders = ",".join("?" for _ in keys)
            connection.execute(
                f"DELETE FROM debug_attempts WHERE request_key IN ({placeholders})",
                keys,
            )
            connection.execute(
                f"DELETE FROM debug_requests WHERE request_key IN ({placeholders})",
                keys,
            )
        while True:
            total = int(
                connection.execute(
                    """
                    SELECT COALESCE((SELECT SUM(COALESCE(length(request_body), 0)) FROM debug_requests), 0)
                         + COALESCE((SELECT SUM(
                                COALESCE(length(request_body), 0)
                              + COALESCE(length(response_body), 0)
                              + COALESCE(length(forwarded_body), 0)
                         ) FROM debug_attempts), 0)
                    """
                ).fetchone()[0]
            )
            if total <= self.max_total_bytes:
                break
            oldest = connection.execute(
                "SELECT request_key FROM debug_requests ORDER BY started_at ASC, request_key ASC LIMIT 1"
            ).fetchone()
            if oldest is None:
                break
            oldest_key = str(oldest[0])
            connection.execute("DELETE FROM debug_attempts WHERE request_key = ?", (oldest_key,))
            connection.execute("DELETE FROM debug_requests WHERE request_key = ?", (oldest_key,))

    def start_request(
        self,
        *,
        run_id: str,
        request_id: int,
        service_id: str | None = None,
        started_at: float,
        method: str,
        path: str,
        query: str,
        thread_id: str | None,
        session_name: str,
        headers: Mapping[str, Any] | None,
    ) -> "RequestDebugSession":
        key = self._key(run_id, request_id)
        now = time.time()
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO debug_requests (
                    request_key, run_id, request_id, service_id, started_at, updated_at,
                    method, path, query, thread_id, session_name, phase, state,
                    request_headers_json, request_body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', 'running', ?, ?)
                ON CONFLICT(request_key) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (
                    key,
                    str(run_id)[:64],
                    int(request_id),
                    str(service_id or self.service_id)[:32],
                    float(started_at),
                    now,
                    str(method)[:20],
                    _safe_url(path),
                    _safe_url(query),
                    thread_id,
                    str(session_name or "未知会话")[:240],
                    _json(_safe_headers(headers)),
                    sqlite3.Binary(b""),
                ),
            )
            self._prune(connection)
        return RequestDebugSession(self, key)

    def update_request(self, key: str, **fields: Any) -> None:
        allowed = {
            "phase", "state", "finished_at", "status_code", "outcome", "error_kind",
            "error_summary", "provider_id", "model", "upstream_model", "reasoning_effort",
            "thread_id", "session_name",
        }
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        for name, value in fields.items():
            if name not in allowed:
                continue
            assignments.append(f"{name} = ?")
            if value is None:
                values.append(None)
            elif name == "finished_at":
                values.append(float(value))
            elif name == "status_code":
                values.append(int(value))
            else:
                values.append(str(value)[:1000])
        values.append(key)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                f"UPDATE debug_requests SET {', '.join(assignments)} WHERE request_key = ?",
                values,
            )

    def update_request_body(self, key: str, body: bytes) -> None:
        bounded, truncated = _bounded_body(body, self.max_body_bytes)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE debug_requests SET updated_at = ?, request_body = ?, request_body_truncated = ? WHERE request_key = ?",
                (time.time(), sqlite3.Binary(bounded), int(truncated), key),
            )

    def start_attempt(
        self,
        key: str,
        *,
        attempt: int,
        provider_id: str,
        url: str,
        headers: Mapping[str, Any] | None,
        body: bytes,
    ) -> "RequestDebugAttempt":
        bounded, truncated = _bounded_body(body, self.max_body_bytes)
        now = time.time()
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                INSERT INTO debug_attempts (
                    request_key, attempt, started_at, updated_at, provider_id, url,
                    request_headers_json, request_body, request_body_truncated, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
                ON CONFLICT(request_key, attempt) DO UPDATE SET updated_at = excluded.updated_at
                RETURNING id
                """,
                (
                    key,
                    int(attempt),
                    now,
                    now,
                    str(provider_id)[:240],
                    _safe_url(url),
                    _json(_safe_headers(headers)),
                    sqlite3.Binary(bounded),
                    int(truncated),
                ),
            ).fetchone()
            attempt_id = int(row[0])
            connection.execute(
                "UPDATE debug_requests SET updated_at = ?, provider_id = ?, phase = ? WHERE request_key = ?",
                (now, str(provider_id)[:240], "connecting", key),
            )
        return RequestDebugAttempt(self, key, attempt_id, self.max_body_bytes)

    def set_attempt_response(
        self,
        key: str,
        attempt_id: int,
        *,
        status_code: int,
        headers: Mapping[str, Any] | None,
    ) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE debug_attempts
                SET updated_at = ?, response_status_code = ?, response_headers_json = ?
                WHERE id = ? AND request_key = ?
                """,
                (time.time(), int(status_code), _json(_safe_headers(headers)), int(attempt_id), key),
            )

    def append_attempt_response(self, key: str, attempt_id: int, data: bytes) -> None:
        if not data:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT length(response_body), response_body_truncated FROM debug_attempts WHERE id = ? AND request_key = ?",
                (int(attempt_id), key),
            ).fetchone()
            if row is None:
                return
            current = int(row[0] or 0)
            remaining = max(0, self.max_body_bytes - current)
            piece = bytes(data[:remaining])
            truncated = bool(row[1]) or len(data) > len(piece)
            if piece:
                connection.execute(
                    "UPDATE debug_attempts SET updated_at = ?, response_body = COALESCE(response_body, X'' ) || ?, response_body_truncated = ? WHERE id = ? AND request_key = ?",
                    (time.time(), sqlite3.Binary(piece), int(truncated), int(attempt_id), key),
                )
            else:
                connection.execute(
                    "UPDATE debug_attempts SET updated_at = ?, response_body_truncated = ? WHERE id = ? AND request_key = ?",
                    (time.time(), int(truncated), int(attempt_id), key),
                )

    def append_attempt_forwarded(self, key: str, attempt_id: int, data: bytes) -> None:
        if not data:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT length(forwarded_body), forwarded_body_truncated FROM debug_attempts WHERE id = ? AND request_key = ?",
                (int(attempt_id), key),
            ).fetchone()
            if row is None:
                return
            current = int(row[0] or 0)
            remaining = max(0, self.max_body_bytes - current)
            piece = bytes(data[:remaining])
            truncated = bool(row[1]) or len(data) > len(piece)
            if piece:
                connection.execute(
                    "UPDATE debug_attempts SET updated_at = ?, forwarded_body = COALESCE(forwarded_body, X'' ) || ?, forwarded_body_truncated = ? WHERE id = ? AND request_key = ?",
                    (time.time(), sqlite3.Binary(piece), int(truncated), int(attempt_id), key),
                )
            else:
                connection.execute(
                    "UPDATE debug_attempts SET updated_at = ?, forwarded_body_truncated = ? WHERE id = ? AND request_key = ?",
                    (time.time(), int(truncated), int(attempt_id), key),
                )

    def finish_attempt(self, key: str, attempt_id: int, **fields: Any) -> None:
        allowed = {"finished_at", "state", "error_kind", "error_summary"}
        assignments = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        for name, value in fields.items():
            if name in allowed:
                assignments.append(f"{name} = ?")
                if value is None:
                    values.append(None)
                elif name == "finished_at":
                    values.append(float(value))
                else:
                    values.append(str(value)[:1000])
        values.extend((int(attempt_id), key))
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                f"UPDATE debug_attempts SET {', '.join(assignments)} WHERE id = ? AND request_key = ?",
                values,
            )

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), self.retention))
        with self._lock, closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT request_key, run_id, request_id, service_id, started_at, updated_at,
                       finished_at, thread_id, session_name, model, upstream_model,
                       reasoning_effort, provider_id, phase, state, status_code, outcome,
                       error_kind, error_summary, length(request_body) AS request_body_bytes,
                       request_body_truncated
                FROM debug_requests ORDER BY started_at DESC, request_key DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [self._request_summary(row) for row in rows]

    @staticmethod
    def _request_summary(row: sqlite3.Row) -> dict[str, Any]:
        body_bytes = (
            row["request_body_bytes"]
            if "request_body_bytes" in row.keys()
            else len(row["request_body"] or b"")
        )
        return {
            "debug_id": str(row["request_key"]),
            "run_id": str(row["run_id"]),
            "request_id": int(row["request_id"]),
            "service_id": str(row["service_id"]),
            "started_at": round(float(row["started_at"]) * 1000),
            "updated_at": round(float(row["updated_at"]) * 1000),
            "finished_at": None if row["finished_at"] is None else round(float(row["finished_at"]) * 1000),
            "thread_id": row["thread_id"],
            "session_name": row["session_name"],
            "model": row["model"],
            "upstream_model": row["upstream_model"],
            "reasoning_effort": row["reasoning_effort"],
            "provider_id": row["provider_id"],
            "phase": row["phase"],
            "state": row["state"],
            "status_code": row["status_code"],
            "outcome": row["outcome"],
            "error_kind": row["error_kind"],
            "error_summary": row["error_summary"],
            "request_body_bytes": int(body_bytes or 0),
            "request_body_truncated": bool(row["request_body_truncated"]),
        }

    def get(self, debug_id: str) -> dict[str, Any] | None:
        key = str(debug_id)[:100]
        with self._lock, closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            request = connection.execute(
                "SELECT * FROM debug_requests WHERE request_key = ?",
                (key,),
            ).fetchone()
            if request is None:
                return None
            attempts = connection.execute(
                "SELECT * FROM debug_attempts WHERE request_key = ? ORDER BY attempt ASC",
                (key,),
            ).fetchall()
        result = self._request_summary(request)
        result.update(
            {
                "method": request["method"],
                "path": request["path"],
                "query": request["query"],
                "request_headers": json.loads(request["request_headers_json"] or "{}"),
                "request_body": _body_payload(request["request_body"], bool(request["request_body_truncated"])),
                "attempts": [
                    {
                        "attempt": int(row["attempt"]),
                        "started_at": round(float(row["started_at"]) * 1000),
                        "updated_at": round(float(row["updated_at"]) * 1000),
                        "finished_at": None if row["finished_at"] is None else round(float(row["finished_at"]) * 1000),
                        "provider_id": row["provider_id"],
                        "url": row["url"],
                        "request_headers": json.loads(row["request_headers_json"] or "{}"),
                        "request_body": _body_payload(row["request_body"], bool(row["request_body_truncated"])),
                        "response_status_code": row["response_status_code"],
                        "response_headers": json.loads(row["response_headers_json"] or "{}"),
                        "response_body": _body_payload(row["response_body"], bool(row["response_body_truncated"])),
                        "forwarded_body": _body_payload(row["forwarded_body"], bool(row["forwarded_body_truncated"])),
                        "state": row["state"],
                        "error_kind": row["error_kind"],
                        "error_summary": row["error_summary"],
                    }
                    for row in attempts
                ],
            }
        )
        return result


class RequestDebugSession:
    def __init__(self, store: RequestDebugStore, key: str) -> None:
        self.store = store
        self.key = key

    def update(self, **fields: Any) -> None:
        self.store.update_request(self.key, **fields)

    def body(self, value: bytes) -> None:
        self.store.update_request_body(self.key, value)

    def attempt(self, **kwargs: Any) -> "RequestDebugAttempt":
        return self.store.start_attempt(self.key, **kwargs)

    def finish(self, **fields: Any) -> None:
        self.store.update_request(self.key, **fields)


class RequestDebugAttempt:
    def __init__(self, store: RequestDebugStore, key: str, attempt_id: int, max_body_bytes: int) -> None:
        self.store = store
        self.key = key
        self.attempt_id = attempt_id
        self.max_body_bytes = max_body_bytes
        self._pending = bytearray()
        self._pending_forwarded = bytearray()
        self._last_flush_at = time.monotonic()
        self._finished = False

    def response(self, data: bytes) -> None:
        if self._finished or not data:
            return
        self._pending.extend(data)

    def forwarded(self, data: bytes) -> None:
        if self._finished or not data:
            return
        self._pending_forwarded.extend(data)

    def should_flush(self) -> bool:
        return bool(self._pending or self._pending_forwarded) and (
            len(self._pending) >= REQUEST_DEBUG_FLUSH_BYTES
            or time.monotonic() - self._last_flush_at >= REQUEST_DEBUG_FLUSH_INTERVAL_SECONDS
        )

    def flush(self) -> None:
        if not self._pending or self._finished:
            payload = b""
        else:
            payload = bytes(self._pending)
            self._pending.clear()
        forwarded = bytes(self._pending_forwarded)
        self._pending_forwarded.clear()
        self._last_flush_at = time.monotonic()
        if payload:
            self.store.append_attempt_response(self.key, self.attempt_id, payload)
        if forwarded:
            self.store.append_attempt_forwarded(self.key, self.attempt_id, forwarded)

    def set_response(self, *, status_code: int, headers: Mapping[str, Any] | None) -> None:
        self.store.set_attempt_response(self.key, self.attempt_id, status_code=status_code, headers=headers)

    def finish(self, *, state: str, error_kind: str | None = None, error_summary: str | None = None) -> None:
        if self._finished:
            return
        self.flush()
        self._finished = True
        self.store.finish_attempt(
            self.key,
            self.attempt_id,
            finished_at=time.time(),
            state=state,
            error_kind=error_kind,
            error_summary=error_summary,
        )
