"""Bounded Codex metadata and display attribution, without conversation content."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


METADATA_HEADER = "x-codex-turn-metadata"
MAX_METADATA_CHARS = 32 * 1024
CONTEXT_FIELDS = (
    "thread_id", "thread_source", "turn_trigger", "request_kind", "parent_thread_id",
    "forked_from_thread_id", "root_thread_id", "root_session_name",
    "parent_session_name", "agent_name", "agent_path", "agent_nickname",
    "agent_role", "session_kind", "attribution_origin", "relation_conflict",
)
KINDS = {
    "main": "", "thread_description": "会话摘要", "thread_title": "会话命名",
    "subagent": "子 agent", "guardian": "自动审查", "side_chat": "侧边聊天",
    "fork": "关联分支", "unknown": "",
}
ATTRIBUTION_COLUMNS = (
    "parent_thread_id", "root_thread_id", "root_session_name", "session_kind",
    "request_kind", "agent_name", "agent_nickname", "session_context_json",
)


def context_values(value: Mapping[str, Any]) -> tuple[str | None, ...]:
    context = clean_context(value)
    return (
        context.get("parent_thread_id") or context.get("forked_from_thread_id"),
        context.get("root_thread_id"), context.get("root_session_name"),
        classify(context) if context else None, context.get("request_kind"),
        context.get("agent_path") or context.get("agent_name"),
        context.get("agent_nickname"), context_json(context),
    )


def safe_text(value: Any, limit: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        return None
    return value


def clean_context(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: text for key in CONTEXT_FIELDS
        if (text := safe_text(value.get(key))) is not None
    }


def request_context(headers: Mapping[str, str]) -> dict[str, str]:
    raw = headers.get(METADATA_HEADER)
    if raw is None:
        raw = next((v for k, v in headers.items() if k.lower() == METADATA_HEADER), None)
    if not isinstance(raw, str) or len(raw) > MAX_METADATA_CHARS:
        return {}
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(metadata, dict):
        return {}
    # Display/snapshot fields are derived locally, never accepted from the client.
    fields = (
        "thread_id", "thread_source", "turn_trigger", "request_kind",
        "parent_thread_id", "forked_from_thread_id", "root_thread_id",
        "agent_name", "agent_path", "agent_nickname", "agent_role",
    )
    return {k: text for k in fields if (text := safe_text(metadata.get(k))) is not None}


def decode_context(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        if len(value) > 8192:
            return {}
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return clean_context(value)


def context_json(value: Mapping[str, Any]) -> str | None:
    context = clean_context(value)
    return json.dumps(context, ensure_ascii=False, separators=(",", ":")) if context else None


def classify(context: Mapping[str, Any]) -> str:
    source = context.get("thread_source")
    trigger = context.get("turn_trigger")
    # These labels require explicit metadata; model names and timing are never used.
    if context.get("session_kind") == "guardian" or source == "guardian":
        return "guardian"
    for marker, kind in (
        ("thread_description", "thread_description"),
        ("thread_title", "thread_title"),
        ("side_chat", "side_chat"),
        ("side_conversation", "side_chat"),
    ):
        if marker in (source, trigger):
            return kind
    if source == "subagent" or context.get("session_kind") == "subagent":
        return "subagent"
    if (context.get("parent_thread_id") or context.get("forked_from_thread_id")
            or (context.get("root_thread_id") and context.get("root_thread_id") != context.get("thread_id"))):
        return "fork"
    if source == "user" or context.get("session_kind") == "main":
        return "main"
    return "unknown"


def session_key(thread_id: Any) -> str | None:
    thread_id = safe_text(thread_id)
    return hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:24] if thread_id else None


def display_attribution(
    thread_id: str | None, name: str, context: Mapping[str, Any],
) -> dict[str, Any]:
    context = clean_context(context)
    if thread_id:
        context["thread_id"] = thread_id
    kind = classify(context)
    own_name = name if name and name != "未知会话" else None
    root_name = context.get("root_session_name")
    parent_name = context.get("parent_session_name")
    label = KINDS[kind]
    agent = context.get("agent_path") or context.get("agent_name")
    agent = agent.rsplit("/", 1)[-1] if agent and agent != "/root" else None
    agent = agent or context.get("agent_nickname")
    if kind == "subagent" and agent:
        label = f"子 agent · {agent}"
    if kind == "fork" and own_name:
        display_name = own_name
        label = f"来自：{parent_name or root_name}" if parent_name or root_name else "关联分支"
    elif kind not in ("main", "unknown"):
        display_name = root_name or own_name or label
        if kind == "side_chat" and root_name and own_name and own_name != root_name:
            label = f"{label} · {own_name}"
        if not root_name:
            label = f"{label} · 所属会话未识别" if own_name else "所属会话未识别"
    else:
        display_name = own_name or root_name or "未识别会话"
        if not own_name and not root_name:
            label = "来源信息不足"
    if context.get("request_kind") == "compaction":
        label = f"{label} · 上下文压缩" if label else "上下文压缩"
    return {
        "session_display_name": display_name,
        "session_label": label,
        "session_tooltip": " · ".join(dict.fromkeys(
            text for text in (display_name, label, f"直接父会话：{parent_name}" if parent_name else "") if text
        )),
        "session_kind": kind,
        "parent_session_key": session_key(context.get("parent_thread_id") or context.get("forked_from_thread_id")),
        "root_session_key": session_key(context.get("root_thread_id")),
        "root_session_name": root_name,
        "agent_name": agent,
        "agent_nickname": context.get("agent_nickname"),
    }
