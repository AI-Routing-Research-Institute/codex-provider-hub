import importlib.util
import json
import queue
import signal
import subprocess
import unittest
from pathlib import Path
from unittest import mock


MODULE_SPEC = importlib.util.find_spec("probe_tools.codex_app_server_client")
if MODULE_SPEC is not None:
    from probe_tools import codex_app_server_client as app_server
else:
    app_server = None


class QueueReader:
    def __init__(self) -> None:
        self.lines: queue.Queue[str] = queue.Queue()

    def push(self, payload: dict) -> None:
        self.lines.put(json.dumps(payload) + "\n")

    def readline(self) -> str:
        return self.lines.get(timeout=2)


class EmptyReader:
    def readline(self) -> str:
        return ""


class ScriptedStdin:
    def __init__(self, process: "ScriptedProcess") -> None:
        self.process = process

    def write(self, value: str) -> int:
        for line in value.splitlines():
            if line.strip():
                self.process.handle(json.loads(line))
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class ScriptedProcess:
    def __init__(self, fail_turn: bool = False) -> None:
        self.stdout = QueueReader()
        self.stderr = EmptyReader()
        self.stdin = ScriptedStdin(self)
        self.fail_turn = fail_turn
        self.messages: list[dict] = []
        self.returncode = None
        self.pid = 12345

    def handle(self, message: dict) -> None:
        self.messages.append(message)
        method = message.get("method")
        if method == "initialize":
            self.stdout.push(
                {
                    "id": message["id"],
                    "result": {
                        "userAgent": (
                            "Codex Desktop/0.144.0-alpha.4 "
                            "(Windows 10.0.26200; x86_64) unknown "
                            "(codex_desktop; 0.144.0-alpha.4)"
                        )
                    },
                }
            )
        elif method == "thread/start":
            self.stdout.push(
                {
                    "id": message["id"],
                    "result": {
                        "thread": {"id": "thread-1"},
                        "model": "gpt-5.6-sol",
                        "modelProvider": "custom",
                    },
                }
            )
        elif method == "turn/start":
            self.stdout.push(
                {
                    "id": message["id"],
                    "result": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}},
                }
            )
            if self.fail_turn:
                self.stdout.push(
                    {
                        "method": "error",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "willRetry": False,
                            "error": {
                                "message": "This channel does not allow the current client",
                                "codexErrorInfo": {
                                    "httpConnectionFailed": {"httpStatusCode": 403}
                                },
                            },
                        },
                    }
                )
                self.stdout.push(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {
                                "id": "turn-1",
                                "status": "failed",
                                "items": [],
                                "error": {
                                    "message": "This channel does not allow the current client",
                                    "codexErrorInfo": {
                                        "httpConnectionFailed": {"httpStatusCode": 403}
                                    },
                                },
                            },
                        },
                    }
                )
            else:
                self.stdout.push(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "completedAtMs": 1,
                            "item": {
                                "id": "message-1",
                                "type": "agentMessage",
                                "text": '{"tasks":["a"],"risks":["b"],"acceptance":["c"]}',
                            },
                        },
                    }
                )
                self.stdout.push(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {
                                "id": "turn-1",
                                "status": "completed",
                                "items": [],
                            },
                        },
                    }
                )

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout=None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class AppServerClientTests(unittest.TestCase):
    def require_module(self):
        self.assertIsNotNone(app_server, "probe_tools.codex_app_server_client module must exist")
        return app_server

    def make_client(
        self,
        process: ScriptedProcess,
        *,
        popen_factory=None,
        process_terminator=None,
    ):
        module = self.require_module()
        return module.CodexAppServerClient(
            codex_bin="codex.exe",
            env={"CODEX_HOME": r"C:\probe\codex-home"},
            workspace=Path(r"C:\probe\workspace"),
            sandbox="read-only",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            model_provider="custom",
            client_version="0.144.0-alpha.4",
            popen_factory=popen_factory or (lambda *args, **kwargs: process),
            process_terminator=process_terminator or (lambda child: child.terminate()),
        )

    def test_start_uses_new_session_on_posix(self) -> None:
        module = self.require_module()
        process = ScriptedProcess()
        captured: dict = {}

        def popen_factory(*args, **kwargs):
            captured.update(kwargs)
            return process

        client = self.make_client(process, popen_factory=popen_factory)
        with mock.patch.object(module.os, "name", "posix"):
            client.run_turn("return JSON", timeout=2)
        client.close()

        self.assertTrue(captured.get("start_new_session"))
        self.assertNotIn("creationflags", captured)

    def test_start_preserves_windows_process_group_flags(self) -> None:
        module = self.require_module()
        process = ScriptedProcess()
        captured: dict = {}

        def popen_factory(*args, **kwargs):
            captured.update(kwargs)
            return process

        client = self.make_client(process, popen_factory=popen_factory)
        with mock.patch.object(module.os, "name", "nt"):
            client.run_turn("return JSON", timeout=2)
        client.close()

        expected_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0,
        )
        self.assertEqual(captured["creationflags"], expected_flags)
        self.assertNotIn("start_new_session", captured)

    def test_default_terminator_stops_posix_process_group(self) -> None:
        module = self.require_module()
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        process.wait.return_value = 0

        with (
            mock.patch.object(module.os, "name", "posix"),
            mock.patch.object(
                module.os,
                "getpgid",
                return_value=67890,
                create=True,
            ) as getpgid,
            mock.patch.object(module.os, "killpg", create=True) as killpg,
        ):
            module._default_terminate_process_tree(process)

        getpgid.assert_called_once_with(12345)
        killpg.assert_called_once_with(67890, signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=3)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_default_terminator_escalates_posix_process_group(self) -> None:
        module = self.require_module()
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("codex", 3)

        with (
            mock.patch.object(module.os, "name", "posix"),
            mock.patch.object(
                module.os,
                "getpgid",
                return_value=67890,
                create=True,
            ),
            mock.patch.object(module.os, "killpg", create=True) as killpg,
        ):
            module._default_terminate_process_tree(process)

        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(67890, signal.SIGTERM),
                mock.call(67890, getattr(signal, "SIGKILL", 9)),
            ],
        )
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_default_terminator_keeps_windows_taskkill(self) -> None:
        module = self.require_module()
        process = mock.Mock(pid=12345)
        process.poll.side_effect = [None, None]

        with (
            mock.patch.object(module.os, "name", "nt"),
            mock.patch.object(module.subprocess, "run") as run,
        ):
            module._default_terminate_process_tree(process)

        self.assertEqual(
            run.call_args.args[0],
            ["taskkill.exe", "/PID", "12345", "/T", "/F"],
        )

    def test_run_turn_uses_desktop_identity_and_ephemeral_thread(self) -> None:
        process = ScriptedProcess()
        client = self.make_client(process)

        result = client.run_turn("return JSON", timeout=2)
        client.close()

        initialize = next(item for item in process.messages if item.get("method") == "initialize")
        thread_start = next(item for item in process.messages if item.get("method") == "thread/start")
        turn_start = next(item for item in process.messages if item.get("method") == "turn/start")
        self.assertEqual(initialize["params"]["clientInfo"]["name"], "codex_desktop")
        self.assertEqual(initialize["params"]["clientInfo"]["title"], "Codex Desktop")
        self.assertTrue(thread_start["params"]["ephemeral"])
        self.assertEqual(thread_start["params"]["modelProvider"], "custom")
        self.assertEqual(turn_start["params"]["threadId"], "thread-1")
        self.assertEqual(turn_start["params"]["effort"], "high")
        self.assertIn("(codex_desktop;", result.user_agent)

    def test_run_turn_collects_final_agent_message(self) -> None:
        client = self.make_client(ScriptedProcess())

        result = client.run_turn("return JSON", timeout=2)
        client.close()

        self.assertEqual(result.turn_status, "completed")
        self.assertEqual(
            result.output_text,
            '{"tasks":["a"],"risks":["b"],"acceptance":["c"]}',
        )
        self.assertFalse(result.timed_out)
        self.assertEqual(result.http_status_code, None)

    def test_run_turn_preserves_structured_http_error(self) -> None:
        client = self.make_client(ScriptedProcess(fail_turn=True))

        result = client.run_turn("return JSON", timeout=2)
        client.close()

        self.assertEqual(result.turn_status, "failed")
        self.assertEqual(result.http_status_code, 403)
        self.assertIn("This channel does not allow", result.error_text)


if __name__ == "__main__":
    unittest.main()
