import json
import unittest
from collections.abc import AsyncIterator

import httpx

from local_proxy.core import ProviderRouter, ProxyProvider, RetryPolicy, create_proxy_app
from local_proxy.protocols.deepseek_dsml import (
    DeepSeekDSMLProtocol,
    DeepSeekProtocolError,
    is_deepseek_provider,
    parse_dsml_tool_calls,
)


async def _chunks(values: list[bytes]) -> AsyncIterator[bytes]:
    for value in values:
        yield value


def _sse(root: dict) -> bytes:
    return (
        f"event: {root['type']}\n".encode()
        + b"data: "
        + json.dumps(root, separators=(",", ":")).encode()
        + b"\n\n"
    )


class DeepSeekDSMLParserTests(unittest.TestCase):
    def test_parses_official_function_calls_with_self_closing_parameters(self) -> None:
        value = (
            '<\uFF5CDSML\uFF5Cfunction_calls>'
            '<\uFF5CDSML\uFF5Cinvoke name="weather" call_id="call-1">'
            '<\uFF5CDSML\uFF5Cparameter name="city" string="Paris"/>'
            '</\uFF5CDSML\uFF5Cinvoke>'
            '</\uFF5CDSML\uFF5Cfunction_calls>'
        )

        calls = parse_dsml_tool_calls(value, allowed_tool_names={"weather"})

        self.assertEqual(
            calls,
            (
                {
                    "name": "weather",
                    "arguments": '{"city":"Paris"}',
                    "call_id": "call-1",
                    "item_id": calls[0]["item_id"],
                },
            ),
        )

    def test_parses_halfwidth_markers_and_name_parameters(self) -> None:
        value = (
            '<|DSML|tool_calls><name>weather</name>'
            '<parameters>{"city":"Paris"}</parameters>'
            '</|DSML|tool_calls>'
        )

        calls = parse_dsml_tool_calls(value)

        self.assertEqual(calls[0]["name"], "weather")
        self.assertEqual(calls[0]["arguments"], '{"city":"Paris"}')

    def test_parses_spaced_double_pipe_markers_from_rendered_tool_output(self) -> None:
        value = (
            '< | | DSML | | tool_calls>'
            '< | | DSML | | invoke name="weather">'
            '< | | DSML | | parameter name="city" string="Paris">'
            'Paris< / | | DSML | | parameter>'
            '< / | | DSML | | invoke>'
            '< / | | DSML | | tool_calls>'
        )

        calls = parse_dsml_tool_calls(value, allowed_tool_names={"weather"})

        self.assertEqual(calls[0]["name"], "weather")
        self.assertEqual(calls[0]["arguments"], '{"city":"Paris"}')

    def test_rejects_unknown_tool_names(self) -> None:
        value = (
            '<\uFF5CDSML\uFF5Cfunction_calls>'
            '<\uFF5CDSML\uFF5Cinvoke name="unknown">'
            '<\uFF5CDSML\uFF5Cparameter name="value" string="1"/>'
            '</\uFF5CDSML\uFF5Cinvoke>'
            '</\uFF5CDSML\uFF5Cfunction_calls>'
        )

        with self.assertRaises(DeepSeekProtocolError):
            parse_dsml_tool_calls(value, allowed_tool_names={"weather"})


class DeepSeekDSMLProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_conversion_buffers_split_dsml_and_emits_responses_events(self) -> None:
        adapter = DeepSeekDSMLProtocol()
        source = b"".join(
            (
                _sse(
                    {
                        "type": "response.created",
                        "response": {"id": "resp-1"},
                    }
                ),
                _sse(
                    {
                        "type": "response.output_text.delta",
                        "delta": (
                            '<|DSML|function_calls><|DSML|invoke name="weather">'
                            '<|DSML|parameter name="city" string="Paris"/>'
                            '</|DSML|invoke></|DSML|function_calls>'
                        ),
                    }
                ),
                _sse(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp-1"},
                    }
                ),
                b"data: [DONE]\n\n",
            )
        )
        split_at = len(source) // 2

        output: list[bytes] = []
        async for chunk in adapter.transform_stream(
            source[:split_at],
            _chunks([source[split_at:]]),
            request_body=b'{"tools":[{"type":"function","name":"weather"}]}',
            model="deepseek-v4",
        ):
            output.append(chunk)

        converted = b"".join(output)
        self.assertNotIn(b"DSML", converted)
        self.assertIn(b"response.function_call_arguments.delta", converted)
        self.assertIn(b'"name":"weather"', converted)
        self.assertIn(b'"id":"resp-1"', converted)
        self.assertIn(b"response.completed", converted)

    async def test_stream_conversion_detects_split_dsml_after_forwarded_prefix(self) -> None:
        adapter = DeepSeekDSMLProtocol()
        prefix = "I will inspect the local records first.\n\n"
        deltas = (
            prefix,
            "<",
            "｜｜DSML｜｜",
            "tool",
            "_c",
            "alls",
            ">\n",
            '<｜｜DSML｜｜invoke name="exec">',
            '<｜｜DSML｜｜parameter name="input" string="true">Get-ChildItem',
            '</｜｜DSML｜｜parameter>',
            '</｜｜DSML｜｜invoke>',
            '<｜｜DSML｜｜invoke name="exec">',
            '<｜｜DSML｜｜parameter name="input" string="true">rg --files',
            '</｜｜DSML｜｜parameter>',
            '</｜｜DSML｜｜invoke>',
            '</｜｜DSML｜｜tool_calls>',
        )
        source = b"".join(
            (
                _sse(
                    {
                        "type": "response.created",
                        "response": {"id": "resp-prefixed"},
                        "sequence_number": 0,
                    }
                ),
                _sse(
                    {
                        "type": "response.reasoning_text.delta",
                        "item_id": "reasoning-1",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "private analysis",
                        "sequence_number": 1,
                    }
                ),
                _sse(
                    {
                        "type": "response.output_item.added",
                        "output_index": 1,
                        "item": {
                            "id": "msg-prefix",
                            "type": "message",
                            "role": "assistant",
                            "status": "in_progress",
                            "content": [],
                        },
                        "sequence_number": 2,
                    }
                ),
                _sse(
                    {
                        "type": "response.content_part.added",
                        "item_id": "msg-prefix",
                        "output_index": 1,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": ""},
                        "sequence_number": 3,
                    }
                ),
                *(
                    _sse(
                        {
                            "type": "response.output_text.delta",
                            "item_id": "msg-prefix",
                            "output_index": 1,
                            "content_index": 0,
                            "delta": delta,
                            "sequence_number": index + 4,
                        }
                    )
                    for index, delta in enumerate(deltas)
                ),
                _sse(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp-prefixed", "status": "completed"},
                        "sequence_number": len(deltas) + 4,
                    }
                ),
                b"data: [DONE]\n\n",
            )
        )

        output: list[bytes] = []
        async for chunk in adapter.transform_stream(
            source[:11],
            _chunks([source[11:]]),
            request_body=b'{"tools":[{"type":"function","name":"exec"}]}',
            model="deepseek-v4",
        ):
            output.append(chunk)

        converted = b"".join(output)
        self.assertNotIn(b"DSML", converted)
        roots = []
        for event in converted.replace(b"\r\n", b"\n").split(b"\n\n"):
            payload = b"\n".join(
                line[5:].lstrip()
                for line in event.split(b"\n")
                if line.startswith(b"data:")
            )
            if payload and payload != b"[DONE]":
                roots.append(json.loads(payload))
        forwarded_prefix = "".join(
            root.get("delta", "")
            for root in roots
            if root.get("type") == "response.output_text.delta"
        )
        self.assertEqual(forwarded_prefix, prefix)
        self.assertTrue(
            any(
                root.get("type") == "response.reasoning_text.delta"
                and root.get("delta") == "private analysis"
                for root in roots
            ),
            [root.get("type") for root in roots],
        )
        self.assertTrue(
            any(
                root.get("type") == "response.output_text.done"
                and root.get("text") == prefix
                for root in roots
            )
        )
        self.assertIn(b'"id":"msg-prefix"', converted)
        self.assertIn(b'"name":"exec"', converted)
        self.assertIn(b'"arguments":"{\\"input\\":\\"Get-ChildItem\\"}"', converted)
        self.assertIn(b'"arguments":"{\\"input\\":\\"rg --files\\"}"', converted)
        self.assertIn(b"response.function_call_arguments.delta", converted)
        self.assertEqual(
            sum(
                root.get("type") == "response.output_item.added"
                and isinstance(root.get("item"), dict)
                and root["item"].get("type") == "function_call"
                for root in roots
            ),
            2,
        )
        self.assertIn(b'"id":"resp-prefixed"', converted)

    async def test_standard_responses_stream_is_unchanged(self) -> None:
        adapter = DeepSeekDSMLProtocol()
        source = b"".join(
            (
                _sse(
                    {
                        "type": "response.created",
                        "response": {"id": "resp-2"},
                    }
                ),
                _sse(
                    {
                        "type": "response.output_text.delta",
                        "delta": "normal <",
                    }
                ),
                _sse(
                    {
                        "type": "response.output_text.delta",
                        "delta": " comparison",
                    }
                ),
                _sse(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp-2", "status": "completed"},
                    }
                ),
                b"data: [DONE]\n\n",
            )
        )

        output: list[bytes] = []
        async for chunk in adapter.transform_stream(
            source[:7],
            _chunks([source[7:]]),
            request_body=b'{"input":[]}',
            model="gpt-test",
        ):
            output.append(chunk)

        self.assertEqual(b"".join(output), source)

    async def test_non_stream_chat_dsml_is_converted(self) -> None:
        adapter = DeepSeekDSMLProtocol()
        body = {
            "id": "chat-1",
            "choices": [
                {
                    "message": {
                        "content": (
                            '<\uFF5CDSML\uFF5Cfunction_calls>'
                            '<\uFF5CDSML\uFF5Cinvoke name="weather">'
                            '<\uFF5CDSML\uFF5Cparameter name="city" string="Paris"/>'
                            '</\uFF5CDSML\uFF5Cinvoke>'
                            '</\uFF5CDSML\uFF5Cfunction_calls>'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }

        output: list[bytes] = []
        encoded = json.dumps(body).encode()
        async for chunk in adapter.transform_body(
            encoded[:4],
            _chunks([encoded[4:]]),
            request_body=b'{"tools":[{"name":"weather"}]}',
            model="deepseek-v4",
        ):
            output.append(chunk)

        converted = json.loads(b"".join(output))
        self.assertEqual(converted["object"], "response")
        self.assertEqual(converted["id"], "chat-1")
        self.assertEqual(converted["output"][0]["name"], "weather")
        self.assertEqual(converted["output"][0]["arguments"], '{"city":"Paris"}')


class DeepSeekProviderSelectionTests(unittest.TestCase):
    def _provider(self, name: str, base_url: str) -> ProxyProvider:
        return ProxyProvider(
            provider_id=name,
            name=name,
            base_url=base_url,
            is_cc_switch_current=True,
            api_key="test-key",
        )

    def test_only_deepseek_provider_is_selected(self) -> None:
        self.assertTrue(
            is_deepseek_provider(self._provider("official", "https://api.deepseek.com"))
        )
        self.assertFalse(
            is_deepseek_provider(self._provider("company", "https://gpt.company.test"))
        )


class DeepSeekProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_resolver_converts_dsml_in_proxy_response(self) -> None:
        source = b"".join(
            (
                _sse(
                    {
                        "type": "response.created",
                        "response": {"id": "resp-proxy"},
                    }
                ),
                _sse(
                    {
                        "type": "response.output_text.delta",
                        "delta": (
                            '<|DSML|function_calls><|DSML|invoke name="weather">'
                            '<|DSML|parameter name="city" string="Paris"/>'
                            '</|DSML|invoke></|DSML|function_calls>'
                        ),
                    }
                ),
                _sse(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp-proxy"},
                    }
                ),
                b"data: [DONE]\n\n",
            )
        )

        async def upstream(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=source,
            )

        provider = ProxyProvider(
            provider_id="official",
            name="Official DeepSeek",
            base_url="https://api.deepseek.com/v1",
            is_cc_switch_current=True,
            api_key="test-key",
        )
        adapter = DeepSeekDSMLProtocol()
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(
            ProviderRouter((provider,)),
            client=upstream_client,
            protocol_adapter_resolver=lambda selected: adapter
            if is_deepseek_provider(selected)
            else None,
            retry_policy=RetryPolicy(enabled=False),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        try:
            response = await client.post(
                "/v1/responses",
                json={
                    "model": "deepseek-v4",
                    "input": [],
                    "tools": [{"type": "function", "name": "weather"}],
                },
            )
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("DSML", response.text)
        self.assertIn('"name":"weather"', response.text)
        self.assertIn("response.function_call_arguments.delta", response.text)


if __name__ == "__main__":
    unittest.main()
