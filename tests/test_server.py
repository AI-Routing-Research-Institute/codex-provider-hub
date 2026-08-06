import unittest
import tempfile
from pathlib import Path

import httpx

from local_proxy.claude import ClaudeProxyProvider
from local_proxy.core import ProviderRouter, ProxyProvider, TokenUsage, UsageStore, create_proxy_app
from local_proxy.protocols.claude_messages import ClaudeMessagesProtocol
from local_proxy.server import ProxyProfile


def codex_provider() -> ProxyProvider:
    return ProxyProvider(
        provider_id="codex-a",
        name="Codex A",
        base_url="https://codex.example.test/v1",
        is_cc_switch_current=True,
        api_key="codex-secret",
    )


def claude_provider() -> ClaudeProxyProvider:
    return ClaudeProxyProvider(
        provider_id="claude-a",
        name="Claude A",
        base_url="https://claude.example.test",
        is_cc_switch_current=True,
        api_key="claude-secret",
    )


class UnifiedProxyAppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.codex_requests: list[httpx.Request] = []
        self.claude_requests: list[httpx.Request] = []

        async def codex_upstream(request: httpx.Request) -> httpx.Response:
            self.codex_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "output": [],
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                },
            )

        async def claude_upstream(request: httpx.Request) -> httpx.Response:
            self.claude_requests.append(request)
            if request.url.path.endswith("count_tokens"):
                return httpx.Response(200, json={"input_tokens": 3})
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "content": [],
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                },
            )

        self.codex_client = httpx.AsyncClient(transport=httpx.MockTransport(codex_upstream))
        self.claude_client = httpx.AsyncClient(transport=httpx.MockTransport(claude_upstream))
        codex = ProxyProfile(
            service_id="codex",
            service_name="codex-local-proxy",
            router=ProviderRouter((codex_provider(),)),
            upstream_client=self.codex_client,
            owns_client=False,
            ui_config=lambda: {
                "service_id": "codex",
                "config_endpoint": "/control/codex/api/codex-config",
                "api_key": "must-not-leak",
            },
            config_fragment=lambda: "codex-config",
            config_endpoint_name="codex-config",
        )
        claude = ProxyProfile(
            service_id="claude",
            service_name="claude-local-proxy",
            router=ProviderRouter((claude_provider(),)),
            upstream_client=self.claude_client,
            owns_client=False,
            protocol_adapter=ClaudeMessagesProtocol(),
            allowed_proxy_paths=frozenset({"messages", "messages/count_tokens"}),
            ui_config=lambda: {
                "service_id": "claude",
                "config_endpoint": "/control/claude/api/claude-config",
            },
            config_fragment=lambda: "claude-config",
            config_endpoint_name="claude-config",
        )
        self.codex_profile = codex
        self.claude_profile = claude
        self.app = create_proxy_app(codex_profile=codex, claude_profile=claude)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.codex_client.aclose()
        await self.claude_client.aclose()

    async def test_routes_messages_to_claude_and_other_v1_paths_to_codex(self) -> None:
        responses = await self.client.post("/v1/responses", json={"model": "gpt-test"})
        messages = await self.client.post("/v1/messages", json={"model": "claude-test"})
        count_tokens = await self.client.post(
            "/v1/messages/count_tokens", json={"model": "claude-test"}
        )
        unversioned = await self.client.post("/messages", json={"model": "claude-test"})

        self.assertEqual(responses.status_code, 200)
        self.assertEqual(messages.status_code, 200)
        self.assertEqual(count_tokens.status_code, 200)
        self.assertEqual(unversioned.status_code, 404)
        self.assertEqual([request.url.path for request in self.codex_requests], ["/v1/responses"])
        self.assertEqual(
            [request.url.path for request in self.claude_requests],
            ["/v1/messages", "/v1/messages/count_tokens"],
        )

    async def test_control_views_share_assets_and_keep_service_state_separate(self) -> None:
        root = await self.client.get("/control/", follow_redirects=False)
        codex_page = await self.client.get("/control/codex/")
        claude_page = await self.client.get("/control/claude/")
        codex_script = await self.client.get("/control/codex/static/app.js")
        claude_script = await self.client.get("/control/claude/static/app.js")
        codex_config = await self.client.get("/control/codex/api/ui-config")
        claude_config = await self.client.get("/control/claude/api/ui-config")
        codex_fragment = await self.client.get("/control/codex/api/codex-config")
        claude_fragment = await self.client.get("/control/claude/api/claude-config")
        codex_status = await self.client.get("/control/codex/api/status")
        claude_status = await self.client.get("/control/claude/api/status")

        self.assertEqual(root.status_code, 307)
        self.assertEqual(root.headers["location"], "/control/codex/")
        self.assertEqual(codex_page.content, claude_page.content)
        self.assertEqual(codex_script.content, claude_script.content)
        self.assertEqual(codex_config.json()["service_id"], "codex")
        self.assertEqual(claude_config.json()["service_id"], "claude")
        self.assertEqual(codex_config.headers["cache-control"], "no-store")
        self.assertNotIn("api_key", codex_config.json())
        self.assertNotIn("codex-secret", codex_config.text)
        self.assertNotIn("claude-secret", claude_config.text)
        self.assertEqual(codex_fragment.status_code, 200)
        self.assertEqual(claude_fragment.status_code, 200)
        self.assertEqual(codex_status.json()["current_provider_id"], "codex-a")
        self.assertEqual(claude_status.json()["current_provider_id"], "claude-a")

    async def test_usage_history_is_kept_separate_by_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.codex_profile.usage_store = UsageStore(root / "codex.sqlite3")
            self.claude_profile.usage_store = UsageStore(root / "claude.sqlite3")
            self.codex_profile.usage_store.record(
                provider_id="codex-a",
                model="gpt-test",
                usage=TokenUsage(5, 2, 7),
                status_code=200,
            )
            self.claude_profile.usage_store.record(
                provider_id="claude-a",
                model="claude-test",
                usage=TokenUsage(11, 3, 14),
                status_code=200,
            )

            codex_history = await self.client.get(
                "/control/codex/api/usage-history",
                params={"provider_id": "codex-a", "usage_window": "all"},
            )
            claude_history = await self.client.get(
                "/control/claude/api/usage-history",
                params={"provider_id": "claude-a", "usage_window": "all"},
            )
            crossed = await self.client.get(
                "/control/claude/api/usage-history",
                params={"provider_id": "codex-a", "usage_window": "all"},
            )

        self.assertEqual(codex_history.json()["items"][0]["model"], "gpt-test")
        self.assertEqual(claude_history.json()["items"][0]["model"], "claude-test")
        self.assertEqual(crossed.status_code, 404)

    async def test_health_reports_one_service_with_two_protocol_profiles(self) -> None:
        response = await self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "codex-provider-hub")
        self.assertEqual(set(response.json()["services"]), {"codex", "claude"})


if __name__ == "__main__":
    unittest.main()
