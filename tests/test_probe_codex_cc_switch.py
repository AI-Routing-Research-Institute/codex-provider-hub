import os
import argparse
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from probe_tools.codex_app_server_client import AppServerTurnResult
import probe_codex_cc_switch as probe


class WindowsEnvironmentTests(unittest.TestCase):
    def test_build_env_preserves_windows_runtime_variables(self) -> None:
        windows_env = {
            "PATH": r"C:\Windows\System32",
            "SYSTEMROOT": r"C:\Windows",
            "WINDIR": r"C:\Windows",
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            "USERPROFILE": r"C:\Users\tester",
            "APPDATA": r"C:\Users\tester\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\tester\AppData\Local",
            "TEMP": r"C:\Users\tester\AppData\Local\Temp",
            "TMP": r"C:\Users\tester\AppData\Local\Temp",
        }

        with patch.dict(os.environ, windows_env, clear=True):
            env = probe.build_env(Path(r"C:\probe\codex-home"))

        for key, value in windows_env.items():
            self.assertEqual(env.get(key), value, key)


class NetworkFailureTests(unittest.TestCase):
    DETAIL = (
        "ERROR: Reconnecting... 5/5\n"
        "ERROR: stream disconnected before completion: error sending request for url "
        "(https://aicodelink.top/v1/responses)"
    )

    def test_transport_failure_is_not_classified_as_bad_output(self) -> None:
        status, retryable = probe.classify_failure(
            returncode=1,
            output_valid=False,
            validation_error="response is not valid JSON",
            detail_text=self.DETAIL,
            timed_out=False,
        )

        self.assertEqual(status, "network_error")
        self.assertTrue(retryable)

    def test_transport_failure_note_includes_endpoint(self) -> None:
        notes = probe.extract_attempt_notes(detail_text=self.DETAIL)

        self.assertIn(
            "连接异常：响应完成前连接已断开（https://aicodelink.top/v1/responses）",
            notes,
        )

    def test_channel_client_rejection_has_its_own_status(self) -> None:
        status, retryable = probe.classify_failure(
            returncode=1,
            output_valid=False,
            validation_error="response is not valid JSON",
            detail_text=(
                "HTTP 403: This channel does not allow the current client "
                "(detected: codex_exec/0.144.1)"
            ),
            timed_out=False,
        )

        self.assertEqual(status, "client_blocked")
        self.assertFalse(retryable)


class AppServerAttemptTests(unittest.TestCase):
    def test_single_attempt_uses_app_server_output(self) -> None:
        provider = probe.ProviderRecord(
            provider_id="provider-1",
            name="Provider",
            is_current=False,
            endpoint_url="https://example.test/v1",
            common_config_enabled=False,
            raw_config="",
            auth={},
            meta={},
        )
        prompt = probe.PromptSpec(
            prompt_id="simple",
            title="Simple",
            body="return JSON",
            required_keys=("answer",),
            type_expectations={"answer": "str"},
        )
        app_result = AppServerTurnResult(
            output_text='{"answer":"ok"}',
            turn_status="completed",
            error_text="",
            diagnostics="",
            timed_out=False,
            http_status_code=None,
            user_agent="Codex Desktop/0.144.1 (codex_desktop; 0.144.1)",
        )
        app_client = unittest.mock.Mock()
        app_client.run_turn.return_value = app_result
        args = argparse.Namespace(attempts=1, timeout=30)

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            codex_home = Path(temp_dir) / "codex-home"
            workspace.mkdir()
            codex_home.mkdir()
            result = probe.run_single_attempt(
                args=args,
                provider=provider,
                prompt=prompt,
                workspace=workspace,
                codex_home=codex_home,
                attempt_index=1,
                model_run=probe.ModelRunSpec("gpt-5.6-sol", "high"),
                app_server_client=app_client,
            )

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["response_payload"], {"answer": "ok"})
        self.assertIn("codex_desktop", result["client_user_agent"])
        app_client.run_turn.assert_called_once_with("return JSON", timeout=30)


class RetryPolicyTests(unittest.TestCase):
    def test_non_retryable_failure_stops_after_first_attempt(self) -> None:
        provider = probe.ProviderRecord(
            provider_id="provider-1",
            name="Provider",
            is_current=False,
            endpoint_url="https://example.test/v1",
            common_config_enabled=False,
            raw_config="",
            auth={},
            meta={},
        )
        args = argparse.Namespace(attempts=2)
        attempt = {"status": "auth_fail", "retryable": False}

        with patch("probe_codex_cc_switch.run_single_attempt", return_value=attempt) as run:
            result = probe.run_provider_model(
                args=args,
                provider=provider,
                rng=random.Random(1),
                workspace=Path("workspace"),
                codex_home=Path("codex-home"),
                model_run=probe.ModelRunSpec("gpt-5.6-sol", "high"),
            )

        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["status"], "auth_fail")
        self.assertEqual(len(result["attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
