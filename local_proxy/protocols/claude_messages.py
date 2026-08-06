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

    def upstream_url(self, provider: Any, upstream_path: str) -> str:
        base_url = provider.base_url.rstrip("/")
        prefix = base_url if base_url.casefold().endswith("/v1") else f"{base_url}/v1"
        return f"{prefix}/{upstream_path.lstrip('/')}"

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
        content_type = response.headers.get("content-type", "")
        media_type = content_type.partition(";")[0].strip().casefold()
        if 200 <= response.status_code < 300 and media_type in {
            "text/html",
            "application/xhtml+xml",
        }:
            return "malformed_response"
        if response.status_code == 429:
            return "rate_limited"
        if response.status_code in self.retryable_status_codes:
            return f"http_{response.status_code}"
        return None

    def empty_response_decision(
        self,
        response: httpx.Response,
    ) -> tuple[str, str | None, str | None]:
        if 200 <= response.status_code < 300:
            return (
                "retry",
                "malformed_response",
                "HTTP 200：上游返回了空响应",
            )
        return "commit", None, None

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
                continue
            if "choices" in root:
                return (
                    "retry",
                    "malformed_response",
                    "HTTP 200：上游返回了 OpenAI SSE，而不是 Anthropic 消息流",
                )
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
            if event_type == "content_block_start":
                content_block = (
                    root.get("content_block")
                    if isinstance(root.get("content_block"), dict)
                    else {}
                )
                block_type = content_block.get("type")
                if block_type in {
                    "tool_use",
                    "server_tool_use",
                    "web_search_tool_result",
                }:
                    return "commit", None, None
                if block_type == "text" and content_block.get("text"):
                    return "commit", None, None
                if block_type == "thinking" and content_block.get("thinking"):
                    return "commit", None, None
                if block_type == "redacted_thinking" and content_block.get("data"):
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
            if event_type == "message_stop":
                return (
                    "retry",
                    "malformed_response",
                    "HTTP 200：上游在没有生成内容时结束了 Anthropic 消息流",
                )
        if end_of_stream:
            return (
                "retry",
                "malformed_response",
                "HTTP 200：上游返回了空响应或非 Anthropic 消息流",
            )
        return "wait", None, None

    def usage_capture(self, request_body: bytes, upstream_path: str) -> "ClaudeUsageCapture":
        return ClaudeUsageCapture(request_body, upstream_path)

    def failure_capture(self) -> "ClaudeFailureCapture":
        return ClaudeFailureCapture()


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
        if not 200 <= status_code < 300:
            return None
        from local_proxy.core import TokenUsage

        if not self._saw_usage:
            from local_proxy.core import _estimate_text_tokens

            request = _decode_json(self.request_body)
            input_text = "\n".join(_anthropic_text_segments(request))
            response = _decode_json(bytes(self._buffer))
            output_text = "\n".join(_anthropic_text_segments(response, response_only=True))
            input_tokens, input_method = _estimate_text_tokens(input_text, self.model)
            output_tokens, output_method = _estimate_text_tokens(output_text, self.model)
            method = input_method if input_method == output_method else f"{input_method}+{output_method}"
            return TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                source="estimated",
                estimate_method=method,
            )

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


class ClaudeFailureCapture:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)

    def finalize(self) -> tuple[str, str] | None:
        normalized = bytes(self._buffer).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        for event in normalized.split(b"\n\n"):
            event_name, payload = _sse_event_payload(event)
            root = _decode_json(payload) if payload else None
            if not isinstance(root, dict) or str(root.get("type") or event_name) != "error":
                continue
            error = root.get("error") if isinstance(root.get("error"), dict) else {}
            error_type = str(error.get("type") or "")
            if error_type in {
                "overloaded_error",
                "api_error",
                "rate_limit_error",
                "internal_server_error",
            }:
                kind = "rate_limited" if error_type == "rate_limit_error" else "upstream_error"
                return kind, str(error.get("message") or "上游临时错误")
        return None

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


def _anthropic_text_segments(value: Any, *, response_only: bool = False) -> list[str]:
    if not isinstance(value, dict):
        return []
    segments: list[str] = []
    if not response_only:
        system = value.get("system")
        if isinstance(system, str):
            segments.append(system)
        elif isinstance(system, list):
            _append_anthropic_content(segments, system)
        messages = value.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    _append_anthropic_content(segments, message.get("content"))
    _append_anthropic_content(segments, value.get("content"))
    return segments


def _append_anthropic_content(segments: list[str], content: Any) -> None:
    if isinstance(content, str):
        if content.strip():
            segments.append(content.strip())
        return
    if not isinstance(content, list):
        return
    for part in content:
        if not isinstance(part, dict):
            continue
        for key in ("text", "thinking", "input", "content"):
            value = part.get(key)
            if isinstance(value, str) and value.strip():
                segments.append(value.strip())
            elif isinstance(value, (dict, list)):
                segments.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
