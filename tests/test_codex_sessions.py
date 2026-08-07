import json
import tempfile
import unittest
from pathlib import Path

from local_proxy.codex_sessions import CodexSessionNameIndex


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


if __name__ == "__main__":
    unittest.main()
