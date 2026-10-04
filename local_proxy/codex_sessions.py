from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from local_proxy.session_attribution import clean_context, classify, safe_text


def _relationship_context(value: Any) -> dict[str, str]:
    """Native session metadata cannot describe a particular request's purpose."""
    return {k: v for k, v in clean_context(value).items() if k in {
        "parent_thread_id", "forked_from_thread_id", "root_thread_id",
        "agent_name", "agent_path", "agent_nickname", "agent_role",
    }}


def default_session_index_path() -> Path:
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    codex_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    return codex_home / "session_index.jsonl"


def _session_key(thread_id: str) -> str:
    return hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:24]


def _updated_at_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class CodexSessionNameIndex:
    """Resolve Codex thread IDs without reading conversation transcripts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_session_index_path()
        self._lock = threading.RLock()
        self._stamp: tuple[int, int] | None = None
        self._names: dict[str, str] = {}
        self._updated_at: dict[str, float] = {}
        self._thread_ids_by_key: dict[str, str] = {}
        self._native: dict[str, dict[str, str]] = {}
        self._native_checked: set[str] = set()
        self._native_at = 0.0
        self._observed: dict[str, dict[str, str]] = {}

    def attribute(self, thread_id: str | None, context: Mapping[str, Any]) -> dict[str, str]:
        """Resolve metadata only; callers run this bounded I/O in a worker thread."""
        thread_id = safe_text(thread_id)
        if not thread_id:
            return clean_context(context)
        with self._lock:
            self._refresh()
            supplied = clean_context(context)
            self._load_native({thread_id, *(
                supplied[k] for k in ("parent_thread_id", "forked_from_thread_id", "root_thread_id")
                if k in supplied
            )})
            native = self._native.get(thread_id, {})
            merged = {**self._observed.get(thread_id, {}), **native, **supplied}
            merged["thread_id"] = thread_id
            if native.get("session_kind") in {"guardian", "subagent"}:
                merged["session_kind"] = native["session_kind"]
            if (native.get("parent_thread_id") and supplied.get("parent_thread_id")
                    and native["parent_thread_id"] != supplied["parent_thread_id"]):
                merged["relation_conflict"] = "true"
            elif native.get("parent_thread_id"):
                # Execution ownership takes precedence over a historical fork origin.
                merged["parent_thread_id"] = native["parent_thread_id"]
            for key in ("parent_thread_id", "forked_from_thread_id"):
                if merged.get(key) == thread_id:
                    if key == "parent_thread_id" or not merged.get("parent_thread_id"):
                        merged["relation_conflict"] = "true"
                    merged.pop(key)
            own_name = self._name(thread_id)
            parent = merged.get("parent_thread_id") or merged.get("forked_from_thread_id")
            if parent and merged.get("root_thread_id") == thread_id:
                merged.pop("root_thread_id", None)
                merged.pop("root_session_name", None)
            if parent:
                self._load_native({parent})
                parent_name = self._name(parent)
                if parent_name:
                    merged["parent_session_name"] = parent_name
            current = thread_id
            seen: set[str] = set()
            root: str | None = None
            for _ in range(16):
                if current in seen:
                    break
                seen.add(current)
                self._load_native({current})
                node = merged if current == thread_id else {
                    **self._observed.get(current, {}), **self._native.get(current, {}),
                }
                if node.get("relation_conflict"):
                    break
                next_id = node.get("parent_thread_id") or node.get("forked_from_thread_id")
                if not next_id:
                    root = current
                    break
                current = next_id
            if root:
                # An explicit root is useful when intermediate temporary forks vanished.
                if root != thread_id and root not in self._native and root not in self._observed:
                    root = merged.get("root_thread_id", root)
                elif root == thread_id:
                    root = merged.get("root_thread_id", root)
                root_name = self._name(root)
                if root_name or parent or own_name or merged.get("thread_source") == "user":
                    merged["root_thread_id"] = root
                if root_name:
                    merged["root_session_name"] = root_name
            else:
                if merged.get("relation_conflict"):
                    merged.pop("parent_thread_id", None)
                    merged.pop("forked_from_thread_id", None)
                    merged.pop("parent_session_name", None)
                merged.pop("root_thread_id", None)
                merged.pop("root_session_name", None)
            if own_name and classify(merged) == "unknown":
                merged["session_kind"] = "main"
            merged["session_kind"] = classify(merged)
            # Preserve stable ownership/name snapshots, never per-request compaction.
            self._observed[thread_id] = {
                k: v for k, v in merged.items()
                if k in {"parent_thread_id", "forked_from_thread_id", "thread_source",
                         "session_kind", "agent_path", "agent_name", "agent_nickname",
                         "root_thread_id", "root_session_name", "parent_session_name",
                         "relation_conflict"}
            }
            if len(self._observed) > 4000:
                self._observed.pop(next(iter(self._observed)))
            return clean_context(merged)

    def matching_thread_ids(self, query: str) -> tuple[str, ...]:
        with self._lock:
            self._refresh()
            folded = query.casefold()
            return tuple(k for k, v in self._names.items() if folded in v.casefold())

    def _name(self, thread_id: str) -> str | None:
        return self._names.get(thread_id) or self._native.get(thread_id, {}).get("name")

    def _load_native(self, requested: set[str]) -> None:
        now = time.monotonic()
        if now - self._native_at > 2.0:
            self._native.clear()
            self._native_checked.clear()
            self._native_at = now
        if len(self._native_checked) + len(requested) > 4000:
            self._native.clear()
            self._native_checked.clear()
        needed = requested - self._native_checked
        if not needed:
            return
        self._native_checked.update(needed)
        candidates = list(self.path.parent.glob("state_*.sqlite"))
        candidates.sort(key=lambda p: int(p.stem.split("_")[-1]) if p.stem.split("_")[-1].isdigit() else -1, reverse=True)
        for database in candidates:
            try:
                with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)) as conn:
                    conn.row_factory = sqlite3.Row
                    columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
                    if "id" not in columns:
                        continue
                    wanted = [k for k in ("id", "name", "title", "source", "thread_source", "agent_path", "agent_nickname", "agent_role", "rollout_path") if k in columns]
                    params = tuple(needed)
                    placeholders = ",".join("?" for _ in params)
                    for row in conn.execute(f"SELECT {','.join(wanted)} FROM threads WHERE id IN ({placeholders})", params):
                        data = dict(row)
                        meta = clean_context(data)
                        meta["name"] = safe_text(data.get("name")) or safe_text(data.get("title")) or ""
                        try:
                            source = json.loads(data.get("source") or "null")
                        except (ValueError, TypeError):
                            source = None
                        subagent = source.get("subagent") if isinstance(source, dict) else None
                        if isinstance(subagent, dict):
                            meta["thread_source"] = "subagent"
                            meta["session_kind"] = "guardian" if subagent.get("other") == "guardian" else "subagent"
                            spawn = subagent.get("thread_spawn")
                            if isinstance(spawn, dict):
                                meta.update(_relationship_context(spawn))
                            if not meta.get("parent_thread_id"):
                                meta.update(self._rollout_context(data.get("rollout_path")))
                        self._native[str(row["id"])] = meta
                    edge_columns = {r[1] for r in conn.execute("PRAGMA table_info(thread_spawn_edges)")}
                    if {"parent_thread_id", "child_thread_id"} <= edge_columns:
                        for row in conn.execute(f"SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges WHERE child_thread_id IN ({placeholders})", params):
                            parent = safe_text(row["parent_thread_id"])
                            if parent:
                                node = self._native.setdefault(row["child_thread_id"], {})
                                if node.get("parent_thread_id") not in (None, parent):
                                    node["relation_conflict"] = "true"
                                node["parent_thread_id"] = parent
                                node.setdefault("session_kind", "subagent")
                                node["thread_source"] = "subagent"
                return
            except (OSError, sqlite3.Error):
                continue

    def _rollout_context(self, value: Any) -> dict[str, str]:
        if not isinstance(value, str):
            return {}
        try:
            path = Path(value).resolve()
            home = self.path.parent.resolve()
            if not any(path.is_relative_to(home / d) for d in ("sessions", "archived_sessions")):
                return {}
            with path.open("r", encoding="utf-8") as stream:
                line = stream.readline(256 * 1024 + 1)
            if len(line) > 256 * 1024:
                return {}
            record = json.loads(line)
            if isinstance(record, dict) and record.get("type") == "session_meta":
                return _relationship_context(record.get("payload"))
        except (OSError, ValueError, TypeError):
            pass
        return {}

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

    def recent(self, since: float) -> tuple[dict[str, Any], ...]:
        cutoff = float(since)
        with self._lock:
            self._refresh()
            sessions = [
                {
                    "thread_id": thread_id,
                    "name": name,
                    "updated_at": self._updated_at[thread_id],
                }
                for thread_id, name in self._names.items()
                if self._updated_at.get(thread_id, 0.0) >= cutoff
            ]
        sessions.sort(key=lambda item: item["updated_at"], reverse=True)
        return tuple(sessions)

    def thread_id_for_session_key(self, session_key: str) -> str | None:
        with self._lock:
            self._refresh()
            return self._thread_ids_by_key.get(session_key)

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            self._stamp = None
            self._names = {}
            self._updated_at = {}
            self._thread_ids_by_key = {}
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return

        names: dict[str, str] = {}
        updated_at: dict[str, float] = {}
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
                    if isinstance(thread_name, str) and thread_name.strip():
                        names[thread_id] = thread_name.strip()
                    timestamp = _updated_at_timestamp(record.get("updated_at"))
                    if timestamp is not None and timestamp >= updated_at.get(thread_id, float("-inf")):
                        updated_at[thread_id] = timestamp
        except OSError:
            self._stamp = None
            self._names = {}
            self._updated_at = {}
            self._thread_ids_by_key = {}
            return
        self._names = names
        self._updated_at = updated_at
        self._thread_ids_by_key = {
            _session_key(thread_id): thread_id for thread_id in names
        }
        self._stamp = stamp
