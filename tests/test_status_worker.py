import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from provider_status.config import ProviderConfig, ServiceConfig
from provider_status.control import ManualProbeControlStore
from provider_status.probe import HealthProbeResult
from provider_status.state import TargetState
from provider_status.store import StatusStore
from provider_status.worker import (
    StatusWorker,
    _random_interval,
    cleanup_stale_run_directories,
)


def make_provider() -> ProviderConfig:
    return ProviderConfig(
        provider_id="provider-alpha",
        name="Provider Alpha",
        base_url="https://alpha.example.com/v1",
        credential_name="provider_alpha_api_key",
        models=("gpt-5.6-sol", "gpt-5.6-terra"),
        healthy_interval_seconds=600,
        unhealthy_interval_seconds=120,
        timeout_seconds=90,
    )


class FakeProbe:
    def __init__(self, results: list[HealthProbeResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str, str]] = []
        self.active = 0
        self.max_active = 0

    def run(
        self,
        provider: ProviderConfig,
        model: str,
        api_key: str,
    ) -> HealthProbeResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((provider.provider_id, model, api_key))
            return self.results.pop(0)
        finally:
            self.active -= 1


class StatusWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root_context = tempfile.TemporaryDirectory()
        self.root = Path(self.root_context.name)
        self.t0 = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
        self.provider = make_provider()
        self.config = ServiceConfig(
            providers=(self.provider,),
            database_path=self.root / "private" / "status.sqlite3",
            public_database_path=self.root / "public" / "status.sqlite3",
            temp_root=self.root / "private" / "tmp",
            codex_bin=Path("/opt/codex/bin/codex"),
        )
        self.credentials = self.root / "credentials"
        self.credentials.mkdir()
        (self.credentials / "provider_alpha_api_key").write_text(
            "dedicated-test-key\n",
            encoding="utf-8",
        )
        self.env = {"CREDENTIALS_DIRECTORY": str(self.credentials)}
        self.store = StatusStore(self.config.database_path)
        self.store.initialize(self.config.providers, self.t0)

    def tearDown(self) -> None:
        self.root_context.cleanup()

    def test_runs_one_due_target_and_records_healthy_schedule(self) -> None:
        probe = FakeProbe(
            [HealthProbeResult(True, 1250, None, None)]
        )
        worker = StatusWorker(self.config, self.store, probe, env=self.env)

        ran = worker.run_due_once(self.t0)

        self.assertTrue(ran)
        self.assertEqual(
            probe.calls,
            [("provider-alpha", "gpt-5.6-sol", "dedicated-test-key")],
        )
        self.assertEqual(probe.max_active, 1)
        public = StatusStore(self.config.public_database_path).get_public_provider(
            "provider-alpha",
            7,
            self.t0 + timedelta(seconds=2),
        )
        sol = next(item for item in public["models"] if item["model"] == "gpt-5.6-sol")
        self.assertEqual(sol["state"], TargetState.HEALTHY.value)
        self.assertEqual(
            sol["next_check"],
            (self.t0 + timedelta(seconds=601.25)).isoformat(),
        )

    def test_configured_intervals_and_injected_jitter_drive_schedule(self) -> None:
        custom_provider = replace(
            self.provider,
            healthy_interval_seconds=700,
            unhealthy_interval_seconds=80,
        )
        custom_config = replace(self.config, providers=(custom_provider,))
        probe = FakeProbe(
            [
                HealthProbeResult(True, 100, None, None),
                HealthProbeResult(False, 200, "no_channel", "No channel"),
            ]
        )
        worker = StatusWorker(
            custom_config,
            self.store,
            probe,
            env=self.env,
            jitter=lambda seconds: seconds + 7,
        )

        self.assertTrue(worker.run_due_once(self.t0))
        self.assertTrue(worker.run_due_once(self.t0 + timedelta(seconds=30)))

        public = StatusStore(custom_config.public_database_path).get_public_provider(
            "provider-alpha",
            7,
            self.t0 + timedelta(seconds=31),
        )
        assert public is not None
        models = {item["model"]: item for item in public["models"]}
        self.assertEqual(
            models["gpt-5.6-sol"]["next_check"],
            (self.t0 + timedelta(seconds=707.1)).isoformat(),
        )
        self.assertEqual(
            models["gpt-5.6-terra"]["next_check"],
            (self.t0 + timedelta(seconds=117.2)).isoformat(),
        )

    def test_interval_sampler_receives_healthy_and_unhealthy_ranges(self) -> None:
        custom_provider = replace(
            self.provider,
            healthy_interval_seconds=600,
            healthy_interval_max_seconds=1200,
            unhealthy_interval_seconds=120,
            unhealthy_interval_max_seconds=300,
        )
        custom_config = replace(self.config, providers=(custom_provider,))
        probe = FakeProbe(
            [
                HealthProbeResult(True, 100, None, None),
                HealthProbeResult(False, 200, "no_channel", "No channel"),
            ]
        )
        samples: list[tuple[float, float]] = []

        def sampler(minimum: float, maximum: float) -> float:
            samples.append((minimum, maximum))
            return maximum - 1

        worker = StatusWorker(
            custom_config,
            self.store,
            probe,
            env=self.env,
            interval_sampler=sampler,
        )

        self.assertTrue(worker.run_due_once(self.t0))
        self.assertTrue(worker.run_due_once(self.t0 + timedelta(seconds=30)))

        self.assertEqual(
            samples,
            [(600, 1200), (120, 300), (600, 1200), (120, 300)],
        )
        public = StatusStore(custom_config.public_database_path).get_public_provider(
            "provider-alpha",
            7,
            self.t0 + timedelta(seconds=31),
        )
        assert public is not None
        models = {item["model"]: item for item in public["models"]}
        self.assertEqual(
            models["gpt-5.6-sol"]["next_check"],
            (self.t0 + timedelta(seconds=1199.1)).isoformat(),
        )
        self.assertEqual(
            models["gpt-5.6-terra"]["next_check"],
            (self.t0 + timedelta(seconds=329.2)).isoformat(),
        )

    def test_random_interval_stays_inside_configured_range(self) -> None:
        samples = [_random_interval(120, 300) for _ in range(100)]

        self.assertTrue(all(120 <= sample <= 300 for sample in samples))

    def test_failure_uses_unhealthy_schedule_and_targets_remain_staggered(self) -> None:
        probe = FakeProbe(
            [
                HealthProbeResult(True, 100, None, None),
                HealthProbeResult(False, 500, "no_channel", "No available channel"),
            ]
        )
        worker = StatusWorker(self.config, self.store, probe, env=self.env)

        self.assertTrue(worker.run_due_once(self.t0))
        self.assertFalse(worker.run_due_once(self.t0 + timedelta(seconds=29)))
        self.assertTrue(worker.run_due_once(self.t0 + timedelta(seconds=30)))

        self.assertEqual(probe.calls[1][1], "gpt-5.6-terra")
        public = StatusStore(self.config.public_database_path).get_public_provider(
            "provider-alpha",
            7,
            self.t0 + timedelta(seconds=31),
        )
        terra = next(item for item in public["models"] if item["model"] == "gpt-5.6-terra")
        self.assertEqual(terra["state"], TargetState.DEGRADED.value)
        self.assertEqual(
            terra["next_check"],
            (self.t0 + timedelta(seconds=150.5)).isoformat(),
        )

    def test_no_due_target_returns_false_without_reading_credential(self) -> None:
        os.remove(self.credentials / "provider_alpha_api_key")
        worker = StatusWorker(
            self.config,
            self.store,
            FakeProbe([]),
            env=self.env,
        )

        self.assertFalse(worker.run_due_once(self.t0 - timedelta(seconds=1)))

    def test_stop_request_prevents_new_probe(self) -> None:
        probe = FakeProbe([HealthProbeResult(True, 1, None, None)])
        worker = StatusWorker(self.config, self.store, probe, env=self.env)

        worker.request_stop()

        self.assertFalse(worker.run_due_once(self.t0))
        self.assertEqual(probe.calls, [])

    def test_snapshot_publish_failure_keeps_committed_probe_result(self) -> None:
        probe = FakeProbe([HealthProbeResult(True, 100, None, None)])
        worker = StatusWorker(self.config, self.store, probe, env=self.env)

        with patch.object(
            self.store,
            "publish_read_snapshot",
            side_effect=OSError("publish failed"),
        ):
            with self.assertRaisesRegex(OSError, "publish failed"):
                worker.run_due_once(self.t0)

        private = self.store.get_public_provider(
            "provider-alpha",
            7,
            self.t0 + timedelta(seconds=1),
        )
        assert private is not None
        sol = next(item for item in private["models"] if item["model"] == "gpt-5.6-sol")
        self.assertEqual(sol["state"], TargetState.HEALTHY.value)

    def test_manual_provider_job_runs_all_display_models_before_scheduled_work(
        self,
    ) -> None:
        provider = replace(
            self.provider,
            models=("gpt-5.6-sol",),
            display_models=("gpt-5.6-sol", "gpt-5.5"),
        )
        config = replace(self.config, providers=(provider,))
        self.store.initialize(config.providers, self.t0)
        control = ManualProbeControlStore(self.root / "control" / "manual.sqlite3")
        control.initialize()
        job, _ = control.enqueue(provider.provider_id, self.t0)
        probe = FakeProbe(
            [
                HealthProbeResult(True, 100, None, None),
                HealthProbeResult(False, 200, "model_unavailable", "not found"),
            ]
        )
        worker = StatusWorker(
            config,
            self.store,
            probe,
            env=self.env,
            control_store=control,
        )

        self.assertTrue(worker.run_manual_once(self.t0))

        self.assertEqual(
            [call[1] for call in probe.calls],
            ["gpt-5.6-sol", "gpt-5.5"],
        )
        completed = control.get_job(job.job_id)
        assert completed is not None
        self.assertEqual(completed.status, "completed")
        self.assertEqual(
            [bool(result["scheduled"]) for result in completed.results],
            [True, False],
        )
        self.assertFalse(self.store.get_target(provider.provider_id, "gpt-5.5"))
        public = StatusStore(config.public_database_path).get_public_provider(
            provider.provider_id,
            7,
            self.t0 + timedelta(seconds=1),
        )
        assert public is not None
        self.assertEqual(public["models"][0]["state"], TargetState.HEALTHY.value)

    def test_manual_provider_job_runs_only_requested_model(self) -> None:
        provider = replace(
            self.provider,
            models=("gpt-5.6-sol",),
            display_models=("gpt-5.6-sol", "gpt-5.5"),
        )
        config = replace(self.config, providers=(provider,))
        self.store.initialize(config.providers, self.t0)
        control = ManualProbeControlStore(self.root / "selected-model.sqlite3")
        control.initialize()
        job, _ = control.enqueue(
            provider.provider_id,
            self.t0,
            requested_models=("gpt-5.5",),
        )
        probe = FakeProbe([HealthProbeResult(True, 100, None, None)])
        worker = StatusWorker(
            config, self.store, probe, env=self.env, control_store=control
        )

        self.assertTrue(worker.run_manual_once(self.t0))

        self.assertEqual([call[1] for call in probe.calls], ["gpt-5.5"])
        completed = control.get_job(job.job_id)
        assert completed is not None
        self.assertEqual(completed.total_models, 1)
        self.assertEqual([result["model"] for result in completed.results], ["gpt-5.5"])

    def test_manual_only_provider_never_enters_scheduled_queue(self) -> None:
        provider = replace(
            self.provider,
            probe_mode="manual_only",
            display_models=("gpt-5.6-sol", "gpt-5.6-terra"),
        )
        config = replace(self.config, providers=(provider,))
        self.store.initialize(config.providers, self.t0)
        control = ManualProbeControlStore(self.root / "manual-only.sqlite3")
        control.initialize()
        job, _ = control.enqueue(provider.provider_id, self.t0)
        probe = FakeProbe(
            [
                HealthProbeResult(True, 100, None, None),
                HealthProbeResult(True, 120, None, None),
            ]
        )
        worker = StatusWorker(
            config,
            self.store,
            probe,
            env=self.env,
            control_store=control,
        )

        self.assertEqual(self.store.list_due_targets(self.t0, limit=10), [])
        self.assertTrue(worker.run_manual_once(self.t0))

        completed = control.get_job(job.job_id)
        assert completed is not None
        self.assertEqual(
            [bool(result["scheduled"]) for result in completed.results],
            [False, False],
        )

    def test_run_forever_claims_manual_job_before_due_target(self) -> None:
        control = ManualProbeControlStore(self.root / "control-priority.sqlite3")
        control.initialize()
        control.enqueue(self.provider.provider_id, self.t0)
        probe = FakeProbe(
            [
                HealthProbeResult(True, 100, None, None),
                HealthProbeResult(True, 100, None, None),
            ]
        )
        sleeps: list[float] = []
        worker: StatusWorker

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            worker.request_stop()

        worker = StatusWorker(
            self.config,
            self.store,
            probe,
            env=self.env,
            now_factory=lambda: self.t0,
            sleep=sleep,
            control_store=control,
        )

        worker.run_forever()

        self.assertEqual(
            [call[1] for call in probe.calls],
            ["gpt-5.6-sol", "gpt-5.6-terra"],
        )
        self.assertEqual(sleeps, [1.0])

    def test_run_forever_sleeps_when_idle_and_stops_cleanly(self) -> None:
        current = self.t0 - timedelta(seconds=1)
        sleeps: list[float] = []
        worker: StatusWorker

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            worker.request_stop()

        worker = StatusWorker(
            self.config,
            self.store,
            FakeProbe([]),
            env=self.env,
            now_factory=lambda: current,
            sleep=sleep,
        )

        worker.run_forever()

        self.assertEqual(sleeps, [1.0])


class CleanupTests(unittest.TestCase):
    def test_default_cleanup_removes_recent_probe_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recent_probe = root / "provider-probe-recent"
            recent_probe.mkdir()

            removed = cleanup_stale_run_directories(
                root,
                datetime.now(timezone.utc),
            )

            self.assertEqual(removed, 1)
            self.assertFalse(recent_probe.exists())

    def test_cleanup_removes_only_old_probe_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_probe = root / "provider-probe-old"
            new_probe = root / "provider-probe-new"
            unrelated = root / "keep-me"
            old_probe.mkdir()
            new_probe.mkdir()
            unrelated.mkdir()
            old_time = datetime.now(timezone.utc).timestamp() - 90000
            os.utime(old_probe, (old_time, old_time))

            removed = cleanup_stale_run_directories(
                root,
                datetime.now(timezone.utc),
                max_age=timedelta(days=1),
            )

            self.assertEqual(removed, 1)
            self.assertFalse(old_probe.exists())
            self.assertTrue(new_probe.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
