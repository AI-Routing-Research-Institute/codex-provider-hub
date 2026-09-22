import tempfile
import time
import unittest
from pathlib import Path

import httpx

from local_proxy.core import (
    ProxyProvider,
    ProviderRouter,
    RetryPolicy,
    TokenUsage,
    UsageCapture,
    UsageStore,
    create_proxy_app,
)
from local_proxy.protocols.claude_messages import ClaudeUsageCapture
from local_proxy.response_models import (
    UpstreamResponseModelObserver,
    canonical_model_name,
    upstream_model_mismatch,
)


class ResponseModelTests(unittest.TestCase):
    def test_observer_prefers_terminal_openai_response_model(self) -> None:
        observer = UpstreamResponseModelObserver()
        observer.observe({"type": "response.created", "response": {"model": "gpt-5"}})
        observer.observe({"type": "response.completed", "response": {"model": "gpt-5.6"}})

        self.assertEqual(observer.model, "gpt-5.6")
        self.assertTrue(observer.conflict)

    def test_observer_marks_conflicting_sse_models(self) -> None:
        observer = UpstreamResponseModelObserver()
        observer.observe({"model": "gpt-5"})
        observer.observe({"model": "gpt-6"})

        self.assertEqual(observer.model, "gpt-5")
        self.assertTrue(observer.conflict)

    def test_usage_capture_reads_json_and_sse_models(self) -> None:
        capture = UsageCapture(b'{"model":"gpt-request"}', "responses")
        capture.feed(b'data: {"type":"response.completed","response":{"model":"gpt-upstream"}}\n\n')
        capture.finalize(200)

        self.assertEqual(capture.upstream_response_model, "gpt-upstream")
        self.assertEqual(upstream_model_mismatch("gpt-request", capture.upstream_response_model), True)

    def test_claude_capture_reads_message_model(self) -> None:
        capture = ClaudeUsageCapture(b'{"model":"claude-request"}', "messages")
        capture.feed(b'{"type":"message_start","message":{"model":"claude-upstream"}}')
        capture.finalize(200)

        self.assertEqual(capture.upstream_response_model, "claude-upstream")

    def test_model_variants_are_not_reported_as_mismatch(self) -> None:
        self.assertEqual(canonical_model_name("gpt-5-latest"), "gpt-5")
        self.assertEqual(canonical_model_name("gpt-5-20250513"), "gpt-5")
        self.assertIs(upstream_model_mismatch("gpt-5", "gpt-5-latest"), False)
        self.assertIsNone(upstream_model_mismatch("gpt-5", None))

    def test_store_persists_response_model_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            store.record_request(
                started_at=1_000,
                provider_id="provider",
                thread_id=None,
                session_name="session",
                model="gpt-request",
                upstream_model="gpt-request",
                upstream_response_model="gpt-upstream",
                upstream_model_mismatch=True,
                status_code=200,
                successful=True,
                outcome="succeeded",
                retry_count=0,
                usage=TokenUsage(1, 2, 3),
                finished_at=1_001,
            )

            item = store.request_history(window="24h", now=1_002)["items"][0]

        self.assertEqual(item["upstream_response_model"], "gpt-upstream")
        self.assertIs(item["upstream_model_mismatch"], True)


class ResponseModelProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_persists_model_declared_by_upstream_json(self) -> None:
        async def upstream(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "gpt-upstream",
                    "output": [],
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                },
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            usage_store = UsageStore(Path(temp_dir) / "usage.sqlite3")
            router = ProviderRouter(
                (
                    ProxyProvider(
                        "provider",
                        "Provider",
                        "https://upstream.example/v1",
                        True,
                        api_key="secret",
                    ),
                )
            )
            upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
            app = create_proxy_app(
                router,
                client=upstream_client,
                retry_policy=RetryPolicy(enabled=False),
                usage_store=usage_store,
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            try:
                response = await client.post(
                    "/v1/responses",
                    json={"model": "gpt-request", "input": []},
                )
            finally:
                await client.aclose()
                await upstream_client.aclose()

            item = usage_store.request_history(window="24h", now=time.time())["items"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(item["upstream_response_model"], "gpt-upstream")
        self.assertIs(item["upstream_model_mismatch"], True)


if __name__ == "__main__":
    unittest.main()
