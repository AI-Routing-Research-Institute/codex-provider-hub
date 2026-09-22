"""Extract and compare model identifiers declared by upstream responses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


MAX_UPSTREAM_RESPONSE_MODEL_CHARS = 200
_DATE_SUFFIX_RE = re.compile(r"-(?:20\d{2}(?:-?\d{2}){2})$")
_TERMINAL_EVENTS = frozenset(
    {
        "response.completed",
        "response.done",
        "response.failed",
        "response.incomplete",
        "response.cancelled",
        "response.canceled",
    }
)


def _clean_model(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:MAX_UPSTREAM_RESPONSE_MODEL_CHARS] or None


def _nested_dict(root: Any, key: str) -> dict[str, Any]:
    value = root.get(key) if isinstance(root, dict) else None
    return value if isinstance(value, dict) else {}


def response_model_candidates(root: Any, protocol: str = "openai") -> tuple[str, ...]:
    """Return model fields in protocol-specific priority order."""
    if not isinstance(root, dict):
        return ()
    protocol_name = str(protocol or "openai").casefold()
    candidates: list[Any] = []
    if protocol_name in {"anthropic", "messages", "claude"}:
        candidates.extend((_nested_dict(root, "message").get("model"), root.get("model")))
    elif protocol_name in {"gemini", "google"}:
        response = _nested_dict(root, "response")
        candidates.extend((root.get("modelVersion"), response.get("modelVersion")))
        candidates.append(_nested_dict(response, "response").get("modelVersion"))
    else:
        response = _nested_dict(root, "response")
        candidates.extend((response.get("model"), root.get("model")))
    result: list[str] = []
    for value in candidates:
        model = _clean_model(value)
        if model is not None and model not in result:
            result.append(model)
    return tuple(result)


@dataclass
class UpstreamResponseModelObserver:
    """Collect the first and terminal model declarations in a response stream."""

    protocol: str = "openai"
    first: str | None = None
    terminal: str | None = None
    conflict: bool = False
    _seen: set[str] = field(default_factory=set, repr=False)

    def observe(self, root: Any) -> None:
        models = response_model_candidates(root, self.protocol)
        if not models:
            return
        event_type = str(root.get("type") or "").casefold() if isinstance(root, dict) else ""
        for model in models:
            key = model.casefold()
            if self._seen and key not in self._seen:
                self.conflict = True
            self._seen.add(key)
            if self.first is None:
                self.first = model
            if event_type in _TERMINAL_EVENTS:
                self.terminal = model

    @property
    def model(self) -> str | None:
        return self.terminal or self.first


def canonical_model_name(value: str | None) -> str:
    """Normalize harmless provider version suffixes before an audit comparison."""
    model = str(value or "").strip().casefold()
    if not model:
        return ""
    if model.startswith("grok-4.5"):
        return "grok-4.5-build"
    if model.startswith("grok-4.6"):
        return "grok-4.6-build"
    model = re.sub(r"-latest$", "", model)
    return _DATE_SUFFIX_RE.sub("", model)


def models_match_for_audit(sent_model: str | None, response_model: str | None) -> bool:
    sent = canonical_model_name(sent_model)
    response = canonical_model_name(response_model)
    return bool(sent and response and sent == response)


def upstream_model_mismatch(
    sent_model: str | None,
    response_model: str | None,
) -> bool | None:
    """Return True/False when a response model is available, otherwise None."""
    if not _clean_model(response_model):
        return None
    return not models_match_for_audit(sent_model, response_model)


def observed_models(observer: UpstreamResponseModelObserver) -> Iterable[str]:
    """Expose a stable iterable for diagnostics and tests."""
    return tuple(observer._seen)
