import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from provider_status.config import ProviderConfig
from provider_status.probe import (
    CodexHealthProbe,
    DirectDiagnosticResult,
    HEALTH_PROMPT,
    _run_direct_diagnostic,
)
from provider_status.tui_probe import (
    CodexTuiClient,
    TuiProtocolError,
    TuiTurnResult,
)


API_KEY = "test-secret-value-123456"
VALID_OUTPUT = '{"status":"ok","check":"codex-provider-health"}'


def make_provider() -> ProviderConfig:
    return ProviderConfig(
        provider_id="custom-provider",
        name="Custom Provider",
        base_url="https://provider.example.com/v1",
        credential_name="provider-key",
        models=("model-a",),
        healthy_interval_seconds=600,
        unhealthy_interval_seconds=120,
        timeout_seconds=90,
    )


def make_turn(
    *,
    output_text: str = VALID_OUTPUT,
    turn_status: str = "completed",
    error_text: str = "",
    diagnostics: str = "",
    timed_out: bool = False,
    http_status_code: int | None = None,
    error_code: str | None = None,
) -> TuiTurnResult:
    return TuiTurnResult(
        output_text=output_text,
        turn_status=turn_status,
        error_text=error_text,
        diagnostics=diagnostics,
        timed_out=timed_out,
        http_status_code=http_status_code,
        originator="codex-tui",
        error_code=error_code,
    )


class FakeClient:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, float]] = []
        self.closed = False

    def run_turn(self, prompt: str, *, timeout: float):
        self.calls.append((prompt, timeout))
        if self.error is not None:
            raise self.error
        return self.result

    def close(self) -> None:
        self.closed = True


class UnreadableResponseBody(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("diagnostic response body must not be read")


class HealthProbeTests(unittest.TestCase):
    def test_defaults_to_real_codex_tui_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            probe = CodexHealthProbe(
                codex_bin="codex",
                temp_root=Path(directory),
            )

        self.assertIs(probe._client_factory, CodexTuiClient)

    def test_success_writes_isolated_minimal_files_and_always_cleans_up(self) -> None:
        provider = make_provider()
        clients: list[FakeClient] = []
        captures: list[dict] = []

        def factory(**kwargs):
            codex_home = Path(kwargs["env"]["CODEX_HOME"])
            config_path = codex_home / "config.toml"
            auth_path = codex_home / "auth.json"
            captures.append(
                {
                    "kwargs": kwargs,
                    "run_dir": codex_home.parent,
                    "config": config_path.read_text(encoding="utf-8"),
                    "auth": json.loads(auth_path.read_text(encoding="utf-8")),
                    "config_mode": stat.S_IMODE(config_path.stat().st_mode),
                    "auth_mode": stat.S_IMODE(auth_path.stat().st_mode),
                }
            )
            client = FakeClient(make_turn(output_text=f"```json\n{VALID_OUTPUT}\n```"))
            clients.append(client)
            return client

        clock_values = iter((10.0, 10.125, 20.0, 20.250))
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "PATH": "/probe/bin",
                        "HTTPS_PROXY": "http://proxy.example:8080",
                    },
                ),
                mock.patch(
                    "provider_status.probe.os.chmod",
                    wraps=os.chmod,
                ) as chmod,
            ):
                probe = CodexHealthProbe(
                    codex_bin="codex",
                    temp_root=temp_root,
                    client_factory=factory,
                    clock=lambda: next(clock_values),
                )
                first = probe.run(provider, "model-a", API_KEY)
                second = probe.run(provider, "model-a", API_KEY)

            self.assertEqual(list(temp_root.iterdir()), [])

        self.assertEqual(first.success, True)
        self.assertEqual(first.latency_ms, 125)
        self.assertIsNone(first.error_code)
        self.assertIsNone(first.error_summary)
        self.assertEqual(second.latency_ms, 250)
        self.assertNotEqual(captures[0]["run_dir"], captures[1]["run_dir"])
        self.assertTrue(all(client.closed for client in clients))

        for capture, client in zip(captures, clients, strict=True):
            kwargs = capture["kwargs"]
            self.assertEqual(kwargs["codex_bin"], "codex")
            self.assertEqual(kwargs["workspace"], capture["run_dir"] / "workspace")
            self.assertEqual(kwargs["sandbox"], "read-only")
            self.assertEqual(kwargs["model"], "model-a")
            self.assertEqual(kwargs["reasoning_effort"], "low")
            self.assertEqual(kwargs["model_provider"], "custom")
            self.assertEqual(kwargs["env"]["PATH"], "/probe/bin")
            self.assertEqual(
                kwargs["env"]["HTTPS_PROXY"],
                "http://proxy.example:8080",
            )
            self.assertEqual(client.calls, [(HEALTH_PROMPT, 90)])
            self.assertEqual(
                capture["auth"],
                {"OPENAI_API_KEY": API_KEY},
            )
            self.assertNotIn(API_KEY, capture["config"])
            self.assertIn('model = "model-a"', capture["config"])
            self.assertIn('model_provider = "custom"', capture["config"])
            self.assertIn('[model_providers.custom]', capture["config"])
            self.assertIn('name = "Custom Provider"', capture["config"])
            self.assertIn(
                'base_url = "https://provider.example.com/v1"',
                capture["config"],
            )
            self.assertIn('wire_api = "responses"', capture["config"])
            self.assertIn("requires_openai_auth = true", capture["config"])
            chmod.assert_any_call(
                capture["run_dir"] / "codex-home" / "config.toml",
                0o600,
            )
            chmod.assert_any_call(
                capture["run_dir"] / "codex-home" / "auth.json",
                0o600,
            )
            if os.name == "posix":
                self.assertEqual(capture["config_mode"], 0o600)
                self.assertEqual(capture["auth_mode"], 0o600)

    def test_maps_public_error_codes_and_sanitizes_summary(self) -> None:
        cases = (
            ("timeout", make_turn(timed_out=True, error_text="wait timeout")),
            (
                "upstream_unavailable",
                make_turn(
                    turn_status="failed",
                    timed_out=True,
                    http_status_code=503,
                    error_code="upstream_unavailable",
                    error_text="HTTP 503；上游服务暂时不可用。",
                ),
            ),
            (
                "model_unavailable",
                make_turn(
                    turn_status="failed",
                    error_code="model_unavailable",
                    http_status_code=404,
                    error_text="供应商未开放该模型。",
                ),
            ),
            ("auth_failed", make_turn(turn_status="failed", http_status_code=401)),
            ("auth_failed", make_turn(turn_status="failed", error_text="authentication failed")),
            (
                "client_blocked",
                make_turn(
                    turn_status="failed",
                    http_status_code=403,
                    error_text="This channel does not allow the current client",
                ),
            ),
            (
                "rate_limited",
                make_turn(
                    turn_status="failed",
                    http_status_code=403,
                    error_code="client_blocked",
                    error_text="用户额度不足, 剩余额度: $0.00",
                ),
            ),
            ("rate_limited", make_turn(turn_status="failed", http_status_code=429)),
            ("rate_limited", make_turn(turn_status="failed", error_text="usage limit reached")),
            (
                "no_channel",
                make_turn(
                    turn_status="failed",
                    error_text=(
                        "Unexpected status 503 Service Unavailable: "
                        "No available channel for model gpt-5.6-sol"
                    ),
                    http_status_code=503,
                ),
            ),
            (
                "stream_interrupted",
                make_turn(turn_status="failed", error_text="stream disconnected"),
            ),
            ("network_error", make_turn(turn_status="failed", error_text="DNS lookup failed")),
            ("network_error", make_turn(turn_status="failed", error_text="TLS handshake failed")),
            ("network_error", make_turn(turn_status="failed", error_text="connect error")),
            ("network_error", make_turn(turn_status="failed", error_text="error sending request")),
            ("unknown_error", make_turn(turn_status="failed", error_text="unexpected failure")),
        )

        for expected_code, turn in cases:
            with self.subTest(expected_code=expected_code, error=turn.error_text):
                secret_turn = make_turn(
                    output_text=turn.output_text,
                    turn_status=turn.turn_status,
                    error_text=f"{turn.error_text} api_key={API_KEY}",
                    diagnostics="diagnostic " * 40,
                    timed_out=turn.timed_out,
                    http_status_code=turn.http_status_code,
                    error_code=turn.error_code,
                )
                client = FakeClient(secret_turn)
                with tempfile.TemporaryDirectory() as directory:
                    result = CodexHealthProbe(
                        codex_bin="codex",
                        temp_root=Path(directory),
                        client_factory=lambda **kwargs: client,
                        clock=lambda: 1.0,
                        diagnostic_runner=lambda **kwargs: DirectDiagnosticResult(
                            "stream_interrupted",
                            "HTTP 200",
                            200,
                            "codex_stream",
                        ),
                    ).run(make_provider(), "model-a", API_KEY)

                self.assertFalse(result.success)
                self.assertEqual(result.error_code, expected_code)
                self.assertLessEqual(len(result.error_summary or ""), 240)
                self.assertNotIn(API_KEY, result.error_summary or "")
                self.assertTrue(client.closed)

    def test_rejects_non_exact_json_objects(self) -> None:
        invalid_outputs = (
            "",
            "not json",
            "[]",
            '{"status":"ok"}',
            '{"status":"bad","check":"codex-provider-health"}',
            '{"status":"ok","check":"codex-provider-health","extra":true}',
            f"prefix {VALID_OUTPUT}",
        )

        for output in invalid_outputs:
            with self.subTest(output=output):
                with tempfile.TemporaryDirectory() as directory:
                    result = CodexHealthProbe(
                        codex_bin="codex",
                        temp_root=Path(directory),
                        client_factory=lambda **kwargs: FakeClient(
                            make_turn(output_text=output)
                        ),
                        clock=lambda: 1.0,
                    ).run(make_provider(), "model-a", API_KEY)

                self.assertFalse(result.success)
                self.assertEqual(result.error_code, "invalid_output")

    def test_protocol_exception_is_classified_and_client_is_closed(self) -> None:
        client = FakeClient(
            error=TuiProtocolError(
                f"stream disconnected; Authorization: Bearer {API_KEY}"
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            result = CodexHealthProbe(
                codex_bin="codex",
                temp_root=temp_root,
                client_factory=lambda **kwargs: client,
                clock=lambda: 1.0,
                diagnostic_runner=lambda **kwargs: DirectDiagnosticResult(
                    "stream_interrupted",
                    "HTTP 200",
                    200,
                    "codex_stream",
                ),
            ).run(make_provider(), "model-a", API_KEY)
            self.assertEqual(list(temp_root.iterdir()), [])

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "stream_interrupted")
        self.assertNotIn(API_KEY, result.error_summary or "")
        self.assertTrue(client.closed)

    def test_ambiguous_stream_failure_uses_one_bounded_direct_diagnostic(self) -> None:
        cases = (
            (520, "upstream_unavailable", "provider_response"),
            (401, "auth_failed", "provider_response"),
            (200, "stream_interrupted", "codex_stream"),
            (None, "network_error", "network"),
        )

        for status_code, expected_code, expected_stage in cases:
            with self.subTest(status_code=status_code):
                calls: list[dict[str, object]] = []

                def diagnose(**kwargs):
                    calls.append(kwargs)
                    return DirectDiagnosticResult(
                        expected_code,
                        f"HTTP {status_code}" if status_code else "connection failed",
                        status_code,
                        expected_stage,
                    )

                with tempfile.TemporaryDirectory() as directory:
                    result = CodexHealthProbe(
                        codex_bin="codex",
                        temp_root=Path(directory),
                        client_factory=lambda **kwargs: FakeClient(
                            make_turn(
                                turn_status="failed",
                                error_text=(
                                    "stream disconnected before completion: "
                                    "Upstream request failed"
                                ),
                                error_code="network_error",
                            )
                        ),
                        clock=lambda: 1.0,
                        diagnostic_runner=diagnose,
                    ).run(make_provider(), "model-a", API_KEY)

                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["timeout_seconds"], 10.0)
                self.assertEqual(calls[0]["api_key"], API_KEY)
                self.assertEqual(result.error_code, expected_code)
                self.assertEqual(result.http_status_code, status_code)
                self.assertEqual(result.failure_stage, expected_stage)
                self.assertEqual(result.diagnostic_source, "direct_responses")
                self.assertNotIn(API_KEY, result.error_summary or "")

    def test_direct_diagnostic_classifies_headers_without_reading_body(self) -> None:
        cases = (
            (200, "stream_interrupted", "codex_stream"),
            (401, "auth_failed", "provider_response"),
            (403, "client_blocked", "provider_response"),
            (429, "rate_limited", "provider_response"),
            (502, "upstream_unavailable", "provider_response"),
            (520, "upstream_unavailable", "provider_response"),
            (526, "upstream_unavailable", "provider_response"),
            (404, "unknown_error", "provider_response"),
        )

        for status_code, expected_code, expected_stage in cases:
            with self.subTest(status_code=status_code):
                requests: list[httpx.Request] = []

                def handler(request: httpx.Request) -> httpx.Response:
                    requests.append(request)
                    return httpx.Response(
                        status_code,
                        stream=UnreadableResponseBody(),
                    )

                client = httpx.Client(transport=httpx.MockTransport(handler))
                with mock.patch(
                    "provider_status.probe.httpx.Client",
                    return_value=client,
                ):
                    result = _run_direct_diagnostic(
                        provider=make_provider(),
                        model="model-a",
                        api_key=API_KEY,
                        timeout_seconds=10.0,
                    )

                self.assertEqual(len(requests), 1)
                self.assertEqual(
                    requests[0].url,
                    httpx.URL("https://provider.example.com/v1/responses"),
                )
                self.assertEqual(
                    requests[0].headers["authorization"],
                    f"Bearer {API_KEY}",
                )
                self.assertEqual(result.error_code, expected_code)
                self.assertEqual(result.http_status_code, status_code)
                self.assertEqual(result.failure_stage, expected_stage)
                self.assertEqual(result.error_summary, f"HTTP {status_code}")
                self.assertNotIn(API_KEY, repr(result))

    def test_explicit_network_failure_does_not_run_direct_diagnostic(self) -> None:
        def unexpected_diagnostic(**kwargs):
            self.fail("explicit DNS/TLS failures must not trigger direct diagnosis")

        with tempfile.TemporaryDirectory() as directory:
            result = CodexHealthProbe(
                codex_bin="codex",
                temp_root=Path(directory),
                client_factory=lambda **kwargs: FakeClient(
                    make_turn(
                        turn_status="failed",
                        error_text="DNS lookup failed during TLS connection",
                    )
                ),
                clock=lambda: 1.0,
                diagnostic_runner=unexpected_diagnostic,
            ).run(make_provider(), "model-a", API_KEY)

        self.assertEqual(result.error_code, "network_error")
        self.assertEqual(result.failure_stage, "codex_tui")
        self.assertEqual(result.diagnostic_source, "codex_tui")

    def test_setup_failure_removes_partially_created_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            with mock.patch(
                "provider_status.probe.os.chmod",
                side_effect=OSError(f"chmod failed for {API_KEY}"),
            ):
                result = CodexHealthProbe(
                    codex_bin="codex",
                    temp_root=temp_root,
                    client_factory=lambda **kwargs: self.fail(
                        "client must not start after setup failure"
                    ),
                    clock=lambda: 1.0,
                ).run(make_provider(), "model-a", API_KEY)

            self.assertEqual(list(temp_root.iterdir()), [])

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "unknown_error")
        self.assertNotIn(API_KEY, result.error_summary or "")


if __name__ == "__main__":
    unittest.main()
