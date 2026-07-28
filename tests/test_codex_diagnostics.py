import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from provider_status.codex_diagnostics import (
    CodexDiagnostic,
    read_codex_diagnostic,
)


class CodexDiagnosticsTests(unittest.TestCase):
    def _make_logs(self, home: Path, rows: list[object]) -> None:
        home.mkdir(parents=True, exist_ok=True)
        path = home / "logs_2.sqlite"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE logs (id INTEGER PRIMARY KEY, payload TEXT, note TEXT)"
            )
            connection.executemany(
                "INSERT INTO logs(payload, note) VALUES (?, ?)",
                [(json.dumps(row), "") for row in rows],
            )
            connection.commit()
        finally:
            connection.close()

    def _make_threaded_logs(
        self,
        home: Path,
        rows: list[tuple[object, str | None]],
    ) -> None:
        home.mkdir(parents=True, exist_ok=True)
        path = home / "logs_2.sqlite"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                CREATE TABLE logs (
                    id INTEGER PRIMARY KEY,
                    payload TEXT,
                    thread_id TEXT
                )
                """
            )
            connection.executemany(
                "INSERT INTO logs(payload, thread_id) VALUES (?, ?)",
                [(json.dumps(row), thread_id) for row, thread_id in rows],
            )
            connection.commit()
        finally:
            connection.close()

    def test_repeated_503_matches_provider_and_ignores_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {
                        "url": "https://provider.example.com/v1/responses",
                        "model": "gpt-5.6-sol",
                        "status_code": 503,
                        "message": "Service Unavailable",
                    },
                    {
                        "url": "https://provider.example.com/v1/responses",
                        "model": "gpt-5.6-sol",
                        "status_code": 503,
                        "message": "Service Unavailable",
                    },
                    {
                        "url": "https://ab.chatgpt.com/backend-api/telemetry",
                        "model": "gpt-5.6-sol",
                        "status_code": 503,
                        "message": "telemetry failure",
                    },
                    {
                        "url": "https://other.example.com/v1/responses",
                        "model": "gpt-5.6-sol",
                        "status_code": 503,
                        "message": "other provider",
                    },
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsInstance(diagnostic, CodexDiagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.kind, "upstream_unavailable")
        self.assertEqual(diagnostic.http_status_code, 503)
        self.assertEqual(diagnostic.occurrences, 2)
        self.assertTrue(diagnostic.retryable)
        self.assertIn("HTTP 503", diagnostic.message)
        self.assertNotIn("telemetry", diagnostic.message)

    def test_model_error_has_priority_and_is_not_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {
                        "request_url": "https://provider.example.com/v1/responses",
                        "requested_model": "gpt-5.6-sol",
                        "status": 404,
                        "error": "model gpt-5.6-sol does not exist",
                    },
                    {
                        "request_url": "https://provider.example.com/v1/responses",
                        "requested_model": "gpt-5.6-sol",
                        "status": 503,
                        "error": "temporary upstream failure",
                    },
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.kind, "model_unavailable")
        self.assertEqual(diagnostic.http_status_code, 404)
        self.assertEqual(diagnostic.occurrences, 1)
        self.assertFalse(diagnostic.retryable)
        self.assertEqual(diagnostic.message, "供应商未开放该模型。")

    def test_stream_disconnect_is_network_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {
                        "url": "https://provider.example.com/v1/responses",
                        "model": "gpt-5.6-sol",
                        "error": "stream disconnected before completion",
                    },
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.kind, "network_error")
        self.assertIsNone(diagnostic.http_status_code)
        self.assertEqual(diagnostic.occurrences, 1)
        self.assertTrue(diagnostic.retryable)
        self.assertEqual(diagnostic.message, "响应流中断。")

    def test_correlates_provider_model_and_error_across_log_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {"configured_model": "gpt-5.6-sol"},
                    {"message": "POST https://provider.example.com/v1/responses"},
                    {"message": "HTTP 503 Service Unavailable"},
                    {
                        "message": (
                            "POST https://ab.chatgpt.com/backend-api/telemetry "
                            "HTTP 503 Service Unavailable"
                        )
                    },
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.kind, "upstream_unavailable")
        self.assertEqual(diagnostic.http_status_code, 503)
        self.assertEqual(diagnostic.occurrences, 1)

    def test_does_not_attach_error_after_telemetry_request_to_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {"configured_model": "gpt-5.6-sol"},
                    {"message": "POST https://provider.example.com/v1/responses"},
                    {
                        "message": (
                            "POST https://ab.chatgpt.com/backend-api/telemetry"
                        )
                    },
                    {"message": "HTTP 503 Service Unavailable"},
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNone(diagnostic)

    def test_uses_isolated_model_context_when_logs_omit_model_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {"message": "POST https://provider.example.com/v1/responses"},
                    {"message": "HTTP 503 Service Unavailable"},
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.kind, "upstream_unavailable")
        self.assertEqual(diagnostic.http_status_code, 503)

    def test_base_url_metadata_does_not_create_request_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {
                        "message": (
                            "Configuring provider base_url: "
                            "https://provider.example.com/v1"
                        )
                    },
                    {
                        "message": (
                            "remote plugin bundle sync failed: "
                            "chatgpt authentication required"
                        )
                    },
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNone(diagnostic)

    def test_unauthorized_retry_metadata_is_not_an_auth_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {"message": "POST https://provider.example.com/v1/responses"},
                    {
                        "message": (
                            'auth_header_attached=true '
                            'auth_retry_after_unauthorized="false" '
                            'auth_error="" auth_error_code=""'
                        )
                    },
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNone(diagnostic)

    def test_ignores_auth_warning_from_another_log_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_threaded_logs(
                home,
                [
                    (
                        {"message": "POST https://provider.example.com/v1/responses"},
                        "provider-thread",
                    ),
                    (
                        {
                            "message": (
                                "remote plugin bundle sync failed: "
                                "chatgpt authentication required"
                            )
                        },
                        None,
                    ),
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNone(diagnostic)

    def test_classifies_non_retryable_provider_errors(self) -> None:
        cases = (
            (
                401,
                "invalid API key",
                "auth_failed",
                "HTTP 401 Unauthorized；供应商鉴权失败。",
            ),
            (
                403,
                "This channel does not allow the current client",
                "client_blocked",
                "供应商拒绝当前客户端。",
            ),
            (429, "rate limit exceeded", "rate_limited", "供应商触发限流。"),
            (
                503,
                "No available channel for model gpt-5.6-sol",
                "no_channel",
                "供应商当前无可用通道。",
            ),
        )

        for status, error, expected_kind, expected_message in cases:
            with self.subTest(kind=expected_kind):
                with tempfile.TemporaryDirectory() as directory:
                    home = Path(directory)
                    self._make_logs(
                        home,
                        [
                            {
                                "url": "https://provider.example.com/v1/responses",
                                "model": "gpt-5.6-sol",
                                "status_code": status,
                                "error": error,
                            }
                        ],
                    )

                    diagnostic = read_codex_diagnostic(
                        home,
                        base_url="https://provider.example.com/v1",
                        model="gpt-5.6-sol",
                    )

                self.assertIsNotNone(diagnostic)
                assert diagnostic is not None
                self.assertEqual(diagnostic.kind, expected_kind)
                self.assertEqual(diagnostic.message, expected_message)
                self.assertEqual(diagnostic.http_status_code, status)
                self.assertEqual(diagnostic.occurrences, 1)
                self.assertFalse(diagnostic.retryable)

    def test_auth_failure_without_status_code_keeps_generic_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._make_logs(
                home,
                [
                    {
                        "url": "https://provider.example.com/v1/responses",
                        "model": "gpt-5.6-sol",
                        "error": "invalid API key",
                    }
                ],
            )

            diagnostic = read_codex_diagnostic(
                home,
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.kind, "auth_failed")
        self.assertIsNone(diagnostic.http_status_code)
        self.assertEqual(diagnostic.message, "供应商鉴权失败。")

    def test_missing_or_invalid_database_returns_empty_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = read_codex_diagnostic(
                Path(directory),
                base_url="https://provider.example.com/v1",
                model="gpt-5.6-sol",
            )

        self.assertIn(diagnostic, (None, CodexDiagnostic()))


if __name__ == "__main__":
    unittest.main()
