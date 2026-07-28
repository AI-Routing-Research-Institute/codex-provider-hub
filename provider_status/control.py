from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from provider_status.store import sanitize_error_summary


DEFAULT_CONTROL_DATABASE = Path(
    "/var/lib/codex-provider-probe/control/manual-probes.sqlite3"
)
ACTIVE_STATUSES = ("queued", "running")


class ManualProbeCooldownError(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("manual probe is cooling down")
        self.retry_after_seconds = max(1, retry_after_seconds)


class ManualProbeQueueFullError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManualProbeJob:
    job_id: str
    provider_id: str
    status: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    total_models: int
    completed_models: int
    error_summary: str | None
    requested_models: tuple[str, ...] | None = None
    results: tuple[dict[str, Any], ...] = ()


class ManualProbeControlStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS manual_probe_jobs (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    total_models INTEGER NOT NULL DEFAULT 0,
                    completed_models INTEGER NOT NULL DEFAULT 0,
                    error_summary TEXT
                );

                CREATE TABLE IF NOT EXISTS manual_probe_results (
                    job_id TEXT NOT NULL
                        REFERENCES manual_probe_jobs(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    scheduled INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    latency_ms INTEGER,
                    error_code TEXT,
                    error_summary TEXT,
                    finished_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, model)
                );

                CREATE INDEX IF NOT EXISTS idx_manual_probe_jobs_queue
                    ON manual_probe_jobs(status, requested_at, id);
                CREATE INDEX IF NOT EXISTS idx_manual_probe_jobs_provider
                    ON manual_probe_jobs(provider_id, requested_at DESC, id DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(manual_probe_jobs)")
            }
            if "requested_models_json" not in columns:
                connection.execute(
                    "ALTER TABLE manual_probe_jobs ADD COLUMN requested_models_json TEXT"
                )
            connection.commit()
        finally:
            connection.close()
        try:
            os.chmod(self._database_path, 0o660)
        except PermissionError:
            # Production creates this as root:codex-provider-control. Group
            # members may use it but cannot change its mode.
            pass

    def recover_interrupted_jobs(self, now: datetime) -> int:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE manual_probe_jobs
                    SET status = 'failed', finished_at = ?,
                        error_summary = '检测服务在任务完成前重新启动。'
                    WHERE status = 'running'
                    """,
                    (_to_iso(now),),
                )
            return cursor.rowcount
        finally:
            connection.close()

    def enqueue(
        self,
        provider_id: str,
        now: datetime,
        *,
        requested_models: tuple[str, ...] | None = None,
        cooldown_seconds: int = 60,
        max_active_jobs: int = 8,
    ) -> tuple[ManualProbeJob, bool]:
        requested_at = _as_utc(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT id
                FROM manual_probe_jobs
                WHERE provider_id = ? AND status IN ('queued', 'running')
                ORDER BY requested_at, id
                LIMIT 1
                """,
                (provider_id,),
            ).fetchone()
            if active is not None:
                connection.commit()
                job = self.get_job(active["id"])
                assert job is not None
                return job, False

            latest = connection.execute(
                """
                SELECT requested_at
                FROM manual_probe_jobs
                WHERE provider_id = ?
                ORDER BY requested_at DESC, id DESC
                LIMIT 1
                """,
                (provider_id,),
            ).fetchone()
            if latest is not None:
                elapsed = (requested_at - _from_iso(latest["requested_at"])).total_seconds()
                if elapsed < cooldown_seconds:
                    connection.rollback()
                    raise ManualProbeCooldownError(round(cooldown_seconds - elapsed))

            active_count = connection.execute(
                "SELECT COUNT(*) FROM manual_probe_jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
            if active_count >= max_active_jobs:
                connection.rollback()
                raise ManualProbeQueueFullError("manual probe queue is full")

            job_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO manual_probe_jobs (
                    id, provider_id, status, requested_at, requested_models_json
                ) VALUES (?, ?, 'queued', ?, ?)
                """,
                (
                    job_id,
                    provider_id,
                    _to_iso(requested_at),
                    json.dumps(requested_models) if requested_models is not None else None,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        job = self.get_job(job_id)
        assert job is not None
        return job, True

    def claim_next(self, now: datetime) -> ManualProbeJob | None:
        connection = self._connect()
        job_id: str | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id
                FROM manual_probe_jobs
                WHERE status = 'queued'
                ORDER BY requested_at, id
                LIMIT 1
                """
            ).fetchone()
            if row is not None:
                job_id = row["id"]
                connection.execute(
                    """
                    UPDATE manual_probe_jobs
                    SET status = 'running', started_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (_to_iso(now), job_id),
                )
            connection.commit()
        finally:
            connection.close()
        return self.get_job(job_id) if job_id else None

    def set_total_models(self, job_id: str, total_models: int) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "UPDATE manual_probe_jobs SET total_models = ? WHERE id = ?",
                    (max(0, total_models), job_id),
                )
        finally:
            connection.close()

    def record_result(
        self,
        job_id: str,
        *,
        model: str,
        position: int,
        scheduled: bool,
        success: bool,
        latency_ms: int | None,
        error_code: str | None,
        error_summary: str | None,
        finished_at: datetime,
    ) -> None:
        sanitized_summary = sanitize_error_summary(error_summary)
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO manual_probe_results (
                        job_id, model, position, scheduled, success, latency_ms,
                        error_code, error_summary, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id, model) DO UPDATE SET
                        position = excluded.position,
                        scheduled = excluded.scheduled,
                        success = excluded.success,
                        latency_ms = excluded.latency_ms,
                        error_code = excluded.error_code,
                        error_summary = excluded.error_summary,
                        finished_at = excluded.finished_at
                    """,
                    (
                        job_id,
                        model,
                        position,
                        int(scheduled),
                        int(success),
                        latency_ms,
                        error_code,
                        sanitized_summary,
                        _to_iso(finished_at),
                    ),
                )
                connection.execute(
                    """
                    UPDATE manual_probe_jobs
                    SET completed_models = (
                        SELECT COUNT(*) FROM manual_probe_results WHERE job_id = ?
                    )
                    WHERE id = ?
                    """,
                    (job_id, job_id),
                )
        finally:
            connection.close()

    def complete(self, job_id: str, now: datetime) -> None:
        self._finish(job_id, "completed", now, None)

    def fail(self, job_id: str, now: datetime, summary: str) -> None:
        self._finish(job_id, "failed", now, sanitize_error_summary(summary))

    def _finish(
        self,
        job_id: str,
        status: str,
        now: datetime,
        error_summary: str | None,
    ) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE manual_probe_jobs
                    SET status = ?, finished_at = ?, error_summary = ?
                    WHERE id = ?
                    """,
                    (status, _to_iso(now), error_summary, job_id),
                )
        finally:
            connection.close()

    def get_job(self, job_id: str | None) -> ManualProbeJob | None:
        if not job_id:
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM manual_probe_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            results = connection.execute(
                """
                SELECT model, position, scheduled, success, latency_ms,
                       error_code, error_summary, finished_at
                FROM manual_probe_results
                WHERE job_id = ?
                ORDER BY position, model
                """,
                (job_id,),
            ).fetchall()
        finally:
            connection.close()
        return _job_from_rows(row, results)

    def latest_jobs(self, provider_ids: Iterable[str]) -> dict[str, ManualProbeJob]:
        jobs: dict[str, ManualProbeJob] = {}
        connection = self._connect()
        try:
            for provider_id in provider_ids:
                row = connection.execute(
                    """
                    SELECT id
                    FROM manual_probe_jobs
                    WHERE provider_id = ?
                    ORDER BY requested_at DESC, id DESC
                    LIMIT 1
                    """,
                    (provider_id,),
                ).fetchone()
                if row is not None:
                    job = self.get_job(row["id"])
                    if job is not None:
                        jobs[provider_id] = job
        finally:
            connection.close()
        return jobs

    def result_history(
        self,
        provider_ids: Iterable[str],
        *,
        limit_per_model: int = 60,
    ) -> dict[str, dict[str, tuple[dict[str, Any], ...]]]:
        provider_id_list = tuple(dict.fromkeys(provider_ids))
        if not provider_id_list or limit_per_model < 1:
            return {}
        placeholders = ", ".join("?" for _ in provider_id_list)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                WITH ranked_results AS (
                    SELECT jobs.provider_id, results.model, results.success,
                           results.latency_ms, results.error_code,
                           results.finished_at, jobs.requested_at,
                           results.position,
                           ROW_NUMBER() OVER (
                               PARTITION BY jobs.provider_id, results.model
                               ORDER BY results.finished_at DESC,
                                        jobs.requested_at DESC,
                                        results.position
                           ) AS result_rank
                    FROM manual_probe_results AS results
                    JOIN manual_probe_jobs AS jobs ON jobs.id = results.job_id
                    WHERE jobs.provider_id IN ({placeholders})
                )
                SELECT provider_id, model, success, latency_ms, error_code,
                       finished_at
                FROM ranked_results
                WHERE result_rank <= ?
                ORDER BY finished_at DESC, requested_at DESC, position
                """,
                (*provider_id_list, limit_per_model),
            ).fetchall()
        finally:
            connection.close()

        collected: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in rows:
            provider_history = collected.setdefault(row["provider_id"], {})
            model_history = provider_history.setdefault(row["model"], [])
            if len(model_history) >= limit_per_model:
                continue
            model_history.append(
                {
                    "success": bool(row["success"]),
                    "latency_ms": row["latency_ms"],
                    "error_code": row["error_code"],
                    "finished_at": row["finished_at"],
                }
            )
        return {
            provider_id: {
                model: tuple(results)
                for model, results in model_history.items()
            }
            for provider_id, model_history in collected.items()
        }


def public_manual_job(job: ManualProbeJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "provider_id": job.provider_id,
        "status": job.status,
        "requested_at": job.requested_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "total_models": job.total_models,
        "completed_models": job.completed_models,
        "requested_models": list(job.requested_models) if job.requested_models else None,
        "error_summary": sanitize_error_summary(job.error_summary),
        "results": [
            {
                "model": result["model"],
                "scheduled": bool(result["scheduled"]),
                "success": bool(result["success"]),
                "latency_ms": result["latency_ms"],
                "error_code": result["error_code"],
                "error_summary": sanitize_error_summary(result["error_summary"]),
                "finished_at": result["finished_at"],
            }
            for result in job.results
        ],
    }


def _job_from_rows(
    row: sqlite3.Row,
    results: Iterable[sqlite3.Row],
) -> ManualProbeJob:
    return ManualProbeJob(
        job_id=row["id"],
        provider_id=row["provider_id"],
        status=row["status"],
        requested_at=_from_iso(row["requested_at"]),
        started_at=_from_iso(row["started_at"]) if row["started_at"] else None,
        finished_at=_from_iso(row["finished_at"]) if row["finished_at"] else None,
        total_models=row["total_models"],
        completed_models=row["completed_models"],
        error_summary=row["error_summary"],
        requested_models=_decode_requested_models(row["requested_models_json"]),
        results=tuple(dict(result) for result in results),
    )


def _decode_requested_models(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    decoded = json.loads(value)
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise ValueError("invalid requested models in manual probe database")
    return tuple(decoded)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(timezone.utc)


def _to_iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _from_iso(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


__all__ = [
    "DEFAULT_CONTROL_DATABASE",
    "ManualProbeControlStore",
    "ManualProbeCooldownError",
    "ManualProbeJob",
    "ManualProbeQueueFullError",
    "public_manual_job",
]
