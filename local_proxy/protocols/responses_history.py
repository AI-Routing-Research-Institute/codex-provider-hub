"""Narrow compatibility for third-party reasoning replayed to GPT."""

from __future__ import annotations

import json
import re
from typing import Any


# The DeepSeek Responses bridge uses a UUID plus an output index as its
# reasoning token. It is not a GPT encrypted reasoning item. Removing just
# encrypted_content or just content still produces HTTP 400 on replay.
_BRIDGE_REASONING_TOKEN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[0-9]+",
    re.IGNORECASE,
)


def _is_bridge_reasoning(item: Any) -> bool:
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return False
    token = item.get("encrypted_content")
    content = item.get("content")
    return (
        isinstance(token, str)
        and _BRIDGE_REASONING_TOKEN.fullmatch(token) is not None
        and isinstance(content, list)
        and any(isinstance(part, dict) and part.get("type") == "reasoning_text" for part in content)
    )


def normalize_deepseek_history(payload: bytes, *, model: str) -> bytes | None:
    """Return a sending copy without known bridge reasoning, or None unchanged.

    Use the model for the current attempt, after explicit mapping. DeepSeek
    continuations keep their own reasoning state. Never touch persisted history
    or infer validity from ciphertext length alone.
    """
    if not model.casefold().startswith("gpt-"):
        return None
    # Most requests do not contain this third-party plaintext reasoning format.
    if b"reasoning_text" not in payload:
        return None
    try:
        root = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(root, dict) or not isinstance(root.get("input"), list):
        return None
    items = root["input"]
    removed_ids = {
        item["id"] for item in items if _is_bridge_reasoning(item)
        and isinstance(item.get("id"), str)
    }
    root["input"] = [
        item for item in items
        if not _is_bridge_reasoning(item)
        and not (isinstance(item, dict) and item.get("type") == "item_reference"
                 and isinstance(item.get("id"), str) and item["id"] in removed_ids)
    ]
    if len(root["input"]) == len(items):
        return None
    return json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
