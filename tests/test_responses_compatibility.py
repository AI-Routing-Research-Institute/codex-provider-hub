import copy
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import httpx

from local_proxy.core import ProviderRouter, ProxyProvider, RetryPolicy, create_proxy_app
from local_proxy.protocols.deepseek_dsml import DeepSeekDSMLProtocol, DeepSeekProtocolError
from local_proxy.protocols.responses_history import normalize_deepseek_history
from local_proxy.protocols.responses_tools import ResponsesTools
from local_proxy.request_debug import RequestDebugStore


BRIDGE_REASONING = {
    "type": "reasoning", "summary": [],
    "content": [{"type": "reasoning_text", "text": "Synthetic third-party analysis"}],
    "encrypted_content": "12345678-1234-1234-1234-123456789abc-0",
}
TOOLS = [{"type": "namespace", "name": "functions", "tools": [
    {"type": "custom", "name": "exec", "format": {"type": "text"}},
    {"type": "function", "name": "wait", "parameters": {"type": "object"}},
]}]


def request_body(*, additional=True):
    root = {"model": "gpt-test", "stream": True, "input": []}
    if additional:
        root["input"].append({"type": "additional_tools", "tools": TOOLS})
    else:
        root["tools"] = TOOLS
    return root


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def sse(value):
    return b"data: " + encoded(value) + b"\n\n"


def events(value):
    return [json.loads(line[6:]) for line in value.splitlines()
            if line.startswith(b"data: ") and line != b"data: [DONE]"]


async def chunks(value, size=17):
    for start in range(0, len(value), size):
        yield value[start:start + size]


def dsml(name="exec", arguments=None):
    return '<tool_call>' + json.dumps({"name": name, "arguments": arguments or {"input": "await tools.test()"}}) + '</tool_call>'


class HistoryCompatibilityTests(unittest.TestCase):
    def test_removes_only_identified_reasoning_and_keeps_full_tool_pairs(self):
        root = request_body()
        original_gpt = {"type": "reasoning", "id": "rs_gpt", "summary": [],
                        "encrypted_content": "gAAAA-valid-original-state", "content": None}
        call = {"type": "function_call", "id": "fc_dsml_legacy", "name": "exec",
                "call_id": "call_dsml_legacy", "arguments": '{"input":"pwd"}'}
        output = {"type": "function_call_output", "call_id": "call_dsml_legacy", "output": "aborted"}
        root["input"].extend([original_gpt, BRIDGE_REASONING, call, output,
                              {"role": "user", "content": "Continue"}])
        before = copy.deepcopy(root)
        result = json.loads(normalize_deepseek_history(encoded(root), model="gpt-test"))
        self.assertEqual(result["input"], [x for x in root["input"] if x != BRIDGE_REASONING])
        self.assertEqual(root, before)
        self.assertEqual(result["model"], "gpt-test")
        self.assertIsNone(normalize_deepseek_history(encoded(result), model="gpt-test"))

    def test_mapping_to_deepseek_preserves_its_state(self):
        root = {"input": [BRIDGE_REASONING]}
        for model in ["deepseek-v4-flash", "claude-test", "unknown"]:
            with self.subTest(model=model):
                self.assertIsNone(normalize_deepseek_history(encoded(root), model=model))

    def test_unknown_reasoning_formats_and_message_text_are_untouched(self):
        variants = [
            {**BRIDGE_REASONING, "encrypted_content": "short-opaque-token"},
            {**BRIDGE_REASONING, "encrypted_content": "gAAAA" + "x" * 2000},
            {**BRIDGE_REASONING, "encrypted_content": None},
            {**BRIDGE_REASONING, "content": None},
            {"type": "message", "content": [BRIDGE_REASONING]},
            {**BRIDGE_REASONING, "encrypted_content": ["malformed"]},
        ]
        for item in variants:
            with self.subTest(item_type=item["type"]):
                self.assertIsNone(normalize_deepseek_history(encoded({"input": [item]}), model="gpt-test"))
        for payload in [b"bad-json", b'[]', b'{"input":"reasoning_text"}', b'{"input":[null,1]}']:
            self.assertIsNone(normalize_deepseek_history(payload, model="gpt-test"))

    def test_removes_matching_references_without_touching_other_items(self):
        root = {"input": [{**BRIDGE_REASONING, "id": "rs_bridge"},
                          {"type": "item_reference", "id": "rs_bridge"},
                          {"type": "item_reference", "id": "rs_gpt"}]}
        result = json.loads(normalize_deepseek_history(encoded(root), model="gpt-test"))
        self.assertEqual(result["input"], [{"type": "item_reference", "id": "rs_gpt"}])


class ToolContractTests(unittest.TestCase):
    def test_additional_and_top_level_namespaces_resolve_custom_input_losslessly(self):
        value = '  await tools.test("&amp;", "你好");\n'
        for additional in [True, False]:
            for name in ["exec", "functions.exec"]:
                for arguments in [{"input": value}, value]:
                    with self.subTest(additional=additional, name=name):
                        item = ResponsesTools(encoded(request_body(additional=additional))).call_item({
                            "name": name, "arguments": json.dumps(arguments),
                            "item_id": "ctc_test", "call_id": "call_test",
                        })
                        self.assertEqual(item["type"], "custom_tool_call")
                        self.assertEqual(item["input"], value)
                        self.assertNotIn("arguments", item)
                        self.assertEqual((item["namespace"], item["name"]), ("functions", "exec"))

    def test_ambiguous_unknown_and_empty_tool_contracts_reject_calls(self):
        duplicate = {"tools": TOOLS + [{"type": "namespace", "name": "other", "tools": [
            {"type": "function", "name": "exec"}]}]}
        registry = ResponsesTools(encoded(duplicate))
        with self.assertRaises(DeepSeekProtocolError):
            registry.resolve("exec")
        self.assertEqual(registry.resolve("functions.exec").kind, "custom")
        for payload in [encoded(request_body()), b'{"tools":[]}']:
            with self.assertRaises(DeepSeekProtocolError):
                ResponsesTools(payload).resolve("invented")

    def test_custom_extra_parameters_are_not_silently_dropped(self):
        registry = ResponsesTools(encoded(request_body()))
        for arguments in [{"input": "pwd", "workdir": "somewhere"}, {"input": 12}, {}, ["code"]]:
            with self.subTest(arguments=arguments), self.assertRaises(DeepSeekProtocolError):
                registry.call_item({"name": "exec", "arguments": json.dumps(arguments),
                                    "item_id": "ctc_test", "call_id": "call_test"})

    def test_chat_style_function_schema_preserves_json_arguments(self):
        registry = ResponsesTools(b'{"tools":[{"type":"function","function":{"name":"weather"}}]}')
        item = registry.call_item({"name": "weather", "arguments": '{"city":"Paris"}',
                                   "item_id": "fc_test", "call_id": "call_test"})
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(json.loads(item["arguments"]), {"city": "Paris"})
        self.assertNotIn("namespace", item)

    def test_malformed_function_calls_are_rejected_before_emission(self):
        registry = ResponsesTools(encoded(request_body()))
        for name, arguments in [("", "{}"), ("x" * 241, "{}"), ("wait", "{broken"), ("wait", '"text"')]:
            with self.subTest(name=name[:10], arguments=arguments), self.assertRaises(DeepSeekProtocolError):
                registry.call_item({"name": name, "arguments": arguments, "item_id": "fc_test", "call_id": "call_test"})


class TypedProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def convert(self, source, *, streaming=True, body=None, model="deepseek-test"):
        adapter = DeepSeekDSMLProtocol()
        method = adapter.transform_stream if streaming else adapter.transform_body
        return b"".join([part async for part in method(b"", chunks(source),
            request_body=encoded(body or request_body()), model=model)])

    async def test_dsml_custom_call_stream_and_json_have_same_contract(self):
        value = 'await tools.test("你好", "&amp;");\n'
        text = dsml("functions.exec", {"input": value})
        stream = sse({"type": "response.output_text.delta", "delta": text})
        roots = events(await self.convert(stream))
        item = next(x["item"] for x in roots if x["type"] == "response.output_item.done")
        deltas = [x["delta"] for x in roots if x["type"] == "response.custom_tool_call_input.delta"]
        self.assertEqual("".join(deltas), value)
        self.assertEqual(item["input"], value)
        self.assertEqual(item["type"], "custom_tool_call")
        self.assertTrue(item["id"].startswith("ctc_dsml_"))
        self.assertFalse(any(x["type"].startswith("response.function_call_arguments") for x in roots))
        response = json.loads(await self.convert(encoded({"choices": [{"message": {"content": text}}]}), streaming=False))
        self.assertEqual({k:v for k,v in response["output"][0].items() if k not in {"id", "call_id"}},
                         {k:v for k,v in item.items() if k not in {"id", "call_id"}})
        self.assertNotEqual(response["output"][0]["call_id"], item["call_id"])

    async def test_dsml_string_parameters_keep_whitespace_and_string_types(self):
        text = ('<|DSML|function_calls><|DSML|invoke name="exec">'
                '<|DSML|parameter name="input" string="true">  123\n</|DSML|parameter>'
                '</|DSML|invoke></|DSML|function_calls>')
        roots = events(await self.convert(sse({"type": "response.output_text.delta", "delta": text})))
        item = roots[-1]["response"]["output"][0]
        self.assertEqual(item["input"], "  123\n")

    async def test_late_dsml_custom_and_function_calls_keep_unique_indexes(self):
        source = sse({"type": "response.output_item.added", "output_index": 2,
                      "item": {"id": "msg_prefix", "type": "message"}})
        source += sse({"type": "response.output_text.delta", "delta": "First. ", "item_id": "msg_prefix"})
        # One container is required so both calls are parsed before emission.
        content = ('<|DSML|function_calls><|DSML|invoke name="exec">'
                   '<|DSML|parameter name="input" string="true">await tools.test()</|DSML|parameter>'
                   '</|DSML|invoke><|DSML|invoke name="wait">{"cell_id":"cell-1"}'
                   '</|DSML|invoke></|DSML|function_calls>')
        for piece in [content[:12], content[12:37], content[37:]]:
            source += sse({"type": "response.output_text.delta", "delta": piece, "item_id": "msg_prefix"})
        roots = events(await self.convert(source))
        calls = [x for x in roots if x["type"] == "response.output_item.added" and x["item"]["type"].endswith("_call")]
        self.assertEqual([x["output_index"] for x in calls], [3, 4])
        self.assertEqual([x["item"]["type"] for x in calls], ["custom_tool_call", "function_call"])
        self.assertEqual(sum(x["type"] == "response.completed" for x in roots), 1)

    async def test_chat_split_arguments_and_parallel_tools_finish_consistently(self):
        source = sse({"choices": [{"delta": {"content": "Checking. "}}]})
        arguments = json.dumps({"input": "await tools.test()"})
        for part, name in [(arguments[:8], "functions."), (arguments[8:], "exec")]:
            source += sse({"choices": [{"delta": {"tool_calls": [{"index": 2, "id": "call_exec",
                "function": {"name": name, "arguments": part}}]}}]})
        source += sse({"choices": [{"delta": {"tool_calls": [{"index": 7, "id": "call_wait",
            "function": {"name": "wait", "arguments": '{"cell_id":"cell-1"}'}}]}, "finish_reason": "tool_calls"}]})
        roots = events(await self.convert(source))
        output = roots[-1]["response"]["output"]
        self.assertEqual([x["type"] for x in output], ["message", "custom_tool_call", "function_call"])
        self.assertEqual(output[0]["content"][0]["text"], "Checking. ")
        self.assertEqual(output[1]["input"], "await tools.test()")
        added = [x for x in roots if x["type"] == "response.output_item.added"]
        self.assertEqual([x["output_index"] for x in added], [0, 1, 2])
        self.assertEqual(roots[-1]["type"], "response.completed")

    async def test_chat_non_stream_converts_custom_and_function_calls(self):
        source = encoded({"id": "chat-id", "model": "deepseek-test", "choices": [{"message": {
            "content": "Checking.", "tool_calls": [
                {"id": "call_exec", "function": {"name": "exec", "arguments": '{"input":"await tools.test()"}'}},
                {"id": "call_wait", "function": {"name": "wait", "arguments": '{"cell_id":"cell-1"}'}}
            ]}}]})
        result = json.loads(await self.convert(source, streaming=False))
        self.assertEqual(result["id"], "chat-id")
        self.assertEqual([x["type"] for x in result["output"]], ["message", "custom_tool_call", "function_call"])

    async def test_invalid_calls_emit_protocol_error_without_executable_item(self):
        for name, arguments in [("unknown", {"input": "code"}), ("exec", {"input": "pwd", "workdir": "elsewhere"})]:
            for chat in [False, True]:
                with self.subTest(name=name, chat=chat):
                    source = (sse({"choices": [{"delta": {"tool_calls": [{"function": {
                        "name": name, "arguments": json.dumps(arguments)}}]}, "finish_reason": "tool_calls"}]})
                        if chat else sse({"type": "response.output_text.delta", "delta": dsml(name, arguments)}))
                    roots = events(await self.convert(source))
                    self.assertEqual(roots[-1]["type"], "response.failed")
                    self.assertFalse(any(x["type"] == "response.output_item.added" for x in roots))

    async def test_native_custom_responses_stay_byte_identical(self):
        source = sse({"type": "response.output_item.done", "item": {"type": "reasoning",
            "id": "rs_native", "encrypted_content": "gAAAA-valid"}})
        source += sse({"type": "response.output_item.added", "item": {"type": "custom_tool_call",
            "name": "exec", "id": "ctc_native", "call_id": "call_native", "input": ""}})
        source += sse({"type": "response.custom_tool_call_input.delta", "delta": "await tools.test()"})
        source += sse({"type": "response.completed", "response": {"id": "resp_native"}})
        self.assertEqual(await self.convert(source, model="gpt-test"), source)


class ProxyHistoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_gpt_deepseek_gpt_round_trip_and_debug_capture(self):
        adapter = DeepSeekDSMLProtocol()
        provider = ProxyProvider("company", "Company", "https://upstream.test", False,
                                 api_key="test", model="gpt-default")
        deepseek = ProxyProvider("deepseek", "DeepSeek", "https://deepseek.test", False,
                                 api_key="test", model_mappings={"gpt-test": "deepseek-test"})
        router = ProviderRouter((provider, deepseek), current_provider_id="company")
        captured = []

        def handle(request):
            body = json.loads(request.content)
            captured.append(body)
            if body["model"] == "deepseek-test":
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                    content=sse({"type": "response.output_item.done", "item": BRIDGE_REASONING}) +
                    sse({"type": "response.output_text.delta", "delta": dsml()}))
            self.assertNotIn(BRIDGE_REASONING, body["input"])
            return httpx.Response(200, json={"id": "resp-gpt", "output": []})

        with tempfile.TemporaryDirectory() as directory:
            debug = RequestDebugStore(Path(directory) / "debug.sqlite3", service_id="codex")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
                app = create_proxy_app(router=router, client=upstream, protocol_adapter=adapter,
                    request_debug_store=debug, retry_policy=RetryPolicy(enabled=False))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                    root = request_body()
                    root["input"].append({"role": "user", "content": "Start"})
                    first = await client.post("/v1/responses", json=root)
                    self.assertEqual(first.status_code, 200)
                    router.select("deepseek")
                    second = await client.post("/v1/responses", json=root)
                    roots = events(second.content)
                    call = next(x["item"] for x in roots if x["type"] == "response.output_item.done" and x["item"]["type"] == "custom_tool_call")
                    self.assertEqual(call["input"], "await tools.test()")
                    root["input"].extend([copy.deepcopy(BRIDGE_REASONING), call,
                        {"type": "custom_tool_call_output", "id": "ctco_test", "call_id": call["call_id"],
                         "output": [{"type": "input_text", "text": "ok"}]},
                        {"role": "user", "content": "Continue in GPT"}])
                    router.select("company")
                    third = await client.post("/v1/responses", json=root)
                    self.assertEqual(third.status_code, 200)
                    self.assertEqual([x["model"] for x in captured], ["gpt-test", "deepseek-test", "gpt-test"])
                    self.assertIn(call, captured[-1]["input"])
                    self.assertIn(BRIDGE_REASONING, root["input"])
            import sqlite3
            with closing(sqlite3.connect(debug.path)) as db:
                original = json.loads(db.execute("select request_body from debug_requests order by started_at desc limit 1").fetchone()[0])
                sent = json.loads(db.execute("select request_body from debug_attempts order by id desc limit 1").fetchone()[0])
                self.assertIn(BRIDGE_REASONING, original["input"])
                self.assertNotIn(BRIDGE_REASONING, sent["input"])

    async def test_retry_uses_original_history_when_new_provider_maps_to_deepseek(self):
        gpt = ProxyProvider("gpt", "GPT", "https://gpt.test", False, api_key="test")
        ds = ProxyProvider("ds", "DeepSeek", "https://ds.test", False, api_key="test",
                           model_mappings={"gpt-test": "deepseek-test"})
        router = ProviderRouter((gpt, ds), current_provider_id="gpt")
        captured = []

        def handle(request):
            body = json.loads(request.content)
            captured.append(body)
            if len(captured) == 1:
                router.select("ds")
                return httpx.Response(503, json={"error": {"message": "busy"}})
            return httpx.Response(200, json={"id": "resp", "output": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
            app = create_proxy_app(router=router, client=upstream, protocol_adapter=DeepSeekDSMLProtocol(),
                retry_policy=RetryPolicy(enabled=True, max_attempts=2, delay_seconds=0))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                root = request_body()
                root["input"].append(copy.deepcopy(BRIDGE_REASONING))
                response = await client.post("/v1/responses", json=root)
                self.assertEqual(response.status_code, 200)
        self.assertNotIn(BRIDGE_REASONING, captured[0]["input"])
        self.assertIn(BRIDGE_REASONING, captured[1]["input"])
        self.assertEqual(captured[1]["model"], "deepseek-test")
