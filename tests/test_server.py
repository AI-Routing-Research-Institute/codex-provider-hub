import unittest
import tempfile
from pathlib import Path

import httpx

from local_proxy.claude import ClaudeProxyProvider
from local_proxy.codex import codex_cli_launch_command
from local_proxy.core import ProviderRouter, ProxyProvider, RetryPolicy, TokenUsage, UsageStore, create_proxy_app
from local_proxy.protocols.claude_messages import ClaudeMessagesProtocol
from local_proxy.server import ProxyProfile
from local_proxy.shared_settings import SharedRuntimeCoordinator, SharedSettingsStore


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
            provider_launch_command=codex_cli_launch_command,
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
        missing_header = await self.client.post(
            "/control/codex/api/providers/codex-a/launch-command",
        )
        codex_command = await self.client.post(
            "/control/codex/api/providers/codex-a/launch-command",
            headers={"X-Local-Proxy-Control": "1"},
        )
        claude_command = await self.client.post(
            "/control/claude/api/providers/claude-a/launch-command",
            headers={"X-Local-Proxy-Control": "1"},
        )

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
        self.assertEqual(missing_header.status_code, 403)
        self.assertEqual(codex_command.status_code, 200)
        self.assertEqual(codex_command.headers["cache-control"], "no-store")
        self.assertEqual(codex_command.json()["provider_id"], "codex-a")
        self.assertIn("codex-secret", codex_command.json()["command"])
        self.assertIn("https://codex.example.test/v1", codex_command.json()["command"])
        self.assertEqual(claude_command.status_code, 404)

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

    async def test_both_control_views_update_the_same_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "cc-switch.db"
            database.touch()
            applied: list[str] = []
            for profile in (self.codex_profile, self.claude_profile):
                profile.load_runtime_database = (
                    lambda path, current=profile: current.router.providers()
                )
                profile.apply_runtime_database = (
                    lambda path, providers, current=profile: (
                        applied.append(current.service_id),
                        current.router.replace_providers(providers),
                    )
                )
                profile.database_validation_summary = lambda providers: {
                    "provider_count": len(providers),
                }
                profile.runtime_metadata = lambda current=profile: {
                    "data_directory": str(root),
                    "settings_file": str(root / f"{current.service_id}-settings.json"),
                    "usage_database": str(root / f"{current.service_id}-usage.sqlite3"),
                }
            store = SharedSettingsStore(
                path=root / "shared-settings.json",
                settings={"port": 17890, "database_path": str(database)},
            )
            SharedRuntimeCoordinator(
                store,
                (self.codex_profile, self.claude_profile),
                active_port=17890,
            )
            headers = {"X-Local-Proxy-Control": "1"}

            codex_update = await self.client.post(
                "/control/codex/api/runtime-settings",
                headers=headers,
                json={
                    "port": 18888,
                    "database_path": str(database),
                    "health_status_url": "https://status.example.test/api",
                },
            )
            claude_view = await self.client.get("/control/claude/api/runtime-settings")
            claude_update = await self.client.post(
                "/control/claude/api/runtime-settings",
                headers=headers,
                json={
                    "port": 19999,
                    "database_path": str(database),
                    "health_status_url": None,
                },
            )
            codex_view = await self.client.get("/control/codex/api/runtime-settings")
            retry_update = await self.client.post(
                "/control/claude/api/retry-policy",
                headers=headers,
                json=RetryPolicy(max_attempts=7).as_public_dict(),
            )
            codex_status = await self.client.get("/control/codex/api/status")

        self.assertEqual(codex_update.status_code, 200)
        self.assertEqual(claude_view.json()["configured_port"], 18888)
        self.assertEqual(claude_update.status_code, 200)
        self.assertEqual(codex_view.json()["configured_port"], 19999)
        self.assertEqual(applied, ["codex", "claude", "codex", "claude"])
        self.assertEqual(retry_update.status_code, 200)
        self.assertEqual(codex_status.json()["retry"]["max_attempts"], 7)

    async def test_health_reports_one_service_with_two_protocol_profiles(self) -> None:
        response = await self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "codex-provider-hub")
        self.assertEqual(set(response.json()["services"]), {"codex", "claude"})


if __name__ == "__main__":
    unittest.main()
