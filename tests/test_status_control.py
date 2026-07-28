import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from provider_status.control import (
    ManualProbeControlStore,
    ManualProbeCooldownError,
    ManualProbeQueueFullError,
    public_manual_job,
)


class ManualProbeControlStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_context.cleanup)
        self.database = Path(self.temp_context.name) / "control" / "manual.sqlite3"
        self.store = ManualProbeControlStore(self.database)
        self.store.initialize()
        self.now = datetime(2026, 7, 27, 7, 0, tzinfo=timezone.utc)

    def test_enqueue_deduplicates_active_job_and_claims_fifo(self) -> None:
        first, created = self.store.enqueue(
            "first", self.now, requested_models=("model-b",)
        )
        duplicate, duplicate_created = self.store.enqueue(
            "first",
            self.now + timedelta(seconds=1),
        )
        second, _ = self.store.enqueue("second", self.now + timedelta(seconds=2))

        self.assertTrue(created)
        self.assertEqual(first.requested_models, ("model-b",))
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertEqual(self.store.claim_next(self.now).job_id, first.job_id)
        self.store.complete(first.job_id, self.now + timedelta(seconds=3))
        self.assertEqual(self.store.claim_next(self.now).job_id, second.job_id)

    def test_initialize_migrates_legacy_jobs_as_all_models(self) -> None:
        legacy_database = Path(self.temp_context.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_database)
        connection.execute(
            """CREATE TABLE manual_probe_jobs (
                id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, status TEXT NOT NULL,
                requested_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                total_models INTEGER NOT NULL DEFAULT 0,
                completed_models INTEGER NOT NULL DEFAULT 0, error_summary TEXT
            )"""
        )
        connection.commit()
        connection.close()

        migrated = ManualProbeControlStore(legacy_database)
        migrated.initialize()
        job, _ = migrated.enqueue("provider", self.now)

        self.assertIsNone(job.requested_models)

    def test_cooldown_and_global_queue_limit_are_enforced(self) -> None:
        job, _ = self.store.enqueue("first", self.now)
        claimed = self.store.claim_next(self.now)
        assert claimed is not None
        self.store.complete(job.job_id, self.now + timedelta(seconds=1))

        with self.assertRaises(ManualProbeCooldownError) as cooldown:
            self.store.enqueue("first", self.now + timedelta(seconds=30))
        self.assertEqual(cooldown.exception.retry_after_seconds, 30)

        self.store.enqueue("second", self.now + timedelta(seconds=31))
        with self.assertRaises(ManualProbeQueueFullError):
            self.store.enqueue(
                "third",
                self.now + timedelta(seconds=32),
                max_active_jobs=1,
            )

    def test_results_are_ordered_sanitized_and_public(self) -> None:
        job, _ = self.store.enqueue("provider", self.now)
        claimed = self.store.claim_next(self.now)
        assert claimed is not None
        self.store.set_total_models(job.job_id, 2)
        self.store.record_result(
            job.job_id,
            model="model-b",
            position=1,
            scheduled=False,
            success=False,
            latency_ms=220,
            error_code="auth_failed",
            error_summary="Authorization: Bearer TOP-SECRET-123456",
            finished_at=self.now + timedelta(seconds=2),
        )
        self.store.record_result(
            job.job_id,
            model="model-a",
            position=0,
            scheduled=True,
            success=True,
            latency_ms=110,
            error_code=None,
            error_summary=None,
            finished_at=self.now + timedelta(seconds=1),
        )
        self.store.complete(job.job_id, self.now + timedelta(seconds=2))

        completed = self.store.get_job(job.job_id)
        assert completed is not None
        payload = public_manual_job(completed)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["completed_models"], 2)
        self.assertEqual(
            [result["model"] for result in payload["results"]],
            ["model-a", "model-b"],
        )
        self.assertNotIn("TOP-SECRET", str(payload))

    def test_recover_interrupted_jobs_marks_them_failed(self) -> None:
        job, _ = self.store.enqueue("provider", self.now)
        self.store.claim_next(self.now)

        recovered = self.store.recover_interrupted_jobs(
            self.now + timedelta(minutes=1)
        )

        self.assertEqual(recovered, 1)
        failed = self.store.get_job(job.job_id)
        assert failed is not None
        self.assertEqual(failed.status, "failed")

    def test_initialize_allows_group_member_without_chmod_ownership(self) -> None:
        with patch("provider_status.control.os.chmod", side_effect=PermissionError):
            self.store.initialize()

        job, created = self.store.enqueue("provider", self.now)
        self.assertTrue(created)
        self.assertEqual(job.provider_id, "provider")

    def test_result_history_is_newest_first_and_limited_per_model(self) -> None:
        for offset, success in ((0, True), (61, False)):
            requested_at = self.now + timedelta(seconds=offset)
            job, _ = self.store.enqueue("provider", requested_at)
            self.store.claim_next(requested_at)
            self.store.set_total_models(job.job_id, 1)
            self.store.record_result(
                job.job_id,
                model="gpt-5.6-sol",
                position=0,
                scheduled=False,
                success=success,
                latency_ms=100 + offset,
                error_code=None if success else "timeout",
                error_summary=None,
                finished_at=requested_at + timedelta(seconds=1),
            )
            self.store.complete(job.job_id, requested_at + timedelta(seconds=1))

        history = self.store.result_history(
            ("provider",),
            limit_per_model=1,
        )

        results = history["provider"]["gpt-5.6-sol"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertEqual(results[0]["error_code"], "timeout")


if __name__ == "__main__":
    unittest.main()
