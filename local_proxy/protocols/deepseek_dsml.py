"""DeepSeek response compatibility for raw DSML tool calls.

The normal DeepSeek Responses endpoint is passed through unchanged.  This
adapter only takes ownership of a response after it sees a DSML prefix (or a
Chat Completions response) and then emits the Responses SSE contract expected
by Codex.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

from local_proxy.protocols.responses_history import normalize_deepseek_history
from local_proxy.protocols.responses_history import _is_bridge_reasoning
from local_proxy.protocols.responses_tools import (
    ResponsesTools,
    prepare_deepseek_tools,
    ToolProtocolError as DeepSeekProtocolError,
    tool_argument_field,
    tool_event_prefix,
)


DSML_BUFFER_LIMIT = 256 * 1024
DSML_ARGUMENT_CHUNK_SIZE = 256

_CANONICAL_OPEN = "<｜DSML｜function_calls>"
_CANONICAL_CLOSE = "</｜DSML｜function_calls>"
_PARTIAL_DSML_STARTS = tuple(
    marker.casefold()
    for marker in (
        "<｜DSML｜function_calls",
        "<｜DSML｜tool_calls",
        "<｜DSML｜invoke",
        "<｜DSML｜parameter",
        "<｜｜DSML｜｜function_calls",
        "<｜｜DSML｜｜tool_calls",
        "<｜｜DSML｜｜invoke",
        "<｜｜DSML｜｜parameter",
        "<|DSML|function_calls",
        "<|DSML|tool_calls",
        "<|DSML|invoke",
        "<|DSML|parameter",
        "<||DSML||function_calls",
        "<||DSML||tool_calls",
        "<||DSML||invoke",
        "<||DSML||parameter",
        "<tool_call",
        "<tool_calls",
        "<invoke",
        "<parameter",
        "<DSML:function_calls",
        "<DSML:tool_calls",
        "<DSMLfunction_calls",
        "<DSMLtool_calls",
        "<DSML>function_calls",
        "<DSML>tool_calls",
    )
)
_DSML_TAG_RE = re.compile(
    r"<\s*(?P<close>/?)\s*(?:[｜|]\s*){1,2}DSML\s*"
    r"(?:[｜|]\s*){1,2}"
    r"(?P<name>function_calls|tool_calls|invoke|parameter)\b",
    re.IGNORECASE,
)
_LEGACY_CONTAINER_RE = re.compile(
    r"<\s*(?P<close>/?)(?:DSML)\s*>\s*"
    r"(?P<name>function_calls|tool_calls)\s*>",
    re.IGNORECASE,
)
_DSML_OPEN_RE = re.compile(
    r"<｜DSML｜(?:function_calls|tool_calls)\s*>", re.IGNORECASE
)
_DSML_CLOSE_RE = re.compile(
    r"</｜DSML｜(?:function_calls|tool_calls)\s*>", re.IGNORECASE
)
_INVOKE_RE = re.compile(
    r"<｜DSML｜invoke\b(?P<attrs>[^>]*)>(?P<body>.*?)</｜DSML｜invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PARAMETER_RE = re.compile(
    r"<｜DSML｜parameter\b(?P<attrs>[^>]*?)(?P<self_closing>/?)>"
    r"(?:(?P<value>.*?)</｜DSML｜parameter\s*>)?",
    re.IGNORECASE | re.DOTALL,
)
_NAME_PARAMETERS_RE = re.compile(
    r"<name>\s*(?P<name>.*?)\s*</name>.*?"
    r"<parameters>\s*(?P<arguments>.*?)\s*</parameters>",
    re.IGNORECASE | re.DOTALL,
)
_TOOL_CALL_RE = re.compile(
    r"<tool_call\s*>(?P<body>.*?)</tool_call\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ATTRIBUTE_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)


def _normalize_dsml_text(value: str) -> str:
    def canonicalize(match: re.Match[str]) -> str:
        close = match.group("close")
        name = match.group("name").casefold()
        return f"<{close}｜DSML｜{name}>"

    text = _DSML_TAG_RE.sub(
        lambda match: f"<{match.group('close')}｜DSML｜{match.group('name').casefold()}",
        value,
    )
    text = _LEGACY_CONTAINER_RE.sub(canonicalize, text)
    text = re.sub(
        r"<(?P<close>/?)(?:DSML)\s*[: ]\s*"
        r"(?P<name>function_calls|tool_calls)\s*>",
        canonicalize,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<tool_calls\s*>", _CANONICAL_OPEN, text, flags=re.IGNORECASE)
    text = re.sub(r"</tool_calls\s*>", _CANONICAL_CLOSE, text, flags=re.IGNORECASE)
    text = re.sub(r"<invoke\b", "<｜DSML｜invoke", text, flags=re.IGNORECASE)
    text = re.sub(r"</invoke\s*>", "</｜DSML｜invoke>", text, flags=re.IGNORECASE)
    text = re.sub(r"<parameter\b", "<｜DSML｜parameter", text, flags=re.IGNORECASE)
    text = re.sub(r"</parameter\s*>", "</｜DSML｜parameter>", text, flags=re.IGNORECASE)
    return text


def has_any_dsml_prefix(value: str) -> bool:
    if not value:
        return False
    text = _normalize_dsml_text(value)
    markers = (
        _CANONICAL_OPEN,
        _CANONICAL_CLOSE,
        "<｜DSML｜tool_calls>",
        "</｜DSML｜tool_calls>",
        "<｜DSML｜invoke",
        "<｜DSML｜parameter",
        "<tool_call>",
        "<tool_calls>",
    )
    if any(marker in text for marker in markers):
        return True
    tail = text[-96:]
    prefixes = tuple(marker[: index] for marker in markers for index in range(3, len(marker)))
    return any(tail.endswith(prefix) for prefix in prefixes)


def _has_partial_dsml_start(value: str) -> bool:
    """Keep a short suffix private until it is or is not a DSML opener."""
    start = value.rfind("<")
    if start < 0:
        return False
    suffix = re.sub(r"\s+", "", value[start:]).casefold()
    if not suffix or len(suffix) > 96:
        return False
    return any(marker.startswith(suffix) for marker in _PARTIAL_DSML_STARTS)


def has_complete_dsml_block(value: str) -> bool:
    text = _normalize_dsml_text(value)
    container = _DSML_OPEN_RE.search(text)
    if container is not None:
        return _DSML_CLOSE_RE.search(text, container.end()) is not None
    return bool(_TOOL_CALL_RE.search(text) or _INVOKE_RE.search(text))


def find_dsml_start(value: str) -> int:
    text = _normalize_dsml_text(value)
    indexes = [
        match.start()
        for match in (
            _DSML_OPEN_RE.search(text),
            _TOOL_CALL_RE.search(text),
            _INVOKE_RE.search(text),
        )
        if match
    ]
    if indexes:
        return min(indexes)
    for marker in ("<｜DSML｜invoke", "<｜DSML｜parameter"):
        index = text.find(marker)
        if index >= 0:
            return index
    return len(text)


def _attributes(value: str) -> dict[str, str]:
    return {
        match.group("key").casefold(): html.unescape(match.group("value"))
        for match in _ATTRIBUTE_RE.finditer(value)
    }


def _json_arguments(value: Any) -> str:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
    unescaped = str(value).strip()
    if not unescaped:
        return "{}"
    try:
        parsed = json.loads(unescaped)
    except (TypeError, ValueError):
        unescaped = html.unescape(unescaped)
        try:
            parsed = json.loads(unescaped)
        except (TypeError, ValueError):
            return json.dumps(unescaped, ensure_ascii=False, separators=(",", ":"))
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _stable_id(prefix: str, index: int, name: str, arguments: str) -> str:
    digest = hashlib.sha256(
        f"{index}:{name}:{arguments}".encode("utf-8", errors="replace")
    ).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _append_call(
    calls: list[dict[str, str]],
    *,
    name: str,
    arguments: str,
    attrs: Mapping[str, str] | None = None,
) -> None:
    normalized_name = html.unescape(name).strip()
    if not normalized_name or len(normalized_name) > 240:
        raise DeepSeekProtocolError("DSML 工具调用缺少有效工具名称")
    normalized_arguments = _json_arguments(arguments)
    # A call with the same arguments in a later response is a new invocation.
    # Content-only hashes collide when the client replays the combined history.
    generated_id = uuid.uuid4().hex
    attributes = {
        str(key).casefold(): str(value)
        for key, value in dict(attrs or {}).items()
        if value is not None
    }
    call_id = attributes.get("call_id") or attributes.get("id")
    item_id = (
        attributes.get("item_id")
        or attributes.get("itemid")
        or attributes.get("output_item_id")
    )
    calls.append(
        {
            "name": normalized_name,
            "arguments": normalized_arguments,
            "call_id": call_id.strip()[:160] if call_id and call_id.strip() else "call_dsml_" + generated_id,
            "item_id": item_id.strip()[:160] if item_id and item_id.strip() else "fc_dsml_" + generated_id,
        }
    )


def parse_dsml_tool_calls(
    value: str,
    *,
    allowed_tool_names: set[str] | None = None,
) -> tuple[dict[str, str], ...]:
    """Parse the known DeepSeek DSML variants into neutral tool calls."""

    text = _normalize_dsml_text(value)
    calls: list[dict[str, str]] = []
    container_pattern = re.compile(
        r"<｜DSML｜(?:function_calls|tool_calls)\s*>(.*?)"
        r"</｜DSML｜(?:function_calls|tool_calls)\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    blocks = [match.group(1) for match in container_pattern.finditer(text)]
    blocks.extend(match.group("body") for match in _TOOL_CALL_RE.finditer(text))
    if not blocks:
        blocks = [text]

    for block in blocks:
        name_parameter_matches = tuple(_NAME_PARAMETERS_RE.finditer(block))
        for match in name_parameter_matches:
            _append_call(calls, name=match.group("name"), arguments=match.group("arguments"))

        invoke_matches = tuple(_INVOKE_RE.finditer(block))
        for match in invoke_matches:
            attributes = _attributes(match.group("attrs"))
            body = match.group("body")
            parameter_matches = tuple(_PARAMETER_RE.finditer(body))
            if parameter_matches:
                parameters: dict[str, Any] = {}
                for parameter in parameter_matches:
                    parameter_attrs = _attributes(parameter.group("attrs"))
                    parameter_name = (
                        parameter_attrs.get("name")
                        or parameter_attrs.get("parameter")
                        or parameter_attrs.get("key")
                        or ""
                    ).strip()
                    if not parameter_name:
                        raise DeepSeekProtocolError("DSML 参数缺少 name")
                    raw_value = parameter.group("value")
                    if raw_value is None:
                        raw_value = (
                            parameter_attrs.get("string")
                            or parameter_attrs.get("value")
                            or parameter_attrs.get("content")
                            or ""
                        )
                    raw_value = html.unescape(raw_value)
                    if parameter_attrs.get("string", "").casefold() == "true":
                        parameters[parameter_name] = raw_value
                    else:
                        try:
                            parameters[parameter_name] = json.loads(raw_value.strip())
                        except (TypeError, ValueError):
                            parameters[parameter_name] = raw_value.strip()
                arguments: Any = parameters
            else:
                arguments = body.strip() or "{}"
            _append_call(
                calls,
                name=attributes.get("name", ""),
                arguments=arguments,
                attrs=attributes,
            )

        if not invoke_matches and not name_parameter_matches:
            stripped = block.strip()
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError):
                try:
                    parsed = json.loads(html.unescape(stripped))
                except (TypeError, ValueError):
                    parsed = None
            if isinstance(parsed, dict):
                function = parsed.get("function")
                function = function if isinstance(function, dict) else parsed
                name = function.get("name") if isinstance(function, dict) else None
                arguments = (
                    function.get("arguments")
                    if isinstance(function, dict)
                    else None
                )
                if isinstance(name, str) and name.strip():
                    _append_call(
                        calls,
                        name=name,
                        arguments=arguments if arguments is not None else {},
                        attrs=parsed,
                    )
    if not calls:
        raise DeepSeekProtocolError("检测到 DSML 工具调用标记，但无法解析工具名称或参数")
    if allowed_tool_names is not None:
        unknown = [call["name"] for call in calls if call["name"] not in allowed_tool_names]
        if unknown:
            raise DeepSeekProtocolError(
                "DSML 调用了请求中未声明的工具：" + ", ".join(unknown[:5])
            )
    return tuple(calls)


def _event_parts(event: bytes) -> tuple[str, bytes | None, Any | None]:
    event_name = ""
    data_lines: list[bytes] = []
    for line in event.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
        if line.startswith(b"event:"):
            event_name = line[6:].strip().decode("utf-8", errors="replace")
        elif line.startswith(b"data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return event_name, None, None
    payload = b"\n".join(data_lines)
    if payload == b"[DONE]":
        return event_name, payload, None
    try:
        return event_name, payload, json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return event_name, payload, None


async def _sse_events(first_chunk: bytes, stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    pending = bytearray()
    if first_chunk:
        pending.extend(first_chunk)
    async for chunk in stream:
        if chunk:
            pending.extend(chunk)
        normalized = bytes(pending).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        while b"\n\n" in normalized:
            event, normalized = normalized.split(b"\n\n", 1)
            pending = bytearray(normalized)
            yield event + b"\n\n"
    normalized = bytes(pending).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    while b"\n\n" in normalized:
        event, normalized = normalized.split(b"\n\n", 1)
        yield event + b"\n\n"
    if normalized:
        yield normalized


def _sse_event(root: Mapping[str, Any]) -> bytes:
    event_type = str(root.get("type") or "message")
    return (
        f"event: {event_type}\n".encode("utf-8")
        + b"data: "
        + json.dumps(dict(root), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n\n"
    )


def _text_from_response_event(root: Mapping[str, Any]) -> str:
    if root.get("type") != "response.output_text.delta":
        return ""
    value = root.get("delta")
    if isinstance(value, str):
        return value
    return ""


def _text_from_chat_event(root: Mapping[str, Any]) -> str:
    choices = root.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ""
    for key in ("content", "reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _response_identity(root: Mapping[str, Any], default_model: str) -> tuple[str, str]:
    response = root.get("response") if isinstance(root.get("response"), dict) else {}
    response_id = response.get("id") or root.get("id")
    model = response.get("model") or root.get("model") or default_model
    return (
        str(response_id) if isinstance(response_id, str) and response_id else "resp_dsml_" + uuid.uuid4().hex,
        str(model) if isinstance(model, str) and model else default_model,
    )


def _response_text_candidates(root: Mapping[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    output_text = root.get("output_text")
    if isinstance(output_text, str):
        candidates.append(output_text)
    choices = root.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            candidates.append(message["content"])
    output = root.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                candidates.append(content)
                continue
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                for key in ("text", "thinking", "reasoning"):
                    value = part.get(key)
                    if isinstance(value, str):
                        candidates.append(value)
    return tuple(candidates)


def _converted_response_body(
    root: Mapping[str, Any],
    content: str,
    *,
    request_body: bytes,
    model: str,
) -> bytes:
    tools = ResponsesTools(request_body)
    calls = parse_dsml_tool_calls(content)
    tool_items = [tools.call_item(call) for call in calls]
    normalized = _normalize_dsml_text(content)
    start = find_dsml_start(normalized)
    prefix = normalized[:start]
    output: list[dict[str, Any]] = []
    if prefix:
        output.append(
            {
                "id": _stable_id("msg_dsml", 0, "message", prefix),
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": prefix,
                        "annotations": [],
                    }
                ],
            }
        )
    output.extend(tool_items)
    response_id, response_model = _response_identity(root, model)
    converted = dict(root)
    converted.pop("choices", None)
    converted.update(
        {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "model": response_model,
            "output": output,
            "output_text": prefix,
        }
    )
    return json.dumps(converted, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class _ChatResponseTranslator:
    def __init__(self, *, default_model: str, tools: ResponsesTools) -> None:
        self.response_id = "resp_chat_" + uuid.uuid4().hex
        self.model = default_model
        self.tools = tools
        self.sequence = 0
        self.message_id = "msg_" + uuid.uuid4().hex
        self.message_started = False
        self.message_text = ""
        self.tool_calls: dict[int, dict[str, str]] = {}
        self.tool_bytes = 0
        self.completed = False

    def _event(self, event_type: str, **values: Any) -> bytes:
        self.sequence += 1
        return _sse_event({"type": event_type, "sequence_number": self.sequence, **values})

    def start_events(self) -> tuple[bytes, ...]:
        return tuple(
            self._event(event_type, response={
                "id": self.response_id, "object": "response", "status": "in_progress",
                "model": self.model, "output": [],
            })
            for event_type in ("response.created", "response.in_progress")
        )

    def process(self, root: Mapping[str, Any]) -> list[bytes]:
        if self.completed:
            return []
        choices = root.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return []
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        output: list[bytes] = []
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.message_started = True
            self.message_text += content
            if len(self.message_text.encode("utf-8")) > DSML_BUFFER_LIMIT:
                raise DeepSeekProtocolError("Chat 正文超过缓冲大小限制")
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw in tool_calls:
                if not isinstance(raw, dict):
                    continue
                index = raw.get("index", 0)
                if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                    raise DeepSeekProtocolError("Chat 工具调用 index 无效")
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                state = self.tool_calls.setdefault(index, {
                    "call_id": "call_chat_" + uuid.uuid4().hex,
                    "item_id": "fc_chat_" + uuid.uuid4().hex,
                    "name": "", "arguments": "",
                })
                if isinstance(raw.get("id"), str) and raw["id"]:
                    state["call_id"] = raw["id"]
                for field in ("name", "arguments"):
                    fragment = function.get(field)
                    if isinstance(fragment, str):
                        state[field] += fragment
                        self.tool_bytes += len(fragment.encode("utf-8"))
                if self.tool_bytes > DSML_BUFFER_LIMIT:
                    raise DeepSeekProtocolError("Chat 工具调用超过缓冲大小限制")
        # Custom tools wrap free-form input in JSON on the Chat wire. Wait for
        # complete arguments before unwrapping, then emit the correct event type.
        if choice.get("finish_reason") is not None:
            output.extend(self.finish())
        return output

    def finish(self) -> list[bytes]:
        if self.completed:
            return []
        tool_items = [self.tools.call_item(self.tool_calls[index]) for index in sorted(self.tool_calls)]
        output: list[bytes] = []
        output_items: list[dict[str, Any]] = []
        if self.message_started:
            part = {"type": "output_text", "text": self.message_text, "annotations": []}
            message = {"id": self.message_id, "type": "message", "role": "assistant",
                       "phase": "commentary" if tool_items else "final_answer",
                       "status": "completed", "content": [part]}
            output_items.append(message)
            output.append(self._event("response.output_item.added", output_index=0,
                item={**message, "status": "in_progress", "content": []}))
            output.append(self._event("response.content_part.added", output_index=0,
                content_index=0, item_id=self.message_id,
                part={"type": "output_text", "text": "", "annotations": []}))
            output.append(self._event("response.output_text.delta", output_index=0,
                content_index=0, item_id=self.message_id, delta=self.message_text))
            output.append(self._event("response.output_text.done", output_index=0,
                content_index=0, item_id=self.message_id, text=self.message_text))
            output.append(self._event("response.content_part.done", output_index=0,
                content_index=0, item_id=self.message_id, part=part))
            output.append(self._event("response.output_item.done", output_index=0, item=message))
        for index, item in enumerate(tool_items, start=len(output_items)):
            field = tool_argument_field(item)
            prefix = tool_event_prefix(item)
            arguments = item[field]
            output.append(self._event("response.output_item.added", output_index=index,
                item={**item, "status": "in_progress", field: ""}))
            for offset in range(0, len(arguments), DSML_ARGUMENT_CHUNK_SIZE):
                output.append(self._event(prefix + ".delta", output_index=index,
                    item_id=item["id"], delta=arguments[offset:offset + DSML_ARGUMENT_CHUNK_SIZE]))
            output.append(self._event(prefix + ".done", output_index=index,
                item_id=item["id"], **{field: arguments}))
            output.append(self._event("response.output_item.done", output_index=index, item=item))
            output_items.append(item)
        output.append(self._event("response.completed", response={
            "id": self.response_id, "object": "response", "status": "completed",
            "model": self.model, "output": output_items,
        }))
        output.append(b"data: [DONE]\n\n")
        self.completed = True
        return output


class DeepSeekDSMLProtocol:
    """Provider-level adapter that detects DSML without altering normal Responses."""

    name = "deepseek_dsml_auto"

    @staticmethod
    def prepare_request_body(payload: bytes, *, model: str) -> bytes | None:
        return normalize_deepseek_history(payload, model=model) or prepare_deepseek_tools(payload, model=model)

    def upstream_url(self, provider: Any, upstream_path: str) -> str:
        return f"{provider.base_url.rstrip('/')}/{upstream_path.lstrip('/')}"

    def request_headers(
        self,
        incoming: Mapping[str, str],
        provider: Any,
    ) -> dict[str, str]:
        from local_proxy.core import _upstream_request_headers

        return _upstream_request_headers(incoming, provider)

    def retry_kind(self, response: Any) -> str | None:
        from local_proxy.core import _retry_kind

        return _retry_kind(response)

    def sse_preflight_decision(
        self,
        buffered: bytes,
        *,
        end_of_stream: bool = False,
    ) -> tuple[str, str | None, str | None]:
        from local_proxy.core import _sse_event_payload, _sse_preflight_decision

        normalized = buffered.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        for event in normalized.split(b"\n\n")[:-1 if not end_of_stream else None]:
            event_name, payload = _sse_event_payload(event)
            if payload is None:
                continue
            if payload == b"[DONE]":
                return "commit", None, None
            try:
                root = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                text = payload.decode("utf-8", errors="replace")
                if has_any_dsml_prefix(text):
                    return "commit", None, None
                continue
            if isinstance(root, dict):
                text = _text_from_response_event(root) or _text_from_chat_event(root)
                if has_any_dsml_prefix(text) or has_any_dsml_prefix(payload.decode("utf-8", errors="replace")):
                    return "commit", None, None
                if "choices" in root:
                    choices = root.get("choices")
                    if isinstance(choices, list) and choices:
                        choice = choices[0] if isinstance(choices[0], dict) else {}
                        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                        if text or delta.get("tool_calls") or choice.get("finish_reason"):
                            return "commit", None, None
        return _sse_preflight_decision(buffered, end_of_stream=end_of_stream)

    def usage_capture(self, request_body: bytes, upstream_path: str) -> Any:
        from local_proxy.core import UsageCapture

        return UsageCapture(request_body, upstream_path)

    def transform_stream(
        self,
        first_chunk: bytes,
        stream: AsyncIterator[bytes],
        *,
        request_body: bytes,
        model: str,
    ) -> AsyncIterator[bytes]:
        converted = self._safe_transform_stream(first_chunk, stream, request_body=request_body, model=model)
        return self._message_phases(converted, model=model)

    async def _safe_transform_stream(
        self, first_chunk: bytes, stream: AsyncIterator[bytes], *, request_body: bytes, model: str,
    ) -> AsyncIterator[bytes]:
        response_id = "resp_dsml_" + uuid.uuid4().hex
        sequence = 0

        async def observed() -> AsyncIterator[bytes]:
            nonlocal response_id, sequence
            async for event in _sse_events(first_chunk, stream):
                _, _, root = _event_parts(event)
                if isinstance(root, dict):
                    response = root.get("response")
                    if isinstance(response, dict) and isinstance(response.get("id"), str):
                        response_id = response["id"]
                    if type(root.get("sequence_number")) is int:
                        sequence = max(sequence, root["sequence_number"])
                yield event

        source = observed()
        converted = self._transform_stream(b"", source, request_body=request_body, model=model)
        try:
            async for chunk in converted:
                _, _, root = _event_parts(chunk)
                if isinstance(root, dict):
                    response = root.get("response")
                    if isinstance(response, dict) and isinstance(response.get("id"), str):
                        response_id = response["id"]
                    if type(root.get("sequence_number")) is int:
                        sequence = max(sequence, root["sequence_number"])
                yield chunk
        except DeepSeekProtocolError as exc:
            yield self._protocol_error(str(exc), response_id=response_id, sequence=sequence + 1, model=model)
            yield b"data: [DONE]\n\n"
        finally:
            await converted.aclose()
            await source.aclose()

    async def _message_phases(self, stream: AsyncIterator[bytes], *, model: str) -> AsyncIterator[bytes]:
        """Hold bridge message events until tool intent or a terminal is known.

        Reasoning keeps streaming. Renumber only bridge events, since buffering
        changes their order. Ordinary GPT bytes remain untouched.
        """
        bridge = model.casefold().startswith("deepseek")
        pending: list[dict[str, Any]] = []
        buffered_bytes = 0
        sequence = 0
        phases: dict[str, str] = {}
        response_id = "resp_dsml_" + uuid.uuid4().hex

        def emit(root: dict[str, Any], phase: str | None = None) -> bytes:
            nonlocal sequence
            item = root.get("item")
            if isinstance(item, dict) and item.get("type") == "message" and phase:
                item["phase"] = phase
                if isinstance(item.get("id"), str):
                    phases[item["id"]] = phase
            response = root.get("response")
            if isinstance(response, dict) and isinstance(response.get("output"), list):
                for item in response["output"]:
                    if isinstance(item, dict) and item.get("type") == "message":
                        item["phase"] = phases.get(item.get("id"), phase or "final_answer")
            sequence += 1
            root["sequence_number"] = sequence
            return _sse_event(root)

        try:
            async for event in stream:
                event_name, payload, root = _event_parts(event)
                if not isinstance(root, dict):
                    if payload == b"[DONE]" and pending:
                        for buffered in pending:
                            yield emit(buffered, "final_answer")
                        pending.clear()
                    yield event
                    continue
                response = root.get("response")
                if isinstance(response, dict) and isinstance(response.get("id"), str):
                    response_id = response["id"]
                response_model = response.get("model", "") if isinstance(response, dict) else ""
                bridge = (bridge or str(response_model).casefold().startswith("deepseek")
                          or isinstance(response, dict)
                          and str(response.get("id", "")).startswith(("resp_chat_", "resp_dsml_"))
                          or _is_bridge_reasoning(root.get("item")))
                if not bridge:
                    raw_sequence = root.get("sequence_number")
                    if type(raw_sequence) is int:
                        sequence = max(sequence, raw_sequence)
                    yield event
                    continue
                kind = root.get("type") or event_name
                item = root.get("item")
                is_tool = isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"}
                terminal = kind in {"response.completed", "response.failed", "response.incomplete", "error"}
                if is_tool or terminal:
                    phase = "commentary" if is_tool or kind != "response.completed" else "final_answer"
                    for buffered in pending:
                        yield emit(buffered, phase)
                    pending.clear()
                    buffered_bytes = 0
                    yield emit(root, phase)
                elif (
                    isinstance(item, dict) and item.get("type") == "message"
                    or str(kind).startswith(("response.output_text.", "response.content_part.", "response.refusal."))
                    or pending and not str(kind).startswith("response.reasoning")
                ):
                    pending.append(root)
                    buffered_bytes += len(event)
                    if buffered_bytes > DSML_BUFFER_LIMIT * 8:
                        # Keep a bounded queue; continuing with a guessed phase
                        # would recreate the display corruption.
                        raise DeepSeekProtocolError("DeepSeek 消息阶段缓冲超过大小限制")
                else:
                    yield emit(root)
            for buffered in pending:
                yield emit(buffered, "commentary")
        except DeepSeekProtocolError as exc:
            # This wrapper may fail after the inner converter has yielded.
            yield self._protocol_error(str(exc), response_id=response_id, sequence=sequence + 1, model=model)
            yield b"data: [DONE]\n\n"
        finally:
            await stream.aclose()

    async def _transform_stream(
        self,
        first_chunk: bytes,
        stream: AsyncIterator[bytes],
        *,
        request_body: bytes,
        model: str,
    ) -> AsyncIterator[bytes]:
        pending: list[bytes] = []
        candidate_text = ""
        candidate_text_bytes = 0
        mode = "unknown"
        allowed_tools = ResponsesTools(request_body)
        chat_translator: _ChatResponseTranslator | None = None
        emitted_text_chars = 0
        response_id_hint: str | None = None
        max_sequence_number = 0
        max_output_index = -1
        active_message: dict[str, Any] | None = None
        dsml_after_passthrough = False

        async for event in _sse_events(first_chunk, stream):
            event_name, payload, root = _event_parts(event)
            if payload == b"[DONE]":
                if mode == "dsml":
                    raise DeepSeekProtocolError("DSML 工具调用未完整闭合")
                if mode == "chat" and chat_translator is not None:
                    for output in chat_translator.finish():
                        yield output
                elif mode == "unknown":
                    for raw in pending:
                        yield raw
                    yield event
                elif mode in {"passthrough", "passthrough_final"}:
                    for raw in pending:
                        yield raw
                    pending.clear()
                    yield event
                return

            if mode == "passthrough_final":
                yield event
                continue

            if not isinstance(root, dict):
                raw_text = payload.decode("utf-8", errors="replace") if payload else ""
                if has_any_dsml_prefix(raw_text):
                    mode = "dsml"
                    candidate_text += raw_text
                    pending.append(event)
                    if len(candidate_text.encode("utf-8")) > DSML_BUFFER_LIMIT:
                        raise DeepSeekProtocolError("DSML 响应超过缓冲大小限制")
                    continue
                if mode == "unknown":
                    pending.append(event)
                    continue
                yield event
                continue

            event_type = str(root.get("type") or event_name)
            response = root.get("response") if isinstance(root.get("response"), dict) else {}
            raw_response_id = response.get("id") or root.get("id")
            if isinstance(raw_response_id, str) and raw_response_id:
                response_id_hint = raw_response_id
            raw_sequence = root.get("sequence_number")
            if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool):
                max_sequence_number = max(max_sequence_number, raw_sequence)
            raw_output_index = root.get("output_index")
            if isinstance(raw_output_index, int) and not isinstance(raw_output_index, bool):
                max_output_index = max(max_output_index, raw_output_index)
            if event_type == "response.output_item.added":
                item = root.get("item") if isinstance(root.get("item"), dict) else {}
                if item.get("type") == "message" and isinstance(item.get("id"), str):
                    active_message = {
                        "id": item["id"],
                        "role": str(item.get("role") or "assistant"),
                        "output_index": (
                            raw_output_index
                            if isinstance(raw_output_index, int)
                            and not isinstance(raw_output_index, bool)
                            else max_output_index
                        ),
                        "content_index": 0,
                    }
            elif event_type == "response.content_part.added" and active_message is not None:
                if root.get("item_id") == active_message["id"]:
                    raw_content_index = root.get("content_index")
                    if isinstance(raw_content_index, int) and not isinstance(raw_content_index, bool):
                        active_message["content_index"] = raw_content_index

            if mode == "passthrough":
                response_text = _text_from_response_event(root)
                if not response_text:
                    for raw in pending:
                        yield raw
                    pending.clear()
                    emitted_text_chars = len(candidate_text)
                    yield event
                    continue

                candidate_text += response_text
                candidate_text_bytes += len(response_text.encode("utf-8"))
                probing = bool(pending)
                if probing:
                    pending.append(event)
                inspect_candidate = probing or "<" in response_text
                if inspect_candidate and has_any_dsml_prefix(candidate_text):
                    mode = "dsml"
                    dsml_after_passthrough = True
                    if not probing:
                        pending.append(event)
                    if len(candidate_text.encode("utf-8")) > DSML_BUFFER_LIMIT:
                        raise DeepSeekProtocolError("DSML 响应超过缓冲大小限制")
                    if has_complete_dsml_block(candidate_text):
                        async for output in self._emit_dsml(
                            candidate_text,
                            pending,
                            allowed_tools,
                            model,
                            response_id_hint=response_id_hint,
                            sequence_number_hint=max_sequence_number,
                            forwarded_message=active_message,
                            emitted_prefix_chars=emitted_text_chars,
                            next_output_index=max_output_index + 1,
                        ):
                            yield output
                        return
                    continue
                if inspect_candidate and _has_partial_dsml_start(candidate_text):
                    if not probing:
                        pending.append(event)
                    continue
                if probing:
                    for raw in pending:
                        yield raw
                    pending.clear()
                else:
                    yield event
                emitted_text_chars = len(candidate_text)
                if candidate_text_bytes > DSML_BUFFER_LIMIT:
                    candidate_text = ""
                    candidate_text_bytes = 0
                    emitted_text_chars = 0
                    mode = "passthrough_final"
                continue

            if "choices" in root:
                text = _text_from_chat_event(root)
                if text:
                    candidate_text += text
                if has_any_dsml_prefix(candidate_text):
                    mode = "dsml"
                    pending.append(event)
                    if len(candidate_text.encode("utf-8")) > DSML_BUFFER_LIMIT:
                        raise DeepSeekProtocolError("DSML 响应超过缓冲大小限制")
                    if has_complete_dsml_block(candidate_text):
                        async for output in self._emit_dsml(
                            candidate_text,
                            pending,
                            allowed_tools,
                            model,
                            response_id_hint=chat_translator.response_id if chat_translator else None,
                            sequence_number_hint=chat_translator.sequence if chat_translator else 0,
                        ):
                            yield output
                        return
                    continue
                if mode == "unknown":
                    mode = "chat"
                    chat_translator = _ChatResponseTranslator(default_model=model, tools=allowed_tools)
                    for output in chat_translator.start_events():
                        yield output
                    for buffered_event in pending:
                        _, _, buffered_root = _event_parts(buffered_event)
                        if isinstance(buffered_root, dict):
                            for output in chat_translator.process(buffered_root):
                                yield output
                    pending.clear()
                if mode == "chat" and chat_translator is not None:
                    for output in chat_translator.process(root):
                        yield output
                continue

            response_text = _text_from_response_event(root)
            if response_text:
                candidate_text += response_text
                candidate_text_bytes += len(response_text.encode("utf-8"))
            if has_any_dsml_prefix(candidate_text):
                mode = "dsml"
                pending.append(event)
                if len(candidate_text.encode("utf-8")) > DSML_BUFFER_LIMIT:
                    raise DeepSeekProtocolError("DSML 响应超过缓冲大小限制")
                if has_complete_dsml_block(candidate_text):
                    async for output in self._emit_dsml(
                        candidate_text,
                        pending,
                        allowed_tools,
                        model,
                        response_id_hint=response_id_hint,
                        sequence_number_hint=max_sequence_number,
                        forwarded_message=(
                            active_message if dsml_after_passthrough else None
                        ),
                        emitted_prefix_chars=(
                            emitted_text_chars if dsml_after_passthrough else 0
                        ),
                        next_output_index=(
                            max_output_index + 1 if dsml_after_passthrough else None
                        ),
                    ):
                        yield output
                    return
                continue

            if mode == "unknown":
                if response_text or event_type in {
                    "response.output_item.added",
                    "response.function_call_arguments.delta",
                    "response.completed",
                    "response.failed",
                    "error",
                }:
                    mode = "passthrough"
                    for raw in pending:
                        yield raw
                    pending.clear()
                    yield event
                    emitted_text_chars = len(candidate_text)
                else:
                    pending.append(event)
            else:
                yield event

        if mode == "dsml":
            raise DeepSeekProtocolError("DSML 工具调用未完整闭合")
        elif mode == "chat" and chat_translator is not None:
            for output in chat_translator.finish():
                yield output
        else:
            for raw in pending:
                yield raw

    async def _emit_dsml(
        self,
        text: str,
        pending: list[bytes],
        allowed_tools: ResponsesTools,
        model: str,
        *,
        response_id_hint: str | None = None,
        sequence_number_hint: int = 0,
        forwarded_message: Mapping[str, Any] | None = None,
        emitted_prefix_chars: int = 0,
        next_output_index: int | None = None,
    ) -> AsyncIterator[bytes]:
        calls = parse_dsml_tool_calls(text)
        tool_items = [allowed_tools.call_item(call) for call in calls]

        response_id = response_id_hint or "resp_dsml_" + uuid.uuid4().hex
        sequence_number = max(0, int(sequence_number_hint))
        for raw in pending:
            event_name, payload, root = _event_parts(raw)
            if not isinstance(root, dict):
                continue
            event_type = str(root.get("type") or event_name)
            response = root.get("response") if isinstance(root.get("response"), dict) else {}
            if isinstance(response.get("id"), str) and response["id"]:
                response_id = response["id"]
            raw_sequence = root.get("sequence_number")
            if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool):
                sequence_number = max(sequence_number, raw_sequence)
            if event_type in {"response.created", "response.in_progress"}:
                yield raw
            if event_type.startswith("response.reasoning_") and not has_any_dsml_prefix(str(root.get("delta") or "")):
                yield raw

        def emit(root: Mapping[str, Any]) -> bytes:
            nonlocal sequence_number
            sequence_number += 1
            payload = dict(root)
            payload.setdefault("sequence_number", sequence_number)
            return _sse_event(payload)

        start = find_dsml_start(text)
        prefix = text[:start]
        output_items: list[dict[str, Any]] = []
        if forwarded_message is not None:
            message_id = str(
                forwarded_message.get("id")
                or _stable_id("msg_dsml", 0, "message", prefix)
            )
            message_output_index = max(
                0,
                int(forwarded_message.get("output_index") or 0),
            )
            content_index = max(
                0,
                int(forwarded_message.get("content_index") or 0),
            )
            role = str(forwarded_message.get("role") or "assistant")
            remaining_prefix = prefix[max(0, int(emitted_prefix_chars)):]
            if remaining_prefix:
                yield emit(
                    {
                        "type": "response.output_text.delta",
                        "item_id": message_id,
                        "output_index": message_output_index,
                        "content_index": content_index,
                        "delta": remaining_prefix,
                    }
                )
            yield emit(
                {
                    "type": "response.output_text.done",
                    "item_id": message_id,
                    "output_index": message_output_index,
                    "content_index": content_index,
                    "text": prefix,
                }
            )
            yield emit(
                {
                    "type": "response.content_part.done",
                    "item_id": message_id,
                    "output_index": message_output_index,
                    "content_index": content_index,
                    "part": {
                        "type": "output_text",
                        "text": prefix,
                        "annotations": [],
                    },
                }
            )
            completed_message = {
                "id": message_id,
                "type": "message",
                "role": role,
                "phase": "commentary",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": prefix,
                        "annotations": [],
                    }
                ],
            }
            output_items.append(completed_message)
            yield emit(
                {
                    "type": "response.output_item.done",
                    "output_index": message_output_index,
                    "item": completed_message,
                }
            )
        elif prefix:
            message_id = _stable_id("msg_dsml", 0, "message", prefix)
            yield emit({"type": "response.output_item.added", "item": {"id": message_id, "type": "message", "role": "assistant", "phase": "commentary", "status": "in_progress", "content": []}, "output_index": 0})
            yield emit({"type": "response.content_part.added", "item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
            yield emit({"type": "response.output_text.delta", "item_id": message_id, "output_index": 0, "content_index": 0, "delta": prefix})
            yield emit({"type": "response.output_text.done", "item_id": message_id, "output_index": 0, "content_index": 0, "text": prefix})
            yield emit({"type": "response.content_part.done", "item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": prefix, "annotations": []}})
            completed_message = {"id": message_id, "type": "message", "role": "assistant", "phase": "commentary", "status": "completed", "content": [{"type": "output_text", "text": prefix, "annotations": []}]}
            output_items.append(completed_message)
            yield emit({"type": "response.output_item.done", "output_index": 0, "item": completed_message})

        output_offset = (
            max(0, int(next_output_index))
            if next_output_index is not None
            else 1 if prefix else 0
        )
        for index, completed_item in enumerate(tool_items):
            output_index = output_offset + index
            field = tool_argument_field(completed_item)
            event_prefix = tool_event_prefix(completed_item)
            arguments = completed_item[field]
            item = {**completed_item, "status": "in_progress", field: ""}
            yield emit({"type": "response.output_item.added", "output_index": output_index, "item": item})
            for offset in range(0, len(arguments), DSML_ARGUMENT_CHUNK_SIZE):
                yield emit({"type": event_prefix + ".delta", "output_index": output_index, "item_id": item["id"], "delta": arguments[offset:offset + DSML_ARGUMENT_CHUNK_SIZE]})
            yield emit({"type": event_prefix + ".done", "output_index": output_index, "item_id": item["id"], field: arguments})
            output_items.append(completed_item)
            yield emit({"type": "response.output_item.done", "output_index": output_index, "item": completed_item})
        yield emit({"type": "response.completed", "response": {"id": response_id, "object": "response", "status": "completed", "model": model, "output": output_items}})
        yield b"data: [DONE]\n\n"

    @staticmethod
    def _protocol_error(message: str, *, response_id: str, sequence: int, model: str) -> bytes:
        return _sse_event({"type": "response.failed", "sequence_number": sequence, "response": {
            "id": response_id, "object": "response", "status": "failed", "model": model,
            "output": [], "error": {"type": "deepseek_protocol_error",
                "code": "deepseek_dsml_parse_error", "message": message},
        }})

    def transform_body(
        self,
        first_chunk: bytes,
        stream: AsyncIterator[bytes],
        *,
        request_body: bytes,
        model: str,
    ) -> AsyncIterator[bytes]:
        return self._transform_body(first_chunk, stream, request_body=request_body, model=model)

    async def _transform_body(
        self,
        first_chunk: bytes,
        stream: AsyncIterator[bytes],
        *,
        request_body: bytes,
        model: str,
    ) -> AsyncIterator[bytes]:
        body = bytearray(first_chunk)
        async for chunk in stream:
            body.extend(chunk)
        try:
            root = json.loads(bytes(body))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raw_body = bytes(body)
            raw_text = raw_body.decode("utf-8", errors="replace")
            if not has_any_dsml_prefix(raw_text) or not has_complete_dsml_block(raw_text):
                yield raw_body
                return
            try:
                yield _converted_response_body(
                    {},
                    raw_text,
                    request_body=request_body,
                    model=model,
                )
            except DeepSeekProtocolError as exc:
                yield self._json_protocol_error(str(exc))
            return
        if not isinstance(root, dict):
            yield bytes(body)
            return
        content = next(
            (
                candidate
                for candidate in _response_text_candidates(root)
                if has_any_dsml_prefix(candidate)
            ),
            None,
        )
        if content is not None:
            try:
                yield _converted_response_body(
                    root,
                    content,
                    request_body=request_body,
                    model=model,
                )
            except DeepSeekProtocolError as exc:
                yield self._json_protocol_error(str(exc))
            return
        choices = root.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                translator = _ChatResponseTranslator(default_model=model, tools=ResponsesTools(request_body))
                translator.response_id, translator.model = _response_identity(root, model)
                delta = dict(message)
                if isinstance(delta.get("tool_calls"), list):
                    delta["tool_calls"] = [
                        {**call, "index": index} for index, call in enumerate(delta["tool_calls"])
                        if isinstance(call, dict)
                    ]
                try:
                    events = translator.process({"choices": [{"delta": delta, "finish_reason": "stop"}]})
                except DeepSeekProtocolError as exc:
                    yield self._json_protocol_error(str(exc))
                    return
                for event in events:
                    _, _, value = _event_parts(event)
                    if isinstance(value, dict) and value.get("type") == "response.completed":
                        response = value["response"]
                        if isinstance(root.get("usage"), dict):
                            usage = root["usage"]
                            response["usage"] = {
                                "input_tokens": usage.get("prompt_tokens", 0),
                                "output_tokens": usage.get("completion_tokens", 0),
                                "total_tokens": usage.get("total_tokens", 0),
                            }
                        yield json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        return
        yield bytes(body)

    @staticmethod
    def _json_protocol_error(message: str) -> bytes:
        return json.dumps(
            {
                "error": {
                    "type": "deepseek_protocol_error",
                    "code": "deepseek_dsml_parse_error",
                    "message": message,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")


def is_deepseek_provider(provider: Any) -> bool:
    from urllib.parse import urlsplit

    host = (urlsplit(str(getattr(provider, "base_url", ""))).hostname or "").casefold()
    name = str(getattr(provider, "name", "")).casefold()
    return host == "api.deepseek.com" or "deepseek" in name
