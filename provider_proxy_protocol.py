from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import httpx


_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class ClaudeMessagesProtocol:
    name = "anthropic_messages"
    retryable_status_codes = frozenset({408, 429, 500, 502, 503, 504, 529})

    def request_headers(
        self,
        incoming: Mapping[str, str],
        provider: Any,
    ) -> dict[str, str]:
        headers = {
            key: value
            for key, value in incoming.items()
            if key.casefold()
            not in _HOP_BY_HOP_HEADERS
            | {"authorization", "content-length", "host", "x-api-key"}
        }
        if provider.api_key:
            if getattr(provider, "credential_kind", "api_key") == "auth_token":
                headers["Authorization"] = f"Bearer {provider.api_key}"
            else:
                headers["x-api-key"] = provider.api_key
        for key, value in provider.configured_headers.items():
            for existing in tuple(headers):
                if existing.casefold() == key.casefold():
                    headers.pop(existing)
            headers[key] = value
        return headers

    def retry_kind(self, response: httpx.Response) -> str | None:
        if response.status_code == 429:
            return "rate_limited"
        if response.status_code in self.retryable_status_codes:
            return f"http_{response.status_code}"
        return None

    def sse_preflight_decision(
        self,
        buffered: bytes,
        *,
        end_of_stream: bool = False,
    ) -> tuple[str, str | None, str | None]:
        normalized = buffered.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        events = normalized.split(b"\n\n")
        complete_events = events if end_of_stream else events[:-1]
        for event in complete_events:
            event_name, payload = _sse_event_payload(event)
            if payload is None:
                continue
            root = _decode_json(payload)
            if not isinstance(root, dict):
                return "commit", None, None
            event_type = str(root.get("type") or event_name)
            if event_type == "error":
                error = root.get("error") if isinstance(root.get("error"), dict) else {}
                error_type = str(error.get("type") or "")
                message = str(error.get("message") or "上游临时错误")
                if error_type in {
                    "overloaded_error",
                    "api_error",
                    "rate_limit_error",
                    "internal_server_error",
                }:
                    kind = "rate_limited" if error_type == "rate_limit_error" else "upstream_error"
                    return "retry", kind, message
                return "commit", None, None
            if event_type == "content_block_delta":
                delta = root.get("delta") if isinstance(root.get("delta"), dict) else {}
                if delta.get("type") in {
                    "text_delta",
                    "thinking_delta",
                    "input_json_delta",
                    "signature_delta",
                }:
                    return "commit", None, None
            if event_type in {"message_stop"}:
                return "commit", None, None
        return "wait", None, None

    def usage_capture(self, request_body: bytes, upstream_path: str) -> "ClaudeUsageCapture":
        return ClaudeUsageCapture(request_body, upstream_path)


class ClaudeUsageCapture:
    def __init__(self, request_body: bytes, upstream_path: str) -> None:
        self.request_body = request_body
        self.upstream_path = upstream_path
        request = _decode_json(request_body)
        self.model = str(request.get("model") or "unknown") if isinstance(request, dict) else "unknown"
        self._buffer = bytearray()
        self._input_tokens = 0
        self._output_tokens = 0
        self._cached_tokens = 0
        self._saw_usage = False
        self._finalized = False

    def feed(self, chunk: bytes) -> None:
        if not self._finalized and chunk:
            self._buffer.extend(chunk)

    def finalize(self, status_code: int):
        if self._finalized:
            return None
        self._finalized = True
        root = _decode_json(bytes(self._buffer))
        if isinstance(root, dict):
            self._observe(root)
        else:
            normalized = bytes(self._buffer).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            for event in normalized.split(b"\n\n"):
                _, payload = _sse_event_payload(event)
                value = _decode_json(payload) if payload else None
                if isinstance(value, dict):
                    self._observe(value)
        if not self._saw_usage or not 200 <= status_code < 300:
            return None
        from codex_local_proxy import TokenUsage

        return TokenUsage(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._input_tokens + self._output_tokens,
            cached_tokens=self._cached_tokens,
            source="upstream",
        )

    def _observe(self, root: dict[str, Any]) -> None:
        usage: Any = root.get("usage")
        message = root.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            usage = message["usage"]
        if not isinstance(usage, dict):
            return
        self._saw_usage = True
        self._input_tokens = max(self._input_tokens, _token_int(usage.get("input_tokens")))
        self._output_tokens = max(self._output_tokens, _token_int(usage.get("output_tokens")))
        self._cached_tokens = max(
            self._cached_tokens,
            _token_int(usage.get("cache_read_input_tokens"))
            + _token_int(usage.get("cache_creation_input_tokens")),
        )


def _decode_json(value: bytes) -> Any | None:
    try:
        return json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _sse_event_payload(event: bytes) -> tuple[str, bytes | None]:
    event_name = ""
    data_lines: list[bytes] = []
    for line in event.split(b"\n"):
        if line.startswith(b"event:"):
            event_name = line[6:].strip().decode("utf-8", errors="replace")
        elif line.startswith(b"data:"):
            data_lines.append(line[5:].lstrip())
    return event_name, b"\n".join(data_lines) if data_lines else None


def _token_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))
