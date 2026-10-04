import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

import httpx

from local_proxy.codex_sessions import CodexSessionNameIndex
from local_proxy.core import ProviderRouter, ProxyProvider, UsageStore, create_proxy_app
from local_proxy.request_debug import RequestDebugStore
from local_proxy.session_attribution import (
    ATTRIBUTION_COLUMNS, METADATA_HEADER, classify, decode_context,
    display_attribution, request_context, session_key,
)


def headers(**metadata):
    return {METADATA_HEADER: json.dumps(metadata)}


class AttributionFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.index_path = self.home / "session_index.jsonl"
        self.names(root="主会话")
        self.index = CodexSessionNameIndex(self.index_path)

    def names(self, **names):
        self.index_path.write_text("".join(
            json.dumps({"id": thread, "thread_name": name}, ensure_ascii=False) + "\n"
            for thread, name in names.items()
        ), encoding="utf-8")

    def native(self, rows, edges=()):
        path = self.home / "state_5.sqlite"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, source TEXT, agent_path TEXT, agent_nickname TEXT, rollout_path TEXT)")
            conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT)")
            conn.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?)", [
                (row["id"], row.get("title"), json.dumps(row.get("source")),
                 row.get("agent_path"), row.get("agent_nickname"), row.get("rollout_path"))
                for row in rows
            ])
            conn.executemany("INSERT INTO thread_spawn_edges VALUES (?,?)", edges)
        return path

    def store(self, **kwargs):
        return UsageStore(self.home / "usage.sqlite3",
                          session_attribution_resolver=self.index.attribute,
                          session_search_resolver=self.index.matching_thread_ids, **kwargs)

    def record(self, store, thread="child", context=None, **kwargs):
        values = dict(started_at=time.time() - 1, provider_id="provider", thread_id=thread,
                      session_name="未知会话", model="test", status_code=200, successful=True,
                      outcome="succeeded", retry_count=0, session_context=context)
        values.update(kwargs)
        store.record_request(**values)


class SessionMetadataTests(unittest.TestCase):
    def test_parser_whitelists_client_evidence_and_bounds_values(self):
        context = request_context(headers(
            thread_id="child", thread_source="thread_description", forked_from_thread_id="root",
            root_session_name="injected", session_kind="guardian", attribution_origin="request_debug",
            agent_name="bad\x00name", agent_nickname="x" * 257, root_turn_id="not-a-thread",
            session_id="not-a-parent", prompt="private", api_key="secret",
        ))
        self.assertEqual(context, {"thread_id": "child", "thread_source": "thread_description",
                                   "forked_from_thread_id": "root"})
        self.assertEqual(request_context({METADATA_HEADER: "x" * 32769}), {})
        for raw in ("{invalid", "[]", "null", "1"):
            self.assertEqual(request_context({METADATA_HEADER: raw}), {})
        self.assertEqual(decode_context("x" * 8193), {})
        self.assertEqual(request_context(headers(agent_name="\ud800")), {})
        self.assertEqual(request_context({METADATA_HEADER.upper(): '{"thread_source":"user"}'}),
                         {"thread_source": "user"})

    def test_explicit_types_do_not_guess_from_model_or_fork(self):
        self.assertEqual(classify({"model": "gpt-luna", "context_window_id": "root"}), "unknown")
        self.assertEqual(classify({"forked_from_thread_id": "root"}), "fork")
        self.assertEqual(classify({"thread_source": "side_chat"}), "side_chat")
        self.assertEqual(classify({"turn_trigger": "thread_title"}), "thread_title")

    def test_display_fallbacks_and_compaction_preserve_ownership(self):
        unknown = display_attribution("child", "未知会话", {})
        self.assertEqual((unknown["session_display_name"], unknown["session_label"]),
                         ("未识别会话", "来源信息不足"))
        agent = display_attribution("child", "未知会话", {
            "thread_source": "subagent", "agent_path": "/root/review", "request_kind": "compaction",
        })
        self.assertEqual(agent["session_display_name"], "子 agent · review")
        self.assertIn("上下文压缩", agent["session_label"])
        self.assertEqual(agent["session_kind"], "subagent")
        main = display_attribution("root", "主会话", {"root_thread_id": "root", "request_kind": "compaction"})
        self.assertEqual(main["session_label"], "上下文压缩")
        for key in ("thread_id", "root_thread_id", "parent_thread_id", "session_context_json"):
            self.assertNotIn(key, agent)


class NativeAttributionTests(AttributionFixture, unittest.TestCase):
    def test_description_header_resolves_root_without_native_child(self):
        context = self.index.attribute("description", request_context(headers(
            thread_id="description", thread_source="thread_description", turn_trigger="thread_description",
            forked_from_thread_id="root",
        )))
        item = display_attribution("description", "未知会话", context)
        self.assertEqual(item["session_display_name"], "主会话")
        self.assertEqual(item["session_label"], "会话摘要")
        self.assertEqual(item["root_session_key"], session_key("root"))
        self.assertEqual(item["parent_session_key"], session_key("root"))

    def test_multilevel_spawn_uses_root_and_current_task_not_parent_task(self):
        self.native([
            {"id": "root", "title": "数据库旧名称"},
            {"id": "agent", "title": "中间任务", "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": "root", "agent_path": "/root/first"}}}},
            {"id": "child", "agent_path": "/root/first/review", "agent_nickname": "Dalton"},
        ], [("agent", "child")])
        context = self.index.attribute("child", {})
        item = display_attribution("child", "未知会话", context)
        self.assertEqual(context["root_thread_id"], "root")
        self.assertEqual(item["session_display_name"], "主会话")
        self.assertEqual(item["session_label"], "子 agent · review")
        self.assertEqual(item["agent_nickname"], "Dalton")
        self.assertIn("中间任务", item["session_tooltip"])

    def test_guardian_reads_only_first_metadata_record_and_remains_guardian_with_edge(self):
        rollout = self.home / "sessions" / "guardian.jsonl"
        rollout.parent.mkdir()
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {"parent_thread_id": "root", "request_kind": "compaction"}})
                           + "\nTHIS IS NOT JSON AND MUST NOT BE READ", encoding="utf-8")
        self.native([{"id": "child", "source": {"subagent": {"other": "guardian"}},
                      "rollout_path": str(rollout)}])
        self.assertEqual(self.index.attribute("child", {})["root_thread_id"], "root")
        self.assertNotIn("request_kind", self.index.attribute("child", {}))
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as conn, conn:
            conn.execute("INSERT INTO thread_spawn_edges VALUES ('root','child')")
        self.index._native_at = 0
        item = display_attribution("child", "未知会话", self.index.attribute("child", {}))
        self.assertEqual(item["session_label"], "自动审查")

    def test_rollout_path_is_bounded_to_codex_sessions(self):
        outside = self.home / "private.jsonl"
        outside.write_text(json.dumps({"type": "session_meta", "payload": {"parent_thread_id": "root"}}), encoding="utf-8")
        self.assertEqual(self.index._rollout_context(str(outside)), {})
        rollout = self.home / "archived_sessions" / "large.jsonl"
        rollout.parent.mkdir()
        rollout.write_text(" " * (256 * 1024 + 1), encoding="utf-8")
        self.assertEqual(self.index._rollout_context(str(rollout)), {})

    def test_named_fork_keeps_own_name_and_side_chat_requires_marker(self):
        self.names(root="主会话", child="分叉名称")
        context = self.index.attribute("child", {"forked_from_thread_id": "root"})
        item = display_attribution("child", "分叉名称", context)
        self.assertEqual((item["session_display_name"], item["session_label"]), ("分叉名称", "来自：主会话"))
        item = display_attribution("child", "分叉名称", {**context, "thread_source": "side_chat"})
        self.assertEqual((item["session_display_name"], item["session_label"]), ("主会话", "侧边聊天 · 分叉名称"))

    def test_explicit_root_without_parent_is_a_related_branch(self):
        context = self.index.attribute("child", {"root_thread_id": "root"})
        self.assertEqual(classify(context), "fork")
        self.assertEqual(display_attribution("child", "未知会话", context)["session_display_name"], "主会话")

    def test_missing_schema_and_unreadable_database_fall_back(self):
        (self.home / "state_9.sqlite").write_bytes(b"not sqlite")
        self.native([{"id": "child", "title": "本地名称"}])
        self.assertEqual(self.index.attribute("child", {})["root_session_name"], "本地名称")
        self.assertEqual(self.index.attribute("missing", {})["session_kind"], "unknown")
        self.assertEqual(self.index.attribute(None, {"request_kind": "compaction"}), {"request_kind": "compaction"})

    def test_execution_relation_precedes_fork_but_conflicting_execution_is_not_guessed(self):
        self.native([{"id": "child", "source": {"subagent": {"thread_spawn": {"parent_thread_id": "root"}}}}])
        context = self.index.attribute("child", {"forked_from_thread_id": "other"})
        self.assertEqual(context["root_thread_id"], "root")
        context = self.index.attribute("child", {"parent_thread_id": "different"})
        self.assertNotIn("root_thread_id", context)
        self.assertNotIn("parent_thread_id", context)
        self.assertEqual(context["session_kind"], "subagent")

    def test_cycles_and_depth_limit_do_not_assign_a_root(self):
        self.native([], [("b", "a"), ("a", "b"), ("self", "self")] + [(f"d{i+1}", f"d{i}") for i in range(20)])
        self.assertNotIn("root_thread_id", self.index.attribute("a", {}))
        self.assertNotIn("root_thread_id", self.index.attribute("self", {}))
        self.assertNotIn("root_thread_id", self.index.attribute("d0", {}))

    def test_names_refresh_and_snapshots_survive_deleted_native_files(self):
        path = self.native([], [("root", "agent"), ("agent", "child")])
        context = self.index.attribute("child", {})
        self.names(root="重新命名的主会话")
        context = self.index.attribute("child", context)
        self.assertEqual(context["root_session_name"], "重新命名的主会话")
        path.unlink()
        self.index_path.unlink()
        fresh = CodexSessionNameIndex(self.index_path)
        restored = fresh.attribute("child", context)
        self.assertEqual(restored["root_thread_id"], "root")
        self.assertEqual(restored["root_session_name"], "重新命名的主会话")

    def test_delayed_name_promotes_unknown_and_compaction_is_not_cached(self):
        context = self.index.attribute("child", {})
        self.names(child="后来才有名字")
        self.assertEqual(self.index.attribute("child", context)["session_kind"], "main")
        self.index.attribute("child", {"request_kind": "compaction"})
        self.assertNotIn("request_kind", self.index.attribute("child", {}))

    def test_native_cache_is_bounded(self):
        for i in range(4002):
            self.index._native_checked.add(str(i))
        self.index._native_at = time.monotonic()
        self.index.attribute("child", {})
        self.assertLessEqual(len(self.index._native_checked), 4000)


class AttributionPersistenceTests(AttributionFixture, unittest.TestCase):
    def test_migration_is_idempotent_for_old_tables(self):
        path = self.home / "usage.sqlite3"
        UsageStore(path)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("DROP INDEX request_history_root_session_time")
            for table in ("request_history", "inflight_requests"):
                for column in ATTRIBUTION_COLUMNS:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        UsageStore(path)
        UsageStore(path)
        with closing(sqlite3.connect(path)) as conn:
            for table in ("request_history", "inflight_requests"):
                columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
                self.assertTrue(set(ATTRIBUTION_COLUMNS) <= set(columns))
                self.assertEqual(len(columns), len(set(columns)))

    def test_inflight_completion_and_restart_preserve_actual_identity_and_context(self):
        store = self.store(run_id="first")
        for request_id in (1, 2):
            store.start_inflight_request(request_id=request_id, started_at=time.time() - 1,
                                         provider_id="provider", thread_id=f"child{request_id}",
                                         session_context={"thread_source": "thread_description", "forked_from_thread_id": "root"})
        self.record(store, thread="child1", request_id=1)
        UsageStore(store.path, run_id="second")
        with closing(sqlite3.connect(store.path)) as conn:
            rows = conn.execute("SELECT thread_id,session_key,root_thread_id,root_session_name,session_kind,outcome FROM request_history ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        for i, row in enumerate(rows, 1):
            self.assertEqual(row[:5], (f"child{i}", session_key(f"child{i}"), "root", "主会话", "thread_description"))
        self.assertEqual(rows[1][5], "interrupted")

    def test_search_root_agent_labels_and_pagination_have_correct_counts(self):
        store = self.store()
        for i in range(5):
            self.record(store, thread=f"child{i}", context={"thread_source": "subagent",
                        "parent_thread_id": "root", "agent_path": "/root/review",
                        "request_kind": "compaction" if i == 0 else "turn"})
        self.record(store, thread="unrelated")
        for query in ("主会话", "review", "子 agent"):
            page = store.request_history(query=query, limit=2)
            self.assertEqual(page["total_count"], 5)
            ids = [item["session_key"] for item in page["items"]]
            while page["next_cursor"]:
                page = store.request_history(query=query, limit=2, cursor=page["next_cursor"])
                ids.extend(item["session_key"] for item in page["items"])
            self.assertEqual(len(ids), 5)
            self.assertEqual(len(set(ids)), 5)
        self.assertEqual(store.request_history(query="上下文压缩")["total_count"], 1)
        self.names(root="更名后的主会话")
        self.assertEqual(store.request_history(query="更名后的主会话")["total_count"], 5)
        self.assertEqual(store.request_history(query="%_")["total_count"], 0)

    def test_callback_failure_cannot_interrupt_recording(self):
        store = self.store()
        store.session_attribution_resolver = lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError("locked"))
        self.record(store, context={"thread_source": "thread_description", "forked_from_thread_id": "root"})
        self.assertEqual(store.request_history()["items"][0]["_session_context"]["thread_source"], "thread_description")

    def test_renamed_root_search_is_not_truncated_after_500_matching_names(self):
        self.names(**{f"r{i}": "旧名称组" for i in range(501)})
        store = self.store()
        self.record(store, context={"thread_source": "subagent", "parent_thread_id": "r500"})
        self.names(**{f"r{i}": "新名称组" for i in range(501)})
        self.assertEqual(store.request_history(query="新名称组")["total_count"], 1)

    def test_unknown_fallback_labels_are_searchable(self):
        store = self.store()
        self.record(store)
        self.assertEqual(store.request_history(query="未识别会话")["total_count"], 1)
        self.assertEqual(store.request_history(query="来源信息不足")["total_count"], 1)

    def test_unique_debug_recovery_is_per_request_and_retries_derived_unknown(self):
        debug = RequestDebugStore(self.home / "debug.sqlite3")
        store = self.store(historical_context_resolver=debug.session_context_at)
        started = time.time() - 10
        for i, kind in enumerate(("compaction", "turn"), 1):
            self.record(store, started_at=started + i * 2)
        # The first history read occurs before debug metadata is available.
        self.assertEqual(store.request_history()["total_count"], 2)
        for i, kind in enumerate(("compaction", "turn"), 1):
            session = debug.start_request(run_id="test", request_id=i, started_at=started + i * 2,
                                          method="POST", path="/v1/responses", query="", thread_id="child",
                                          session_name="未知会话", headers=headers(thread_id="child",
                                          thread_source="thread_description", forked_from_thread_id="root", request_kind=kind))
            session.update(model="test")
            session.finish(finished_at=started + i * 2 + 1, state="succeeded")
        store._attribution_cursor = None
        store._attribution_refresh_at = 0
        first = store.request_history()["items"]
        self.assertEqual({row["_session_context"]["request_kind"] for row in first}, {"compaction", "turn"})
        self.assertTrue(all(row["_session_context"]["root_thread_id"] == "root" for row in first))
        store._attribution_cursor = None
        store._attribution_refresh_at = 0
        self.assertEqual(first, store.request_history()["items"])

    def test_ambiguous_debug_metadata_is_skipped(self):
        debug = RequestDebugStore(self.home / "debug.sqlite3")
        started = time.time()
        for i in (1, 2):
            session = debug.start_request(run_id="test", request_id=i, started_at=started,
                                          method="POST", path="/v1/responses", query="", thread_id="child",
                                          session_name="未知会话", headers=headers(request_kind="compaction"))
            session.update(model="test")
        self.assertEqual(debug.session_context_at("child", started, "test"), {})
        self.assertEqual(debug.session_context_at("child", started, "wrong-model"), {})

    def test_historical_header_read_is_bounded(self):
        debug = RequestDebugStore(self.home / "debug.sqlite3")
        started = time.time()
        session = debug.start_request(run_id="test", request_id=1, started_at=started,
                                      method="POST", path="/v1/responses", query="", thread_id="child",
                                      session_name="未知会话", headers={**headers(request_kind="compaction"), "x-big": "x" * 262145})
        session.update(model="test")
        self.assertEqual(debug.session_context_at("child", started, "test"), {})


class AttributionProxyTests(AttributionFixture, unittest.IsolatedAsyncioTestCase):
    async def test_main_summary_and_agents_are_concurrent_and_cancel_is_isolated(self):
        started = {thread: asyncio.Event() for thread in ("root", "description", "a", "b")}
        response_sent = {thread: asyncio.Event() for thread in started}
        allow_response = asyncio.Event()
        released = {thread: asyncio.Event() for thread in started}
        closed = {thread: asyncio.Event() for thread in started}
        routed = {}
        responses = {}

        class Stream(httpx.AsyncByteStream):
            def __init__(self, thread):
                self.thread = thread

            async def __aiter__(self):
                try:
                    started[self.thread].set()
                    yield b'data: {"type":"response.output_text.delta","delta":"test"}\n\n'
                    await released[self.thread].wait()
                    yield b'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
                finally:
                    closed[self.thread].set()

            async def aclose(self):
                closed[self.thread].set()

        async def upstream(request):
            thread = json.loads(request.headers[METADATA_HEADER])["thread_id"]
            routed[thread] = request.url.host
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream(thread))

        store = self.store()
        router = ProviderRouter((
            ProxyProvider("provider", "Provider", "https://main.example/v1", True, api_key="test-secret"),
            ProxyProvider("other", "Other", "https://agent.example/v1", False, api_key="test-secret"),
        ), session_provider_overrides={"a": "other"})
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = create_proxy_app(router, client=upstream_client, usage_store=store,
                               session_name_resolver=self.index.resolve)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        tasks = {}
        disconnect = {thread: asyncio.Event() for thread in started}

        async def run_request(thread, context):
            body_sent = False

            async def receive():
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {"type": "http.request", "body": b'{"model":"test"}', "more_body": False}
                await disconnect[thread].wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    await allow_response.wait()
                responses.setdefault(thread, []).append(message)
                if message["type"] == "http.response.body" and message.get("body"):
                    response_sent[thread].set()

            scope = {
                "type": "http", "asgi": {"spec_version": "2.4"}, "http_version": "1.1",
                "method": "POST", "path": "/v1/responses", "raw_path": b"/v1/responses",
                "query_string": b"", "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"), (METADATA_HEADER.encode(), headers(**context)[METADATA_HEADER].encode())],
                "scheme": "http", "server": ("testserver", 80), "client": ("127.0.0.1", 1), "root_path": "",
            }
            await app(scope, receive, send)

        try:
            for thread in started:
                context = {"thread_id": thread, "thread_source": "user"}
                if thread == "description":
                    context.update(thread_source="thread_description", forked_from_thread_id="root")
                elif thread in ("a", "b"):
                    context.update(thread_source="subagent", parent_thread_id="root", agent_path=f"/root/{thread}")
                tasks[thread] = asyncio.create_task(run_request(thread, context))
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 5)
            except TimeoutError:
                self.fail(f"Requests did not start: {responses}; task errors: {[task.exception() for task in tasks.values() if task.done()]}")
            # Upstream iteration can begin before the client receives a response.
            # Keep that window deterministic, then cancel only after body delivery.
            self.assertFalse(any(event.is_set() for event in response_sent.values()))
            allow_response.set()
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in response_sent.values())), 5)
            payload = (await client.get("/control/api/requests")).json()
            self.assertEqual(len(payload["active"]), 4)
            self.assertEqual({item["session_display_name"] for item in payload["active"]}, {"主会话"})
            self.assertEqual({item["session_key"] for item in payload["active"]}, {session_key(thread) for thread in started})
            self.assertEqual((await client.get("/control/api/requests", params={"query": "主会话"})).json()["total_count"], 4)
            self.assertEqual(routed["a"], "agent.example")
            self.assertEqual(routed["description"], "main.example")
            disconnect["a"].set()
            await asyncio.wait_for(tasks["a"], 5)
            await asyncio.wait_for(closed["a"].wait(), 5)
            self.assertEqual(len(router.status().active_request_details), 3)
            self.assertFalse(any(closed[thread].is_set() for thread in ("root", "description", "b")))
            for event in released.values():
                event.set()
            await asyncio.wait_for(asyncio.gather(*(task for thread, task in tasks.items() if thread != "a")), 5)
            payload = (await client.get("/control/api/requests", params={"query": "主会话"})).json()
            self.assertEqual(payload["total_count"], 4)
            self.assertEqual(len(payload["active"]), 0)
            self.assertEqual(sum(row["succeeded"] for row in payload["items"]), 3)
            cancelled = next(row for row in payload["items"] if row["session_key"] == session_key("a"))
            self.assertEqual(cancelled["session_label"], "子 agent · a")
            self.assertEqual(cancelled["route_provider_id"], "other")
            for item in payload["items"]:
                self.assertFalse(any(key in item for key in ("thread_id", "parent_thread_id", "root_thread_id", "_thread_id", "_session_context", "session_context_json")))
            self.assertEqual(router._active_session_contexts, {})
        finally:
            allow_response.set()
            for event in released.values():
                event.set()
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            await client.aclose()
            await upstream_client.aclose()

    async def test_plain_non_codex_profile_keeps_old_fields(self):
        async def upstream(_):
            return httpx.Response(200, json={"output": []})
        store = UsageStore(self.home / "usage.sqlite3")
        router = ProviderRouter((ProxyProvider("provider", "Provider", "https://test.example/v1", True, api_key="test-secret"),))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
            app = create_proxy_app(router, client=upstream_client, usage_store=store)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                await client.post("/v1/responses", json={"model": "test"})
                item = (await client.get("/control/api/requests")).json()["items"][0]
        self.assertEqual(item["session_name"], "未知会话")
        self.assertNotIn("session_display_name", item)


if __name__ == "__main__":
    unittest.main()
