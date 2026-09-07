import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

import httpx

from local_proxy.core import ProviderRouter, ProxyProvider, UsageStore, create_proxy_app
from local_proxy.request_debug import RequestDebugStore


class RequestDebugStoreTests(unittest.TestCase):
    def test_persists_complete_request_attempt_and_redacts_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RequestDebugStore(
                Path(temp_dir) / "request-debug.sqlite3",
                service_id="codex",
            )
            session = store.start_request(
                run_id="run-a",
                request_id=7,
                started_at=100.0,
                method="POST",
                path="/v1/responses",
                query="token=secret&x=1",
                thread_id="thread-a",
                session_name="绘画",
                headers={"Authorization": "Bearer secret", "X-Trace": "trace"},
            )
            session.body(b'{"model":"gpt-5.6","input":[{"role":"user"}]}')
            attempt = session.attempt(
                attempt=1,
                provider_id="provider-a",
                url="https://example.test/v1/responses?api_key=secret",
                headers={"Authorization": "Bearer upstream-secret", "Accept": "text/event-stream"},
                body=b'{"model":"gpt-5.6-sol"}',
            )
            attempt.set_response(
                status_code=200,
                headers={"Set-Cookie": "secret", "Content-Type": "text/event-stream"},
            )
            attempt.response(b"data: first\n\n")
            attempt.response(b"data: second\n\n")
            attempt.forwarded(b"data: forwarded\n\n")
            attempt.finish(state="succeeded")
            session.finish(
                finished_at=101.0,
                state="succeeded",
                outcome="succeeded",
                status_code=200,
            )

            detail = store.get("run-a:7")
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["request_body"]["text"], '{"model":"gpt-5.6","input":[{"role":"user"}]}')
            self.assertEqual(detail["query"], "token=<redacted>&x=1")
            self.assertEqual(detail["request_headers"]["Authorization"], "<redacted>")
            self.assertEqual(detail["attempts"][0]["url"], "https://example.test/v1/responses?api_key=<redacted>")
            self.assertEqual(detail["attempts"][0]["response_body"]["text"], "data: first\n\ndata: second\n\n")
            self.assertEqual(detail["attempts"][0]["forwarded_body"]["text"], "data: forwarded\n\n")
            self.assertEqual(detail["attempts"][0]["response_headers"]["Set-Cookie"], "<redacted>")

    def test_retains_latest_logical_requests_and_recovers_running_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "request-debug.sqlite3"
            store = RequestDebugStore(path, retention=2)
            for request_id in (1, 2, 3):
                session = store.start_request(
                    run_id="run-a",
                    request_id=request_id,
                    started_at=float(request_id),
                    method="POST",
                    path="/v1/responses",
                    query="",
                    thread_id=None,
                    session_name="未知会话",
                    headers={},
                )
                session.finish(finished_at=float(request_id), state="succeeded")
            self.assertEqual([item["request_id"] for item in store.list(limit=10)], [3, 2])

            recovered = RequestDebugStore(path, retention=2)
            item = recovered.get("run-a:3")
            self.assertIsNotNone(item)

            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "UPDATE debug_requests SET state = 'running' WHERE request_key = 'run-a:3'"
                )
            restarted = RequestDebugStore(path, retention=2)
            item = restarted.get("run-a:3")
            self.assertEqual(item["state"], "interrupted")
            self.assertEqual(item["error_kind"], "process_restarted")


class RequestDebugEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_writes_and_exposes_debug_detail(self) -> None:
        async def upstream(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'{"id":"resp_1","output":[]}')

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        with tempfile.TemporaryDirectory() as temp_dir:
            debug_store = RequestDebugStore(Path(temp_dir) / "request-debug.sqlite3")
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            provider = ProxyProvider(
                provider_id="provider-a",
                name="Provider A",
                base_url="https://example.test/v1",
                is_cc_switch_current=True,
                api_key="test-secret",
            )
            app = create_proxy_app(
                ProviderRouter((provider,)),
                client=upstream_client,
                usage_store=usage_store,
                request_debug_store=debug_store,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            try:
                response = await client.post(
                    "/v1/responses",
                    headers={"Authorization": "Bearer downstream-secret"},
                    content=json.dumps({"model": "gpt-5.6", "input": []}),
                )
                self.assertEqual(response.status_code, 200)
                listing = await client.get("/control/api/request-debug")
                self.assertEqual(listing.status_code, 200)
                debug_id = listing.json()["items"][0]["debug_id"]
                detail = await client.get(f"/control/api/request-debug/{debug_id}")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["attempts"][0]["response_status_code"], 200)
                self.assertEqual(detail.json()["request_headers"]["authorization"], "<redacted>")
            finally:
                await client.aclose()
                await upstream_client.aclose()

    async def test_preflight_disconnect_cleans_core_and_debug_state(self) -> None:
        never_continue = asyncio.Event()
        stream_closed = asyncio.Event()

        class StalledPreflight(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    yield b'data: {"type":"response.created"}\n\n'
                    await never_continue.wait()
                finally:
                    stream_closed.set()

            async def aclose(self) -> None:
                stream_closed.set()

        async def upstream(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StalledPreflight(),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        with tempfile.TemporaryDirectory() as temp_dir:
            debug_path = Path(temp_dir) / "request-debug.sqlite3"
            usage_path = Path(temp_dir) / "usage.sqlite3"
            debug_store = RequestDebugStore(debug_path)
            usage_store = UsageStore(usage_path)
            provider = ProxyProvider(
                provider_id="provider-a",
                name="Provider A",
                base_url="https://example.test/v1",
                is_cc_switch_current=True,
                api_key="test-secret",
                wire_api="responses",
            )
            router = ProviderRouter((provider,))
            app = create_proxy_app(
                router,
                client=upstream_client,
                usage_store=usage_store,
                request_debug_store=debug_store,
            )
            route = next(
                route
                for route in app.routes
                if getattr(route, "path", "") == "/v1/{upstream_path:path}"
            )
            from starlette.requests import Request

            body_sent = False

            async def receive():
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {
                        "type": "http.request",
                        "body": b'{"model":"test"}',
                        "more_body": False,
                    }
                return {"type": "http.disconnect"}

            scope = {
                "type": "http",
                "asgi": {"spec_version": "2.4"},
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

            try:
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(
                        route.endpoint("responses", Request(scope, receive)),
                        timeout=2,
                    )
            finally:
                never_continue.set()
                await upstream_client.aclose()

            self.assertTrue(stream_closed.is_set())
            self.assertEqual(router.status().active_request_details, ())
            history = usage_store.request_history(window="24h")
            self.assertEqual(history["total_count"], 1)
            self.assertEqual(history["items"][0]["error_kind"], "client_disconnected")
            self.assertEqual(debug_store.list(limit=5)[0]["state"], "cancelled")
            with closing(sqlite3.connect(usage_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM inflight_requests").fetchone()[0],
                    0,
                )

            restarted = UsageStore(usage_path)
            restarted_history = restarted.request_history(window="24h")
            self.assertFalse(
                any(
                    item["error_kind"] == "process_restarted"
                    for item in restarted_history["items"]
                )
            )

    async def test_terminal_cleanup_precedes_slow_debug_finish(self) -> None:
        debug_finish_started = threading.Event()
        allow_debug_finish = threading.Event()

        class SlowFinishDebugStore(RequestDebugStore):
            def update_request(self, key: str, **fields):
                if fields.get("finished_at") is not None:
                    debug_finish_started.set()
                    allow_debug_finish.wait(timeout=2)
                return super().update_request(key, **fields)

        never_continue = asyncio.Event()

        class CompletedThenStalled(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"response.completed",'
                    b'"response":{"status":"completed"}}\n\n'
                )
                await never_continue.wait()

        async def upstream(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=CompletedThenStalled(),
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_path = Path(temp_dir) / "usage.sqlite3"
            usage_store = UsageStore(usage_path)
            debug_store = SlowFinishDebugStore(Path(temp_dir) / "request-debug.sqlite3")
            provider = ProxyProvider(
                provider_id="provider-a",
                name="Provider A",
                base_url="https://example.test/v1",
                is_cc_switch_current=True,
                api_key="test-secret",
                wire_api="responses",
            )
            router = ProviderRouter((provider,))
            app = create_proxy_app(
                router,
                client=upstream_client,
                usage_store=usage_store,
                request_debug_store=debug_store,
            )
            route = next(
                route
                for route in app.routes
                if getattr(route, "path", "") == "/v1/{upstream_path:path}"
            )
            from starlette.requests import Request

            body_sent = False
            request_disconnect = asyncio.Event()

            async def request_receive():
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {
                        "type": "http.request",
                        "body": b'{"model":"test"}',
                        "more_body": False,
                    }
                await request_disconnect.wait()
                return {"type": "http.disconnect"}

            scope = {
                "type": "http",
                "asgi": {"spec_version": "2.4"},
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
            response = await route.endpoint("responses", Request(scope, request_receive))
            terminal_sent = asyncio.Event()

            async def response_receive():
                await terminal_sent.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if (
                    message["type"] == "http.response.body"
                    and b"response.completed" in message.get("body", b"")
                ):
                    terminal_sent.set()

            try:
                response_task = asyncio.create_task(
                    response(scope, response_receive, send)
                )
                self.assertTrue(
                    await asyncio.to_thread(debug_finish_started.wait, 1)
                )
                self.assertEqual(router.status().active_request_details, ())
                with closing(sqlite3.connect(usage_path)) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM inflight_requests"
                        ).fetchone()[0],
                        0,
                    )
                allow_debug_finish.set()
                await asyncio.wait_for(response_task, timeout=2)
            finally:
                allow_debug_finish.set()
                request_disconnect.set()
                never_continue.set()
                await upstream_client.aclose()

            self.assertEqual(router.status().active_request_details, ())
            with closing(sqlite3.connect(usage_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM inflight_requests").fetchone()[0],
                    0,
                )
            history = usage_store.request_history(window="24h")
            self.assertEqual(history["total_count"], 1)
            self.assertTrue(history["items"][0]["succeeded"])
