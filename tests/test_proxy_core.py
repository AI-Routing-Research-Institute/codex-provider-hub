import asyncio
import gzip
import json
import re
import sqlite3
import socket
import tempfile
import threading
import time
import unittest
from collections.abc import AsyncIterator
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from local_proxy.core import (
    HealthStatusUrlStore,
    InputItemIdCompatibilityStore,
    LocalProxyServer,
    ProviderRouter,
    ProxyProvider,
    RecoveryHistoryStore,
    RetryPolicy,
    RetryPolicyStore,
    SSECapacityFailureCapture,
    SSETerminalCapture,
    TokenUsage,
    UsageCapture,
    UsageStore,
    RETRY_ERROR_BODY_BYTES,
    _codex_thread_id,
    _inspect_http_400_before_output,
    _inspect_sse_before_output,
    _input_item_id_error_index,
    _sse_preflight_decision,
    _strip_input_item_ids,
    _rewrite_request_model,
    _public_control_status,
    _public_requests,
    _request_reasoning_effort,
    create_proxy_app,
    filter_self_referencing_providers,
    order_proxy_providers,
)
from local_proxy.codex import load_proxy_providers
from local_proxy.protocols.claude_messages import ClaudeMessagesProtocol


async def _empty_wait() -> None:
    return None


def provider(
    provider_id: str,
    *,
    current: bool = False,
    api_key: str | None = "test-upstream-credential",
    model: str | None = None,
    model_mappings: dict[str, str] | None = None,
) -> ProxyProvider:
    return ProxyProvider(
        provider_id=provider_id,
        name=provider_id.title(),
        base_url=f"https://{provider_id}.example.test/v1",
        is_cc_switch_current=current,
        api_key=api_key,
        model=model,
        model_mappings=model_mappings or {},
    )


class ProviderRouterTests(unittest.TestCase):
    def test_request_model_rewrite_only_changes_a_matching_string_model(self) -> None:
        mappings = {"gpt-5.6": "gpt-5.6-sol"}

        self.assertEqual(
            json.loads(_rewrite_request_model(b'{"model":"gpt-5.6","input":"hello"}', mappings)),
            {"model": "gpt-5.6-sol", "input": "hello"},
        )
        for payload in (
            b'{"model":"gpt-5","input":"hello"}',
            b'{"input":"hello"}',
            b'{"model":42}',
            b'not-json',
        ):
            with self.subTest(payload=payload):
                self.assertEqual(_rewrite_request_model(payload, mappings), payload)

    def test_active_request_exposes_resolved_name_without_thread_id(self) -> None:
        router = ProviderRouter((provider("first", current=True),))
        thread_id = "019fa83f-2a11-73b0-a862-4d51679219ef"
        request = router.begin_request(thread_id=thread_id)

        payload = _public_control_status(
            router,
            session_name_resolver=lambda requested: {
                item: "Codex服务可用检测" for item in requested
            },
        )

        self.assertEqual(
            payload["providers"][0]["active_sessions"],
            [{"name": "Codex服务可用检测"}],
        )
        self.assertNotIn(thread_id, json.dumps(payload, ensure_ascii=False))
        router.finish_request(request, status_code=200)
        self.assertEqual(router.status().active_request_details, ())

    def test_codex_thread_id_reads_bounded_json_metadata(self) -> None:
        thread_id = "019fa83f-2a11-73b0-a862-4d51679219ef"
        headers = {
            "x-codex-turn-metadata": json.dumps(
                {"thread_id": thread_id, "turn_id": "turn-fixture"}
            )
        }

        self.assertEqual(_codex_thread_id(headers), thread_id)
        self.assertIsNone(_codex_thread_id({"x-codex-turn-metadata": "not-json"}))

    def test_active_request_exposes_reasoning_effort(self) -> None:
        router = ProviderRouter((provider("first", current=True),))
        request = router.begin_request()
        router.update_request_model(request, "gpt-5", "high")
        router.update_request_upstream_model(request, "gpt-5-upstream")
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = _public_requests(
                router,
                UsageStore(Path(temp_dir) / "usage.sqlite3"),
            )

        self.assertEqual(payload["active"][0]["model"], "gpt-5")
        self.assertEqual(payload["active"][0]["upstream_model"], "gpt-5-upstream")
        self.assertEqual(payload["active"][0]["reasoning_effort"], "high")
        router.finish_request(request, status_code=200)

    def test_switch_affects_new_requests_without_moving_active_request(self) -> None:
        router = ProviderRouter((provider("first", current=True), provider("second")))

        first_request = router.begin_request()
        router.select("second")
        second_request = router.begin_request()

        self.assertEqual(first_request.provider.provider_id, "first")
        self.assertEqual(second_request.provider.provider_id, "second")
        self.assertEqual(router.status().active_by_provider, {"first": 1, "second": 1})
        router.finish_request(first_request, status_code=200)
        self.assertEqual(router.status().active_by_provider, {"second": 1})

    def test_finishing_the_same_request_twice_does_not_remove_another_request(self) -> None:
        router = ProviderRouter((provider("first", current=True),))
        first_request = router.begin_request()
        second_request = router.begin_request()

        router.finish_request(first_request, status_code=200)
        router.finish_request(first_request, status_code=200)

        status = router.status()
        self.assertEqual(status.active_by_provider, {"first": 1})
        self.assertEqual(
            [detail.request_id for detail in status.active_request_details],
            [second_request.request_id],
        )
        router.finish_request(second_request, status_code=200)

    def test_retry_can_move_active_request_to_current_provider(self) -> None:
        router = ProviderRouter((provider("first", current=True), provider("second")))
        request = router.begin_request()
        router.record_retry(
            request,
            attempt=2,
            max_attempts=-1,
            delay_seconds=2,
            kind="http_503",
        )

        router.select("second")
        rerouted, changed = router.route_retry_to_current(request)

        self.assertTrue(changed)
        self.assertEqual(rerouted.request_id, request.request_id)
        self.assertEqual(rerouted.provider.provider_id, "second")
        status = router.status()
        self.assertEqual(status.active_by_provider, {"second": 1})
        self.assertEqual(
            status.retrying_by_request[request.request_id].provider_id,
            "second",
        )
        router.record_retry(
            rerouted,
            attempt=3,
            max_attempts=-1,
            delay_seconds=0,
            kind="http_503",
            error_provider_id="first",
        )
        self.assertEqual(router.status().recent_retry_errors[0].provider_id, "first")
        router.finish_request(rerouted, status_code=200)
        self.assertEqual(router.status().active_by_provider, {})

    def test_session_override_routes_new_requests_and_retries_to_fixed_provider(self) -> None:
        router = ProviderRouter(
            (provider("first", current=True), provider("second")),
            session_provider_overrides={"thread-a": "second"},
        )

        request = router.begin_request(thread_id="thread-a")
        router.select("first")
        rerouted, changed = router.route_retry_to_current(request)

        self.assertEqual(request.provider.provider_id, "second")
        self.assertFalse(changed)
        self.assertEqual(rerouted.provider.provider_id, "second")
        router.finish_request(request, status_code=200)
        router.set_session_provider_override("thread-a", None)
        following = router.begin_request(thread_id="thread-a")
        self.assertEqual(following.provider.provider_id, "first")
        router.finish_request(following, status_code=200)

    def test_retry_uses_session_override_changed_after_request_started(self) -> None:
        router = ProviderRouter((provider("first", current=True), provider("second")))
        request = router.begin_request(thread_id="thread-a")

        router.set_session_provider_override("thread-a", "second")
        rerouted, changed = router.route_retry_to_current(request)

        self.assertTrue(changed)
        self.assertEqual(rerouted.provider.provider_id, "second")
        self.assertEqual(rerouted.session_provider_id, "second")
        self.assertEqual(router.status().active_by_provider, {"second": 1})
        router.finish_request(rerouted, status_code=200)

    def test_retry_follows_current_provider_after_session_override_is_removed(self) -> None:
        router = ProviderRouter(
            (provider("first", current=True), provider("second")),
            session_provider_overrides={"thread-a": "second"},
        )
        request = router.begin_request(thread_id="thread-a")

        router.set_session_provider_override("thread-a", None)
        rerouted, changed = router.route_retry_to_current(request)

        self.assertTrue(changed)
        self.assertEqual(rerouted.provider.provider_id, "first")
        self.assertIsNone(rerouted.session_provider_id)
        self.assertEqual(router.status().active_by_provider, {"first": 1})
        router.finish_request(rerouted, status_code=200)

    def test_draining_requests_respect_each_sessions_effective_provider(self) -> None:
        router = ProviderRouter(
            (provider("first", current=True), provider("second")),
            session_provider_overrides={"thread-a": "second"},
        )
        request = router.begin_request(thread_id="thread-a")

        active_payload = _public_control_status(router)
        active_by_id = {
            item["provider_id"]: item for item in active_payload["providers"]
        }
        self.assertEqual(active_by_id["second"]["active_requests"], 1)
        self.assertEqual(active_by_id["second"]["draining_requests"], 0)

        router.set_session_provider_override("thread-a", "first")
        changed_payload = _public_control_status(router)
        changed_by_id = {
            item["provider_id"]: item for item in changed_payload["providers"]
        }
        self.assertEqual(changed_by_id["second"]["draining_requests"], 1)
        router.finish_request(request, status_code=200)

    def test_global_provider_switch_marks_only_unrouted_old_requests_draining(self) -> None:
        router = ProviderRouter((provider("first", current=True), provider("second")))
        request = router.begin_request()
        router.select("second")

        payload = _public_control_status(router)
        by_id = {item["provider_id"]: item for item in payload["providers"]}

        self.assertEqual(by_id["first"]["active_requests"], 1)
        self.assertEqual(by_id["first"]["draining_requests"], 1)
        self.assertEqual(by_id["second"]["draining_requests"], 0)
        router.finish_request(request, status_code=200)

    def test_refresh_preserves_selection_and_falls_back_safely(self) -> None:
        router = ProviderRouter((provider("first"), provider("second", current=True)))
        router.select("first")

        router.replace_providers((provider("first"), provider("third")))
        self.assertEqual(router.current_provider().provider_id, "first")

        router.replace_providers((provider("third", current=True),))
        self.assertEqual(router.current_provider().provider_id, "third")

    def test_provider_repr_never_contains_upstream_credential(self) -> None:
        upstream = provider("private", api_key="credential-that-must-not-appear")

        self.assertNotIn("credential-that-must-not-appear", repr(upstream))

    def test_concurrent_retries_are_tracked_per_request(self) -> None:
        router = ProviderRouter((provider("same", current=True),))
        first = router.begin_request()
        second = router.begin_request()

        router.record_retry(first, attempt=2, max_attempts=-1, delay_seconds=1, kind="connection")
        router.record_retry(second, attempt=4, max_attempts=-1, delay_seconds=4, kind="http_503")

        status = router.status()
        self.assertEqual(len(status.retrying_by_request), 2)
        self.assertEqual({item.attempt for item in status.retrying_by_request.values()}, {2, 4})
        self.assertEqual(len(status.recent_retry_errors), 2)
        self.assertEqual(status.recent_retry_errors[0].attempt, 3)
        router.finish_request(first, status_code=200)
        self.assertEqual(len(router.status().retrying_by_request), 1)
        router.finish_request(second, status_code=200)

    def test_retry_history_redacts_sensitive_error_details(self) -> None:
        router = ProviderRouter((provider("selected", current=True),))
        request = router.begin_request()

        router.record_retry(
            request,
            attempt=2,
            max_attempts=4,
            delay_seconds=1,
            kind="http_503",
            error_summary='"Authorization": "Bearer fixture-private-token"',
        )

        status = router.status()
        self.assertEqual(len(status.recent_retry_errors), 1)
        self.assertIn("[已隐藏]", status.recent_retry_errors[0].summary)
        self.assertNotIn("fixture-private-token", status.recent_retry_errors[0].summary)

        for attempt in range(3, 9):
            router.record_retry(
                request,
                attempt=attempt,
                max_attempts=-1,
                delay_seconds=1,
                kind="connection",
            )
        self.assertEqual(len(router.status().recent_retry_errors), 5)

    def test_local_order_is_stable_and_self_provider_is_removed(self) -> None:
        first = provider("first")
        second = provider("second")
        loop = ProxyProvider(
            provider_id="local-loop",
            name="Codex 本地中转",
            base_url="http://localhost:17890/v1",
            is_cc_switch_current=False,
            api_key="placeholder",
        )

        filtered = filter_self_referencing_providers((first, loop, second), 17890)
        ordered = order_proxy_providers(filtered, ("second", "stale"))

        self.assertEqual([item.provider_id for item in ordered], ["second", "first"])


class UsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_context.cleanup)
        self.store = UsageStore(Path(self.temp_context.name) / "usage.sqlite3")

    def test_creates_missing_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "missing" / "nested" / "usage.sqlite3"

            store = UsageStore(database)

            self.assertTrue(database.is_file())
            self.assertEqual(store.summary("today")["total"]["request_count"], 0)

    def test_existing_database_adds_request_history_usage_index(self) -> None:
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("DROP INDEX request_history_usage_id")

        UsageStore(self.store.path)

        with closing(sqlite3.connect(self.store.path)) as connection:
            indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list(request_history)")
            }
            query_plan = {
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    "SELECT 1 FROM request_history WHERE usage_id = ?",
                    (1,),
                )
            }

        self.assertIn("request_history_usage_id", indexes)
        self.assertTrue(
            any("request_history_usage_id" in step for step in query_plan),
            query_plan,
        )

    def test_existing_database_adds_request_model_tracking_columns(self) -> None:
        old_path = Path(self.temp_context.name) / "old-request-history.sqlite3"
        with closing(sqlite3.connect(old_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE request_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL,
                    provider_id TEXT NOT NULL,
                    thread_id TEXT,
                    session_key TEXT,
                    session_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status_code INTEGER,
                    succeeded INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    error_kind TEXT,
                    error_summary TEXT,
                    usage_id INTEGER,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_source TEXT,
                    estimate_method TEXT
                );
                """
            )

        UsageStore(old_path)

        with closing(sqlite3.connect(old_path)) as connection:
            history_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(request_history)")
            }
            inflight_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(inflight_requests)")
            }
        self.assertIn("reasoning_effort", history_columns)
        self.assertIn("upstream_model", history_columns)
        self.assertIn("upstream_model", inflight_columns)

    def test_inflight_request_is_updated_and_removed_on_completion(self) -> None:
        started_at = time.time() - 2
        self.store.start_inflight_request(
            request_id=17,
            started_at=started_at,
            provider_id="provider-a",
            thread_id="thread-a",
        )
        self.store.update_inflight_request(
            17,
            provider_id="provider-b",
            model="gpt-test",
            upstream_model="gpt-upstream",
            reasoning_effort="high",
            phase="receiving",
            request_body_bytes=321,
            retry_count=2,
        )

        with closing(sqlite3.connect(self.store.path)) as connection:
            inflight = connection.execute(
                """
                SELECT provider_id, model, upstream_model, reasoning_effort, phase,
                       request_body_bytes, retry_count
                FROM inflight_requests
                """
            ).fetchone()

        self.assertEqual(
            inflight,
            (
                "provider-b",
                "gpt-test",
                "gpt-upstream",
                "high",
                "receiving",
                321,
                2,
            ),
        )

        self.store.record_request(
            request_id=17,
            started_at=started_at,
            finished_at=time.time(),
            provider_id="provider-b",
            thread_id="thread-a",
            session_name="测试会话",
            model="gpt-test",
            upstream_model="gpt-upstream",
            reasoning_effort="high",
            status_code=200,
            successful=True,
            outcome="succeeded",
            retry_count=2,
        )

        with closing(sqlite3.connect(self.store.path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM inflight_requests"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        history = self.store.request_history(query="gpt-upstream")
        self.assertEqual(history["items"][0]["upstream_model"], "gpt-upstream")

    def test_reopens_database_and_recovers_inflight_request(self) -> None:
        started_at = time.time() - 3
        self.store.start_inflight_request(
            request_id=23,
            started_at=started_at,
            provider_id="provider-a",
            thread_id="thread-a",
            session_name="中断会话",
            model="gpt-test",
            upstream_model="gpt-upstream",
            phase="waiting_first_chunk",
            request_body_bytes=456,
            retry_count=1,
        )

        recovered = UsageStore(self.store.path, run_id="next-run")
        history = recovered.request_history(window="24h", status="failed")

        self.assertEqual(history["total_count"], 1)
        item = history["items"][0]
        self.assertEqual(item["outcome"], "interrupted")
        self.assertEqual(item["error_kind"], "process_restarted")
        self.assertEqual(item["upstream_model"], "gpt-upstream")
        self.assertIn("waiting_first_chunk", item["error_summary"])
        self.assertIn("456", item["error_summary"])
        with closing(sqlite3.connect(self.store.path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM inflight_requests"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_reads_nested_and_legacy_reasoning_effort(self) -> None:
        self.assertEqual(
            _request_reasoning_effort(b'{"reasoning":{"effort":"high"}}'),
            "high",
        )
        self.assertEqual(
            _request_reasoning_effort(b'{"reasoning_effort":" low "}'),
            "low",
        )
        self.assertIsNone(_request_reasoning_effort(b'{"reasoning":{"effort":7}}'))
        self.assertIsNone(_request_reasoning_effort(b"not-json"))

    def test_upstream_usage_wins_over_local_estimate(self) -> None:
        capture = UsageCapture(
            b'{"model":"gpt-5","input":"this would be estimated"}',
            "responses",
        )
        capture.feed(
            b'data: {"type":"response.completed","response":{"usage":'
            b'{"input_tokens":101,"output_tokens":23,"total_tokens":124,'
            b'"input_tokens_details":{"cached_tokens":40},'
            b'"output_tokens_details":{"reasoning_tokens":7}}}}\n\n'
        )

        usage = capture.finalize(200)

        self.assertEqual(
            usage,
            TokenUsage(101, 23, 124, 40, 7, source="upstream"),
        )

    def test_missing_usage_estimates_input_and_streamed_output(self) -> None:
        capture = UsageCapture(
            b'{"model":"gpt-5","instructions":"be concise",'
            b'"input":[{"type":"message","content":'
            b'[{"type":"input_text","text":"hello world"}]}]}',
            "responses",
        )
        capture.feed(
            b'data: {"type":"response.output_text.delta","delta":"hello back"}\n\n'
        )

        usage = capture.finalize(200)

        self.assertIsNotNone(usage)
        self.assertEqual(usage.source, "estimated")
        self.assertGreater(usage.input_tokens, 0)
        self.assertGreater(usage.output_tokens, 0)
        self.assertEqual(usage.total_tokens, usage.input_tokens + usage.output_tokens)

    def test_sqlite_summary_uses_exact_168_hour_window(self) -> None:
        now = 2_000_000.0
        self.store.record(
            provider_id="inside",
            model="gpt-5",
            usage=TokenUsage(10, 5, 15, source="upstream"),
            status_code=200,
            recorded_at=now - 7 * 24 * 3600 + 1,
        )
        self.store.record(
            provider_id="outside",
            model="gpt-5",
            usage=TokenUsage(100, 50, 150, source="estimated"),
            status_code=200,
            recorded_at=now - 7 * 24 * 3600 - 1,
        )

        summary = self.store.summary("7d", now=now)

        self.assertEqual(summary["total"]["total_tokens"], 15)
        self.assertEqual(summary["total"]["request_count"], 1)
        self.assertEqual(set(summary["by_provider"]), {"inside"})
        self.assertEqual(
            summary["by_provider"]["inside"]["last_success_at"],
            round((now - 7 * 24 * 3600 + 1) * 1000),
        )

    def test_custom_usage_range_filters_summary_and_history(self) -> None:
        for recorded_at, tokens in ((100.0, 10), (101.0, 20), (102.0, 30)):
            self.store.record(
                provider_id="provider-a",
                model="gpt-5",
                usage=TokenUsage(tokens, 0, tokens),
                status_code=200,
                recorded_at=recorded_at,
            )

        summary = self.store.summary("custom", start_at=101.0, end_at=102.0)
        history = self.store.history(
            provider_id="provider-a",
            window="custom",
            start_at=101.0,
            end_at=102.0,
        )

        self.assertEqual(summary["total"]["total_tokens"], 20)
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["total_tokens"], 20)
        self.assertEqual(history["start_at"], 101000)
        self.assertEqual(history["end_at"], 101999)

    def test_custom_request_range_filters_and_enforces_seven_day_retention(self) -> None:
        now = 2_000_000.0
        for offset, name in ((-3.0, "inside"), (-1.0, "outside")):
            self.store.record_request(
                started_at=now + offset,
                finished_at=now + offset,
                provider_id="provider-a",
                thread_id=None,
                session_name=name,
                model="gpt-5",
                status_code=200,
                successful=True,
                outcome="succeeded",
                retry_count=0,
            )

        history = self.store.request_history(
            window="custom",
            start_at=now - 4,
            end_at=now - 2,
            now=now,
        )

        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["session_name"], "inside")
        with self.assertRaisesRegex(ValueError, "7 天"):
            self.store.request_history(
                window="custom",
                start_at=now - 8 * 24 * 3600,
                end_at=now,
                now=now,
            )

    def test_request_history_uses_full_168_hour_window_for_seven_days(self) -> None:
        now = 2_000_000.0
        recorded_at = now - 8 * 3600
        self.store.record_request(
            started_at=recorded_at,
            finished_at=recorded_at,
            provider_id="provider-a",
            thread_id=None,
            session_name="eight-hours-ago",
            model="gpt-5",
            status_code=200,
            successful=True,
            outcome="succeeded",
            retry_count=0,
        )

        six_hours = self.store.request_history(window="6h", now=now)
        seven_days = self.store.request_history(window="7d", now=now)

        self.assertEqual(six_hours["total_count"], 0)
        self.assertEqual(seven_days["total_count"], 1)
        self.assertEqual(seven_days["items"][0]["session_name"], "eight-hours-ago")

    def test_request_history_cleanup_is_throttled(self) -> None:
        now = 2_000_000.0
        self.store.request_history(now=now)
        expired_at = now - 7 * 24 * 3600 - 1
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO request_history (
                    started_at, finished_at, provider_id, session_name, model,
                    succeeded, outcome, duration_ms, retry_count
                ) VALUES (?, ?, 'provider-a', 'expired', 'gpt-5', 1,
                          'succeeded', 0, 0)
                """,
                (expired_at, expired_at),
            )

        self.store.request_history(now=now + 1)
        with closing(sqlite3.connect(self.store.path)) as connection:
            retained_count = connection.execute(
                "SELECT COUNT(*) FROM request_history WHERE session_name = 'expired'"
            ).fetchone()[0]

        self.store.request_history(now=now + 3600)
        with closing(sqlite3.connect(self.store.path)) as connection:
            cleaned_count = connection.execute(
                "SELECT COUNT(*) FROM request_history WHERE session_name = 'expired'"
            ).fetchone()[0]

        self.assertEqual(retained_count, 1)
        self.assertEqual(cleaned_count, 0)

    def test_request_history_includes_failures_and_paginates(self) -> None:
        now = 2_000_000.0
        records = (
            (now - 3, 200, TokenUsage(10, 2, 12, 4, 1, source="upstream")),
            (now - 2, 503, TokenUsage(20, 3, 23, source="upstream")),
            (
                now - 1,
                200,
                TokenUsage(
                    30,
                    4,
                    34,
                    source="estimated",
                    estimate_method="fixture-estimator",
                ),
            ),
        )
        for recorded_at, status_code, usage in records:
            self.store.record(
                provider_id="provider-a",
                model="gpt-5.6-sol",
                usage=usage,
                status_code=status_code,
                recorded_at=recorded_at,
            )
        self.store.record(
            provider_id="provider-a",
            model="gpt-5.6-sol",
            usage=TokenUsage(90, 9, 99),
            status_code=200,
            successful=False,
            recorded_at=now - 0.5,
        )
        self.store.record(
            provider_id="provider-b",
            model="other-model",
            usage=TokenUsage(100, 20, 120),
            status_code=200,
            recorded_at=now,
        )

        first = self.store.history(
            provider_id="provider-a",
            window="all",
            limit=1,
            now=now,
        )
        second = self.store.history(
            provider_id="provider-a",
            window="all",
            cursor=first["next_cursor"],
            limit=2,
            now=now,
        )

        self.assertEqual(first["total_count"], 4)
        self.assertEqual(first["total"]["total_tokens"], 168)
        self.assertEqual(first["total"]["successful_requests"], 2)
        self.assertEqual(first["total"]["failed_requests"], 2)
        self.assertEqual(first["total"]["successful_tokens"], 46)
        self.assertEqual(first["total"]["failed_tokens"], 122)
        self.assertEqual(first["items"][0]["total_tokens"], 99)
        self.assertFalse(first["items"][0]["succeeded"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(second["items"][0]["total_tokens"], 34)
        self.assertTrue(second["items"][0]["succeeded"])
        self.assertEqual(second["items"][0]["usage_source"], "estimated")
        self.assertEqual(second["items"][0]["estimate_method"], "fixture-estimator")
        self.assertEqual(second["items"][1]["status_code"], 503)
        self.assertFalse(second["items"][1]["succeeded"])
        third = self.store.history(
            provider_id="provider-a",
            window="all",
            cursor=second["next_cursor"],
            limit=2,
            now=now,
        )
        self.assertEqual(third["items"][0]["total_tokens"], 12)
        self.assertTrue(third["items"][0]["succeeded"])
        self.assertIsNone(third["next_cursor"])
        with self.assertRaisesRegex(ValueError, "游标"):
            self.store.history(
                provider_id="provider-a",
                window="all",
                cursor="invalid",
                now=now,
            )

    def test_recent_sessions_uses_latest_request_name_and_activity(self) -> None:
        now = 2_000_000.0
        self.store.record_request(
            started_at=now - 20,
            finished_at=now - 10,
            provider_id="provider-a",
            thread_id="thread-a",
            session_name="old-service",
            model="gpt-5",
            status_code=200,
            successful=True,
            outcome="succeeded",
            retry_count=0,
        )
        self.store.record_request(
            started_at=now - 5,
            finished_at=now - 1,
            provider_id="provider-a",
            thread_id="thread-a",
            session_name="latest-service",
            model="gpt-5",
            status_code=200,
            successful=True,
            outcome="succeeded",
            retry_count=0,
        )
        self.store.record_request(
            started_at=now - 3,
            finished_at=now - 2,
            provider_id="provider-b",
            thread_id="thread-b",
            session_name="another-session",
            model="gpt-5",
            status_code=200,
            successful=True,
            outcome="succeeded",
            retry_count=0,
        )

        sessions = self.store.recent_sessions(now - 24 * 3600)

        self.assertEqual(
            [(item["thread_id"], item["name"]) for item in sessions],
            [("thread-a", "latest-service"), ("thread-b", "another-session")],
        )
        self.assertEqual(sessions[0]["updated_at"], now - 1)

    def test_existing_usage_database_adds_success_marker(self) -> None:
        old_path = Path(self.temp_context.name) / "old-usage.sqlite3"
        now = time.time()
        with closing(sqlite3.connect(old_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE request_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL,
                    provider_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cached_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    usage_source TEXT NOT NULL,
                    estimate_method TEXT,
                    status_code INTEGER NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO request_usage (
                    recorded_at, provider_id, model, input_tokens, output_tokens,
                    total_tokens, cached_tokens, reasoning_tokens, usage_source,
                    estimate_method, status_code
                ) VALUES (?, 'provider-a', 'gpt-5.6-sol', 10, 2, 12, 0, 0,
                          'upstream', NULL, 200)
                """,
                (now,),
            )

        migrated = UsageStore(old_path)
        history = migrated.history(provider_id="provider-a", window="all", now=now)

        self.assertEqual(history["total_count"], 1)
        with closing(sqlite3.connect(old_path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(request_usage)")
            }
            succeeded = connection.execute(
                "SELECT succeeded FROM request_usage"
            ).fetchone()[0]
        self.assertIn("succeeded", columns)
        self.assertEqual(succeeded, 1)

    def test_usage_database_never_stores_request_or_response_content(self) -> None:
        self.store.record(
            provider_id="provider-a",
            model="gpt-5",
            usage=TokenUsage(1, 2, 3),
            status_code=200,
        )
        connection = sqlite3.connect(self.store.path)
        try:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(request_usage)")
            }
        finally:
            connection.close()

        self.assertNotIn("request_body", columns)
        self.assertNotIn("response_body", columns)
        self.assertNotIn("api_key", columns)

    def test_local_request_history_keeps_seven_days_without_duplicating_usage(self) -> None:
        now = 2_000_000.0
        usage = TokenUsage(12, 3, 15, cached_tokens=4)
        usage_id = self.store.record(
            provider_id="provider-a",
            model="gpt-5.6-sol",
            usage=usage,
            status_code=200,
            recorded_at=now,
            successful=False,
        )
        self.store.record_request(
            started_at=now - 4,
            finished_at=now,
            provider_id="provider-a",
            thread_id="thread-fixture",
            session_name="Codex 服务可用检测",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            status_code=200,
            successful=False,
            outcome="failed",
            retry_count=2,
            error_kind="stream_interrupted",
            error_summary='Authorization: Bearer fixture-private-token',
            usage=usage,
            usage_id=usage_id,
        )

        history = self.store.request_history(
            window="24h",
            status="failed",
            query="Codex 服务",
            now=now + 1,
        )

        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["retry_count"], 2)
        self.assertEqual(history["items"][0]["total_tokens"], 15)
        self.assertEqual(history["items"][0]["reasoning_effort"], "high")
        self.assertNotIn("fixture-private-token", history["items"][0]["error_summary"])
        with closing(sqlite3.connect(self.store.path)) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(request_history)")
            }
        self.assertNotIn("request_body", columns)


class SSEPreflightTests(unittest.IsolatedAsyncioTestCase):
    def test_terminal_capture_requires_a_complete_protocol_event(self) -> None:
        capture = SSETerminalCapture()

        self.assertIsNone(
            capture.feed(
                b'data: {"type":"response.output_text.delta",'
                b'"delta":"literal response.completed text"}\n\n'
                b'data: {"type":"response.com'
            )
        )
        self.assertEqual(
            capture.feed(b'pleted","response":{"status":"completed"}}\r\n\r\n'),
            "response.completed",
        )
        self.assertEqual(capture.terminal_event, "response.completed")

    def test_terminal_capture_recognizes_done_and_failure_events(self) -> None:
        done = SSETerminalCapture()
        failed = SSETerminalCapture()

        self.assertEqual(done.feed(b"data: [DONE]\n\n"), "[DONE]")
        self.assertEqual(
            failed.feed(
                b'event: response.failed\ndata: {"response":{"status":"failed"}}\n\n'
            ),
            "response.failed",
        )

    def test_capacity_capture_respects_bare_cr_event_boundaries(self) -> None:
        separate_events = SSECapacityFailureCapture()
        self.assertIsNone(
            separate_events.feed(
                b'data: {"error":"permanent"}\r\r'
                b'data: {"code":"model_capacity"}\r\r'
            )
        )

        same_event = SSECapacityFailureCapture()
        failure = same_event.feed(
            b'data: {"error":"temporary"}\r'
            b'data: {"code":"model_capacity"}\r\r'
        )
        self.assertIsNotNone(failure)
        self.assertEqual(failure[0], "model_capacity")

    async def test_many_reasoning_events_are_inspected_once(self) -> None:
        reasoning_events = [
            (
                b'data: {"type":"response.reasoning_summary_text.delta",'
                + f'"delta":"step-{index}"'.encode()
                + b"}\n\n"
            )
            for index in range(2000)
        ]
        visible_event = (
            b'data: {"type":"response.output_text.delta","delta":"done"}\n\n'
        )
        chunks = iter((*reasoning_events[1:], visible_event))

        async def stream() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        inspected_bytes = 0
        decision_calls = 0

        def decision(
            event: bytes,
            *,
            end_of_stream: bool = False,
        ) -> tuple[str, str | None, str | None]:
            nonlocal inspected_bytes, decision_calls
            inspected_bytes += len(event)
            decision_calls += 1
            return _sse_preflight_decision(event, end_of_stream=end_of_stream)

        buffered, retry_kind, _ = await _inspect_sse_before_output(
            reasoning_events[0],
            stream(),
            decision=decision,
        )

        total_bytes = sum(map(len, reasoning_events)) + len(visible_event)
        self.assertIsNone(retry_kind)
        self.assertIsNotNone(buffered)
        self.assertIn(b'"delta":"done"', buffered)
        self.assertEqual(decision_calls, 2001)
        self.assertLessEqual(inspected_bytes, total_bytes + decision_calls * 2)

    async def test_cross_chunk_crlf_and_bare_cr_events_are_incremental(self) -> None:
        chunks = (
            b'\n\r\ndata: {"type":"response.reasoning_summary_text.delta",',
            b'"delta":"thinking"}\r\rdata: {"type":"response.output_text.delta",',
            b'"delta":"visible"}\r\r',
        )

        async def stream() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        first = b'data: {"type":"response.created"}\r'
        buffered, retry_kind, _ = await _inspect_sse_before_output(first, stream())

        self.assertIsNone(retry_kind)
        self.assertIsNotNone(buffered)
        self.assertIn(b'"delta":"visible"', buffered)

    async def test_failure_before_visible_output_retries_but_later_failure_commits(self) -> None:
        failure = (
            b'data: {"type":"response.failed","response":{"status":"failed",'
            b'"error":{"code":"upstream_error","message":"try again later"}}}\n\n'
        )
        visible = (
            b'data: {"type":"response.output_text.delta","delta":"visible"}\n\n'
        )

        async def no_more() -> AsyncIterator[bytes]:
            if False:
                yield b""

        buffered, retry_kind, _ = await _inspect_sse_before_output(
            failure + visible,
            no_more(),
        )
        self.assertIsNone(buffered)
        self.assertEqual(retry_kind, "upstream_error")

        buffered, retry_kind, _ = await _inspect_sse_before_output(
            visible + failure,
            no_more(),
        )
        self.assertIsNotNone(buffered)
        self.assertIsNone(retry_kind)

    async def test_end_of_stream_decision_receives_only_unfinished_event(self) -> None:
        calls: list[tuple[bytes, bool]] = []

        def decision(
            event: bytes,
            *,
            end_of_stream: bool = False,
        ) -> tuple[str, str | None, str | None]:
            calls.append((event, end_of_stream))
            return "wait", None, None

        async def no_more() -> AsyncIterator[bytes]:
            if False:
                yield b""

        first = b"data: first\n\ndata: unfinished"
        buffered, retry_kind, _ = await _inspect_sse_before_output(
            first,
            no_more(),
            decision=decision,
        )

        self.assertEqual(buffered, first)
        self.assertIsNone(retry_kind)
        self.assertEqual(calls, [(b"data: first\n\n", False), (b"data: unfinished", True)])


class RecoveryHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_context.cleanup)
        self.path = Path(self.temp_context.name) / "usage.sqlite3"
        self.store = RecoveryHistoryStore(self.path)

    def test_history_persists_only_recent_24_hours_and_sanitizes_summary(self) -> None:
        now = time.time()
        self.store.record(
            request_id=1,
            provider_id="expired",
            attempt=1,
            max_attempts=4,
            delay_seconds=1,
            kind="connection",
            summary="expired",
            stage="before_output",
            outcome="retrying",
            recorded_at=now - 25 * 3600,
        )
        self.store.record(
            request_id=2,
            provider_id="provider-a",
            attempt=2,
            max_attempts=-1,
            delay_seconds=2,
            kind="model_capacity",
            summary='Authorization: Bearer fixture-private-token',
            stage="before_output",
            outcome="retrying",
            recorded_at=now - 2,
            request_started_at=now - 120,
        )
        self.store.record(
            request_id=3,
            provider_id="provider-b",
            attempt=3,
            max_attempts=4,
            delay_seconds=None,
            kind="stream_interrupted",
            summary="stream disconnected",
            stage="after_output",
            outcome="passed_through",
            recorded_at=now - 1,
        )

        reopened = RecoveryHistoryStore(self.path)
        history = reopened.history(now=now)

        self.assertEqual(history["window_hours"], 24)
        self.assertEqual(history["total_count"], 2)
        self.assertFalse(history["truncated"])
        self.assertEqual(
            [item["provider_id"] for item in history["items"]],
            ["provider-b", "provider-a"],
        )
        self.assertEqual(history["items"][0]["stage"], "after_output")
        self.assertIsNone(history["items"][0]["request_started_at"])
        self.assertEqual(
            history["items"][1]["request_started_at"],
            round((now - 120) * 1000),
        )
        self.assertIn("[已隐藏]", history["items"][1]["summary"])
        self.assertNotIn("fixture-private-token", str(history))

        with closing(sqlite3.connect(self.path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(recovery_events)")
            }
            stored_count = connection.execute(
                "SELECT COUNT(*) FROM recovery_events"
            ).fetchone()[0]
        self.assertEqual(stored_count, 2)
        self.assertIn("request_started_at", columns)
        self.assertNotIn("request_body", columns)
        self.assertNotIn("response_body", columns)
        self.assertNotIn("api_key", columns)

    def test_existing_database_is_migrated_without_inventing_start_times(self) -> None:
        old_path = Path(self.temp_context.name) / "old-recovery.sqlite3"
        now = time.time()
        with closing(sqlite3.connect(old_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE recovery_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL,
                    request_id INTEGER NOT NULL,
                    provider_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    delay_seconds REAL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO recovery_events (
                    recorded_at, request_id, provider_id, attempt, max_attempts,
                    delay_seconds, kind, summary, stage, outcome
                ) VALUES (?, 1, 'provider-a', 1, 4, 1, 'connection',
                          'temporary failure', 'before_output', 'retrying')
                """,
                (now,),
            )

        migrated = RecoveryHistoryStore(old_path)
        history = migrated.history(now=now + 1)

        self.assertEqual(history["total_count"], 1)
        self.assertIsNone(history["items"][0]["request_started_at"])
        with closing(sqlite3.connect(old_path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(recovery_events)")
            }
        self.assertIn("request_started_at", columns)

    def test_history_uses_cursor_pagination(self) -> None:
        now = time.time()
        for request_id in range(1, 4):
            self.store.record(
                request_id=request_id,
                provider_id="provider-a",
                attempt=request_id,
                max_attempts=4,
                delay_seconds=1,
                kind="connection",
                summary=f"failure {request_id}",
                stage="before_output",
                outcome="retrying",
                recorded_at=now - (3 - request_id),
            )

        first = self.store.history(now=now, limit=2)
        second = self.store.history(
            now=now,
            limit=2,
            cursor=first["next_cursor"],
        )

        self.assertEqual(first["total_count"], 3)
        self.assertEqual(
            [item["request_id"] for item in first["items"]],
            [3, 2],
        )
        self.assertTrue(first["truncated"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(
            [item["request_id"] for item in second["items"]],
            [1],
        )
        self.assertFalse(second["truncated"])
        self.assertIsNone(second["next_cursor"])
        with self.assertRaisesRegex(ValueError, "游标"):
            self.store.history(now=now, cursor="invalid")


class CCSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_context.cleanup)
        self.database = Path(self.temp_context.name) / "cc-switch.db"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                is_current INTEGER NOT NULL,
                settings_config TEXT NOT NULL,
                meta TEXT,
                app_type TEXT NOT NULL,
                sort_index INTEGER,
                created_at TEXT
            );
            CREATE TABLE provider_endpoints (
                provider_id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                url TEXT
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        payload = {
            "config": """
model_provider = "custom"
[model_providers.custom]
base_url = "https://upstream.example.test/v1/"
env_key = "UPSTREAM_KEY"
wire_api = "responses"
[model_providers.custom.http_headers]
X-Client = "codex-local-proxy"
[model_providers.custom.env_http_headers]
X-Extra-Auth = "EXTRA_AUTH"
[model_providers.custom.query_params]
api-version = "2026-07-01"
""",
            "auth": {
                "UPSTREAM_KEY": "fixture-primary-credential",
                "EXTRA_AUTH": "fixture-extra-credential",
            },
        }
        connection.execute(
            """INSERT INTO providers
               (id, name, is_current, settings_config, meta, app_type, sort_index, created_at)
               VALUES (?, ?, 1, ?, '{}', 'codex', 1, '2026-07-27')""",
            ("fixture", "Fixture", json.dumps(payload)),
        )
        connection.execute(
            "INSERT INTO provider_endpoints VALUES (?, 'codex', ?)",
            ("fixture", "https://fallback.example.test/v1"),
        )
        connection.execute(
            "INSERT INTO settings VALUES ('common_config_codex', '')"
        )
        connection.commit()
        connection.close()

    def test_loads_effective_provider_from_read_only_database(self) -> None:
        providers = load_proxy_providers(self.database)

        self.assertEqual(len(providers), 1)
        loaded = providers[0]
        self.assertEqual(loaded.name, "Fixture")
        self.assertEqual(loaded.base_url, "https://upstream.example.test/v1")
        self.assertTrue(loaded.has_credentials)
        self.assertEqual(loaded.configured_headers["X-Client"], "codex-local-proxy")
        self.assertIn("X-Extra-Auth", loaded.configured_headers)
        self.assertEqual(loaded.default_query, {"api-version": "2026-07-01"})


class InputItemIdCompatibilityTests(unittest.TestCase):
    def test_store_is_scoped_to_session_and_provider(self) -> None:
        now = [100.0]
        store = InputItemIdCompatibilityStore(
            ttl_seconds=10,
            clock=lambda: now[0],
        )

        store.remember("thread-a", "provider-a")

        self.assertTrue(store.should_strip("thread-a", "provider-a"))
        self.assertFalse(store.should_strip("thread-a", "provider-b"))
        self.assertFalse(store.should_strip("thread-b", "provider-a"))
        self.assertFalse(store.should_strip(None, "provider-a"))
        now[0] = 111.0
        self.assertFalse(store.should_strip("thread-a", "provider-a"))

    def test_strip_input_item_ids_keeps_references_and_call_ids(self) -> None:
        payload = json.dumps(
            {
                "previous_response_id": "resp_old",
                "input": [
                    {"type": "message", "id": "msg_old", "role": "user"},
                    {"type": "function_call", "id": "fc_old", "call_id": "call_1"},
                    {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
                    {"type": "item_reference", "id": "msg_reference"},
                ],
            },
            ensure_ascii=False,
        ).encode()

        stripped, changed = _strip_input_item_ids(payload)

        self.assertIsNotNone(stripped)
        self.assertEqual(changed, 2)
        root = json.loads(stripped)
        self.assertEqual(root["previous_response_id"], "resp_old")
        self.assertNotIn("id", root["input"][0])
        self.assertNotIn("id", root["input"][1])
        self.assertEqual(root["input"][1]["call_id"], "call_1")
        self.assertEqual(root["input"][2]["call_id"], "call_1")
        self.assertEqual(root["input"][3]["id"], "msg_reference")

    def test_rewrite_request_model_replaces_only_model(self) -> None:
        payload = json.dumps(
            {
                "model": "gpt-5.6-sol",
                "input": [{"type": "message", "role": "user", "content": "hi"}],
            },
            ensure_ascii=False,
        ).encode()

        rewritten = _rewrite_request_model(payload, "deepseek-v4-pro")

        self.assertIsNotNone(rewritten)
        root = json.loads(rewritten)
        self.assertEqual(root["model"], "deepseek-v4-pro")
        self.assertEqual(root["input"][0]["content"], "hi")

    def test_rewrite_request_model_skips_same_missing_and_invalid(self) -> None:
        same_model = json.dumps({"model": "same"}).encode()
        self.assertIsNone(_rewrite_request_model(same_model, "same"))
        without_model = json.dumps({"input": []}).encode()
        self.assertIsNone(_rewrite_request_model(without_model, "other"))
        blank_model = json.dumps({"model": "   "}).encode()
        self.assertIsNone(_rewrite_request_model(blank_model, "other"))
        self.assertIsNone(_rewrite_request_model(b"not-json", "other"))
        self.assertIsNone(_rewrite_request_model(json.dumps({"model": "x"}).encode(), "  "))
        self.assertIsNone(_rewrite_request_model(json.dumps([1, 2]).encode(), "other"))

    def test_error_index_requires_exact_invalid_input_id_shape(self) -> None:
        self.assertEqual(
            _input_item_id_error_index(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": "invalid_value",
                        "param": "input[147].id",
                    }
                }
            ),
            147,
        )
        self.assertIsNone(
            _input_item_id_error_index(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": "invalid_value",
                        "param": "input[147].role",
                    }
                }
            )
        )


class ProxyAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_selects_client_for_the_current_provider_transport(self) -> None:
        attempts: list[tuple[str, str]] = []
        standard = ProxyProvider(
            provider_id="standard",
            name="Standard",
            base_url="https://standard.example.test/v1",
            is_cc_switch_current=True,
            api_key="standard-key",
        )
        compatible = ProxyProvider(
            provider_id="compatible",
            name="Compatible",
            base_url="https://compatible.example.test/v1",
            is_cc_switch_current=False,
            api_key="compatible-key",
            transport="curl_cffi",
        )
        router = ProviderRouter((standard, compatible))

        async def standard_upstream(request: httpx.Request) -> httpx.Response:
            attempts.append(("httpx", request.url.host))
            router.select("compatible")
            return httpx.Response(503, content=b"retry")

        async def compatible_upstream(request: httpx.Request) -> httpx.Response:
            attempts.append(("curl_cffi", request.url.host))
            return httpx.Response(200, content=b"ok")

        standard_client = httpx.AsyncClient(
            transport=httpx.MockTransport(standard_upstream)
        )
        compatible_client = httpx.AsyncClient(
            transport=httpx.MockTransport(compatible_upstream)
        )
        app = create_proxy_app(
            router,
            client=standard_client,
            client_selector=lambda selected: (
                compatible_client
                if selected.transport == "curl_cffi"
                else standard_client
            ),
            retry_policy=RetryPolicy(max_attempts=2, delay_seconds=0),
            retry_sleep=lambda _: _empty_wait(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(standard_client.aclose)
        self.addAsyncCleanup(compatible_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            attempts,
            [
                ("httpx", "standard.example.test"),
                ("curl_cffi", "compatible.example.test"),
            ],
        )

    async def test_invalid_input_item_id_is_repaired_without_normal_retry_policy(self) -> None:
        attempts: list[dict] = []
        thread_id = "thread-item-id-repair"
        body = {
            "model": "test",
            "previous_response_id": "resp_old",
            "input": [
                {"type": "message", "id": "bad_message_id", "role": "user", "content": "hi"},
                {"type": "function_call", "id": "bad_call_id", "call_id": "call_1"},
            ],
        }

        async def upstream(request: httpx.Request) -> httpx.Response:
            parsed = json.loads(request.content)
            attempts.append(parsed)
            if len(attempts) == 1:
                return httpx.Response(
                    400,
                    headers={"content-type": "application/json"},
                    json={
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_value",
                            "param": "input[0].id",
                        }
                    },
                )
            return httpx.Response(200, content=b"recovered")

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(enabled=False),
            input_item_id_compatibility_store=InputItemIdCompatibilityStore(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            headers={
                "x-codex-turn-metadata": json.dumps({"thread_id": thread_id}),
            },
            json=body,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(attempts), 2)
        self.assertIn("id", attempts[0]["input"][0])
        self.assertNotIn("id", attempts[1]["input"][0])
        self.assertIn("id", attempts[1]["input"][1])
        self.assertEqual(attempts[1]["previous_response_id"], "resp_old")
        self.assertEqual(attempts[1]["input"][1]["call_id"], "call_1")

    async def test_provider_model_is_rewritten_for_upstream(self) -> None:
        attempts: list[dict] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            attempts.append(json.loads(request.content))
            return httpx.Response(200, content=b"ok")

        router = ProviderRouter(
            (provider("selected", current=True, model="deepseek-v4-pro"),)
        )
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            json={"model": "gpt-5.6-sol", "input": []},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["model"], "deepseek-v4-pro")

    async def test_provider_model_rewrite_follows_rerouted_provider(self) -> None:
        attempts: list[dict] = []
        router = ProviderRouter(
            (
                provider("primary", current=True, model="gpt-5.6-sol"),
                provider("fallback", model="glm-5.3"),
            )
        )

        async def upstream(request: httpx.Request) -> httpx.Response:
            parsed = json.loads(request.content)
            attempts.append({"url": str(request.url), "model": parsed["model"]})
            if "primary" in str(request.url):
                router.select("fallback")
                return httpx.Response(503, text="temporary")
            return httpx.Response(200, content=b"recovered")

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(enabled=True, max_attempts=3, delay_seconds=0.0),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            json={"model": "codex-default", "input": []},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["model"], "gpt-5.6-sol")
        self.assertEqual(attempts[1]["model"], "glm-5.3")
        self.assertIn("primary", attempts[0]["url"])
        self.assertIn("fallback", attempts[1]["url"])

    async def test_provider_without_model_keeps_passthrough(self) -> None:
        attempts: list[dict] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            attempts.append(json.loads(request.content))
            return httpx.Response(200, content=b"ok")

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            json={"model": "gpt-5.6-sol", "input": []},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts[0]["model"], "gpt-5.6-sol")

    async def test_input_item_id_compatibility_applies_to_following_request(self) -> None:
        seen: list[dict] = []
        thread_id = "thread-item-id-memory"
        compatibility = InputItemIdCompatibilityStore()

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, content=b"ok")

        router = ProviderRouter((provider("selected", current=True),))
        compatibility.remember(thread_id, "selected")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            input_item_id_compatibility_store=compatibility,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            headers={"x-codex-turn-metadata": json.dumps({"thread_id": thread_id})},
            json={
                "model": "test",
                "input": [{"type": "message", "id": "old", "role": "user"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("id", seen[0]["input"][0])

    async def test_item_id_repair_stays_on_the_rejecting_provider(self) -> None:
        seen_hosts: list[str] = []
        router = ProviderRouter(
            (provider("first", current=True), provider("second")),
        )

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen_hosts.append(request.url.host)
            if len(seen_hosts) == 1:
                router.select("second")
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_value",
                            "param": "input[0].id",
                        }
                    },
                )
            return httpx.Response(200, content=b"ok")

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            headers={"x-codex-turn-metadata": json.dumps({"thread_id": "thread-a"})},
            json={
                "model": "test",
                "input": [{"type": "message", "id": "old", "role": "user"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            seen_hosts,
            ["first.example.test", "first.example.test"],
        )

    async def test_item_id_repair_stops_at_strict_limit(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            root = json.loads(request.content)
            bad_index = next(
                index for index, item in enumerate(root["input"]) if "id" in item
            )
            return httpx.Response(
                400,
                json={
                    "error": {
                        "type": "invalid_request_error",
                        "code": "invalid_value",
                        "param": f"input[{bad_index}].id",
                    }
                },
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            headers={"x-codex-turn-metadata": json.dumps({"thread_id": "thread-limit"})},
            json={
                "model": "test",
                "input": [
                    {"type": "message", "id": f"old-{index}", "role": "user"}
                    for index in range(9)
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["param"], "input[8].id")
        self.assertEqual(attempts, 9)

    async def test_item_id_error_is_not_repaired_outside_responses(self) -> None:
        attempts = 0
        error = {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_value",
                "param": "input[0].id",
            }
        }

        async def upstream(_: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(400, json=error)

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "test",
                "input": [{"type": "message", "id": "old", "role": "user"}],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), error)
        self.assertEqual(attempts, 1)

    async def test_persistence_lock_does_not_block_following_upstream_request(self) -> None:
        class LockingUsageStore(UsageStore):
            def __init__(self, path: Path) -> None:
                super().__init__(path)
                self.history_locked = threading.Event()
                self.persistence_waiting = threading.Event()
                self.release_history = threading.Event()

            def request_history(self, **kwargs):
                with self._lock:
                    self.history_locked.set()
                    self.release_history.wait(timeout=2)
                return {
                    "window": kwargs["window"],
                    "start_at": None,
                    "end_at": None,
                    "total_count": 0,
                    "items": [],
                    "next_cursor": None,
                }

            def record(self, **kwargs):
                self.persistence_waiting.set()
                return super().record(**kwargs)

            def record_request(self, **kwargs):
                self.persistence_waiting.set()
                return super().record_request(**kwargs)

        upstream_count = 0
        second_upstream_started = threading.Event()

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal upstream_count
            upstream_count += 1
            if upstream_count == 2:
                second_upstream_started.set()
            return httpx.Response(
                200,
                json={
                    "id": f"response-{upstream_count}",
                    "output": [],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = LockingUsageStore(Path(temp_dir) / "usage.sqlite3")
            upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            app = create_proxy_app(
                ProviderRouter((provider("selected", current=True),)),
                client=upstream_client,
                usage_store=usage_store,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            history_task = asyncio.create_task(client.get("/control/api/requests"))
            first_task = None
            second_task = None
            watchdog = threading.Timer(1, usage_store.release_history.set)
            try:
                self.assertTrue(
                    await asyncio.to_thread(usage_store.history_locked.wait, 1)
                )
                watchdog.start()
                first_task = asyncio.create_task(
                    client.post(
                        "/v1/responses",
                        json={"model": "gpt-test", "input": "first"},
                    )
                )
                self.assertTrue(
                    await asyncio.to_thread(usage_store.persistence_waiting.wait, 2)
                )
                second_task = asyncio.create_task(
                    client.post(
                        "/v1/responses",
                        json={"model": "gpt-test", "input": "second"},
                    )
                )
                self.assertTrue(
                    await asyncio.to_thread(second_upstream_started.wait, 2)
                )
                self.assertFalse(usage_store.release_history.is_set())
            finally:
                usage_store.release_history.set()
                watchdog.cancel()
                tasks = [task for task in (history_task, first_task, second_task) if task]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                await client.aclose()
                await upstream_client.aclose()

        self.assertTrue(all(isinstance(result, httpx.Response) for result in results))
        self.assertTrue(all(result.status_code == 200 for result in results))

    async def test_request_api_hides_thread_id_and_persists_session_route(self) -> None:
        seen_hosts: list[str] = []
        seen_models: list[str] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen_hosts.append(request.url.host)
            seen_models.append(json.loads(await request.aread())["model"])
            return httpx.Response(
                200,
                json={
                    "id": "response-fixture",
                    "output": [],
                    "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                },
            )

        thread_id = "019fa83f-2a11-73b0-a862-4d51679219ef"
        metadata = json.dumps({"thread_id": thread_id})
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            router = ProviderRouter(
                (
                    provider(
                        "first",
                        current=True,
                        model_mappings={"gpt-test": "first-upstream"},
                    ),
                    provider(
                        "second",
                        model_mappings={"gpt-test": "second-upstream"},
                    ),
                )
            )
            upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            app = create_proxy_app(
                router,
                client=upstream_client,
                usage_store=usage_store,
                session_name_resolver=lambda requested: {
                    item: "请求列表测试" for item in requested
                },
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            try:
                first = await client.post(
                    "/v1/responses",
                    headers={"x-codex-turn-metadata": metadata},
                    json={"model": "gpt-test", "input": "hello"},
                )
                requests = await client.get("/control/api/requests")
                item = requests.json()["items"][0]
                routed = await client.post(
                    f"/control/api/session-routes/{item['session_key']}",
                    headers={**{"X-Local-Proxy-Control": "1"}},
                    json={"provider_id": "second"},
                )
                second = await client.post(
                    "/v1/responses",
                    headers={"x-codex-turn-metadata": metadata},
                    json={"model": "gpt-test", "input": "again"},
                )
                history = await client.get("/control/api/requests")
            finally:
                await client.aclose()
                await upstream_client.aclose()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(routed.status_code, 200)
        self.assertEqual(seen_hosts, ["first.example.test", "second.example.test"])
        self.assertEqual(seen_models, ["first-upstream", "second-upstream"])
        self.assertEqual(item["session_name"], "请求列表测试")
        self.assertEqual(item["model"], "gpt-test")
        self.assertEqual(item["upstream_model"], "first-upstream")
        self.assertEqual(item["total_tokens"], 6)
        self.assertNotIn(thread_id, requests.text)
        self.assertEqual(
            [entry["provider_name"] for entry in history.json()["items"]],
            ["Second", "First"],
        )
        self.assertEqual(
            {entry["model"] for entry in history.json()["items"]},
            {"gpt-test"},
        )
        self.assertEqual(
            [entry["upstream_model"] for entry in history.json()["items"]],
            ["second-upstream", "first-upstream"],
        )
        self.assertEqual(
            {entry["route_provider_id"] for entry in history.json()["items"]},
            {"second"},
        )

    async def test_sessions_api_lists_active_recent_sessions_without_thread_ids(self) -> None:
        active_thread = "thread-active"
        recent_thread = "thread-recent"
        catalog_since: list[float] = []
        now = time.time()

        def session_catalog(since: float):
            catalog_since.append(since)
            return (
                {
                    "thread_id": recent_thread,
                    "name": "最近会话",
                    "updated_at": now - 3600,
                },
            )

        router = ProviderRouter((provider("first", current=True), provider("second")))
        router.set_session_provider_override(active_thread, "second")
        active_request = router.begin_request(thread_id=active_thread)
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            upstream_client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200))
            )
            app = create_proxy_app(
                router,
                client=upstream_client,
                usage_store=usage_store,
                session_name_resolver=lambda requested: {
                    item: "当前活动会话" for item in requested if item == active_thread
                },
                session_catalog=session_catalog,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            try:
                response = await client.get("/control/api/sessions")
            finally:
                await client.aclose()
                await upstream_client.aclose()
                router.finish_request(active_request, status_code=200)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["window_days"], 7)
        self.assertGreaterEqual(catalog_since[0], now - 7 * 24 * 3600 - 2)
        self.assertEqual(
            [item["name"] for item in payload["items"]],
            ["当前活动会话", "最近会话"],
        )
        self.assertTrue(payload["items"][0]["active"])
        self.assertEqual(payload["items"][0]["route_provider_id"], "second")
        self.assertTrue(all(len(item["session_key"]) == 24 for item in payload["items"]))
        self.assertNotIn(active_thread, response.text)
        self.assertNotIn(recent_thread, response.text)

    async def test_protocol_adapter_replaces_claude_placeholder_auth(self) -> None:
        seen: list[httpx.Request] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"type": "message", "usage": {"input_tokens": 1, "output_tokens": 1}},
            )

        selected = provider("selected", current=True)
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((selected,)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post(
                "/v1/messages",
                headers={
                    "x-api-key": "local-placeholder",
                    "anthropic-version": "2023-06-01",
                },
                json={"model": "claude-test", "messages": []},
            )
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen[0].url.path, "/v1/messages")
        self.assertEqual(seen[0].headers["x-api-key"], "test-upstream-credential")
        self.assertNotIn("authorization", seen[0].headers)

    async def test_claude_protocol_retries_http_529(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(529, json={"error": {"type": "overloaded_error"}})
            return httpx.Response(200, json={"type": "message", "content": []})

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=lambda _: _empty_wait(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post(
                "/v1/messages",
                json={"model": "claude-test", "messages": []},
            )
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)

    async def test_stream_usage_is_persisted_for_final_provider(self) -> None:
        class UsageStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                yield (
                    b'data: {"type":"response.completed","response":{"usage":'
                    b'{"input_tokens":12,"output_tokens":3,"total_tokens":15}}}\n\n'
                )

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=UsageStream(),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            app = create_proxy_app(
                ProviderRouter((provider("selected", current=True),)),
                client=upstream_client,
                usage_store=usage_store,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            )
            try:
                response = await client.post(
                    "/v1/responses",
                    json={"model": "gpt-5", "input": "hello"},
                )
                status = (
                    await client.get("/control/api/status?usage_window=all")
                ).json()
            finally:
                await client.aclose()
                await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(status["usage"]["total"]["total_tokens"], 15)
        self.assertEqual(status["usage"]["total"]["estimated_requests"], 0)
        self.assertEqual(
            status["usage"]["by_provider"]["selected"]["input_tokens"], 12
        )
        self.assertIsNotNone(
            status["usage"]["by_provider"]["selected"]["last_success_at"]
        )

    async def test_usage_history_endpoint_returns_only_selected_provider(self) -> None:
        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        usage_store.record(
            provider_id="selected",
            model="gpt-5.6-sol",
            usage=TokenUsage(12, 3, 15, cached_tokens=8),
            status_code=200,
        )
        usage_store.record(
            provider_id="selected",
            model="gpt-5.6-sol",
            usage=TokenUsage(18, 2, 20, cached_tokens=12),
            status_code=200,
            successful=False,
        )
        usage_store.record(
            provider_id="other",
            model="gpt-5.6-sol",
            usage=TokenUsage(20, 5, 25),
            status_code=200,
        )
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True), provider("other"))),
            client=upstream_client,
            usage_store=usage_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.get(
            "/control/api/usage-history",
            params={"provider_id": "selected", "usage_window": "all"},
        )
        missing = await client.get(
            "/control/api/usage-history",
            params={"provider_id": "missing", "usage_window": "all"},
        )
        invalid_cursor = await client.get(
            "/control/api/usage-history",
            params={
                "provider_id": "selected",
                "usage_window": "all",
                "cursor": "invalid",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["total_count"], 2)
        self.assertEqual(response.json()["items"][0]["total_tokens"], 20)
        self.assertEqual(response.json()["items"][0]["cached_tokens"], 12)
        self.assertFalse(response.json()["items"][0]["succeeded"])
        self.assertEqual(response.json()["items"][1]["total_tokens"], 15)
        self.assertTrue(response.json()["items"][1]["succeeded"])
        self.assertEqual(response.json()["total"]["total_tokens"], 35)
        self.assertEqual(response.json()["total"]["successful_tokens"], 15)
        self.assertEqual(response.json()["total"]["failed_tokens"], 20)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(invalid_cursor.status_code, 422)

    async def test_status_summarizes_history_and_detail_endpoint_returns_all(self) -> None:
        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        history_store = RecoveryHistoryStore(
            Path(temp_context.name) / "usage.sqlite3"
        )
        now = time.time()
        for index in range(3):
            history_store.record(
                request_id=index + 1,
                provider_id="selected",
                attempt=index + 1,
                max_attempts=4,
                delay_seconds=1,
                kind="connection",
                summary=f"failure {index + 1}",
                stage="before_output",
                outcome="retrying",
                recorded_at=now - index,
            )
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            recovery_history_store=history_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        status = (await client.get("/control/api/status")).json()
        detail_response = await client.get(
            "/control/api/recovery-history",
            params={"limit": 2},
        )
        detail = detail_response.json()
        older = (
            await client.get(
                "/control/api/recovery-history",
                params={"limit": 2, "cursor": detail["next_cursor"]},
            )
        ).json()

        self.assertEqual(status["retry"]["history"]["total_count"], 3)
        self.assertEqual(len(status["retry"]["history"]["items"]), 1)
        self.assertTrue(status["retry"]["history"]["truncated"])
        self.assertEqual(detail["total_count"], 3)
        self.assertEqual(len(detail["items"]), 2)
        self.assertTrue(detail["truncated"])
        self.assertIsNotNone(detail["next_cursor"])
        self.assertEqual(len(older["items"]), 1)
        self.assertFalse(older["truncated"])
        self.assertIsNone(older["next_cursor"])

    async def test_provider_visibility_and_order_control_api(self) -> None:
        hidden_changes: list[tuple[str, ...]] = []
        order_changes: list[tuple[str, ...]] = []
        router = ProviderRouter((provider("first", current=True), provider("second")))
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(
            router,
            client=upstream_client,
            on_hidden_provider_ids_changed=hidden_changes.append,
            on_provider_order_changed=order_changes.append,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)
        headers = {"X-Local-Proxy-Control": "1"}

        current_hidden = await client.post(
            "/control/api/providers/first/visibility",
            headers=headers,
            json={"hidden": True},
        )
        hidden = await client.post(
            "/control/api/providers/second/visibility",
            headers=headers,
            json={"hidden": True},
        )
        reordered = await client.post(
            "/control/api/providers/order",
            headers=headers,
            json={"provider_ids": ["second", "first"]},
        )

        self.assertEqual(current_hidden.status_code, 409)
        self.assertTrue(hidden.json()["providers"][1]["hidden"])
        self.assertEqual(
            [item["provider_id"] for item in reordered.json()["providers"]],
            ["second", "first"],
        )
        self.assertEqual(hidden_changes, [("second",)])
        self.assertEqual(order_changes, [("second", "first")])

    async def test_infinite_retry_mode_recovers_without_fixed_attempt_limit(self) -> None:
        attempts = 0
        observed_models: list[str] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            observed_models.append(json.loads(await request.aread())["model"])
            if attempts <= 6:
                return httpx.Response(503, content=b"temporary")
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter(
                (
                    provider(
                        "selected",
                        current=True,
                        model_mappings={"test": "upstream-test"},
                    ),
                )
            ),
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 7)
        self.assertEqual(observed_models, ["upstream-test"] * 7)

    async def test_model_mapping_is_not_applied_outside_responses_requests(self) -> None:
        observed_body = b""

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal observed_body
            observed_body = await request.aread()
            return httpx.Response(200, content=b"ok")

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter(
                (
                    provider(
                        "selected",
                        current=True,
                        model_mappings={"test": "upstream-test"},
                    ),
                )
            ),
            client=upstream_client,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/chat/completions",
            content=b'{"model":"test"}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed_body, b'{"model":"test"}')

    async def test_failed_retry_is_transparently_taken_over_by_new_provider(self) -> None:
        observed: list[dict[str, object]] = []
        first = ProxyProvider(
            provider_id="first",
            name="First",
            base_url="https://first.example.test/v1",
            is_cc_switch_current=True,
            api_key="first-upstream-key",
            configured_headers={"X-Provider-Route": "first"},
            default_query={"provider": "first"},
            model_mappings={"test": "first-upstream"},
        )
        second = ProxyProvider(
            provider_id="second",
            name="Second",
            base_url="https://second.example.test/v1",
            is_cc_switch_current=False,
            api_key="second-upstream-key",
            configured_headers={"X-Provider-Route": "second"},
            default_query={"provider": "second"},
            model_mappings={"test": "second-upstream"},
        )
        router = ProviderRouter((first, second))

        async def upstream(request: httpx.Request) -> httpx.Response:
            observed.append(
                {
                    "url": str(request.url),
                    "authorization": request.headers.get("authorization"),
                    "route": request.headers.get("x-provider-route"),
                    "body": await request.aread(),
                }
            )
            if request.url.host == "first.example.test":
                router.select("second")
                return httpx.Response(
                    503,
                    json={"error": {"message": "no available channel"}},
                )
            return httpx.Response(200, content=b"recovered by second")

        sleeps: list[float] = []

        async def no_wait(delay: float) -> None:
            sleeps.append(delay)

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1, delay_seconds=2, strategy="fixed"),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses?client=value",
            headers={"Authorization": "Bearer local-placeholder"},
            content=b'{"model":"test"}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered by second")
        self.assertEqual(sleeps, [0.0])
        self.assertEqual(
            [item["url"] for item in observed],
            [
                "https://first.example.test/v1/responses?client=value&provider=first",
                "https://second.example.test/v1/responses?client=value&provider=second",
            ],
        )
        self.assertEqual(
            [item["authorization"] for item in observed],
            ["Bearer first-upstream-key", "Bearer second-upstream-key"],
        )
        self.assertEqual([item["route"] for item in observed], ["first", "second"])
        self.assertEqual(
            [item["body"] for item in observed],
            [b'{"model":"first-upstream"}', b'{"model":"second-upstream"}'],
        )
        status = router.status()
        self.assertEqual(status.active_by_provider, {})
        self.assertEqual(status.recent_retry_errors[0].provider_id, "first")

    async def test_retry_to_unmapped_provider_clears_upstream_model(self) -> None:
        observed_models: list[str] = []
        second_upstream_started = asyncio.Event()
        release_second_upstream = asyncio.Event()
        router = ProviderRouter(
            (
                provider(
                    "first",
                    current=True,
                    model_mappings={"test": "first-upstream"},
                ),
                provider("second"),
            )
        )

        async def upstream(request: httpx.Request) -> httpx.Response:
            observed_models.append(json.loads(await request.aread())["model"])
            if request.url.host == "first.example.test":
                router.select("second")
                return httpx.Response(
                    503,
                    json={"error": {"message": "no available channel"}},
                )
            second_upstream_started.set()
            await release_second_upstream.wait()
            return httpx.Response(
                200,
                json={
                    "id": "response-fixture",
                    "output": [],
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                },
            )

        async def no_wait(_: float) -> None:
            return None

        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            upstream_client = httpx.AsyncClient(
                transport=httpx.MockTransport(upstream)
            )
            app = create_proxy_app(
                router,
                client=upstream_client,
                usage_store=usage_store,
                retry_policy=RetryPolicy(max_attempts=2),
                retry_sleep=no_wait,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            request_task = asyncio.create_task(
                client.post("/v1/responses", json={"model": "test"})
            )
            try:
                await asyncio.wait_for(second_upstream_started.wait(), timeout=2)
                running = await client.get(
                    "/control/api/requests",
                    params={"status": "running"},
                )
                running_item = running.json()["active"][0]
                self.assertEqual(running_item["provider_id"], "second")
                self.assertIsNone(running_item["upstream_model"])

                release_second_upstream.set()
                response = await request_task
                history = await client.get("/control/api/requests")
            finally:
                release_second_upstream.set()
                await asyncio.gather(request_task, return_exceptions=True)
                await client.aclose()
                await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed_models, ["first-upstream", "test"])
        self.assertEqual(history.json()["items"][0]["provider_id"], "second")
        self.assertIsNone(history.json()["items"][0]["upstream_model"])

    async def test_disabled_retry_passes_upstream_error_through(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, content=b"upstream unavailable")

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            retry_policy=RetryPolicy(enabled=False),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"upstream unavailable")
        self.assertEqual(attempts, 1)

    async def test_http_403_is_retried_before_reaching_client(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(403, content=b"temporary forbidden")
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().last_retry_kind, "http_403")

    async def test_http_403_exhausts_configured_attempts(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, content=b"still forbidden")

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().total_retries, 1)
        self.assertEqual(router.status().last_error, "http_403")

    async def test_http_402_is_retried_with_upstream_error_summary(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    402,
                    headers={"content-type": "application/json"},
                    content=json.dumps(
                        {
                            "error": {
                                "message": "Budget pool quota has been exhausted"
                            }
                        }
                    ).encode(),
                )
            return httpx.Response(200, content=b"recovered")

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=lambda _: _empty_wait(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        status = router.status()
        self.assertEqual(status.last_retry_kind, "http_402")
        self.assertIn(
            "Budget pool quota has been exhausted",
            status.recent_retry_errors[0].summary,
        )

    async def test_http_402_retries_indefinitely_until_provider_recovers(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts <= 5:
                return httpx.Response(402, content=b"quota exhausted")
            return httpx.Response(200, content=b"recovered")

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=lambda _: _empty_wait(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 6)
        self.assertEqual(router.status().total_retries, 5)

    async def test_retry_policy_control_api_validates_updates_and_hides_secrets(self) -> None:
        changed: list[RetryPolicy] = []
        store = RetryPolicyStore()
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True, api_key="fixture-secret"),)),
            client=upstream_client,
            retry_policy_store=store,
            on_retry_policy_changed=changed.append,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)
        payload = {
            "enabled": True,
            "max_attempts": -1,
            "delay_seconds": 2,
            "strategy": "fixed",
            "max_delay_seconds": 30,
            "circuit_failure_threshold": 5,
            "circuit_cooldown_seconds": 60,
        }

        forbidden = await client.post("/control/api/retry-policy", json=payload)
        invalid = await client.post(
            "/control/api/retry-policy",
            headers={"X-Local-Proxy-Control": "1"},
            json={**payload, "max_attempts": 0},
        )
        updated = await client.post(
            "/control/api/retry-policy",
            headers={"X-Local-Proxy-Control": "1"},
            json=payload,
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["retry"]["max_attempts"], -1)
        self.assertEqual(store.get().strategy, "fixed")
        self.assertEqual(changed, [store.get()])
        self.assertNotIn("fixture-secret", updated.text)

    async def test_runtime_settings_api_validates_updates_and_refreshes_health_url(self) -> None:
        runtime = {
            "configured_port": 17890,
            "active_port": 17890,
            "restart_required": False,
            "database_path": "~/.cc-switch/cc-switch.db",
            "health_status_url": None,
            "data_directory": "~/.codex-local-proxy",
            "codex_config_file": "~/.codex/config.toml",
        }
        changed: list[dict[str, object]] = []
        health_store = HealthStatusUrlStore()

        def snapshot() -> dict[str, object]:
            return dict(runtime)

        def update(payload: dict[str, object]) -> dict[str, object]:
            if payload.get("port") == 80:
                raise ValueError("端口无效")
            changed.append(dict(payload))
            runtime["configured_port"] = payload["port"]
            runtime["restart_required"] = payload["port"] != runtime["active_port"]
            runtime["database_path"] = payload["database_path"]
            runtime["health_status_url"] = payload["health_status_url"]
            health_store.replace(payload["health_status_url"])
            return snapshot()

        def validate_database(database_path: str) -> dict[str, object]:
            if database_path == "missing.db":
                raise ValueError("未找到数据库")
            return {
                "database_path": database_path,
                "provider_count": 2,
                "current_provider_configured": True,
            }

        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            health_status_url_store=health_store,
            runtime_settings_snapshot=snapshot,
            on_runtime_settings_changed=update,
            validate_runtime_database=validate_database,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)
        payload = {
            "port": 18888,
            "database_path": "~/.cc-switch/alternate.db",
            "health_status_url": "https://status.example.test/api/status",
        }

        current = await client.get("/control/api/runtime-settings")
        forbidden = await client.post("/control/api/runtime-settings", json=payload)
        invalid = await client.post(
            "/control/api/runtime-settings",
            headers={"X-Local-Proxy-Control": "1"},
            json={**payload, "port": 80},
        )
        invalid_database = await client.post(
            "/control/api/runtime-settings/validate-database",
            headers={"X-Local-Proxy-Control": "1"},
            json={"database_path": "missing.db"},
        )
        valid_database = await client.post(
            "/control/api/runtime-settings/validate-database",
            headers={"X-Local-Proxy-Control": "1"},
            json={"database_path": payload["database_path"]},
        )
        updated = await client.post(
            "/control/api/runtime-settings",
            headers={"X-Local-Proxy-Control": "1"},
            json=payload,
        )
        status = await client.get("/control/api/status")

        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.headers["cache-control"], "no-store")
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid_database.status_code, 422)
        self.assertEqual(valid_database.json()["provider_count"], 2)
        self.assertTrue(updated.json()["restart_required"])
        self.assertEqual(changed, [payload])
        self.assertEqual(
            status.json()["health_status_url"],
            "https://status.example.test/api/status",
        )

    async def test_retries_retryable_status_before_returning_response(self) -> None:
        attempts = 0
        sleeps: list[float] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(
                    503,
                    json={"error": {"message": "temporary upstream overload"}},
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(delay: float) -> None:
            sleeps.append(delay)

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client, retry_sleep=no_wait)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})
        status = (await client.get("/control/api/status")).json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 3)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(status["retry"]["total_retries"], 2)
        self.assertEqual(status["retry"]["active"], [])
        self.assertTrue(
            all(
                item["request_started_at"] <= item["recorded_at"]
                for item in status["retry"]["recent_errors"]
            )
        )
        self.assertEqual(
            len(
                {
                    item["request_started_at"]
                    for item in status["retry"]["recent_errors"]
                }
            ),
            1,
        )
        self.assertEqual(
            [item["attempt"] for item in status["retry"]["recent_errors"]],
            [2, 1],
        )
        self.assertIn(
            "HTTP 503：temporary upstream overload",
            status["retry"]["recent_errors"][0]["summary"],
        )

    async def test_retries_nginx_html_404_before_returning_response(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    404,
                    headers={"content-type": "text/html; charset=utf-8"},
                    content=(
                        b"<html><head><title>404 Not Found</title></head>"
                        b"<body><h1>404 Not Found</h1><hr>"
                        b"<center>nginx</center></body></html>"
                    ),
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        history_store = RecoveryHistoryStore(
            Path(temp_context.name) / "recovery.sqlite3"
        )
        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
            recovery_history_store=history_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})
        history = history_store.history()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().last_retry_kind, "http_404")
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["provider_id"], "selected")
        self.assertEqual(history["items"][0]["kind"], "http_404")
        self.assertEqual(history["items"][0]["stage"], "before_output")
        self.assertEqual(history["items"][0]["outcome"], "retrying")
        self.assertLessEqual(
            history["items"][0]["request_started_at"],
            history["items"][0]["recorded_at"],
        )
        self.assertIn("HTTP 404", history["items"][0]["summary"])
        self.assertNotIn("<html", history["items"][0]["summary"])

    async def test_json_business_404_is_not_retried(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                404,
                json={
                    "error": {
                        "code": "model_not_found",
                        "message": "requested model does not exist",
                    }
                },
            )

        async def no_wait(_: float) -> None:
            return None

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        history_store = RecoveryHistoryStore(
            Path(temp_context.name) / "recovery.sqlite3"
        )
        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_sleep=no_wait,
            recovery_history_store=history_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "model_not_found")
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)
        self.assertEqual(history_store.history()["total_count"], 0)

    async def test_transient_http_400_model_capacity_is_retried(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": (
                                "当前模型 gpt-5.6-sol 负载已经达到上限，"
                                "请稍后重试"
                            )
                        }
                    },
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        history_store = RecoveryHistoryStore(
            Path(temp_context.name) / "recovery.sqlite3"
        )
        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
            recovery_history_store=history_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})
        history = history_store.history()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().last_retry_kind, "model_capacity")
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["kind"], "model_capacity")
        self.assertEqual(history["items"][0]["stage"], "before_output")
        self.assertIn("负载已经达到上限", history["items"][0]["summary"])

    async def test_transient_http_400_channel_error_is_retried(self) -> None:
        attempts = 0

        class ChunkedChannelError(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"error":{"message":"No available '
                yield b'channel for model gpt-5.6-sol"}}'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    400,
                    headers={"content-type": "application/json"},
                    stream=ChunkedChannelError(),
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().last_retry_kind, "upstream_error")
        self.assertIn(
            "No available channel",
            router.status().recent_retry_errors[0].summary,
        )

    async def test_transient_plain_text_http_400_is_retried(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    400,
                    headers={"content-type": "text/plain; charset=utf-8"},
                    content="upstream temporarily unavailable, please try again later",
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().last_retry_kind, "upstream_error")

    async def test_transient_top_level_http_400_message_is_retried(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    400,
                    json={"message": "No available channel for model gpt-5.6-sol"},
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().last_retry_kind, "upstream_error")

    async def test_permanent_http_400_is_passed_through_unchanged(self) -> None:
        attempts = 0
        body = (
            b'{"code":"context_length_exceeded",'
            b'"message":"context is too long; please try again later"}'
        )

        class ChunkedPermanentError(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield body[:31]
                yield body[31:]

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                400,
                headers={"content-type": "application/json"},
                stream=ChunkedPermanentError(),
            )

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, body)
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)

    async def test_permanent_http_400_message_overrides_retry_hint(self) -> None:
        attempts = 0
        body = b"invalid parameter: reasoning_effort; please try again later"

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                400,
                headers={"content-type": "text/plain; charset=utf-8"},
                content=body,
            )

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, body)
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)

    async def test_top_level_permanent_http_400_code_is_not_retried(self) -> None:
        attempts = 0
        body = (
            b'{"type":"error","code":"invalid_request_error",'
            b'"message":"please try again later"}'
        )

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                400,
                headers={"content-type": "application/json"},
                content=body,
            )

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, body)
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)

    async def test_unknown_http_400_is_passed_through_unchanged(self) -> None:
        attempts = 0
        body = b'{"error":{"message":"business rule rejected request"}}'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                400,
                headers={"content-type": "application/json"},
                content=body,
            )

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, body)
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)

    async def test_malformed_json_http_400_is_passed_through_unchanged(self) -> None:
        attempts = 0
        body = b'{"error":{"message":"No available channel"}} trailing'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                400,
                headers={"content-type": "application/json"},
                content=body,
            )

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, body)
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)

    async def test_slow_permanent_http_400_is_passed_through_unchanged(self) -> None:
        attempts = 0
        body = (
            b'{"code":"context_length_exceeded",'
            b'"message":"context is too long"}'
        )

        class SlowPermanentError(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield body[:24]
                await asyncio.sleep(0.3)
                yield body[24:]

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                400,
                headers={"content-type": "application/json"},
                stream=SlowPermanentError(),
            )

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, body)
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)

    async def test_gzip_transient_http_400_is_retried(self) -> None:
        attempts = 0
        body = b'{"error":{"message":"No available channel for model"}}'
        compressed_body = gzip.compress(body)

        class GzipTransientError(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield compressed_body

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    400,
                    headers={
                        "content-encoding": "gzip",
                        "content-type": "application/json",
                    },
                    stream=GzipTransientError(),
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(router.status().last_retry_kind, "upstream_error")

    async def test_gzip_permanent_http_400_is_decoded_before_pass_through(self) -> None:
        attempts = 0
        body = (
            b'{"code":"context_length_exceeded",'
            b'"message":"context is too long"}'
        )
        compressed_body = gzip.compress(body)

        class GzipPermanentError(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield compressed_body

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                400,
                headers={
                    "content-encoding": "gzip",
                    "content-type": "application/json",
                },
                stream=GzipPermanentError(),
            )

        async def no_wait(_: float) -> None:
            return None

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, body)
        self.assertNotIn("content-encoding", response.headers)
        self.assertEqual(attempts, 1)
        self.assertEqual(router.status().total_retries, 0)

    async def test_gzip_http_403_is_decoded_when_retry_is_disabled(self) -> None:
        attempts = 0
        body = b"<html><title>Just a moment...</title></html>"
        compressed_body = gzip.compress(body)

        class GzipForbidden(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield compressed_body

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                403,
                headers={
                    "content-encoding": "gzip",
                    "content-type": "text/html; charset=utf-8",
                    "cf-mitigated": "challenge",
                },
                stream=GzipForbidden(),
            )

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(enabled=False),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content, body)
        self.assertNotIn("content-encoding", response.headers)
        self.assertEqual(response.headers["cf-mitigated"], "challenge")
        self.assertEqual(attempts, 1)

    async def test_http_400_inspection_preserves_bytes_past_limit(self) -> None:
        first_chunk = b"a" * (RETRY_ERROR_BODY_BYTES - 4)
        crossing_chunk = b"bcde" + b"overflow"
        trailing_chunk = b"tail"

        async def remaining_chunks() -> AsyncIterator[bytes]:
            yield crossing_chunk
            yield trailing_chunk

        response = httpx.Response(
            400,
            headers={"content-type": "text/plain; charset=utf-8"},
        )
        stream = remaining_chunks()

        buffered, resumed_stream, retry_kind, retry_summary, repair_index = (
            await _inspect_http_400_before_output(
                response,
                first_chunk,
                stream,
            )
        )
        remainder = b"".join([chunk async for chunk in resumed_stream])

        self.assertEqual(buffered, first_chunk + b"bcde")
        self.assertEqual(len(buffered), RETRY_ERROR_BODY_BYTES)
        self.assertEqual(remainder, b"overflow" + trailing_chunk)
        self.assertIsNone(retry_kind)
        self.assertIsNone(retry_summary)
        self.assertIsNone(repair_index)

    async def test_sniffed_nginx_404_exhausts_without_html_leak(self) -> None:
        attempts = 0

        class ChunkedNginx404(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"<html><head><title>"
                yield b"404 Not Found</title></head><body>"
                yield (
                    b"<center><h1>404 Not Found</h1></center>"
                    b"<hr><center>nginx</center></body></html>"
                )

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(404, stream=ChunkedNginx404())

        async def no_wait(_: float) -> None:
            return None

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        history_store = RecoveryHistoryStore(
            Path(temp_context.name) / "recovery.sqlite3"
        )
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
            recovery_history_store=history_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})
        history = history_store.history()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(attempts, 2)
        self.assertNotIn(b"nginx", response.content)
        self.assertNotIn(b"<html", response.content)
        self.assertEqual(history["total_count"], 2)
        self.assertEqual(
            {item["kind"] for item in history["items"]},
            {"http_404"},
        )
        self.assertEqual(history["items"][0]["outcome"], "exhausted")

    async def test_retry_status_redacts_sensitive_upstream_message(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    503,
                    json={"error": {"message": "api_key=fixture-private-value"}},
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})
        status = await client.get("/control/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertIn("[已隐藏]", status.text)
        self.assertNotIn("fixture-private-value", status.text)

    async def test_retries_connection_error_before_first_response(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("fixture connection failure", request=request)
            return httpx.Response(200, content=b"ok")

        async def no_wait(_: float) -> None:
            return None

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)

    async def test_retry_refreshes_session_route_changed_during_retry_delay(self) -> None:
        attempts: list[str] = []
        thread_id = "thread-route-changed-during-request"
        router = ProviderRouter((provider("first", current=True), provider("second")))

        async def upstream(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            if request.url.host == "first.example.test":
                return httpx.Response(503, json={"error": {"message": "retry"}})
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            router.set_session_provider_override(thread_id, "second")

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=2),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post(
            "/v1/responses",
            headers={
                "x-codex-turn-metadata": json.dumps({"thread_id": thread_id})
            },
            json={"model": "test"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, ["first.example.test", "second.example.test"])
        self.assertEqual(router.status().total_retries, 1)

    async def test_retries_rate_limit_without_retry_after(self) -> None:
        attempts = 0
        sleeps: list[float] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, json={"error": {"message": "quota"}})
            return httpx.Response(200, content=b"recovered")

        async def no_wait(delay: float) -> None:
            sleeps.append(delay)

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [1.0])

    async def test_caps_rate_limit_retry_after_to_local_delay_budget(self) -> None:
        attempts = 0
        sleeps: list[float] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "120"},
                    json={"error": {"message": "try much later"}},
                )
            return httpx.Response(200, content=b"recovered")

        async def no_wait(delay: float) -> None:
            sleeps.append(delay)

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            retry_policy=RetryPolicy(max_delay_seconds=5),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [5])

    async def test_embedded_rate_limit_before_output_retries_on_current_provider(self) -> None:
        attempts: list[str] = []
        sleeps: list[float] = []
        first = provider("first", current=True)
        second = provider("second")
        router = ProviderRouter((first, second))

        class RateLimitedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.created","response":{"status":"in_progress"}}\n\n'
                yield b'data: {"type":"response.failed","response":{"status":"failed","error":'
                yield b'{"message":"exceeded retry limit, last status: 429 Too Many Requests"}}}\n\n'

        class RecoveredStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n'
                yield b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'

        async def upstream(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            if request.url.host == "first.example.test":
                router.select("second")
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=RateLimitedStream(),
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=RecoveredStream(),
            )

        async def no_wait(delay: float) -> None:
            sleeps.append(delay)

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_policy=RetryPolicy(max_attempts=-1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"recovered", response.content)
        self.assertNotIn(b"exceeded retry limit", response.content)
        self.assertEqual(attempts, ["first.example.test", "second.example.test"])
        self.assertEqual(sleeps, [0.0])
        status = router.status()
        self.assertEqual(status.total_retries, 1)
        self.assertEqual(status.last_retry_kind, "rate_limited")
        self.assertIn("HTTP 429", status.recent_retry_errors[0].summary)

    async def test_embedded_rate_limit_after_output_is_not_replayed(self) -> None:
        attempts = 0

        class OutputThenRateLimit(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                yield (
                    b'data: {"type":"response.failed","response":{"status":"failed",'
                    b'"error":{"message":"last status: 429 Too Many Requests"}}}\n\n'
                )

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=OutputThenRateLimit(),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(attempts, 1)
        self.assertIn(b"partial", response.content)
        self.assertIn(b"429 Too Many Requests", response.content)
        self.assertEqual(router.status().total_retries, 0)

    async def test_embedded_model_capacity_before_output_is_retried(self) -> None:
        attempts = 0

        class AtCapacityStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.created","response":{"status":"in_progress"}}\n\n'
                yield b'data: {"type":"response.reasoning_text.delta","delta":"hidden"}\n\n'
                yield b'data: {"type":"response.function_call_arguments.delta","delta":"{}"}\n\n'
                yield b'data: {"type":"response.failed","response":{"status":"failed","error":'
                yield (
                    b'{"message":"Selected model is at capacity. '
                    b'Please try a different model."}}}'
                )

        class RecoveredStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n'
                yield b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            stream = AtCapacityStream() if attempts == 1 else RecoveredStream()
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )

        async def no_wait(_: float) -> None:
            return None

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        history_store = RecoveryHistoryStore(
            Path(temp_context.name) / "usage.sqlite3"
        )
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_sleep=no_wait,
            recovery_history_store=history_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})
        api_history = (
            await client.get("/control/api/status")
        ).json()["retry"]["history"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertIn(b"recovered", response.content)
        self.assertNotIn(b"at capacity", response.content)
        status = router.status()
        self.assertEqual(status.total_retries, 1)
        self.assertEqual(status.last_retry_kind, "model_capacity")
        self.assertIn(
            "Selected model is at capacity",
            status.recent_retry_errors[0].summary,
        )
        history = history_store.history()
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["kind"], "model_capacity")
        self.assertEqual(history["items"][0]["stage"], "before_output")
        self.assertEqual(history["items"][0]["outcome"], "retrying")
        self.assertEqual(api_history["total_count"], 1)
        self.assertEqual(api_history["items"][0]["kind"], "model_capacity")

    async def test_oversized_nested_model_capacity_before_output_is_retried(self) -> None:
        attempts = 0

        class OversizedCapacityStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.failed","response":'
                    b'{"status":"failed","metadata":{"padding":"'
                )
                padding = b"x" * (320 * 1024)
                for offset in range(0, len(padding), 32 * 1024):
                    yield padding[offset : offset + 32 * 1024]
                yield (
                    b'"},"diagnostic":{"nested":{"message":'
                    b'"Selected model is at cap'
                )
                yield b'acity. Please try a different model."}}}}\n\n'

        class RecoveredStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n'
                yield b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            stream = OversizedCapacityStream() if attempts == 1 else RecoveredStream()
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )

        async def no_wait(_: float) -> None:
            return None

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client, retry_sleep=no_wait)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertIn(b"recovered", response.content)
        self.assertNotIn(b"at capacity", response.content)
        self.assertEqual(router.status().total_retries, 1)
        self.assertEqual(router.status().last_retry_kind, "model_capacity")

    async def test_capacity_words_in_visible_output_do_not_trigger_retry(self) -> None:
        attempts = 0

        class ExplanationStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                text = (
                    'Example {"type":"response.failed"}: '
                    "Selected model is at capacity. Please try a different model."
                )
                event = {
                    "type": "response.output_text.delta",
                    "delta": text,
                }
                yield b"data: " + json.dumps(event).encode() + b"\n\n"
                yield b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=ExplanationStream(),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(attempts, 1)
        self.assertIn(b"at capacity", response.content)
        self.assertEqual(router.status().total_retries, 0)

    async def test_embedded_model_capacity_after_output_is_not_replayed(self) -> None:
        attempts = 0

        class OutputThenCapacity(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                yield (
                    b'data: {"type":"response.failed","response":{"status":"failed",'
                    b'"error":{"message":"Selected model is at capacity. '
                    b'Please try a different model."}}}\n\n'
                )

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=OutputThenCapacity(),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_path = Path(temp_context.name) / "usage.sqlite3"
        history_store = RecoveryHistoryStore(usage_path)
        usage_store = UsageStore(usage_path)
        app = create_proxy_app(
            router,
            client=upstream_client,
            recovery_history_store=history_store,
            usage_store=usage_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(attempts, 1)
        self.assertIn(b"partial", response.content)
        self.assertIn(b"at capacity", response.content)
        self.assertEqual(router.status().total_retries, 0)
        history = history_store.history()
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["kind"], "model_capacity")
        self.assertEqual(history["items"][0]["stage"], "after_output")
        self.assertEqual(history["items"][0]["outcome"], "passed_through")
        usage_summary = usage_store.summary("all")
        usage_history = usage_store.history(provider_id="selected", window="all")
        self.assertEqual(usage_summary["total"]["request_count"], 1)
        self.assertEqual(usage_summary["total"]["successful_requests"], 0)
        self.assertEqual(usage_summary["total"]["failed_requests"], 1)
        self.assertIsNone(usage_summary["by_provider"]["selected"]["last_success_at"])
        self.assertEqual(usage_history["total_count"], 1)
        self.assertFalse(usage_history["items"][0]["succeeded"])
        self.assertEqual(usage_history["items"][0]["status_code"], 200)

    async def test_oversized_model_capacity_after_output_is_recorded(self) -> None:
        attempts = 0

        class OutputThenOversizedCapacity(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                yield (
                    b'data: {"type":"response.failed","response":'
                    b'{"status":"failed","metadata":{"padding":"'
                )
                padding = b"x" * (320 * 1024)
                for offset in range(0, len(padding), 32 * 1024):
                    yield padding[offset : offset + 32 * 1024]
                yield (
                    b'"},"error":{"details":{"message":'
                    b'"Selected model is at cap'
                )
                yield b'acity. Please try a different model."}}}}\n\n'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=OutputThenOversizedCapacity(),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        history_store = RecoveryHistoryStore(
            Path(temp_context.name) / "usage.sqlite3"
        )
        app = create_proxy_app(
            router,
            client=upstream_client,
            recovery_history_store=history_store,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(attempts, 1)
        self.assertIn(b"partial", response.content)
        self.assertIn(b"at capacity", response.content)
        self.assertEqual(router.status().total_retries, 0)
        history = history_store.history()
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["kind"], "model_capacity")
        self.assertEqual(history["items"][0]["stage"], "after_output")
        self.assertEqual(history["items"][0]["outcome"], "passed_through")

    async def test_embedded_upstream_failure_before_output_is_retried(self) -> None:
        attempts = 0

        class FailedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.created","response":{"status":"in_progress"}}\n\n'
                yield b'data: {"type":"response.failed","response":{"status":"failed","error":'
                yield b'{"code":"upstream_error","message":"Upstream request failed"}}}\n\n'

        class RecoveredStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n'
                yield b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            stream = FailedStream() if attempts == 1 else RecoveredStream()
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )

        async def no_wait(_: float) -> None:
            return None

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(
            router,
            client=upstream_client,
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertIn(b"recovered", response.content)
        self.assertNotIn(b"Upstream request failed", response.content)
        status = router.status()
        self.assertEqual(status.total_retries, 1)
        self.assertEqual(status.last_retry_kind, "upstream_error")
        self.assertIn("Upstream request failed", status.recent_retry_errors[0].summary)

    async def test_embedded_permanent_failure_is_not_retried(self) -> None:
        attempts = 0

        class InvalidRequestStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.failed","response":{"status":"failed",'
                    b'"error":{"code":"invalid_request_error",'
                    b'"message":"Unknown parameter"}}}\n\n'
                )

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=InvalidRequestStream(),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(attempts, 1)
        self.assertIn(b"Unknown parameter", response.content)
        self.assertEqual(router.status().total_retries, 0)

    async def test_retries_stream_failure_before_first_chunk(self) -> None:
        attempts = 0

        class BrokenBeforeOutput(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise httpx.ReadError("fixture early disconnect")
                yield b"unreachable"

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(200, stream=BrokenBeforeOutput())
            return httpx.Response(200, content=b"recovered")

        async def no_wait(_: float) -> None:
            return None

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"recovered")
        self.assertEqual(attempts, 2)

    async def test_does_not_replay_stream_after_first_chunk(self) -> None:
        attempts = 0

        class BrokenAfterOutput(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"data: started\n\n"
                raise httpx.ReadError("fixture late disconnect")

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, stream=BrokenAfterOutput())

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client)
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "raw_path": b"/v1/responses",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "root_path": "",
        }
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": b"{}", "more_body": False}

        response = await route.endpoint("responses", Request(scope, receive))
        iterator = response.body_iterator

        self.assertEqual(await anext(iterator), b"data: started\n\n")
        with self.assertRaises(httpx.ReadError):
            await anext(iterator)
        self.assertEqual(attempts, 1)
        await upstream_client.aclose()

    async def test_completed_event_before_client_close_is_recorded_as_success(self) -> None:
        class CompletedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.completed","response":{"status":"completed",'
                    b'"usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}\n\n'
                )

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=CompletedStream(),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            usage_store=usage_store,
        )
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"test","reasoning":{"effort":"high"}}',
                "more_body": False,
            }

        scope = {
            "type": "http", "method": "POST", "path": "/v1/responses",
            "raw_path": b"/v1/responses", "query_string": b"", "headers": [],
            "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1), "root_path": "",
        }
        response = await route.endpoint("responses", Request(scope, receive))
        iterator = response.body_iterator

        self.assertIn(b"response.completed", await anext(iterator))
        await iterator.aclose()
        await upstream_client.aclose()

        history = usage_store.request_history()
        self.assertEqual(history["total_count"], 1)
        self.assertTrue(history["items"][0]["succeeded"])
        self.assertIsNone(history["items"][0]["error_kind"])
        self.assertEqual(history["items"][0]["reasoning_effort"], "high")

    async def test_cancelled_persistence_keeps_usage_and_request_history_linked(self) -> None:
        class BlockingUsageStore(UsageStore):
            def __init__(self, path: Path) -> None:
                super().__init__(path)
                self.usage_started = threading.Event()
                self.release_usage = threading.Event()

            def record(self, **kwargs):
                self.usage_started.set()
                self.release_usage.wait(timeout=2)
                return super().record(**kwargs)

        class CompletedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.completed","response":{"usage":'
                    b'{"input_tokens":12,"output_tokens":3,"total_tokens":15}}}\n\n'
                )

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=CompletedStream(),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = BlockingUsageStore(Path(temp_context.name) / "usage.sqlite3")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            usage_store=usage_store,
        )
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"test"}',
                "more_body": False,
            }

        scope = {
            "type": "http", "method": "POST", "path": "/v1/responses",
            "raw_path": b"/v1/responses", "query_string": b"", "headers": [],
            "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1), "root_path": "",
        }
        response = await route.endpoint("responses", Request(scope, receive))
        iterator = response.body_iterator
        self.assertIn(b"response.completed", await anext(iterator))
        close_task = asyncio.create_task(iterator.aclose())
        try:
            self.assertTrue(await asyncio.to_thread(usage_store.usage_started.wait, 1))
            close_task.cancel()
            usage_store.release_usage.set()
            with self.assertRaises(asyncio.CancelledError):
                await close_task
        finally:
            usage_store.release_usage.set()

        linked_usage_id = None
        for _ in range(100):
            with closing(sqlite3.connect(usage_store.path)) as connection:
                row = connection.execute(
                    "SELECT usage_id FROM request_history ORDER BY id DESC LIMIT 1"
                ).fetchone()
                linked_usage_id = None if row is None else row[0]
            if linked_usage_id is not None:
                break
            await asyncio.sleep(0.01)
        await upstream_client.aclose()

        with closing(sqlite3.connect(usage_store.path)) as connection:
            usage_count = connection.execute(
                "SELECT COUNT(*) FROM request_usage"
            ).fetchone()[0]
            history_count = connection.execute(
                "SELECT COUNT(*) FROM request_history"
            ).fetchone()[0]
        self.assertEqual(usage_count, 1)
        self.assertEqual(history_count, 1)
        self.assertIsNotNone(linked_usage_id)

    async def test_client_close_before_terminal_event_uses_short_cancel_summary(self) -> None:
        class PartialStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=PartialStream(),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider("selected", current=True),)),
            client=upstream_client,
            usage_store=usage_store,
        )
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"test"}',
                "more_body": False,
            }

        scope = {
            "type": "http", "method": "POST", "path": "/v1/responses",
            "raw_path": b"/v1/responses", "query_string": b"", "headers": [],
            "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1), "root_path": "",
        }
        response = await route.endpoint("responses", Request(scope, receive))
        iterator = response.body_iterator

        self.assertIn(b"partial", await anext(iterator))
        await iterator.aclose()
        await upstream_client.aclose()

        history = usage_store.request_history()
        self.assertEqual(history["total_count"], 1)
        self.assertFalse(history["items"][0]["succeeded"])
        self.assertEqual(history["items"][0]["error_kind"], "client_disconnected")
        self.assertEqual(history["items"][0]["error_summary"], "客户端取消")

    async def test_new_same_thread_request_supersedes_streaming_request(self) -> None:
        first_stream_started = asyncio.Event()
        first_stream_closed = asyncio.Event()
        never_continue = asyncio.Event()
        attempts = 0

        class StalledStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    first_stream_started.set()
                    yield (
                        b'data: {"type":"response.output_text.delta",'
                        b'"delta":"partial"}\n\n'
                    )
                    await never_continue.wait()
                finally:
                    first_stream_closed.set()

            async def aclose(self) -> None:
                first_stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=StalledStream(),
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"type":"response.completed",'
                    b'"response":{"status":"completed"}}\n\n'
                ),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client, usage_store=usage_store)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        metadata = json.dumps({"thread_id": "thread-superseded-stream"})

        first_request = asyncio.create_task(
            client.post(
                "/v1/responses",
                headers={"x-codex-turn-metadata": metadata},
                json={"model": "test", "input": "first"},
            )
        )
        try:
            await asyncio.wait_for(first_stream_started.wait(), timeout=1)
            second_response = await asyncio.wait_for(
                client.post(
                    "/v1/responses",
                    headers={"x-codex-turn-metadata": metadata},
                    json={"model": "test", "input": "second"},
                ),
                timeout=1,
            )
            with self.assertRaises(asyncio.CancelledError):
                await first_request
            await asyncio.wait_for(first_stream_closed.wait(), timeout=1)
        finally:
            never_continue.set()
            if not first_request.done():
                first_request.cancel()
            await asyncio.gather(first_request, return_exceptions=True)
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(router.status().active_request_details, ())
        history = usage_store.request_history(window="24h", status="all")
        self.assertEqual(history["total_count"], 2)
        superseded = next(
            item for item in history["items"]
            if item["error_kind"] == "session_superseded"
        )
        self.assertFalse(superseded["succeeded"])
        self.assertEqual(superseded["outcome"], "cancelled")
        self.assertEqual(superseded["error_summary"], "已由同会话新请求接管")
        self.assertTrue(any(item["succeeded"] for item in history["items"]))
        with closing(sqlite3.connect(usage_store.path)) as connection:
            inflight_count = connection.execute(
                "SELECT COUNT(*) FROM inflight_requests"
            ).fetchone()[0]
        self.assertEqual(inflight_count, 0)

    async def test_new_same_thread_request_supersedes_sse_preflight(self) -> None:
        preflight_started = asyncio.Event()
        first_stream_closed = asyncio.Event()
        never_continue = asyncio.Event()
        attempts = 0

        class StalledPreflight(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    preflight_started.set()
                    yield b'data: {"type":"response.created"}\n\n'
                    await never_continue.wait()
                finally:
                    first_stream_closed.set()

            async def aclose(self) -> None:
                first_stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=StalledPreflight(),
                )
            return httpx.Response(200, content=b"recovered")

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client, usage_store=usage_store)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        metadata = json.dumps({"thread_id": "thread-superseded-preflight"})

        first_request = asyncio.create_task(
            client.post(
                "/v1/responses",
                headers={"x-codex-turn-metadata": metadata},
                json={"model": "test", "input": "first"},
            )
        )
        try:
            await asyncio.wait_for(preflight_started.wait(), timeout=1)
            for _ in range(100):
                active = router.status().active_request_details
                if active and active[0].phase == "preflighting_sse":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(router.status().active_request_details[0].phase, "preflighting_sse")

            second_response = await asyncio.wait_for(
                client.post(
                    "/v1/responses",
                    headers={"x-codex-turn-metadata": metadata},
                    json={"model": "test", "input": "second"},
                ),
                timeout=1,
            )
            with self.assertRaises(asyncio.CancelledError):
                await first_request
            await asyncio.wait_for(first_stream_closed.wait(), timeout=1)
        finally:
            never_continue.set()
            if not first_request.done():
                first_request.cancel()
            await asyncio.gather(first_request, return_exceptions=True)
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(second_response.status_code, 200)
        history = usage_store.request_history(window="24h", status="all")
        self.assertEqual(history["total_count"], 2)
        self.assertEqual(
            sum(item["error_kind"] == "session_superseded" for item in history["items"]),
            1,
        )
        self.assertEqual(router.status().active_request_details, ())

    async def test_different_threads_with_same_name_remain_concurrent(self) -> None:
        first_stream_started = asyncio.Event()
        release_first_stream = asyncio.Event()
        first_stream_closed = asyncio.Event()
        attempts = 0

        class StalledStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    first_stream_started.set()
                    yield (
                        b'data: {"type":"response.output_text.delta",'
                        b'"delta":"partial"}\n\n'
                    )
                    await release_first_stream.wait()
                finally:
                    first_stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=StalledStream(),
                )
            return httpx.Response(200, content=b"second")

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            router,
            client=upstream_client,
            session_name_resolver=lambda thread_ids: {
                thread_id: "相同显示名称" for thread_id in thread_ids
            },
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        first_request = asyncio.create_task(
            client.post(
                "/v1/responses",
                headers={
                    "x-codex-turn-metadata": json.dumps({"thread_id": "thread-a"})
                },
                json={"model": "test"},
            )
        )
        try:
            await asyncio.wait_for(first_stream_started.wait(), timeout=1)
            second_response = await asyncio.wait_for(
                client.post(
                    "/v1/responses",
                    headers={
                        "x-codex-turn-metadata": json.dumps({"thread_id": "thread-b"})
                    },
                    json={"model": "test"},
                ),
                timeout=1,
            )
            self.assertEqual(second_response.status_code, 200)
            self.assertFalse(first_stream_closed.is_set())
            self.assertEqual(len(router.status().active_request_details), 1)
        finally:
            release_first_stream.set()
            await asyncio.wait_for(first_request, timeout=1)
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(router.status().active_request_details, ())

    async def test_requests_without_thread_ids_remain_concurrent(self) -> None:
        first_stream_started = asyncio.Event()
        release_first_stream = asyncio.Event()
        first_stream_closed = asyncio.Event()
        attempts = 0

        class StalledStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    first_stream_started.set()
                    yield (
                        b'data: {"type":"response.output_text.delta",'
                        b'"delta":"partial"}\n\n'
                    )
                    await release_first_stream.wait()
                finally:
                    first_stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=StalledStream(),
                )
            return httpx.Response(200, content=b"second")

        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        first_request = asyncio.create_task(
            client.post("/v1/responses", json={"model": "test"})
        )
        try:
            await asyncio.wait_for(first_stream_started.wait(), timeout=1)
            second_response = await asyncio.wait_for(
                client.post("/v1/responses", json={"model": "test"}),
                timeout=1,
            )
            self.assertEqual(second_response.status_code, 200)
            self.assertFalse(first_stream_closed.is_set())
            self.assertEqual(len(router.status().active_request_details), 1)
        finally:
            release_first_stream.set()
            await asyncio.wait_for(first_request, timeout=1)
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(router.status().active_request_details, ())

    async def test_completed_same_thread_request_is_not_downgraded_by_racing_request(self) -> None:
        first_close_started = asyncio.Event()
        keep_first_close_open = asyncio.Event()
        attempts = 0

        class CompletedThenSlowClose(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.completed",'
                    b'"response":{"status":"completed"}}\n\n'
                )

            async def aclose(self) -> None:
                first_close_started.set()
                await keep_first_close_open.wait()

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=CompletedThenSlowClose(),
                )
            return httpx.Response(200, content=b"second")

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        router = ProviderRouter((provider("selected", current=True),))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client, usage_store=usage_store)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        metadata = json.dumps({"thread_id": "thread-terminal-race"})
        first_request = asyncio.create_task(
            client.post(
                "/v1/responses",
                headers={"x-codex-turn-metadata": metadata},
                json={"model": "test", "input": "first"},
            )
        )
        try:
            await asyncio.wait_for(first_close_started.wait(), timeout=1)
            second_response = await asyncio.wait_for(
                client.post(
                    "/v1/responses",
                    headers={"x-codex-turn-metadata": metadata},
                    json={"model": "test", "input": "second"},
                ),
                timeout=1,
            )
            self.assertEqual(second_response.status_code, 200)
            with self.assertRaises(asyncio.CancelledError):
                await first_request
        finally:
            keep_first_close_open.set()
            if not first_request.done():
                first_request.cancel()
            await asyncio.gather(first_request, return_exceptions=True)
            await client.aclose()
            await upstream_client.aclose()

        history = usage_store.request_history(window="24h", status="all")
        self.assertEqual(history["total_count"], 2)
        self.assertEqual(sum(item["succeeded"] for item in history["items"]), 2)
        self.assertFalse(
            any(item["error_kind"] == "session_superseded" for item in history["items"])
        )

    async def test_client_disconnect_cancels_a_stalled_upstream_stream(self) -> None:
        stream_closed = asyncio.Event()
        never_continue = asyncio.Event()

        class StalledStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                    await never_continue.wait()
                finally:
                    stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StalledStream(),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client, usage_store=usage_store)
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        request_body_sent = False

        async def request_receive():
            nonlocal request_body_sent
            if request_body_sent:
                return {"type": "http.disconnect"}
            request_body_sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"test"}',
                "more_body": False,
            }

        scope = {
            "type": "http", "asgi": {"spec_version": "2.4"},
            "method": "POST", "path": "/v1/responses",
            "raw_path": b"/v1/responses", "query_string": b"", "headers": [],
            "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1), "root_path": "",
        }
        response = await route.endpoint("responses", Request(scope, request_receive))
        first_body_sent = asyncio.Event()

        async def response_receive():
            await first_body_sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                first_body_sent.set()
                await never_continue.wait()

        await asyncio.wait_for(response(scope, response_receive, send), timeout=1)
        await upstream_client.aclose()

        self.assertTrue(stream_closed.is_set())
        self.assertEqual(router.status().active_request_details, ())
        history = usage_store.request_history()
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["error_kind"], "client_disconnected")
        self.assertEqual(history["items"][0]["error_summary"], "客户端取消")

    async def test_downstream_send_error_closes_suspended_response_body(self) -> None:
        stream_closed = asyncio.Event()
        never_continue = asyncio.Event()

        class StalledStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                await never_continue.wait()

            async def aclose(self) -> None:
                stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StalledStream(),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client, usage_store=usage_store)
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        request_body_sent = False

        async def request_receive():
            nonlocal request_body_sent
            if request_body_sent:
                return {"type": "http.disconnect"}
            request_body_sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"test"}',
                "more_body": False,
            }

        scope = {
            "type": "http", "asgi": {"spec_version": "2.4"},
            "method": "POST", "path": "/v1/responses",
            "raw_path": b"/v1/responses", "query_string": b"", "headers": [],
            "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1), "root_path": "",
        }
        response = await route.endpoint("responses", Request(scope, request_receive))
        never_disconnect = asyncio.Event()

        async def response_receive():
            await never_disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise OSError("fixture downstream closed")

        with self.assertRaises(OSError):
            await asyncio.wait_for(response(scope, response_receive, send), timeout=1)
        await upstream_client.aclose()

        self.assertTrue(stream_closed.is_set())
        self.assertEqual(router.status().active_request_details, ())
        history = usage_store.request_history()
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["error_kind"], "client_disconnected")
        with closing(sqlite3.connect(usage_store.path)) as connection:
            inflight_count = connection.execute(
                "SELECT COUNT(*) FROM inflight_requests"
            ).fetchone()[0]
        self.assertEqual(inflight_count, 0)

    async def test_terminal_event_finishes_when_upstream_never_reaches_eof(self) -> None:
        stream_closed = asyncio.Event()
        never_continue = asyncio.Event()

        class StalledAfterCompleted(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.completed","response":{"status":"completed",'
                    b'"usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}\n\n'
                )
                await never_continue.wait()

            async def aclose(self) -> None:
                stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StalledAfterCompleted(),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client, usage_store=usage_store)
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        request_body_sent = False

        async def request_receive():
            nonlocal request_body_sent
            if request_body_sent:
                return {"type": "http.disconnect"}
            request_body_sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"test"}',
                "more_body": False,
            }

        scope = {
            "type": "http", "asgi": {"spec_version": "2.4"},
            "method": "POST", "path": "/v1/responses",
            "raw_path": b"/v1/responses", "query_string": b"", "headers": [],
            "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1), "root_path": "",
        }
        response = await route.endpoint("responses", Request(scope, request_receive))
        never_disconnect = asyncio.Event()
        sent_body = bytearray()

        async def response_receive():
            await never_disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                sent_body.extend(message.get("body", b""))

        await asyncio.wait_for(response(scope, response_receive, send), timeout=1)
        await upstream_client.aclose()

        self.assertIn(b"response.completed", sent_body)
        self.assertTrue(stream_closed.is_set())
        self.assertEqual(router.status().active_request_details, ())
        history = usage_store.request_history()
        self.assertEqual(history["total_count"], 1)
        self.assertTrue(history["items"][0]["succeeded"])
        self.assertEqual(history["items"][0]["total_tokens"], 12)

    async def test_failure_terminal_event_finishes_stalled_upstream_as_failure(self) -> None:
        stream_closed = asyncio.Event()
        never_continue = asyncio.Event()

        class StalledAfterFailure(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                    b'data: {"type":"response.failed","response":{"status":"failed",'
                    b'"error":{"code":"upstream_error","message":"try again later"}}}\n\n'
                )
                await never_continue.wait()

            async def aclose(self) -> None:
                stream_closed.set()

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StalledAfterFailure(),
            )

        temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_context.cleanup)
        usage_store = UsageStore(Path(temp_context.name) / "usage.sqlite3")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        router = ProviderRouter((provider("selected", current=True),))
        app = create_proxy_app(router, client=upstream_client, usage_store=usage_store)
        route = next(
            route for route in app.routes if getattr(route, "path", "") == "/v1/{upstream_path:path}"
        )
        from starlette.requests import Request

        request_body_sent = False

        async def request_receive():
            nonlocal request_body_sent
            if request_body_sent:
                return {"type": "http.disconnect"}
            request_body_sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"test"}',
                "more_body": False,
            }

        scope = {
            "type": "http", "asgi": {"spec_version": "2.4"},
            "method": "POST", "path": "/v1/responses",
            "raw_path": b"/v1/responses", "query_string": b"", "headers": [],
            "scheme": "http", "server": ("testserver", 80),
            "client": ("127.0.0.1", 1), "root_path": "",
        }
        response = await route.endpoint("responses", Request(scope, request_receive))
        never_disconnect = asyncio.Event()

        async def response_receive():
            await never_disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            return None

        await asyncio.wait_for(response(scope, response_receive, send), timeout=1)
        await upstream_client.aclose()

        self.assertTrue(stream_closed.is_set())
        self.assertEqual(router.status().active_request_details, ())
        history = usage_store.request_history()
        self.assertEqual(history["total_count"], 1)
        self.assertFalse(history["items"][0]["succeeded"])
        self.assertEqual(history["items"][0]["error_kind"], "upstream_error")

    async def test_forwards_request_stream_headers_query_and_response(self) -> None:
        observed: dict[str, object] = {}

        class EventStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"data: ok\n\n"

        async def upstream(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["local_header"] = request.headers.get("x-local-header")
            observed["body"] = await request.aread()
            return httpx.Response(
                201,
                headers={
                    "content-type": "text/event-stream",
                    "connection": "keep-alive",
                    "x-upstream": "yes",
                },
                stream=EventStream(),
            )

        selected = ProxyProvider(
            provider_id="selected",
            name="Selected",
            base_url="https://selected.example.test/v1",
            is_cc_switch_current=True,
            api_key="fixture-upstream-key",
            configured_headers={"X-Local-Header": "configured"},
            default_query={"api-version": "1", "existing": "ignored"},
        )
        router = ProviderRouter((selected,))
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client)
        proxy_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:17890",
        )
        self.addAsyncCleanup(proxy_client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await proxy_client.post(
            "/v1/responses?existing=request",
            headers={
                "Authorization": "Bearer local-placeholder",
                "Connection": "close",
            },
            content=b'{"model":"gpt-test"}',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.content, b"data: ok\n\n")
        self.assertEqual(response.headers["x-upstream"], "yes")
        self.assertNotIn("connection", response.headers)
        self.assertEqual(
            observed["url"],
            "https://selected.example.test/v1/responses?existing=request&api-version=1",
        )
        self.assertEqual(observed["authorization"], "Bearer fixture-upstream-key")
        self.assertEqual(observed["local_header"], "configured")
        self.assertEqual(observed["body"], b'{"model":"gpt-test"}')
        self.assertEqual(router.status().active_by_provider, {})

    async def test_missing_credentials_returns_sanitized_error(self) -> None:
        router = ProviderRouter((provider("empty", current=True, api_key=None),))
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(router, client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:17890",
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("credential", response.text.casefold())

    async def test_rejects_route_back_to_same_proxy_address(self) -> None:
        selected = ProxyProvider(
            provider_id="loop",
            name="Loop",
            base_url="http://localhost:17890/v1",
            is_cc_switch_current=True,
            api_key="fixture-key",
        )
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(ProviderRouter((selected,)), client=upstream_client)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:17890",
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        response = await client.post("/v1/responses", json={"model": "test"})

        self.assertEqual(response.status_code, 508)
        self.assertNotIn("fixture-key", response.text)

    def test_server_rejects_non_loopback_binding(self) -> None:
        router = ProviderRouter((provider("selected"),))

        with self.assertRaisesRegex(ValueError, "回环地址"):
            LocalProxyServer(router, host="0.0.0.0")

    async def test_control_api_switches_without_exposing_credentials(self) -> None:
        first = provider("first", current=True, api_key="first-private-value")
        second = provider("second", api_key="second-private-value")
        selected: list[str] = []
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(
            ProviderRouter((first, second)),
            client=upstream_client,
            on_provider_selected=selected.append,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        status = await client.get("/control/api/status")
        forbidden = await client.post("/control/api/providers/second/select")
        switched = await client.post(
            "/control/api/providers/second/select",
            headers={"X-Local-Proxy-Control": "1"},
        )

        self.assertEqual(status.status_code, 200)
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["current_provider_id"], "second")
        self.assertEqual(selected, ["second"])
        serialized = status.text + switched.text
        self.assertNotIn("first-private-value", serialized)
        self.assertNotIn("second-private-value", serialized)
        self.assertNotIn("api_key", serialized.casefold())

    async def test_control_page_refresh_config_and_shutdown(self) -> None:
        router = ProviderRouter((provider("first", current=True),))
        stopped: list[bool] = []
        upstream_client = httpx.AsyncClient()
        app = create_proxy_app(
            router,
            client=upstream_client,
            reload_providers=lambda: (provider("refreshed", current=True),),
            config_fragment=lambda: 'base_url = "http://127.0.0.1:17890/v1"\n',
            on_shutdown_requested=lambda: stopped.append(True),
            health_status_url="https://status.example.test/api/status?window=24h",
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        self.addAsyncCleanup(upstream_client.aclose)

        page = await client.get("/control/")
        classic_page = await client.get("/control/?ui=classic")
        invalid_override_page = await client.get("/control/?ui=unknown")
        script = None
        styles = None
        ui_config = await client.get("/control/api/ui-config")
        refreshed = await client.post(
            "/control/api/refresh",
            headers={"X-Local-Proxy-Control": "1"},
        )
        config = await client.get("/control/api/codex-config")
        shutdown = await client.post(
            "/control/api/shutdown",
            headers={"X-Local-Proxy-Control": "1"},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn("本地中转", page.text)
        self.assertIn('./static/app.js', classic_page.text)
        self.assertEqual(invalid_override_page.content, page.content)
        script_match = re.search(r'src="\./static/(assets/[^"]+\.js)"', page.text)
        style_match = re.search(r'href="\./static/(assets/[^"]+\.css)"', page.text)
        self.assertIsNotNone(script_match)
        self.assertIsNotNone(style_match)
        script = await client.get(f"/control/static/{script_match.group(1)}")
        styles = await client.get(f"/control/static/{style_match.group(1)}")
        classic_script = await client.get("/control/static/app.js")
        classic_styles = await client.get("/control/static/styles.css")
        self.assertEqual(script.status_code, 200)
        self.assertEqual(styles.status_code, 200)
        self.assertEqual(classic_script.status_code, 200)
        self.assertIn("runtime-console-ui", classic_script.text)
        self.assertEqual(classic_styles.status_code, 200)
        self.assertIn(".setting-segmented", classic_styles.text)
        self.assertGreater(len(script.content), 50_000)
        self.assertGreater(len(styles.content), 10_000)
        self.assertIn("local-proxy-theme", script.text)
        self.assertIn("/api/ui-config", script.text)
        self.assertIn("/api/runtime-settings", script.text)
        self.assertIn("/api/providers/", script.text)
        self.assertIn(":root[data-theme=dark]", styles.text)
        self.assertIn(".app-window", styles.text)
        self.assertIn("height:100dvh", styles.text)
        self.assertRegex(styles.text, r"@media\s*\(max-width:\s*680px\)")
        self.assertNotIn("width:min(1440px,100%)", styles.text)
        self.assertIn("scrollbar-gutter:stable", styles.text)
        self.assertEqual(script.headers["cache-control"], "no-store")
        self.assertEqual(styles.headers["cache-control"], "no-store")
        self.assertEqual(ui_config.headers["cache-control"], "no-store")
        self.assertEqual(ui_config.json()["service_id"], "codex")
        self.assertEqual(ui_config.json()["config_endpoint"], "/control/api/codex-config")
        self.assertTrue(ui_config.json()["features"]["usage_history"])
        self.assertEqual(refreshed.json()["current_provider_id"], "refreshed")
        self.assertEqual(
            refreshed.json()["health_status_url"],
            "https://status.example.test/api/status?window=24h",
        )
        self.assertIn("127.0.0.1:17890", config.text)
        self.assertEqual(shutdown.json()["status"], "stopping")
        self.assertEqual(stopped, [True])


class LiveProxyTests(unittest.TestCase):
    def test_uvicorn_proxy_forwards_to_live_streaming_upstream(self) -> None:
        observed: dict[str, object] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                observed["path"] = self.path
                observed["authorization"] = self.headers.get("Authorization")
                observed["body"] = self.rfile.read(content_length)
                body = b"data: live-ok\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args) -> None:
                return

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)

        proxy_port = self._free_port()
        selected = ProxyProvider(
            provider_id="live",
            name="Live",
            base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
            is_cc_switch_current=True,
            api_key="live-fixture-key",
        )
        router = ProviderRouter((selected,))
        server = LocalProxyServer(router, port=proxy_port)
        server.start()
        self.addCleanup(server.stop)

        response = httpx.post(
            f"http://127.0.0.1:{proxy_port}/v1/responses?stream=true",
            headers={"Authorization": "Bearer local-placeholder"},
            json={"model": "fixture-model"},
            timeout=5,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"data: live-ok\n\n")
        self.assertEqual(observed["path"], "/v1/responses?stream=true")
        self.assertEqual(observed["authorization"], "Bearer live-fixture-key")
        self.assertIn(b"fixture-model", observed["body"])
        self.assertEqual(router.status().active_by_provider, {})

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
