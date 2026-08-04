import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx

from claude_local_proxy import (
    ClaudeProxyProvider,
    create_claude_proxy_app,
    load_claude_proxy_providers,
)
from codex_local_proxy import ProviderRouter, RetryPolicy, UsageStore, create_proxy_app
from codex_local_proxy import RecoveryHistoryStore
from provider_proxy_protocol import ClaudeMessagesProtocol


async def no_wait(_: float) -> None:
    return None


def claude_provider(
    provider_id: str = "claude-a",
    *,
    credential_kind: str = "api_key",
) -> ClaudeProxyProvider:
    return ClaudeProxyProvider(
        provider_id=provider_id,
        name=provider_id,
        base_url=f"https://{provider_id}.example.test",
        is_cc_switch_current=True,
        api_key="fixture-upstream-secret",
        credential_kind=credential_kind,
    )


class ClaudeCCSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.database = Path(self.context.name) / "cc-switch.db"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE providers (
                id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                name TEXT NOT NULL,
                settings_config TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}',
                is_current INTEGER NOT NULL DEFAULT 0,
                sort_index INTEGER,
                created_at INTEGER,
                PRIMARY KEY (id, app_type)
            );
            CREATE TABLE provider_endpoints (
                id INTEGER PRIMARY KEY,
                provider_id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                url TEXT NOT NULL
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        connection.execute(
            "INSERT INTO settings VALUES ('common_config_claude', ?)",
            (json.dumps({"env": {"API_TIMEOUT_MS": "60000", "SHARED": "yes"}}),),
        )
        self._insert_provider(
            connection,
            provider_id="api-key",
            name="API Key Provider",
            current=0,
            settings={
                "env": {
                    "ANTHROPIC_BASE_URL": "https://claude.example.test/",
                    "ANTHROPIC_API_KEY": "fixture-api-key",
                    "ANTHROPIC_MODEL": "claude-opus-test",
                    "SHARED": "provider-wins",
                }
            },
            meta={
                "apiFormat": "anthropic",
                "apiKeyField": "ANTHROPIC_API_KEY",
                "commonConfigEnabled": True,
            },
        )
        self._insert_provider(
            connection,
            provider_id="auth-token",
            name="Auth Token Provider",
            current=0,
            settings={"env": {"ANTHROPIC_AUTH_TOKEN": "fixture-auth-token"}},
            meta={"apiFormat": "anthropic"},
            endpoint="https://fallback.example.test",
        )
        self._insert_provider(
            connection,
            provider_id="chat-only",
            name="Chat Only",
            current=1,
            settings={
                "env": {
                    "ANTHROPIC_BASE_URL": "https://chat.example.test",
                    "ANTHROPIC_AUTH_TOKEN": "fixture-chat-token",
                }
            },
            meta={"apiFormat": "openai_chat"},
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _insert_provider(
        connection: sqlite3.Connection,
        *,
        provider_id: str,
        name: str,
        current: int,
        settings: dict,
        meta: dict,
        endpoint: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO providers VALUES (?, 'claude', ?, ?, ?, ?, 1, 1)",
            (provider_id, name, json.dumps(settings), json.dumps(meta), current),
        )
        if endpoint:
            connection.execute(
                "INSERT INTO provider_endpoints(provider_id, app_type, url) VALUES (?, 'claude', ?)",
                (provider_id, endpoint),
            )

    def test_loads_api_key_auth_token_and_incompatible_provider(self) -> None:
        providers = load_claude_proxy_providers(self.database)

        self.assertEqual([item.provider_id for item in providers], ["api-key", "auth-token", "chat-only"])
        self.assertEqual(providers[0].base_url, "https://claude.example.test")
        self.assertEqual(providers[0].credential_kind, "api_key")
        self.assertEqual(providers[0].default_models["model"], "claude-opus-test")
        self.assertEqual(providers[1].base_url, "https://fallback.example.test")
        self.assertEqual(providers[1].credential_kind, "auth_token")
        self.assertTrue(providers[1].compatible)
        self.assertFalse(providers[2].compatible)
        self.assertFalse(providers[2].is_cc_switch_current)
        self.assertNotIn("fixture-api-key", repr(providers[0]))
        self.assertNotIn("fixture-auth-token", repr(providers[1]))

    def test_database_is_opened_read_only(self) -> None:
        load_claude_proxy_providers(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM providers").fetchone()[0], 3)


class ClaudeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_html_gateway_response_is_retried(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html; charset=utf-8"},
                    content=b"<html><title>Gateway</title></html>",
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'event: content_block_delta\ndata: {"type":"content_block_delta",'
                    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
                ),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertNotIn("Gateway", response.text)
        self.assertIn("text_delta", response.text)

    async def test_empty_success_response_is_retried_before_reaching_claude_code(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                body = b""
            else:
                body = (
                    b'event: content_block_delta\ndata: {"type":"content_block_delta",'
                    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertIn("text_delta", response.text)

    async def test_non_anthropic_success_stream_is_retried_before_output(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                body = (
                    b'data: {"choices":[{"delta":{"content":"wrong protocol"}}]}\n\n'
                    b'data: [DONE]\n\n'
                )
            else:
                body = (
                    b'event: content_block_delta\ndata: {"type":"content_block_delta",'
                    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertNotIn("wrong protocol", response.text)
        self.assertIn("text_delta", response.text)

    async def test_anthropic_stream_without_content_is_retried_before_output(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                body = (
                    b'event: message_start\ndata: {"type":"message_start",'
                    b'"message":{"usage":{"input_tokens":10}}}\n\n'
                    b'event: content_block_start\ndata: {"type":"content_block_start",'
                    b'"content_block":{"type":"text","text":""}}\n\n'
                    b'event: content_block_stop\ndata: {"type":"content_block_stop"}\n\n'
                    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
                )
            else:
                body = (
                    b'event: content_block_delta\ndata: {"type":"content_block_delta",'
                    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertNotIn("message_stop", response.text)
        self.assertIn("text_delta", response.text)

    async def test_zero_argument_tool_use_stream_is_not_treated_as_empty(self) -> None:
        attempts = 0
        body = (
            b'event: message_start\ndata: {"type":"message_start","message":{}}\n\n'
            b'event: content_block_start\ndata: {"type":"content_block_start",'
            b'"content_block":{"type":"tool_use","id":"tool-1",'
            b'"name":"status","input":{}}}\n\n'
            b'event: content_block_stop\ndata: {"type":"content_block_stop"}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 1)
        self.assertIn('"type":"tool_use"', response.text)

    async def test_malformed_success_stream_returns_gateway_error_after_retries(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"",
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(max_attempts=2, delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(attempts, 2)
        self.assertEqual(response.status_code, 502)
        self.assertIn("自动重试后仍未恢复", response.text)

    async def test_empty_json_success_response_is_retried(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=b"",
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'event: content_block_delta\ndata: {"type":"content_block_delta",'
                    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
                ),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertIn("text_delta", response.text)

    async def test_stream_usage_is_recorded_from_anthropic_events(self) -> None:
        stream_body = (
            b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
            b'{"input_tokens":12,"cache_read_input_tokens":4,'
            b'"cache_creation_input_tokens":2}}}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta",'
            b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
            b'event: message_delta\ndata: {"type":"message_delta","usage":'
            b'{"output_tokens":3}}\n\n'
        )

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=stream_body,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            app = create_proxy_app(
                ProviderRouter((claude_provider(),)),
                client=upstream_client,
                protocol_adapter=ClaudeMessagesProtocol(),
                usage_store=usage_store,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            )
            try:
                response = await client.post(
                    "/v1/messages",
                    json={"model": "claude-test", "messages": [{"role": "user", "content": "hello"}]},
                )
                summary = usage_store.summary("all")
            finally:
                await client.aclose()
                await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary["total"]["input_tokens"], 12)
        self.assertEqual(summary["total"]["output_tokens"], 3)
        self.assertEqual(summary["total"]["cached_tokens"], 6)
        self.assertEqual(summary["total"]["estimated_requests"], 0)

    async def test_embedded_overload_before_content_is_retried(self) -> None:
        attempts = 0

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                body = (
                    b'event: message_start\ndata: {"type":"message_start","message":{}}\n\n'
                    b'event: error\ndata: {"type":"error","error":'
                    b'{"type":"overloaded_error","message":"busy"}}\n\n'
                )
            else:
                body = (
                    b'event: content_block_delta\ndata: {"type":"content_block_delta",'
                    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
            retry_policy=RetryPolicy(delay_seconds=0.1),
            retry_sleep=no_wait,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempts, 2)
        self.assertIn("text_delta", response.text)

    async def test_auth_token_uses_bearer_header(self) -> None:
        seen: list[httpx.Request] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"type": "message", "content": []})

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(credential_kind="auth_token"),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            await client.post(
                "/v1/messages",
                headers={"x-api-key": "local-placeholder"},
                json={"model": "claude-test"},
            )
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(seen[0].headers["authorization"], "Bearer fixture-upstream-secret")
        self.assertNotIn("x-api-key", seen[0].headers)

    async def test_messages_are_sent_to_anthropic_v1_path(self) -> None:
        seen: list[httpx.Request] = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"type": "message", "content": []})

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
            protocol_adapter=ClaudeMessagesProtocol(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            await client.post("/v1/messages", json={"model": "claude-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(seen[0].url.path, "/v1/messages")

    def test_existing_v1_base_url_is_not_duplicated(self) -> None:
        selected = ClaudeProxyProvider(
            provider_id="versioned",
            name="Versioned",
            base_url="https://versioned.example.test/v1",
            is_cc_switch_current=True,
            api_key="fixture-secret",
        )

        url = ClaudeMessagesProtocol().upstream_url(selected, "messages")

        self.assertEqual(url, "https://versioned.example.test/v1/messages")

    async def test_missing_usage_is_estimated_for_successful_message(self) -> None:
        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "type": "message",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "hello back"}],
                },
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            app = create_proxy_app(
                ProviderRouter((claude_provider(),)),
                client=upstream_client,
                protocol_adapter=ClaudeMessagesProtocol(),
                usage_store=usage_store,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            )
            try:
                await client.post(
                    "/v1/messages",
                    json={
                        "model": "claude-test",
                        "system": "be concise",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )
                summary = usage_store.summary("all")["total"]
            finally:
                await client.aclose()
                await upstream_client.aclose()

        self.assertGreater(summary["input_tokens"], 0)
        self.assertGreater(summary["output_tokens"], 0)
        self.assertEqual(summary["estimated_requests"], 1)

    async def test_anthropic_error_after_content_is_recorded_without_replay(self) -> None:
        attempts = 0
        body = (
            b'event: content_block_delta\ndata: {"type":"content_block_delta",'
            b'"delta":{"type":"text_delta","text":"partial"}}\n\n'
            b'event: error\ndata: {"type":"error","error":'
            b'{"type":"overloaded_error","message":"busy"}}\n\n'
        )

        async def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            history_store = RecoveryHistoryStore(Path(temp_dir) / "usage.sqlite3")
            upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            app = create_proxy_app(
                ProviderRouter((claude_provider(),)),
                client=upstream_client,
                protocol_adapter=ClaudeMessagesProtocol(),
                recovery_history_store=history_store,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            )
            try:
                response = await client.post("/v1/messages", json={"model": "claude-test"})
                history = history_store.history()
            finally:
                await client.aclose()
                await upstream_client.aclose()

        self.assertEqual(attempts, 1)
        self.assertIn("partial", response.text)
        self.assertEqual(history["total_count"], 1)
        self.assertEqual(history["items"][0]["stage"], "after_output")


class ClaudeProxyAppTests(unittest.IsolatedAsyncioTestCase):
    def test_production_app_uses_curl_client_factory(self) -> None:
        curl_client = object()
        with patch("claude_local_proxy.ClaudeCurlClient", return_value=curl_client) as factory:
            app = create_claude_proxy_app(ProviderRouter((claude_provider(),)))

        factory.assert_called_once_with()
        self.assertIsNotNone(app)

    def test_injected_client_does_not_create_curl_client(self) -> None:
        injected = object()
        with patch("claude_local_proxy.ClaudeCurlClient") as factory:
            app = create_claude_proxy_app(
                ProviderRouter((claude_provider(),)),
                client=injected,
            )

        factory.assert_not_called()
        self.assertIsNotNone(app)

    async def test_status_identifies_claude_service_and_provider_compatibility(self) -> None:
        compatible = claude_provider("compatible")
        incompatible = ClaudeProxyProvider(
            provider_id="chat-only",
            name="Chat Only",
            base_url="https://chat.example.test",
            is_cc_switch_current=False,
            api_key="fixture-secret",
            api_format="openai_chat",
            compatible=False,
        )
        app = create_claude_proxy_app(ProviderRouter((compatible, incompatible)))
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            status = await client.get("/control/api/status")
            rejected = await client.post(
                "/control/api/providers/chat-only/select",
                headers={"X-Local-Proxy-Control": "1"},
            )
        finally:
            await client.aclose()

        payload = status.json()
        self.assertEqual(payload["service"], "claude-local-proxy")
        self.assertEqual(payload["current_provider_id"], "compatible")
        self.assertTrue(payload["providers"][0]["compatible"])
        self.assertFalse(payload["providers"][1]["compatible"])
        self.assertEqual(rejected.status_code, 409)

    async def test_only_messages_proxy_path_is_available(self) -> None:
        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"type": "message", "content": []})

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_claude_proxy_app(
            ProviderRouter((claude_provider(),)),
            client=upstream_client,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            messages = await client.post("/v1/messages", json={"model": "claude-test"})
            count_tokens = await client.post(
                "/v1/messages/count_tokens", json={"model": "claude-test"}
            )
            responses = await client.post("/v1/responses", json={"model": "gpt-test"})
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(messages.status_code, 200)
        self.assertEqual(count_tokens.status_code, 200)
        self.assertEqual(responses.status_code, 404)

    async def test_control_assets_and_claude_config_endpoint(self) -> None:
        app = create_claude_proxy_app(
            ProviderRouter((claude_provider(),)),
            config_fragment=lambda: json.dumps(
                {
                    "powershell": '$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:17891"\n',
                    "bash": 'export ANTHROPIC_BASE_URL="http://127.0.0.1:17891"\n',
                }
            ),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            page = await client.get("/control/")
            script = await client.get("/control/static/app.js")
            config = await client.get("/control/api/claude-config")
            old_config = await client.get("/control/api/codex-config")
        finally:
            await client.aclose()

        self.assertIn("Claude Code 本地中转", page.text)
        self.assertIn("127.0.0.1:17891", page.text)
        self.assertIn("http://127.0.0.1:17890/control/", page.text)
        self.assertIn("/control/api/claude-config", script.text)
        self.assertEqual(config.status_code, 200)
        self.assertEqual(old_config.status_code, 404)


if __name__ == "__main__":
    unittest.main()
