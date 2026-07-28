from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from provider_status.config import PROBE_MODE_AUTOMATIC, ProviderConfig
from provider_status.state import (
    AvailabilityEvent,
    TargetState,
    aggregate_provider_state,
    time_weighted_availability,
    transition_target,
)


_VALID_WINDOWS = (0.125, 1, 7, 15, 30)
_PUBLIC_ERROR_CODES = frozenset(
    {
        "auth_failed",
        "client_blocked",
        "no_channel",
        "rate_limited",
        "model_unavailable",
        "upstream_unavailable",
        "timeout",
        "network_error",
        "invalid_output",
        "unknown_error",
    }
)
_SENSITIVE_HEADER_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:authorization|proxy-authorization|cookie|"
    r"set-cookie|x-api-key)[ \t]*:[ \t]*)[^\r\n]*",
    re.IGNORECASE | re.MULTILINE,
)
_INLINE_AUTHORIZATION_RE = re.compile(
    r"(?P<prefix>\b(?:proxy-)?authorization\s*:\s*"
    r"(?:bearer|basic)\s+)[^\s,;]+",
    re.IGNORECASE,
)
_JSON_SENSITIVE_KEY_RE = re.compile(
    r"(?P<prefix>[\"'](?:x[-_]?api[-_]?key|api[-_]?key|client[-_]?secret|"
    r"refresh[-_]?token|access[-_]?token|proxy[-_]?authorization|"
    r"authorization|set[-_]?cookie|cookie|password|passwd|secret|token)"
    r"[\"']\s*:\s*)"
    r'(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^,\s}\]]+)',
    re.IGNORECASE,
)
_SENSITIVE_KEY_VALUE_RE = re.compile(
    r"(?P<prefix>\b(?:x[-_]?api[-_]?key|api[-_]?key|client[-_]?secret|"
    r"refresh[-_]?token|access[-_]?token|proxy[-_]?authorization|"
    r"authorization|set[-_]?cookie|cookie|password|passwd|secret|token)"
    r"\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&]+)",
    re.IGNORECASE,
)
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{10,}")
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.IGNORECASE)
_MAX_ERROR_SUMMARY_LENGTH = 240
_AVAILABILITY_COLUMNS = {
    0.125: "availability_3h",
    1: "availability_24h",
    7: "availability_7d",
    15: "availability_15d",
    30: "availability_30d",
}


@dataclass(frozen=True)
class DueTarget:
    id: int
    provider_id: str
    model: str
    state: TargetState
    consecutive_successes: int
    consecutive_failures: int
    next_check_at: datetime


@dataclass(frozen=True)
class ProbeRecord:
    started_at: datetime
    success: bool
    latency_ms: int | None
    error_code: str | None = None
    error_summary: str | None = None


class StatusStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        database: Path | str = self._database_path
        connect_options: dict[str, Any] = {}
        if read_only:
            database = f"{self._database_path.resolve().as_uri()}?mode=ro"
            connect_options["uri"] = True
        connection = sqlite3.connect(database, timeout=5.0, **connect_options)
        connection.row_factory = sqlite3.Row
        if read_only:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(
        self,
        providers: Sequence[ProviderConfig],
        now: datetime,
    ) -> None:
        recorded_at = _to_iso(now)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            self._create_schema(connection)
            with connection:
                connection.execute(
                    "UPDATE providers SET enabled = 0, updated_at = ?",
                    (recorded_at,),
                )
                for display_order, provider in enumerate(providers):
                    enabled = int(bool(getattr(provider, "enabled", True)))
                    display_models = (
                        provider.display_models
                        if provider.display_models is not None
                        else provider.models
                    )
                    connection.execute(
                        """
                        INSERT INTO providers (
                            id, name, base_url, enabled, display_order,
                            display_models_json, probe_mode,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name = excluded.name,
                            base_url = excluded.base_url,
                            enabled = excluded.enabled,
                            display_order = excluded.display_order,
                            display_models_json = excluded.display_models_json,
                            probe_mode = excluded.probe_mode,
                            updated_at = excluded.updated_at
                        """,
                        (
                            provider.provider_id,
                            provider.name,
                            provider.base_url,
                            enabled,
                            display_order,
                            json.dumps(display_models, ensure_ascii=False),
                            provider.probe_mode,
                            recorded_at,
                            recorded_at,
                        ),
                    )
                    scheduled_models = (
                        provider.models
                        if provider.probe_mode == PROBE_MODE_AUTOMATIC
                        else ()
                    )
                    if scheduled_models:
                        model_placeholders = ", ".join(
                            "?" for _ in scheduled_models
                        )
                        connection.execute(
                            f"""
                            DELETE FROM probe_targets
                            WHERE provider_id = ?
                              AND model NOT IN ({model_placeholders})
                            """,
                            (provider.provider_id, *scheduled_models),
                        )
                    else:
                        connection.execute(
                            "DELETE FROM probe_targets WHERE provider_id = ?",
                            (provider.provider_id,),
                        )
                    schedule = connection.execute(
                        """
                        SELECT COUNT(*) AS target_count, MAX(next_check_at) AS latest
                        FROM probe_targets
                        WHERE provider_id = ?
                        """,
                        (provider.provider_id,),
                    ).fetchone()
                    next_new_target_at = _as_utc(now)
                    if schedule["target_count"]:
                        next_new_target_at = max(
                            next_new_target_at,
                            _from_iso(schedule["latest"]) + timedelta(seconds=30),
                        )
                    for model in scheduled_models:
                        existing = connection.execute(
                            """
                            SELECT id
                            FROM probe_targets
                            WHERE provider_id = ? AND model = ?
                            """,
                            (provider.provider_id, model),
                        ).fetchone()
                        if existing is not None:
                            continue
                        connection.execute(
                            """
                            INSERT INTO probe_targets (
                                provider_id,
                                model,
                                state,
                                consecutive_successes,
                                consecutive_failures,
                                next_check_at
                            ) VALUES (?, ?, ?, 0, 0, ?)
                            """,
                            (
                                provider.provider_id,
                                model,
                                TargetState.UNKNOWN.value,
                                _to_iso(next_new_target_at),
                            ),
                        )
                        next_new_target_at += timedelta(seconds=30)
        finally:
            connection.close()

    def list_due_targets(
        self,
        now: datetime,
        limit: int = 1,
    ) -> list[DueTarget]:
        if limit < 1:
            return []
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT
                    targets.id,
                    targets.provider_id,
                    targets.model,
                    targets.state,
                    targets.consecutive_successes,
                    targets.consecutive_failures,
                    targets.next_check_at
                FROM probe_targets AS targets
                JOIN providers ON providers.id = targets.provider_id
                WHERE providers.enabled = 1 AND targets.next_check_at <= ?
                ORDER BY targets.next_check_at, targets.id
                LIMIT ?
                """,
                (_to_iso(now), limit),
            ).fetchall()
        finally:
            connection.close()
        return [
            DueTarget(
                id=row["id"],
                provider_id=row["provider_id"],
                model=row["model"],
                state=TargetState(row["state"]),
                consecutive_successes=row["consecutive_successes"],
                consecutive_failures=row["consecutive_failures"],
                next_check_at=_from_iso(row["next_check_at"]),
            )
            for row in rows
        ]

    def get_target(self, provider_id: str, model: str) -> DueTarget | None:
        connection = self._connect(read_only=True)
        try:
            row = connection.execute(
                """
                SELECT
                    targets.id,
                    targets.provider_id,
                    targets.model,
                    targets.state,
                    targets.consecutive_successes,
                    targets.consecutive_failures,
                    targets.next_check_at
                FROM probe_targets AS targets
                JOIN providers ON providers.id = targets.provider_id
                WHERE providers.enabled = 1
                  AND targets.provider_id = ?
                  AND targets.model = ?
                """,
                (provider_id, model),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return DueTarget(
            id=row["id"],
            provider_id=row["provider_id"],
            model=row["model"],
            state=TargetState(row["state"]),
            consecutive_successes=row["consecutive_successes"],
            consecutive_failures=row["consecutive_failures"],
            next_check_at=_from_iso(row["next_check_at"]),
        )

    def record_probe(
        self,
        target_id: int,
        result: ProbeRecord,
        now: datetime,
        *,
        healthy_interval_seconds: float = 600,
        unhealthy_interval_seconds: float = 120,
    ) -> None:
        finished_at = _as_utc(now)
        started_at = _as_utc(result.started_at)
        connection = self._connect()
        try:
            with connection:
                target = connection.execute(
                    """
                    SELECT
                        id,
                        provider_id,
                        state,
                        consecutive_successes,
                        consecutive_failures
                    FROM probe_targets
                    WHERE id = ?
                    """,
                    (target_id,),
                ).fetchone()
                if target is None:
                    raise KeyError(f"unknown target: {target_id}")

                transition = transition_target(
                    TargetState(target["state"]),
                    target["consecutive_successes"],
                    target["consecutive_failures"],
                    result.success,
                    healthy_interval_seconds=healthy_interval_seconds,
                    unhealthy_interval_seconds=unhealthy_interval_seconds,
                )
                error_code = (
                    None
                    if result.success
                    else _normalize_error_code(result.error_code, allow_none=False)
                )
                error_summary = (
                    None
                    if result.success
                    else sanitize_error_summary(result.error_summary)
                )
                connection.execute(
                    """
                    INSERT INTO probe_runs (
                        target_id,
                        started_at,
                        finished_at,
                        success,
                        latency_ms,
                        error_code,
                        error_summary,
                        state_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_id,
                        _to_iso(started_at),
                        _to_iso(finished_at),
                        int(result.success),
                        result.latency_ms,
                        error_code,
                        error_summary,
                        transition.state.value,
                    ),
                )
                connection.execute(
                    """
                    UPDATE probe_targets
                    SET
                        state = ?,
                        consecutive_successes = ?,
                        consecutive_failures = ?,
                        last_checked_at = ?,
                        next_check_at = ?,
                        last_latency_ms = ?,
                        last_error_code = ?,
                        last_error_summary = ?
                    WHERE id = ?
                    """,
                    (
                        transition.state.value,
                        transition.consecutive_successes,
                        transition.consecutive_failures,
                        _to_iso(finished_at),
                        _to_iso(
                            finished_at
                            + timedelta(seconds=transition.next_interval_seconds)
                        ),
                        result.latency_ms,
                        error_code,
                        error_summary,
                        target_id,
                    ),
                )
                self._insert_provider_snapshot(
                    connection,
                    target["provider_id"],
                    finished_at,
                )
        finally:
            connection.close()

    def get_public_status(
        self,
        window_days: float,
        now: datetime,
    ) -> list[dict[str, Any]]:
        _validate_window(window_days)
        current_time = _as_utc(now)
        connection = self._connect(read_only=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            provider_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(providers)")
            }
            order_by = (
                "display_order, id" if "display_order" in provider_columns else "id"
            )
            display_models_column = (
                "display_models_json"
                if "display_models_json" in provider_columns
                else "'[]' AS display_models_json"
            )
            probe_mode_column = (
                "probe_mode"
                if "probe_mode" in provider_columns
                else f"'{PROBE_MODE_AUTOMATIC}' AS probe_mode"
            )
            providers = connection.execute(
                f"""
                SELECT id, name, base_url, {display_models_column},
                       {probe_mode_column}
                FROM providers
                WHERE enabled = 1
                ORDER BY {order_by}
                """
            ).fetchall()
            return [
                self._public_provider(connection, provider, window_days, current_time)
                for provider in providers
            ]
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def get_public_provider(
        self,
        provider_id: str,
        window_days: float,
        now: datetime,
    ) -> dict[str, Any] | None:
        _validate_window(window_days)
        current_time = _as_utc(now)
        connection = self._connect(read_only=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            provider_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(providers)")
            }
            display_models_column = (
                "display_models_json"
                if "display_models_json" in provider_columns
                else "'[]' AS display_models_json"
            )
            probe_mode_column = (
                "probe_mode"
                if "probe_mode" in provider_columns
                else f"'{PROBE_MODE_AUTOMATIC}' AS probe_mode"
            )
            provider = connection.execute(
                f"""
                SELECT id, name, base_url, {display_models_column},
                       {probe_mode_column}
                FROM providers
                WHERE id = ? AND enabled = 1
                """,
                (provider_id,),
            ).fetchone()
            if provider is None:
                return None
            return self._public_provider(
                connection,
                provider,
                window_days,
                current_time,
            )
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def delete_history_before(self, cutoff: datetime) -> None:
        cutoff_iso = _to_iso(cutoff)
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "DELETE FROM probe_runs WHERE finished_at < ?",
                    (cutoff_iso,),
                )
                connection.execute(
                    "DELETE FROM provider_snapshots WHERE recorded_at < ?",
                    (cutoff_iso,),
                )
        finally:
            connection.close()

    def resanitize_error_summaries(self) -> int:
        changed = 0
        connection = self._connect()
        try:
            with connection:
                for table, column in (
                    ("probe_targets", "last_error_summary"),
                    ("probe_runs", "error_summary"),
                ):
                    rows = connection.execute(
                        f"SELECT id, {column} AS summary FROM {table} "
                        f"WHERE {column} IS NOT NULL"
                    ).fetchall()
                    for row in rows:
                        sanitized = sanitize_error_summary(row["summary"])
                        if sanitized == row["summary"]:
                            continue
                        connection.execute(
                            f"UPDATE {table} SET {column} = ? WHERE id = ?",
                            (sanitized, row["id"]),
                        )
                        changed += 1
        finally:
            connection.close()
        return changed

    def publish_read_snapshot(self, destination: Path) -> None:
        public_path = Path(destination)
        public_path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{public_path.name}.",
            suffix=".tmp",
            dir=public_path.parent,
        )
        os.close(file_descriptor)
        temporary_path = Path(temporary_name)
        try:
            source = self._connect(read_only=True)
            try:
                target = sqlite3.connect(temporary_path, timeout=5.0)
                try:
                    source.backup(target)
                    journal_mode = target.execute(
                        "PRAGMA journal_mode=DELETE"
                    ).fetchone()[0]
                    if str(journal_mode).casefold() != "delete":
                        raise sqlite3.OperationalError(
                            "unable to create clean public database snapshot"
                        )
                finally:
                    target.close()
            finally:
                source.close()
            os.chmod(temporary_path, 0o640)
            os.replace(temporary_path, public_path)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                display_order INTEGER NOT NULL DEFAULT 0,
                display_models_json TEXT NOT NULL DEFAULT '[]',
                probe_mode TEXT NOT NULL DEFAULT 'automatic',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS probe_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                model TEXT NOT NULL,
                state TEXT NOT NULL,
                consecutive_successes INTEGER NOT NULL DEFAULT 0,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                next_check_at TEXT NOT NULL,
                last_latency_ms INTEGER,
                last_error_code TEXT,
                last_error_summary TEXT,
                UNIQUE(provider_id, model)
            );

            CREATE TABLE IF NOT EXISTS probe_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL REFERENCES probe_targets(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                success INTEGER NOT NULL,
                latency_ms INTEGER,
                error_code TEXT,
                error_summary TEXT,
                state_after TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS provider_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                recorded_at TEXT NOT NULL,
                state TEXT NOT NULL,
                availability_3h REAL,
                availability_24h REAL,
                availability_7d REAL,
                availability_15d REAL,
                availability_30d REAL
            );

            CREATE INDEX IF NOT EXISTS idx_probe_targets_due
                ON probe_targets(next_check_at, id);
            CREATE INDEX IF NOT EXISTS idx_probe_runs_target_finished
                ON probe_runs(target_id, finished_at);
            CREATE INDEX IF NOT EXISTS idx_provider_snapshots_recent
                ON provider_snapshots(provider_id, recorded_at DESC, id DESC);
            """
        )
        provider_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(providers)")
        }
        if "display_order" not in provider_columns:
            connection.execute(
                "ALTER TABLE providers "
                "ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0"
            )
        if "display_models_json" not in provider_columns:
            connection.execute(
                "ALTER TABLE providers "
                "ADD COLUMN display_models_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "probe_mode" not in provider_columns:
            connection.execute(
                "ALTER TABLE providers "
                "ADD COLUMN probe_mode TEXT NOT NULL DEFAULT 'automatic'"
            )
        snapshot_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(provider_snapshots)")
        }
        for column in ("availability_3h", "availability_24h"):
            if column not in snapshot_columns:
                connection.execute(
                    f"ALTER TABLE provider_snapshots ADD COLUMN {column} REAL"
                )

    def _insert_provider_snapshot(
        self,
        connection: sqlite3.Connection,
        provider_id: str,
        now: datetime,
    ) -> None:
        targets = connection.execute(
            """
            SELECT id, state
            FROM probe_targets
            WHERE provider_id = ?
            ORDER BY id
            """,
            (provider_id,),
        ).fetchall()
        state = aggregate_provider_state(TargetState(row["state"]) for row in targets)
        availabilities = {
            window_days: self._provider_availability(
                connection,
                targets,
                window_days,
                now,
            )
            for window_days in _VALID_WINDOWS
        }
        connection.execute(
            """
            INSERT INTO provider_snapshots (
                provider_id,
                recorded_at,
                state,
                availability_3h,
                availability_24h,
                availability_7d,
                availability_15d,
                availability_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider_id,
                _to_iso(now),
                state.value,
                availabilities[0.125],
                availabilities[1],
                availabilities[7],
                availabilities[15],
                availabilities[30],
            ),
        )

    def _public_provider(
        self,
        connection: sqlite3.Connection,
        provider: sqlite3.Row,
        window_days: float,
        now: datetime,
    ) -> dict[str, Any]:
        targets = connection.execute(
            """
            SELECT *
            FROM probe_targets
            WHERE provider_id = ?
            ORDER BY id
            """,
            (provider["id"],),
        ).fetchall()
        models = []
        model_availabilities: list[float] = []
        for target in targets:
            availability = self._target_availability(
                connection,
                target["id"],
                window_days,
                now,
            )
            if availability is not None:
                model_availabilities.append(availability)
            model_history_rows = connection.execute(
                """
                SELECT
                    finished_at AS recorded_at,
                    state_after AS state,
                    error_code
                FROM probe_runs
                WHERE target_id = ? AND finished_at <= ?
                ORDER BY finished_at DESC, id DESC
                LIMIT 60
                """,
                (target["id"], _to_iso(now)),
            ).fetchall()
            last_success = connection.execute(
                """
                SELECT MAX(finished_at) AS finished_at
                FROM probe_runs
                WHERE target_id = ? AND success = 1 AND finished_at <= ?
                """,
                (target["id"], _to_iso(now)),
            ).fetchone()
            models.append(
                {
                    "model": target["model"],
                    "state": target["state"],
                    "availability": availability,
                    "latest_latency": target["last_latency_ms"],
                    "last_checked": target["last_checked_at"],
                    "next_check": target["next_check_at"],
                    "error_code": _normalize_error_code(
                        target["last_error_code"],
                        allow_none=True,
                    ),
                    "error_summary": sanitize_error_summary(
                        target["last_error_summary"]
                    ),
                    "consecutive_successes": target["consecutive_successes"],
                    "last_success_at": last_success["finished_at"],
                    "history": [
                        {
                            "recorded_at": row["recorded_at"],
                            "state": row["state"],
                            "error_code": _normalize_error_code(
                                row["error_code"],
                                allow_none=True,
                            ),
                        }
                        for row in model_history_rows
                    ],
                }
            )

        checked_targets = [row for row in targets if row["last_checked_at"] is not None]
        latest_target = max(
            checked_targets,
            key=lambda row: (row["last_checked_at"], row["id"]),
            default=None,
        )
        next_check = min(
            (row["next_check_at"] for row in targets),
            default=None,
        )
        availability_column = _AVAILABILITY_COLUMNS[window_days]
        history_rows = []
        if provider["probe_mode"] == PROBE_MODE_AUTOMATIC:
            history_rows = connection.execute(
                f"""
                SELECT recorded_at, state, {availability_column} AS availability
                FROM provider_snapshots
                WHERE provider_id = ? AND recorded_at <= ?
                ORDER BY recorded_at DESC, id DESC
                LIMIT 60
                """,
                (provider["id"], _to_iso(now)),
            ).fetchall()
        return {
            "provider_id": provider["id"],
            "name": provider["name"],
            "base_url": provider["base_url"],
            "probe_mode": provider["probe_mode"],
            "state": aggregate_provider_state(
                TargetState(row["state"]) for row in targets
            ).value,
            "availability": min(model_availabilities)
            if model_availabilities
            else None,
            "latest_latency": latest_target["last_latency_ms"]
            if latest_target is not None
            else None,
            "last_checked": latest_target["last_checked_at"]
            if latest_target is not None
            else None,
            "next_check": next_check,
            "model_count": len(models),
            "display_models": _decode_display_models(
                provider["display_models_json"],
                models,
            ),
            "models": models,
            "history": [
                {
                    "recorded_at": row["recorded_at"],
                    "state": row["state"],
                    "availability": row["availability"],
                }
                for row in history_rows
            ],
        }

    def _provider_availability(
        self,
        connection: sqlite3.Connection,
        targets: Sequence[sqlite3.Row],
        window_days: float,
        now: datetime,
    ) -> float | None:
        values = [
            availability
            for target in targets
            if (
                availability := self._target_availability(
                    connection,
                    target["id"],
                    window_days,
                    now,
                )
            )
            is not None
        ]
        return min(values) if values else None

    @staticmethod
    def _target_availability(
        connection: sqlite3.Connection,
        target_id: int,
        window_days: float,
        now: datetime,
    ) -> float | None:
        rows = connection.execute(
            """
            SELECT finished_at, success
            FROM probe_runs
            WHERE target_id = ? AND finished_at <= ?
            ORDER BY finished_at, id
            """,
            (target_id, _to_iso(now)),
        ).fetchall()
        events = tuple(
            AvailabilityEvent(
                at=_from_iso(row["finished_at"]),
                success=bool(row["success"]),
            )
            for row in rows
        )
        return time_weighted_availability(
            events,
            now - timedelta(days=window_days),
            now,
        )


def _validate_window(window_days: float) -> None:
    if window_days not in _VALID_WINDOWS:
        raise ValueError("window_days must be one of 0.125, 1, 7, 15, or 30")


def _decode_display_models(
    value: str | None,
    models: Sequence[dict[str, Any]],
) -> list[str]:
    fallback = [model["model"] for model in models]
    if not value:
        return fallback
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
    if (
        not isinstance(decoded, list)
        or not decoded
        or any(not isinstance(model, str) or not model for model in decoded)
        or len(set(decoded)) != len(decoded)
        or any(model not in decoded for model in fallback)
    ):
        return fallback
    return decoded


def _normalize_error_code(
    value: str | None,
    *,
    allow_none: bool,
) -> str | None:
    if value is None and allow_none:
        return None
    return value if value in _PUBLIC_ERROR_CODES else "unknown_error"


def sanitize_error_summary(
    value: str | None,
    *,
    sensitive_values: Sequence[str] = (),
) -> str | None:
    if value is None:
        return None
    sanitized = value
    for sensitive_value in sensitive_values:
        if sensitive_value:
            sanitized = sanitized.replace(sensitive_value, "[REDACTED]")
    sanitized = _SENSITIVE_HEADER_RE.sub(_redact_matched_value, sanitized)
    sanitized = _INLINE_AUTHORIZATION_RE.sub(_redact_matched_value, sanitized)
    sanitized = _URL_RE.sub(_sanitize_url, sanitized)
    sanitized = _JSON_SENSITIVE_KEY_RE.sub(_redact_matched_value, sanitized)
    sanitized = _SENSITIVE_KEY_VALUE_RE.sub(_redact_matched_value, sanitized)
    sanitized = _SK_TOKEN_RE.sub("[REDACTED]", sanitized)
    return sanitized[:_MAX_ERROR_SUMMARY_LENGTH]


def _redact_matched_value(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}[REDACTED]"


def _sanitize_url(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    try:
        parts = urlsplit(raw_url)
        hostname = parts.hostname
        if not hostname:
            return "[REDACTED_URL]"
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parts.port}" if parts.port is not None else ""
    except ValueError:
        return "[REDACTED_URL]"

    netloc = parts.netloc
    if parts.username is not None or parts.password is not None:
        netloc = f"redacted@{host}{port}"
    query = _sanitize_url_parameters(parts.query)
    fragment = (
        _sanitize_url_parameters(parts.fragment)
        if "=" in parts.fragment
        else "[REDACTED]" if parts.fragment else ""
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


def _sanitize_url_parameters(value: str) -> str:
    return urlencode(
        [
            (name, "[REDACTED]")
            for name, _query_value in parse_qsl(value, keep_blank_values=True)
        ],
        doseq=True,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(timezone.utc)


def _to_iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _from_iso(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


__all__ = ["DueTarget", "ProbeRecord", "StatusStore", "sanitize_error_summary"]
