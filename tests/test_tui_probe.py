import json
import tempfile
import unittest
from pathlib import Path

from provider_status.tui_probe import (
    CodexTuiClient,
    RolloutSnapshot,
    scan_rollouts,
)
from provider_status.codex_diagnostics import CodexDiagnostic


HEALTH_PROMPT = (
    "Do not call tools. Return only this JSON object with exactly these fields: "
    '{"status":"ok","check":"codex-provider-health"}'
)
VALID_OUTPUT = '{"status":"ok","check":"codex-provider-health"}'


def event(event_type: str, payload: dict) -> dict:
    return {
        "timestamp": "2026-07-17T00:00:00Z",
        "type": event_type,
        "payload": payload,
    }


def message(role: str, text: str) -> dict:
    content_type = "output_text" if role == "assistant" else "input_text"
    return event(
        "response_item",
        {
            "type": "message",
            "role": role,
            "content": [{"type": content_type, "text": text}],
        },
    )


def write_events(codex_home: Path, events: list[dict], *, partial: str = "") -> None:
    rollout = codex_home / "sessions" / "2026" / "07" / "17" / "rollout.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(item, ensure_ascii=False) for item in events)
    if text:
        text += "\n"
    rollout.write_text(text + partial, encoding="utf-8")


class RolloutParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.codex_home = Path(self.temp_directory.name)

    def test_returns_only_assistant_output_after_matching_user_prompt(self) -> None:
        write_events(
            self.codex_home,
            [
                event("session_meta", {"originator": "codex-tui"}),
                message("developer", VALID_OUTPUT),
                message("assistant", "old session output"),
                message("user", HEALTH_PROMPT),
                message("assistant", VALID_OUTPUT),
            ],
        )

        snapshot = scan_rollouts(self.codex_home, HEALTH_PROMPT)

        self.assertEqual(snapshot.originator, "codex-tui")
        self.assertEqual(snapshot.output_text, VALID_OUTPUT)
        self.assertEqual(snapshot.error_text, "")
        self.assertTrue(snapshot.complete)

    def test_does_not_treat_marker_in_user_prompt_as_assistant_output(self) -> None:
        write_events(
            self.codex_home,
            [
                event("session_meta", {"originator": "codex-tui"}),
                message("user", HEALTH_PROMPT),
            ],
        )

        snapshot = scan_rollouts(self.codex_home, HEALTH_PROMPT)

        self.assertEqual(snapshot.output_text, "")
        self.assertFalse(snapshot.complete)


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self.returncode = None

    def poll(self):
        return self.returncode


class FakePtyAdapter:
    def __init__(self, reads: list[bytes] | None = None) -> None:
        self.reads = list(reads or [])
        self.opened = False
        self.window_sizes: list[tuple[int, int, int]] = []
        self.nonblocking: list[int] = []
        self.writes: list[tuple[int, bytes]] = []
        self.closed: list[int] = []

    def open(self) -> tuple[int, int]:
        self.opened = True
        return 10, 11

    def set_window_size(self, fd: int, rows: int, columns: int) -> None:
        self.window_sizes.append((fd, rows, columns))

    def set_nonblocking(self, fd: int) -> None:
        self.nonblocking.append(fd)

    def wait_readable(self, fd: int, timeout: float) -> bool:
        del fd, timeout
        return bool(self.reads)

    def read(self, fd: int, size: int) -> bytes:
        del fd, size
        return self.reads.pop(0)

    def write(self, fd: int, value: bytes) -> int:
        self.writes.append((fd, value))
        return len(value)

    def close(self, fd: int) -> None:
        self.closed.append(fd)


class SnapshotSequence:
    def __init__(self, *snapshots: RolloutSnapshot) -> None:
        self.snapshots = list(snapshots)
        self.calls: list[tuple[Path, str]] = []

    def __call__(self, codex_home: Path, prompt: str) -> RolloutSnapshot:
        self.calls.append((codex_home, prompt))
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


class AdvancingClock:
    def __init__(self, *values: float) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


class CodexTuiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name)
        self.codex_home = self.root / "codex-home"
        self.workspace = self.root / "workspace"
        self.codex_home.mkdir()
        self.workspace.mkdir()

    def make_client(
        self,
        *,
        pty_adapter: FakePtyAdapter,
        rollout_scanner,
        clock=None,
        base_url=None,
        diagnostic_reader=None,
    ):
        process = FakeProcess()
        captured: dict = {}
        terminated: list[FakeProcess] = []

        def process_factory(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return process

        client = CodexTuiClient(
            codex_bin="codex",
            env={"CODEX_HOME": str(self.codex_home)},
            workspace=self.workspace,
            sandbox="read-only",
            model="gpt-5.6-sol",
            reasoning_effort="low",
            model_provider="custom",
            pty_adapter=pty_adapter,
            process_factory=process_factory,
            process_terminator=terminated.append,
            rollout_scanner=rollout_scanner,
            clock=clock or AdvancingClock(0.0, 0.1, 0.2),
            platform_name="posix",
            base_url=base_url,
            diagnostic_reader=diagnostic_reader,
        )
        return client, process, captured, terminated

    def test_starts_bare_codex_in_new_session_and_returns_assistant_output(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(
            RolloutSnapshot(
                output_text=VALID_OUTPUT,
                originator="codex-tui",
                complete=True,
            )
        )
        client, process, captured, terminated = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
        )

        result = client.run_turn(HEALTH_PROMPT, timeout=90)

        cmd = captured["cmd"]
        self.assertEqual(cmd[0], "codex")
        self.assertIn("--no-alt-screen", cmd)
        self.assertNotIn("exec", cmd)
        self.assertNotIn("app-server", cmd)
        self.assertEqual(cmd[-1], HEALTH_PROMPT)
        self.assertIn('model_reasoning_effort="low"', cmd)
        self.assertEqual(captured["kwargs"]["stdin"], 11)
        self.assertEqual(captured["kwargs"]["stdout"], 11)
        self.assertEqual(captured["kwargs"]["stderr"], 11)
        self.assertTrue(captured["kwargs"]["start_new_session"])
        self.assertEqual(captured["kwargs"]["cwd"], str(self.workspace))
        self.assertEqual(captured["kwargs"]["env"]["TERM"], "xterm-256color")
        self.assertEqual(adapter.window_sizes, [(11, 40, 120)])
        self.assertEqual(adapter.nonblocking, [10])
        self.assertEqual(result.output_text, VALID_OUTPUT)
        self.assertEqual(result.turn_status, "completed")
        self.assertEqual(result.originator, "codex-tui")
        self.assertEqual(terminated, [process])
        self.assertCountEqual(adapter.closed, [10, 11])

    def test_confirms_trust_prompt_once_before_completion(self) -> None:
        adapter = FakePtyAdapter(
            [
                b"Do you trust the contents of this directory? Press enter to continue",
                b"Do you trust the contents of this directory? Press enter to continue",
            ]
        )
        scanner = SnapshotSequence(
            RolloutSnapshot(originator="codex-tui"),
            RolloutSnapshot(
                output_text=VALID_OUTPUT,
                originator="codex-tui",
                complete=True,
            ),
        )
        client, _, _, _ = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
        )

        client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertEqual(adapter.writes, [(10, b"\r")])

    def test_confirms_trust_prompt_with_ansi_controls_and_collapsed_spaces(self) -> None:
        adapter = FakePtyAdapter(
            [
                (
                    b"\x1b[2KDo\x1b[1Cyou\x1b[1Ctrust\x1b[1Cthe\x1b[1Ccontents"
                    b"\x1b[1Cof\x1b[1Cthis\x1b[1Cdirectory?\x1b[0m"
                )
            ]
        )
        scanner = SnapshotSequence(
            RolloutSnapshot(originator="codex-tui"),
            RolloutSnapshot(
                output_text=VALID_OUTPUT,
                originator="codex-tui",
                complete=True,
            ),
        )
        client, _, _, _ = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
        )

        client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertEqual(adapter.writes, [(10, b"\r")])

    def test_returns_final_no_channel_error(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(
            RolloutSnapshot(
                error_text="No available channel for model gpt-5.6-sol",
                http_status_code=503,
                originator="codex-tui",
                complete=True,
            )
        )
        client, _, _, _ = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
        )

        result = client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertEqual(result.turn_status, "failed")
        self.assertEqual(result.http_status_code, 503)
        self.assertIn("No available channel", result.error_text)

    def test_empty_task_complete_returns_invalid_output_without_timeout(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(
            RolloutSnapshot(originator="codex-tui", complete=True)
        )
        client, _, _, _ = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
        )

        result = client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertFalse(result.timed_out)
        self.assertEqual(result.turn_status, "failed")
        self.assertEqual(result.error_code, "invalid_output")
        self.assertIn("没有输出", result.error_text)

    def test_task_complete_uses_structured_diagnostic(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(
            RolloutSnapshot(originator="codex-tui", complete=True)
        )

        def diagnostic_reader(*args, **kwargs):
            del args, kwargs
            return CodexDiagnostic(
                kind="upstream_unavailable",
                message="HTTP 503；上游服务暂时不可用。",
                http_status_code=503,
                occurrences=2,
                retryable=True,
            )

        client, _, _, _ = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
            base_url="https://provider.example.com/v1",
            diagnostic_reader=diagnostic_reader,
        )

        result = client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertFalse(result.timed_out)
        self.assertEqual(result.error_code, "upstream_unavailable")
        self.assertEqual(result.http_status_code, 503)
        self.assertIn("HTTP 503", result.error_text)

    def test_successful_output_wins_over_retryable_diagnostic(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(
            RolloutSnapshot(
                output_text=VALID_OUTPUT,
                originator="codex-tui",
                complete=True,
            )
        )

        def diagnostic_reader(*args, **kwargs):
            del args, kwargs
            return CodexDiagnostic(
                kind="upstream_unavailable",
                message="HTTP 503；上游服务暂时不可用。",
                http_status_code=503,
                occurrences=1,
                retryable=True,
            )

        client, _, _, _ = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
            base_url="https://provider.example.com/v1",
            diagnostic_reader=diagnostic_reader,
        )

        result = client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertEqual(result.turn_status, "completed")
        self.assertEqual(result.output_text, VALID_OUTPUT)
        self.assertIsNone(result.error_code)

    def test_repeated_retryable_diagnostic_stops_after_confirmation_window(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(RolloutSnapshot(originator="codex-tui"))
        diagnostics = iter(
            (
                CodexDiagnostic(
                    kind="upstream_unavailable",
                    message="HTTP 503；上游服务暂时不可用。",
                    http_status_code=503,
                    occurrences=1,
                    retryable=True,
                ),
                CodexDiagnostic(
                    kind="upstream_unavailable",
                    message="HTTP 503；上游服务暂时不可用。",
                    http_status_code=503,
                    occurrences=2,
                    retryable=True,
                ),
            )
        )

        def diagnostic_reader(*args, **kwargs):
            del args, kwargs
            try:
                return next(diagnostics)
            except StopIteration:
                return CodexDiagnostic(
                    kind="upstream_unavailable",
                    message="HTTP 503；上游服务暂时不可用。",
                    http_status_code=503,
                    occurrences=2,
                    retryable=True,
                )

        client, _, _, _ = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
            base_url="https://provider.example.com/v1",
            diagnostic_reader=diagnostic_reader,
            clock=AdvancingClock(0.0, 0.0, 1.0, 12.0),
        )

        result = client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertFalse(result.timed_out)
        self.assertEqual(result.error_code, "upstream_unavailable")
        self.assertEqual(result.http_status_code, 503)

    def test_timeout_terminates_process_and_marks_result(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(RolloutSnapshot(originator="codex-tui"))
        client, process, _, terminated = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
            clock=AdvancingClock(0.0, 91.0),
        )

        result = client.run_turn(HEALTH_PROMPT, timeout=90)

        self.assertTrue(result.timed_out)
        self.assertEqual(result.turn_status, "failed")
        self.assertEqual(terminated, [process])

    def test_close_is_idempotent_after_run(self) -> None:
        adapter = FakePtyAdapter()
        scanner = SnapshotSequence(
            RolloutSnapshot(
                output_text=VALID_OUTPUT,
                originator="codex-tui",
                complete=True,
            )
        )
        client, process, _, terminated = self.make_client(
            pty_adapter=adapter,
            rollout_scanner=scanner,
        )

        client.run_turn(HEALTH_PROMPT, timeout=90)
        client.close()
        client.close()

        self.assertEqual(terminated, [process])
        self.assertEqual(adapter.closed.count(10), 1)
        self.assertEqual(adapter.closed.count(11), 1)


class RolloutParserAdditionalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.codex_home = Path(self.temp_directory.name)

    def test_extracts_final_503_no_channel_error(self) -> None:
        write_events(
            self.codex_home,
            [
                event("session_meta", {"originator": "codex-tui"}),
                message("user", HEALTH_PROMPT),
                event(
                    "event_msg",
                    {
                        "type": "stream_error",
                        "message": (
                            "Unexpected status 503 Service Unavailable: "
                            "No available channel for model gpt-5.6-sol under group default"
                        ),
                        "willRetry": False,
                        "codexErrorInfo": {
                            "httpConnectionFailed": {"httpStatusCode": 503}
                        },
                    },
                ),
            ],
        )

        snapshot = scan_rollouts(self.codex_home, HEALTH_PROMPT)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.http_status_code, 503)
        self.assertIn("No available channel", snapshot.error_text)

    def test_ignores_retryable_stream_error_until_final_event(self) -> None:
        write_events(
            self.codex_home,
            [
                event("session_meta", {"originator": "codex-tui"}),
                message("user", HEALTH_PROMPT),
                event(
                    "event_msg",
                    {
                        "type": "stream_error",
                        "message": "Unexpected status 503; reconnecting 1/5",
                        "willRetry": True,
                        "httpStatusCode": 503,
                    },
                ),
            ],
        )

        snapshot = scan_rollouts(self.codex_home, HEALTH_PROMPT)

        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.error_text, "")

    def test_extracts_403_client_error_and_snake_case_retry_flag(self) -> None:
        write_events(
            self.codex_home,
            [
                event("session_meta", {"originator": "codex-tui"}),
                message("user", HEALTH_PROMPT),
                event(
                    "event_msg",
                    {
                        "type": "stream_error",
                        "message": "This channel does not allow the current client",
                        "will_retry": False,
                        "http_status_code": 403,
                    },
                ),
            ],
        )

        snapshot = scan_rollouts(self.codex_home, HEALTH_PROMPT)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.http_status_code, 403)
        self.assertIn("current client", snapshot.error_text)

    def test_marks_empty_task_complete_after_matching_prompt(self) -> None:
        write_events(
            self.codex_home,
            [
                event("session_meta", {"originator": "codex-tui"}),
                message("user", HEALTH_PROMPT),
                event("event_msg", {"type": "task_complete"}),
            ],
        )

        snapshot = scan_rollouts(self.codex_home, HEALTH_PROMPT)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.output_text, "")
        self.assertEqual(snapshot.error_text, "")

    def test_tolerates_partial_unknown_and_non_object_json_lines(self) -> None:
        write_events(
            self.codex_home,
            [
                ["not", "an", "object"],
                event("unknown_event", {"value": 1}),
                event("session_meta", {"originator": "codex-tui"}),
                message("user", HEALTH_PROMPT),
            ],
            partial='{"timestamp":"unfinished"',
        )

        snapshot = scan_rollouts(self.codex_home, HEALTH_PROMPT)

        self.assertEqual(snapshot.originator, "codex-tui")
        self.assertFalse(snapshot.complete)


if __name__ == "__main__":
    unittest.main()
