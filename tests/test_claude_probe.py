import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from provider_status.claude_probe import (
    ClaudeHealthProbe,
    ClaudeProcessResult,
    _build_claude_env,
    _run_claude_cli,
    _run_direct_messages_diagnostic,
)
from provider_status.config import ProviderConfig
from provider_status.probe import (
    DirectDiagnosticResult,
    ProviderHealthProbe,
)


API_KEY = "test-claude-secret-123456"
VALID_OUTPUT = {"status": "ok", "check": "codex-provider-health"}


def make_provider(credential_kind: str = "api_key") -> ProviderConfig:
    return ProviderConfig(
        provider_id="provider-alpha",
        name="Provider Alpha",
        base_url="https://alpha.example.com/v1",
        credential_name="provider-alpha-key",
        models=("gpt-5.6-sol", "claude-opus-5"),
        healthy_interval_seconds=600,
        unhealthy_interval_seconds=120,
        timeout_seconds=90,
        model_clients=(("claude-opus-5", "claude"),),
        claude_base_url="https://alpha.example.com",
        credential_kind=credential_kind,
    )


def envelope(**values: object) -> str:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": json.dumps(VALID_OUTPUT),
    }
    payload.update(values)
    return json.dumps(payload, ensure_ascii=False)


class UnreadableResponseBody(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("diagnostic response body must not be read")


class FakeProcess:
    def __init__(
        self,
        stdout: str = "stdout",
        stderr: str = "",
        *,
        timeout_once: bool = False,
    ) -> None:
        self.pid = 4321
        self.returncode: int | None = 0
        self.stdout = stdout
        self.stderr = stderr
        self.timeout_once = timeout_once
        self.communicate_calls: list[float | None] = []

    def communicate(self, timeout=None):
        self.communicate_calls.append(timeout)
        if self.timeout_once:
            self.timeout_once = False
            self.returncode = None
            raise subprocess.TimeoutExpired("claude", timeout, output="partial")
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode


class ClaudeHealthProbeTests(unittest.TestCase):
    def test_auth_token_environment_does_not_set_api_key(self) -> None:
        environment = _build_claude_env(
            Path("/tmp/claude"),
            "https://alpha.example.com/",
            API_KEY,
            "auth_token",
        )

        self.assertEqual(environment["ANTHROPIC_AUTH_TOKEN"], API_KEY)
        self.assertNotIn("ANTHROPIC_API_KEY", environment)

    def run_probe(self, process_result: ClaudeProcessResult, **kwargs):
        captures: list[dict] = []

        def runner(**runner_kwargs):
            captures.append(runner_kwargs)
            self.assertTrue(Path(runner_kwargs["env"]["HOME"]).is_dir())
            self.assertTrue(Path(runner_kwargs["workspace"]).is_dir())
            return process_result

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            probe = ClaudeHealthProbe(
                "claude",
                temp_root,
                runner=runner,
                clock=lambda: 1.0,
                **kwargs,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "ANTHROPIC_AUTH_TOKEN": "inherited-token",
                    "CLAUDE_CONFIG_DIR": "inherited-config",
                },
            ):
                result = probe.run(make_provider(), "claude-opus-5", API_KEY)
            self.assertEqual(list(temp_root.iterdir()), [])
        return result, captures

    def test_success_uses_isolated_claude_environment_and_structured_output(self) -> None:
        result, captures = self.run_probe(
            ClaudeProcessResult(
                0,
                envelope(structured_output=VALID_OUTPUT),
                "",
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(result.diagnostic_source, "claude_cli")
        self.assertEqual(len(captures), 1)
        capture = captures[0]
        self.assertEqual(capture["claude_bin"], "claude")
        self.assertEqual(capture["model"], "claude-opus-5")
        self.assertEqual(capture["timeout"], 90)
        self.assertEqual(capture["env"]["ANTHROPIC_API_KEY"], API_KEY)
        self.assertEqual(
            capture["env"]["ANTHROPIC_BASE_URL"],
            "https://alpha.example.com",
        )
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", capture["env"])
        self.assertNotIn("CLAUDE_CONFIG_DIR", capture["env"])

    def test_success_accepts_exact_json_result_text(self) -> None:
        result, _ = self.run_probe(ClaudeProcessResult(0, envelope(), ""))

        self.assertTrue(result.success)

    def test_explicit_403_is_auth_failure_and_never_runs_direct_diagnostic(self) -> None:
        def unexpected_diagnostic(**kwargs):
            self.fail("explicit Claude HTTP status must not trigger direct diagnosis")

        result, _ = self.run_probe(
            ClaudeProcessResult(
                1,
                envelope(
                    is_error=True,
                    api_error_status=403,
                    result=f"Failed to authenticate: group disabled api_key={API_KEY}",
                ),
                "",
            ),
            diagnostic_runner=unexpected_diagnostic,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "auth_failed")
        self.assertEqual(result.http_status_code, 403)
        self.assertEqual(result.failure_stage, "claude_cli")
        self.assertEqual(result.diagnostic_source, "claude_cli")
        self.assertNotIn(API_KEY, result.error_summary or "")

    def test_timeout_and_invalid_output_are_classified(self) -> None:
        timeout_result, _ = self.run_probe(
            ClaudeProcessResult(1, "", "wait timeout", timed_out=True)
        )
        invalid_result, _ = self.run_probe(
            ClaudeProcessResult(0, envelope(result="not json"), "")
        )

        self.assertEqual(timeout_result.error_code, "timeout")
        self.assertEqual(invalid_result.error_code, "invalid_output")

    def test_ambiguous_cli_failure_uses_one_bounded_messages_diagnostic(self) -> None:
        calls: list[dict] = []

        def diagnose(**kwargs):
            calls.append(kwargs)
            return DirectDiagnosticResult(
                "upstream_unavailable",
                "HTTP 520",
                520,
                "provider_response",
            )

        result, _ = self.run_probe(
            ClaudeProcessResult(1, "", "API Error: upstream request failed"),
            diagnostic_runner=diagnose,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout_seconds"], 10.0)
        self.assertEqual(calls[0]["model"], "claude-opus-5")
        self.assertEqual(result.error_code, "upstream_unavailable")
        self.assertEqual(result.http_status_code, 520)
        self.assertEqual(result.diagnostic_source, "direct_messages")

    def test_direct_messages_diagnostic_classifies_without_reading_body(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(403, stream=UnreadableResponseBody())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with mock.patch(
            "provider_status.claude_probe.httpx.Client",
            return_value=client,
        ):
            result = _run_direct_messages_diagnostic(
                provider=make_provider(),
                model="claude-opus-5",
                api_key=API_KEY,
                timeout_seconds=10.0,
            )

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0].url,
            httpx.URL("https://alpha.example.com/v1/messages"),
        )
        self.assertEqual(requests[0].headers["x-api-key"], API_KEY)
        self.assertEqual(requests[0].headers["anthropic-version"], "2023-06-01")
        self.assertEqual(result.error_code, "auth_failed")
        self.assertEqual(result.http_status_code, 403)

    def test_auth_token_diagnostic_uses_bearer_authorization(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(403)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with mock.patch("provider_status.claude_probe.httpx.Client", return_value=client):
            _run_direct_messages_diagnostic(
                provider=make_provider("auth_token"),
                model="claude-opus-5",
                api_key=API_KEY,
                timeout_seconds=10.0,
            )

        self.assertEqual(requests[0].headers["Authorization"], f"Bearer {API_KEY}")
        self.assertNotIn("x-api-key", requests[0].headers)

    def test_provider_router_selects_model_client(self) -> None:
        class FakeProbe:
            def __init__(self, name: str) -> None:
                self.name = name
                self.calls: list[str] = []

            def run(self, provider, model, api_key):
                del provider, api_key
                self.calls.append(model)
                return self.name

        codex = FakeProbe("codex")
        claude = FakeProbe("claude")
        router = ProviderHealthProbe(
            codex,
            claude,
            resolver=lambda *args, **kwargs: ["8.8.8.8"],
        )

        self.assertEqual(router.run(make_provider(), "gpt-5.6-sol", API_KEY), "codex")
        self.assertEqual(
            router.run(make_provider(), "claude-opus-5", API_KEY),
            "claude",
        )
        self.assertEqual(codex.calls, ["gpt-5.6-sol"])
        self.assertEqual(claude.calls, ["claude-opus-5"])

    def test_provider_router_blocks_unresolved_endpoint_before_client(self) -> None:
        class FakeProbe:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, provider, model, api_key):
                del provider, model, api_key
                self.calls += 1
                raise AssertionError("client must not run for an unresolved endpoint")

        def unavailable_resolver(*args, **kwargs):
            del args, kwargs
            raise OSError("name resolution failed")

        codex = FakeProbe()
        claude = FakeProbe()
        router = ProviderHealthProbe(codex, claude, resolver=unavailable_resolver)

        result = router.run(make_provider(), "gpt-5.6-sol", API_KEY)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "network_error")
        self.assertEqual(result.failure_stage, "probe_setup")
        self.assertEqual(result.diagnostic_source, "probe_router")
        self.assertEqual(codex.calls, 0)
        self.assertEqual(claude.calls, 0)

    def test_provider_router_blocks_private_dns_answer_before_client(self) -> None:
        class FakeProbe:
            def run(self, provider, model, api_key):
                del provider, model, api_key
                raise AssertionError("client must not run for a private endpoint")

        router = ProviderHealthProbe(
            FakeProbe(),
            FakeProbe(),
            resolver=lambda *args, **kwargs: ["127.0.0.1"],
        )

        result = router.run(make_provider(), "claude-opus-5", API_KEY)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "network_error")
        self.assertIn("public HTTPS endpoint", result.error_summary)


class ClaudeProcessTests(unittest.TestCase):
    def test_run_claude_cli_uses_noninteractive_isolated_arguments(self) -> None:
        process = FakeProcess(stdout=envelope())
        capture: dict = {}

        def process_factory(command, **kwargs):
            capture["command"] = command
            capture["kwargs"] = kwargs
            return process

        result = _run_claude_cli(
            claude_bin="claude",
            env={"ANTHROPIC_API_KEY": API_KEY},
            workspace=Path("workspace"),
            model="claude-opus-5",
            prompt="health prompt",
            timeout=90,
            process_factory=process_factory,
        )

        self.assertEqual(result.returncode, 0)
        command = capture["command"]
        self.assertEqual(command[0], "claude")
        for argument in (
            "--bare",
            "--safe-mode",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--tools",
            "--json-schema",
        ):
            self.assertIn(argument, command)
        self.assertEqual(command[-2:], ["-p", "health prompt"])
        self.assertEqual(capture["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertEqual(capture["kwargs"]["cwd"], "workspace")
        if os.name == "posix":
            self.assertTrue(capture["kwargs"]["start_new_session"])

    def test_run_claude_cli_terminates_process_tree_on_timeout(self) -> None:
        process = FakeProcess(timeout_once=True)
        terminated: list[FakeProcess] = []

        def terminate(value):
            value.returncode = -15
            terminated.append(value)

        result = _run_claude_cli(
            claude_bin="claude",
            env={},
            workspace=Path("workspace"),
            model="claude-opus-5",
            prompt="health prompt",
            timeout=1,
            process_factory=lambda *args, **kwargs: process,
            process_terminator=terminate,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, -15)
        self.assertEqual(terminated, [process])
        self.assertIn("partial", result.stdout)


if __name__ == "__main__":
    unittest.main()
