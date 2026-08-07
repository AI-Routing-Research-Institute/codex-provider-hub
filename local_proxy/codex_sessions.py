from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterable


def default_session_index_path() -> Path:
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    codex_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    return codex_home / "session_index.jsonl"


class CodexSessionNameIndex:
    """Resolve Codex thread IDs without reading conversation transcripts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_session_index_path()
        self._lock = threading.RLock()
        self._stamp: tuple[int, int] | None = None
        self._names: dict[str, str] = {}

    def resolve(self, thread_ids: Iterable[str]) -> dict[str, str]:
        requested = {
            thread_id
            for thread_id in thread_ids
            if isinstance(thread_id, str) and thread_id
        }
        if not requested:
            return {}
        with self._lock:
            self._refresh()
            return {
                thread_id: self._names[thread_id]
                for thread_id in requested
                if thread_id in self._names
            }

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            self._stamp = None
            self._names = {}
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return

        names: dict[str, str] = {}
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    thread_id = record.get("id")
                    thread_name = record.get("thread_name")
                    if not isinstance(thread_id, str) or not thread_id:
                        continue
                    if not isinstance(thread_name, str) or not thread_name.strip():
                        continue
                    names[thread_id] = thread_name.strip()
        except OSError:
            self._stamp = None
            self._names = {}
            return
        self._names = names
        self._stamp = stamp
