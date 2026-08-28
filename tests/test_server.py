import asyncio
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx

from local_proxy.claude import ClaudeProxyProvider
from local_proxy.codex import codex_cli_launch_command
from local_proxy.core import ProviderRouter, ProxyProvider, RetryPolicy, TokenUsage, UsageStore, create_proxy_app
from local_proxy.diagnostics import DiagnosticLog
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

    async def test_codex_request_uses_profile_client_selector(self) -> None:
        compatible_requests: list[httpx.Request] = []

        async def compatible_upstream(request: httpx.Request) -> httpx.Response:
            compatible_requests.append(request)
            return httpx.Response(200, content=b"compatible")

        compatible_client = httpx.AsyncClient(
            transport=httpx.MockTransport(compatible_upstream)
        )
        self.addAsyncCleanup(compatible_client.aclose)
        selected = ProxyProvider(
            provider_id="codex-compatible",
            name="Codex Compatible",
            base_url="https://compatible.example.test/v1",
            is_cc_switch_current=True,
            api_key="compatible-secret",
            transport="curl_cffi",
        )
        self.codex_profile.router.replace_providers((selected,))
        self.codex_profile.client_selector = lambda provider: (
            compatible_client
            if provider.transport == "curl_cffi"
            else self.codex_client
        )

        response = await self.client.post(
            "/v1/responses",
            json={"model": "gpt-test"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"compatible")
        self.assertEqual(len(compatible_requests), 1)
        self.assertEqual(self.codex_requests, [])

    async def test_status_upload_routes_preview_and_send_backend_payload(self) -> None:
        class FakeManager:
            def __init__(self):
                self.payload = None

            def public_settings(self):
                return {"host": "status.example", "port": 22, "initialized": True}

            def upload(self, payload):
                self.payload = payload
                return {"status": "imported", "provider_id": payload["provider_id"]}

        manager = FakeManager()
        self.codex_profile.status_upload_manager = manager
        self.codex_profile.status_upload_preview = lambda provider, models: {
            "supported": True,
            "suggested_models": list(models) or ["gpt-test"],
        }
        self.codex_profile.status_upload_payload = lambda provider, models: {
            "provider_id": provider.provider_id,
            "models": list(models),
            "credential": provider.api_key,
        }

        settings = await self.client.get("/control/codex/api/status-upload/settings")
        preview = await self.client.post(
            "/control/codex/api/providers/codex-a/status-upload/preview",
            headers={"X-Local-Proxy-Control": "1"},
            json={"models": []},
        )
        uploaded = await self.client.post(
            "/control/codex/api/providers/codex-a/status-upload",
            headers={"X-Local-Proxy-Control": "1"},
            json={"models": ["gpt-test"]},
        )

        self.assertEqual(settings.status_code, 200)
        self.assertEqual(preview.json()["suggested_models"], ["gpt-test"])
        self.assertEqual(uploaded.json()["status"], "imported")
        self.assertEqual(manager.payload["credential"], "codex-secret")

    async def test_status_management_proxies_manual_probe_through_backend(self) -> None:
        class FakeManager:
            def __init__(self):
                self.probe = None

            def manual_probe(self, provider_id, models):
                self.probe = (provider_id, models)
                return {"status": "queued", "provider_id": provider_id}

        manager = FakeManager()
        self.codex_profile.status_upload_manager = manager

        response = await self.client.post(
            "/control/codex/api/status-management/providers/remote-a/probe",
            headers={"X-Local-Proxy-Control": "1"},
            json={"models": ["gpt-test"]},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(manager.probe, ("remote-a", ("gpt-test",)))

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
        script_match = re.search(r'src="\./static/(assets/[^"]+\.js)"', codex_page.text)
        style_match = re.search(r'href="\./static/(assets/[^"]+\.css)"', codex_page.text)
        self.assertIsNotNone(script_match)
        self.assertIsNotNone(style_match)
        codex_script = await self.client.get(
            f"/control/codex/static/{script_match.group(1)}"
        )
        claude_script = await self.client.get(
            f"/control/claude/static/{script_match.group(1)}"
        )
        codex_styles = await self.client.get(
            f"/control/codex/static/{style_match.group(1)}"
        )
        claude_styles = await self.client.get(
            f"/control/claude/static/{style_match.group(1)}"
        )
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
        self.assertEqual(codex_script.status_code, 200)
        self.assertEqual(codex_script.content, claude_script.content)
        self.assertEqual(codex_styles.status_code, 200)
        self.assertEqual(codex_styles.content, claude_styles.content)
        traversal = await self.client.get("/control/codex/static/../index.html")
        self.assertEqual(traversal.status_code, 404)
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
        self.assertIn("event_loop_lag_ms", response.json()["diagnostics"])
        self.assertIn("watchdog_event_count", response.json()["diagnostics"])
        self.assertIn("last_watchdog_at", response.json()["diagnostics"])

    async def test_health_reports_diagnostic_log_path_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "proxy-diagnostics.log"
            diagnostic_log = DiagnosticLog(path)
            app = create_proxy_app(
                codex_profile=self.codex_profile,
                claude_profile=self.claude_profile,
                diagnostic_log=diagnostic_log,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            try:
                async with app.router.lifespan_context(app):
                    response = await client.get("/healthz")
            finally:
                await client.aclose()

            self.assertEqual(
                response.json()["diagnostics"]["diagnostic_log_path"],
                str(path.resolve()),
            )
            log_text = path.read_text(encoding="utf-8")
            self.assertIn('"event":"service_started"', log_text)
            self.assertIn('"event":"service_stopped"', log_text)

    async def test_slow_request_history_does_not_block_health_endpoint(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class SlowUsageStore:
            def request_history(self, **kwargs):
                started.set()
                release.wait(timeout=2)
                return {
                    "window": kwargs["window"],
                    "total_count": 0,
                    "items": [],
                    "next_cursor": None,
                }

        self.codex_profile.usage_store = SlowUsageStore()
        request_task = asyncio.create_task(
            self.client.get("/control/codex/api/requests", params={"window": "24h"})
        )
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        try:
            health = await asyncio.wait_for(self.client.get("/healthz"), timeout=0.5)
            self.assertEqual(health.status_code, 200)
            self.assertFalse(request_task.done())
        finally:
            release.set()
        response = await asyncio.wait_for(request_task, timeout=1)
        self.assertEqual(response.status_code, 200)

    async def test_request_history_resolves_session_names_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir) / "codex.sqlite3")
            now = time.time()
            for offset, thread_id in enumerate(("thread-a", "thread-b")):
                store.record_request(
                    started_at=now - offset - 1,
                    finished_at=now - offset,
                    provider_id="codex-a",
                    thread_id=thread_id,
                    session_name="旧名称",
                    model="gpt-test",
                    status_code=200,
                    successful=True,
                    outcome="succeeded",
                    retry_count=0,
                )
            resolver_calls: list[set[str]] = []

            def resolve_session_names(thread_ids):
                requested = set(thread_ids)
                resolver_calls.append(requested)
                return {thread_id: f"名称-{thread_id}" for thread_id in requested}

            self.codex_profile.usage_store = store
            self.codex_profile.session_name_resolver = resolve_session_names
            response = await self.client.get(
                "/control/codex/api/requests",
                params={"window": "24h"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolver_calls, [{"thread-a", "thread-b"}])
        self.assertEqual(
            {item["session_name"] for item in response.json()["items"]},
            {"名称-thread-a", "名称-thread-b"},
        )


class FakeUpdateController:
    def __init__(self, *, supported=True, check_error=None, download_error=None):
        self.supported = supported
        self._check_error = check_error
        self._download_error = download_error
        self.finalized = False
        self.downloaded = False

    def status(self):
        return {
            "supported": self.supported,
            "current_version": "0.7.1",
            "has_update": True,
            "latest_version": "0.8.0",
            "release_url": "https://example/release",
            "notes": "",
        }

    def check(self):
        if self._check_error is not None:
            raise self._check_error
        return self.status()

    def download(self):
        if self._download_error is not None:
            raise self._download_error
        self.downloaded = True
        return Path("/tmp/new.exe")

    def finalize(self):
        self.finalized = True


class UpdateRouteTests(unittest.IsolatedAsyncioTestCase):
    def _build(self, controller):
        codex = ProxyProfile(
            service_id="codex",
            service_name="codex-local-proxy",
            router=ProviderRouter((codex_provider(),)),
            upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
            owns_client=False,
            config_endpoint_name="codex-config",
        )
        claude = ProxyProfile(
            service_id="claude",
            service_name="claude-local-proxy",
            router=ProviderRouter((claude_provider(),)),
            upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
            owns_client=False,
            protocol_adapter=ClaudeMessagesProtocol(),
            config_endpoint_name="claude-config",
        )
        app = create_proxy_app(
            codex_profile=codex,
            claude_profile=claude,
            update_controller=controller,
        )
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def test_status_returns_version_without_controller(self) -> None:
        client = self._build(None)
        try:
            response = await client.get("/control/codex/api/update")
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["supported"])

    async def test_check_returns_update_info(self) -> None:
        client = self._build(FakeUpdateController())
        try:
            response = await client.post(
                "/control/codex/api/update/check",
                headers={"X-Local-Proxy-Control": "1"},
            )
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latest_version"], "0.8.0")

    async def test_check_requires_control_header(self) -> None:
        client = self._build(FakeUpdateController())
        try:
            response = await client.post("/control/codex/api/update/check")
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 403)

    async def test_check_maps_update_error_to_502(self) -> None:
        from local_proxy.updater import UpdateError

        client = self._build(FakeUpdateController(check_error=UpdateError("检查更新失败：HTTP 403")))
        try:
            response = await client.post(
                "/control/codex/api/update/check",
                headers={"X-Local-Proxy-Control": "1"},
            )
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 502)
        self.assertIn("403", response.json()["detail"])
        self.assertIn("release_url", response.json())

    async def test_apply_rejects_unsupported_platform(self) -> None:
        controller = FakeUpdateController(supported=False)
        client = self._build(controller)
        try:
            response = await client.post(
                "/control/codex/api/update/apply",
                headers={"X-Local-Proxy-Control": "1"},
            )
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(controller.finalized)

    async def test_apply_downloads_then_schedules_finalize(self) -> None:
        controller = FakeUpdateController()
        client = self._build(controller)
        try:
            response = await client.post(
                "/control/codex/api/update/apply",
                headers={"X-Local-Proxy-Control": "1"},
            )
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "restarting")
        self.assertTrue(controller.downloaded)
        self.assertTrue(controller.finalized)


if __name__ == "__main__":
    unittest.main()
