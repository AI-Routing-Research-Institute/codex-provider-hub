import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from local_proxy.diagnostics import DiagnosticLog, EventLoopWatchdog


class DiagnosticLogTests(unittest.TestCase):
    def test_writes_json_thread_dump_and_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "proxy-diagnostics.log"
            diagnostic_log = DiagnosticLog(path, max_bytes=1024, backup_count=2)
            try:
                diagnostic_log.dump_threads("test_dump", service="test")
                for index in range(3):
                    diagnostic_log.write_event(
                        "padding",
                        index=index,
                        value="x" * 200,
                    )
            finally:
                diagnostic_log.close()

            combined = "\n".join(
                item.read_text(encoding="utf-8")
                for item in sorted(Path(temp_dir).glob("proxy-diagnostics.log*"))
            )
            self.assertIn('"event":"test_dump"', combined)
            self.assertIn("python thread dump begin", combined)
            self.assertTrue((Path(f"{path}.1")).exists())

    def test_json_event_contains_only_supplied_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "proxy-diagnostics.log"
            diagnostic_log = DiagnosticLog(path)
            diagnostic_log.write_event(
                "service_started",
                service="codex-provider-hub",
            )
            diagnostic_log.close()

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["event"], "service_started")
            self.assertEqual(payload["service"], "codex-provider-hub")
            self.assertEqual(set(payload), {"timestamp", "event", "service"})


class EventLoopWatchdogTests(unittest.TestCase):
    def test_reports_stall_once_during_cooldown(self) -> None:
        class FakeDiagnostics:
            def __init__(self) -> None:
                self.events: list[float] = []

            def stalled_for_ms(self) -> float:
                return 5000.0

            def observe_watchdog_event(self, stalled_ms: float) -> None:
                self.events.append(stalled_ms)

        class FakeLog:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []
                self.written = threading.Event()

            def dump_threads(self, event: str, **fields) -> None:
                self.events.append((event, fields))
                self.written.set()

        diagnostics = FakeDiagnostics()
        diagnostic_log = FakeLog()
        watchdog = EventLoopWatchdog(
            diagnostics,
            diagnostic_log,
            active_requests=lambda: (
                {
                    "service": "codex",
                    "request_id": 7,
                    "provider_id": "provider-a",
                    "phase": "waiting_first_chunk",
                },
            ),
            stall_threshold_seconds=0.1,
            check_interval_seconds=0.05,
            report_cooldown_seconds=1.0,
        )
        watchdog.start()
        try:
            self.assertTrue(diagnostic_log.written.wait(timeout=1))
            time.sleep(0.15)
        finally:
            watchdog.stop()

        self.assertEqual(diagnostics.events, [5000.0])
        self.assertEqual(len(diagnostic_log.events), 1)
        event, fields = diagnostic_log.events[0]
        self.assertEqual(event, "event_loop_stall")
        self.assertEqual(fields["stalled_ms"], 5000)
        self.assertEqual(fields["active_requests"][0]["request_id"], 7)


if __name__ == "__main__":
    unittest.main()
