import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import httpx

from local_proxy import codex_profile, claude_profile
from local_proxy.core import ProviderRouter, ProxyProvider, RetryPolicy, SSETerminalCapture, create_proxy_app
from local_proxy.protocols.deepseek_dsml import DeepSeekDSMLProtocol
from local_proxy.protocols.responses_tools import ResponsesTools, ToolProtocolError
from local_proxy.request_debug import RequestDebugStore
from local_proxy.server import ProxyProfile
from local_proxy.shared_settings import SharedRuntimeCoordinator, SharedSettingsStore, load_protocol_settings, save_protocol_settings
from tests.test_responses_compatibility import BRIDGE_REASONING, chunks, dsml, encoded, events, request_body, sse
from tests.test_shared_settings import FakeProfile


def exec_request():
    root = copy.deepcopy(request_body())
    root['input'][0]['tools'][0]['tools'][0]['description'] = 'Run JavaScript code. Use await tools.exec_command(...).'
    return root


def bridge_stream(text, *, complete=False):
    message = {'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': []}
    roots = [
        {'type': 'response.created', 'response': {'id': 'resp_test', 'model': 'deepseek-test'}},
        {'type': 'response.output_item.added', 'output_index': 0, 'item': message},
        {'type': 'response.content_part.added', 'output_index': 0, 'item_id': 'msg_test', 'content_index': 0,
         'part': {'type': 'output_text', 'text': '', 'annotations': []}},
    ]
    roots.extend({'type': 'response.output_text.delta', 'output_index': 0, 'item_id': 'msg_test', 'delta': piece}
                 for piece in [text[:9], text[9:33], text[33:]])
    if complete:
        part = {'type': 'output_text', 'text': text, 'annotations': []}
        done = {**message, 'status': 'completed', 'content': [part]}
        roots.extend([
            {'type': 'response.output_text.done', 'item_id': 'msg_test', 'text': text},
            {'type': 'response.content_part.done', 'item_id': 'msg_test', 'part': part},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': done},
            {'type': 'response.completed', 'response': {'id': 'resp_test', 'status': 'completed', 'output': [done]}},
        ])
    return b''.join(sse({**root, 'sequence_number': index}) for index, root in enumerate(roots)) + b'data: [DONE]\n\n'


class ExecConversionTests(unittest.TestCase):
    def call(self, value, root=None):
        return ResponsesTools(encoded(root or exec_request())).call_item({
            'name': 'exec', 'arguments': json.dumps(value), 'call_id': 'call_test', 'item_id': 'fc_dsml_test',
        })['input']

    def test_shell_options_and_special_characters_are_preserved_as_data(self):
        value = {'input': 'git status; echo "中文 `$() &amp;"\n', 'workdir': 'C:\\a b',
                 'yield_time_ms': 30000, 'max_output_tokens': 12000}
        result = self.call(value)
        payload = json.loads(result.split('tools.exec_command(', 1)[1].split('); text(result);')[0])
        self.assertEqual(payload, {**{k: v for k, v in value.items() if k != 'input'}, 'cmd': value['input']})
        self.assertIn('await tools.exec_command(', result)

    def test_bare_observed_git_shell_is_wrapped_and_javascript_is_untouched(self):
        self.assertIn('"cmd": "git status --short --branch"', self.call('git status --short --branch'))
        for javascript in ['const r = await tools.exec_command({cmd:"git status"}); text(r);',
                           'await tools.test("git status")', '// comment\ntext("hello");']:
            self.assertEqual(self.call({'input': javascript}), javascript)

    def test_unrelated_custom_tool_and_invalid_shapes_are_not_rewritten(self):
        with self.assertRaises(ToolProtocolError):
            self.call({'input': 'git status', 'workdir': '.'}, request_body())
        for value in [{'input': 'git status', 'workdir': 4}, {'input': 'git status', 'yield_time_ms': True},
                      {'input': 'git status', 'extra': 1}, {'input': 'text(1)', 'workdir': '.'},
                      {'input': 'git status', 'cmd': 'echo conflict'}]:
            with self.subTest(value=value), self.assertRaises(ToolProtocolError):
                self.call(value)

    def test_request_guidance_is_model_specific_idempotent_and_keeps_history(self):
        adapter = DeepSeekDSMLProtocol()
        source = encoded(exec_request())
        result = adapter.prepare_request_body(source, model='deepseek-test')
        self.assertIsNotNone(result)
        self.assertIsNone(adapter.prepare_request_body(result, model='deepseek-test'))
        self.assertIsNone(adapter.prepare_request_body(source, model='gpt-test'))
        expected = json.loads(source)
        actual = json.loads(result)
        expected['input'][0]['tools'][0]['tools'][0]['description'] = actual['input'][0]['tools'][0]['tools'][0]['description']
        self.assertEqual(actual, expected)


class StreamConversionTests(unittest.IsolatedAsyncioTestCase):
    async def convert(self, source, model='deepseek-test'):
        return b''.join([part async for part in DeepSeekDSMLProtocol().transform_stream(
            b'', chunks(source), request_body=encoded(exec_request()), model=model)])

    async def test_empty_and_nonempty_tool_preludes_close_with_matching_commentary(self):
        for prefix in ['', 'Checking the repository. ']:
            with self.subTest(prefix=prefix):
                roots = events(await self.convert(bridge_stream(prefix + dsml(arguments={'input': 'git status', 'workdir': '.'}))))
                added = [r['item'] for r in roots if r['type'] == 'response.output_item.added']
                done = [r['item'] for r in roots if r['type'] == 'response.output_item.done']
                self.assertEqual([x['id'] for x in added], [x['id'] for x in done])
                self.assertEqual(added[0]['phase'], 'commentary')
                self.assertEqual(done[0]['phase'], 'commentary')
                self.assertEqual(done[0]['content'][0]['text'], prefix)
                self.assertEqual(roots[-1]['response']['output'][0]['phase'], 'commentary')
                self.assertEqual(roots[-1]['response']['id'], 'resp_test')
                sequences = [x['sequence_number'] for x in roots]
                self.assertEqual(sequences, sorted(set(sequences)))

    async def test_final_answer_preserves_all_fragments_and_final_phase(self):
        roots = events(await self.convert(bridge_stream('A complete final answer.', complete=True)))
        self.assertEqual(''.join(x['delta'] for x in roots if x['type'] == 'response.output_text.delta'), 'A complete final answer.')
        for root in roots:
            if root.get('item', {}).get('type') == 'message':
                self.assertEqual(root['item']['phase'], 'final_answer')
        self.assertEqual(roots[-1]['response']['output'][0]['phase'], 'final_answer')

    async def test_failure_has_identity_details_terminal_and_no_false_completion(self):
        for text in [dsml('unknown'), '<tool_call>{"name":"exec"']:
            result = await self.convert(bridge_stream(text))
            roots = events(result)
            self.assertEqual(roots[-1]['type'], 'response.failed')
            self.assertEqual(roots[-1]['response']['id'], 'resp_test')
            self.assertEqual(roots[-1]['response']['error']['type'], 'deepseek_protocol_error')
            self.assertFalse(any(r['type'] == 'response.completed' for r in roots))
            self.assertTrue(result.endswith(b'data: [DONE]\n\n'))
            capture = SSETerminalCapture()
            for byte in result:
                capture.feed(bytes([byte]))
            self.assertEqual(capture.terminal_event, 'response.failed')
            self.assertIn('本地 DeepSeek 兼容转换失败', capture.error_summary)

    async def test_buffer_limit_failure_keeps_response_identity(self):
        source = bridge_stream('x' * 200, complete=True)
        with mock.patch('local_proxy.protocols.deepseek_dsml.DSML_BUFFER_LIMIT', 20):
            roots = events(await self.convert(source))
        self.assertEqual(roots[-1]['type'], 'response.failed')
        self.assertEqual(roots[-1]['response']['id'], 'resp_test')

    async def test_gpt_stream_remains_byte_identical(self):
        source = bridge_stream('Normal GPT text.', complete=True).replace(b'deepseek-test', b'gpt-test')
        self.assertEqual(await self.convert(source, model='gpt-test'), source)

    async def test_chat_late_dsml_emits_one_prelude_and_one_response(self):
        source = sse({'choices': [{'delta': {'content': 'Checking. '}}]})
        source += sse({'choices': [{'delta': {'content': dsml(arguments={'input': 'git status'})}}]})
        roots = events(await self.convert(source))
        messages = [r['item'] for r in roots if r['type'] == 'response.output_item.done' and r['item']['type'] == 'message']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['content'][0]['text'], 'Checking. ')
        self.assertEqual(roots[0]['response']['id'], roots[-1]['response']['id'])


class RuntimeSwitchTests(unittest.TestCase):
    def test_switch_is_advertised_only_for_codex(self):
        self.assertTrue(codex_profile.codex_ui_config(17890)['features']['deepseek_compatibility'])
        self.assertNotIn('deepseek_compatibility', claude_profile.claude_ui_config(17890)['features'])

    def test_defaults_persistence_validation_and_live_adapter_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(codex_profile, 'load_local_proxy_providers', return_value=()), \
                 mock.patch.object(codex_profile.httpx, 'AsyncClient'), mock.patch.object(codex_profile, 'CurlClient'):
                profile = codex_profile.build_codex_profile(database=root/'source.db', port=17890, data_root=root)
            provider = ProxyProvider('relay', 'Relay', 'https://test.invalid', False)
            self.assertFalse(profile.runtime_metadata()['deepseek_compatibility_enabled'])
            self.assertIsNone(profile.protocol_adapter_resolver(provider))
            profile.apply_runtime_preferences({'deepseek_compatibility_enabled': False})
            self.assertIsNone(profile.protocol_adapter_resolver(provider))
            self.assertFalse(load_protocol_settings(root/'codex-settings.json')['deepseek_compatibility_enabled'])
            with self.assertRaises(ValueError):
                profile.apply_runtime_preferences({'deepseek_compatibility_enabled': 'false'})
            profile.apply_runtime_preferences({'deepseek_compatibility_enabled': True})
            self.assertIsNotNone(profile.protocol_adapter_resolver(provider))
            self.assertTrue(load_protocol_settings(root/'codex-settings.json')['deepseek_compatibility_enabled'])
            save_protocol_settings({'deepseek_compatibility_enabled': 'false'}, root/'invalid.json')
            self.assertFalse(load_protocol_settings(root/'invalid.json')['deepseek_compatibility_enabled'])

    def test_invalid_switch_rejected_before_shared_settings_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.db'
            source.touch()
            store = SharedSettingsStore(path=root/'shared.json')
            codex, claude = FakeProfile('codex'), FakeProfile('claude')
            coordinator = SharedRuntimeCoordinator(store, (codex, claude), active_port=17890)
            before = store.snapshot()
            with self.assertRaisesRegex(ValueError, 'DeepSeek'):
                coordinator.apply_runtime_settings('codex', {'port': 18888, 'database_path': str(source), 'deepseek_compatibility_enabled': 'false'})
            self.assertEqual(store.snapshot(), before)
            self.assertEqual(codex.applied, [])
            self.assertFalse((root/'shared.json').exists())


class RuntimeSwitchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_capability_and_runtime_api_persist_both_switch_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.db'
            source.touch()
            with mock.patch.object(codex_profile, 'load_local_proxy_providers', return_value=()), \
                 mock.patch.object(codex_profile.httpx, 'AsyncClient'), mock.patch.object(codex_profile, 'CurlClient'):
                codex = codex_profile.build_codex_profile(database=source, port=17890, data_root=root)
            claude = ProxyProfile(service_id='claude', service_name='claude-local-proxy',
                router=ProviderRouter(()), upstream_client=codex.upstream_client,
                runtime_metadata=lambda: {}, ui_config=lambda: claude_profile.claude_ui_config(17890))
            # Isolate unrelated provider imports; keep real profile preferences,
            # settings persistence, coordinator and HTTP handlers in the path.
            for profile in (codex, claude):
                profile.load_runtime_database = lambda _: ()
                profile.apply_runtime_database = lambda _source, _providers: None
            store = SharedSettingsStore(path=root/'shared-settings.json')
            SharedRuntimeCoordinator(store, (codex, claude), active_port=17890)
            app = create_proxy_app(codex_profile=codex, claude_profile=claude)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                config = (await client.get('/control/codex/api/ui-config')).json()
                self.assertTrue(config['features']['deepseek_compatibility'])
                claude_config = (await client.get('/control/claude/api/ui-config')).json()
                self.assertNotIn('deepseek_compatibility', claude_config['features'])
                provider = ProxyProvider('relay', 'Relay', 'https://test.invalid', False)
                for enabled in (False, True, False):
                    payload = {'port': 17890, 'database_path': str(source)}
                    if config['features'].get('deepseek_compatibility'):
                        payload['deepseek_compatibility_enabled'] = enabled
                    response = await client.post('/control/codex/api/runtime-settings', json=payload,
                        headers={'X-Local-Proxy-Control': '1'})
                    self.assertEqual(response.status_code, 200)
                    self.assertIs(response.json()['deepseek_compatibility_enabled'], enabled)
                    loaded = await client.get('/control/codex/api/runtime-settings')
                    self.assertIs(loaded.json()['deepseek_compatibility_enabled'], enabled)
                    self.assertIs(load_protocol_settings(root/'codex-settings.json')['deepseek_compatibility_enabled'], enabled)
                    self.assertEqual(codex.protocol_adapter_resolver(provider) is not None, enabled)

    async def test_switch_controls_gpt_history_cleanup(self):
        enabled = False
        captured = []
        adapter = DeepSeekDSMLProtocol()

        def handle(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 'resp_test', 'output': []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
            provider = ProxyProvider('gpt', 'GPT', 'https://test.invalid', False, api_key='test')
            app = create_proxy_app(router=ProviderRouter((provider,)), client=upstream,
                protocol_adapter_resolver=lambda _: adapter if enabled else None, retry_policy=RetryPolicy(enabled=False))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                body = exec_request()
                body['input'].append(copy.deepcopy(BRIDGE_REASONING))
                self.assertEqual((await client.post('/v1/responses', json=body)).status_code, 200)
                enabled = True
                self.assertEqual((await client.post('/v1/responses', json=body)).status_code, 200)
        self.assertIn(BRIDGE_REASONING, captured[0]['input'])
        self.assertNotIn(BRIDGE_REASONING, captured[1]['input'])

    async def test_disabled_passthrough_enabled_transform_and_inflight_snapshot(self):
        enabled = False
        adapter = DeepSeekDSMLProtocol()
        source = bridge_stream(dsml(arguments={'input': 'git status', 'workdir': '.'}))
        received = []

        def upstream(request):
            nonlocal enabled
            received.append(json.loads(request.content))
            enabled = not enabled  # Changing settings cannot split the current request/response pair.
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=source)

        provider = ProxyProvider('ds', 'DeepSeek', 'https://upstream.test', False, api_key='test', model_mappings={'gpt-test': 'deepseek-test'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = create_proxy_app(router=ProviderRouter((provider,)), client=client,
                protocol_adapter_resolver=lambda _: adapter if enabled else None, retry_policy=RetryPolicy(enabled=False))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as downstream:
                body = exec_request()
                first = await downstream.post('/v1/responses', json=body)
                self.assertEqual(first.content, source)
                expected = copy.deepcopy(body)
                expected['model'] = 'deepseek-test'
                self.assertEqual(received[0], expected)
                second = await downstream.post('/v1/responses', json=body)
                self.assertIn(b'custom_tool_call', second.content)
                self.assertNotIn(b'<tool_call>', second.content)
                self.assertIn('DeepSeek compatibility', received[1]['input'][0]['tools'][0]['tools'][0]['description'])

    async def test_local_failure_detail_is_saved_in_debug_history(self):
        with tempfile.TemporaryDirectory() as directory:
            debug = RequestDebugStore(Path(directory)/'debug.sqlite3', service_id='codex')
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, headers={'content-type': 'text/event-stream'}, content=bridge_stream(dsml('unknown'))))) as upstream:
                app = create_proxy_app(router=ProviderRouter((ProxyProvider('ds', 'DS', 'https://test.invalid', False, api_key='test'),)),
                    client=upstream, protocol_adapter=DeepSeekDSMLProtocol(), request_debug_store=debug,
                    retry_policy=RetryPolicy(enabled=False))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                    response = await client.post('/v1/responses', json=exec_request())
                    self.assertEqual(events(response.content)[-1]['type'], 'response.failed')
            with closing(sqlite3.connect(debug.path)) as database:
                row = database.execute('select error_summary from debug_requests').fetchone()
            self.assertIn('本地 DeepSeek 兼容转换失败', row[0])
            self.assertIn('unknown', row[0])
