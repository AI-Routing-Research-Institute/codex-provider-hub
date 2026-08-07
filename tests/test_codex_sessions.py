import hashlib
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from local_proxy.codex_sessions import CodexSessionNameIndex
from local_proxy.codex_profile import _merge_session_catalogs


class CodexSessionNameIndexTests(unittest.TestCase):
    def test_latest_name_wins_and_malformed_rows_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "session_index.jsonl"
            records = [
                {"id": "thread-one", "thread_name": "旧名称"},
                {"id": "thread-two", "thread_name": "另一个会话"},
                {"id": "thread-one", "thread_name": "当前名称"},
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
                + "\n{unfinished",
                encoding="utf-8",
            )
            index = CodexSessionNameIndex(path)

            self.assertEqual(
                index.resolve(("thread-one", "missing")),
                {"thread-one": "当前名称"},
            )

    def test_missing_index_returns_no_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            index = CodexSessionNameIndex(Path(temporary_directory) / "missing.jsonl")

            self.assertEqual(index.resolve(("thread-one",)), {})

    def test_recent_filters_to_seven_day_window_and_resolves_private_key(self) -> None:
        now = time.time()

        def timestamp(offset: float) -> str:
            return datetime.fromtimestamp(now + offset, timezone.utc).isoformat()

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "session_index.jsonl"
            records = [
                {"id": "thread-old", "thread_name": "旧会话", "updated_at": timestamp(-8 * 24 * 3600)},
                {"id": "thread-recent", "thread_name": "最近会话", "updated_at": timestamp(-3600)},
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
                encoding="utf-8",
            )
            index = CodexSessionNameIndex(path)

            recent = index.recent(now - 7 * 24 * 3600)
            self.assertEqual([item["name"] for item in recent], ["最近会话"])
            session_key = hashlib.sha256(b"thread-recent").hexdigest()[:24]
            self.assertEqual(index.thread_id_for_session_key(session_key), "thread-recent")
            self.assertIsNone(index.thread_id_for_session_key("not-a-session-key"))

    def test_recent_keeps_max_timestamp_when_index_rows_are_repeated(self) -> None:
        now = time.time()

        def timestamp(offset: float) -> str:
            return datetime.fromtimestamp(now + offset, timezone.utc).isoformat()

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "session_index.jsonl"
            records = [
                {
                    "id": "thread-repeated",
                    "thread_name": "鏈€鏂板悕绉?",
                    "updated_at": timestamp(-3600),
                },
                {
                    "id": "thread-repeated",
                    "thread_name": "鏃ф椂闂存洿鏂?",
                    "updated_at": timestamp(-2 * 3600),
                },
                {
                    "id": "thread-repeated",
                    "thread_name": "鏈€鏂板悕绉?",
                },
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
                encoding="utf-8",
            )
            index = CodexSessionNameIndex(path)

            recent = index.recent(now - 7 * 24 * 3600)

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["name"], "鏈€鏂板悕绉?")
        self.assertAlmostEqual(recent[0]["updated_at"], now - 3600, delta=1)

    def test_catalog_merge_keeps_request_only_sessions_and_latest_activity(self) -> None:
        merged = _merge_session_catalogs(
            (
                {
                    "thread_id": "request-only",
                    "name": "璇锋眰璁板綍浼氳瘽",
                    "updated_at": 20.0,
                },
                {
                    "thread_id": "shared",
                    "name": "璇锋眰涓殑鍚嶇О",
                    "updated_at": 30.0,
                },
            ),
            (
                {
                    "thread_id": "shared",
                    "name": "Codex 绱㈠紩鍚嶇О",
                    "updated_at": 25.0,
                },
                {
                    "thread_id": "index-only",
                    "name": "绱㈠紩浼氳瘽",
                    "updated_at": 40.0,
                },
            ),
        )

        by_id = {item["thread_id"]: item for item in merged}
        self.assertEqual(set(by_id), {"request-only", "shared", "index-only"})
        self.assertEqual(by_id["shared"]["name"], "Codex 绱㈠紩鍚嶇О")
        self.assertEqual(by_id["shared"]["updated_at"], 30.0)


if __name__ == "__main__":
    unittest.main()
