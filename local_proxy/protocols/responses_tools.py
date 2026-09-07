"""Read the Responses tool contract before converting third-party calls."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class ToolProtocolError(ValueError):
    """A converted tool call cannot be represented without guessing."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str
    namespace: str = ""
    codex_javascript: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name


class ResponsesTools:
    def __init__(self, payload: bytes) -> None:
        self.declared = False
        self.specs: dict[str, ToolSpec] = {}
        try:
            root = json.loads(payload)
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(root, dict):
            return
        self._collect(root.get("tools"))
        items = root.get("input")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("type") == "additional_tools":
                    self._collect(item.get("tools"))

    def _collect(self, tools: Any, namespace: str = "") -> None:
        if not isinstance(tools, list):
            return
        self.declared = True
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            kind = tool.get("type", "function")
            if kind == "namespace":
                name = tool.get("name")
                if isinstance(name, str) and name:
                    self._collect(tool.get("tools"), f"{namespace}.{name}" if namespace else name)
                continue
            if kind not in {"function", "custom"}:
                continue
            definition = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            name = definition.get("name")
            if isinstance(name, str) and name:
                spec = ToolSpec(name, kind, namespace, is_codex_javascript_tool(tool, namespace))
                self.specs[spec.qualified_name] = spec

    def resolve(self, name: str) -> ToolSpec:
        if not isinstance(name, str) or not name.strip() or len(name) > 240:
            raise ToolProtocolError("响应缺少有效工具名称")
        if name in self.specs:
            return self.specs[name]
        matches = [spec for spec in self.specs.values() if spec.name == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ToolProtocolError("工具名称存在多个命名空间，必须指定完整名称：" + name)
        if self.declared:
            raise ToolProtocolError("响应调用了请求中未声明的工具：" + name)
        return ToolSpec(name, "function")

    def call_item(self, call: Mapping[str, str]) -> dict[str, Any]:
        spec = self.resolve(call["name"])
        custom = spec.kind == "custom"
        arguments = call["arguments"]
        try:
            value = json.loads(arguments)
        except ValueError as exc:
            raise ToolProtocolError("工具参数不是完整 JSON：" + spec.qualified_name) from exc
        if custom:
            if spec.codex_javascript:
                arguments = codex_exec_input(value)
            elif isinstance(value, dict) and set(value) == {"input"} and isinstance(value["input"], str):
                arguments = value["input"]
            elif isinstance(value, str):
                arguments = value
            else:
                raise ToolProtocolError("自定义工具需要字符串或仅含 input 字符串的参数：" + spec.qualified_name)
        elif not isinstance(value, dict):
            raise ToolProtocolError("普通函数工具需要 JSON 对象参数：" + spec.qualified_name)
        item_id = call["item_id"]
        if custom and item_id.startswith(("fc_dsml_", "fc_chat_")):
            item_id = "ctc_" + item_id[3:]
        item: dict[str, Any] = {
            "id": item_id,
            "type": "custom_tool_call" if custom else "function_call",
            "status": "completed",
            "call_id": call["call_id"],
            "name": spec.name,
            "input" if custom else "arguments": arguments,
        }
        if spec.namespace:
            item["namespace"] = spec.namespace
        return item


def is_codex_javascript_tool(tool: Mapping[str, Any], namespace: str) -> bool:
    description = tool.get("description", "")
    return (
        tool.get("type") == "custom" and tool.get("name") == "exec"
        and namespace == "functions" and isinstance(description, str)
        and "JavaScript" in description and "tools.exec_command" in description
    )


_SHELL_START = re.compile(
    r"^\s*(?:git|rg|pwd|ls|cat|cd|echo|python(?:3|\.exe)?|node|npm|npx|"
    r"Get-Location|Get-Content|Get-ChildItem|Set-Location|Write-Output|"
    r"powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?)\s+[^\s(]",
    re.IGNORECASE,
)


def codex_exec_input(value: Any) -> str:
    """Adapt explicit shell-shaped calls; the proxy never executes the result."""
    if isinstance(value, str):
        value = {"input": value}
    if not isinstance(value, dict):
        raise ToolProtocolError("functions.exec 需要 JavaScript 字符串或 input 参数")
    if set(value) == {"input"} and isinstance(value["input"], str):
        if not _SHELL_START.match(value["input"]):
            return value["input"]
    allowed = {"input", "cmd", "workdir", "yield_time_ms", "max_output_tokens", "shell", "login", "tty"}
    if set(value) - allowed or ("input" in value) == ("cmd" in value):
        raise ToolProtocolError("functions.exec 的 shell 调用包含未知或冲突参数")
    command = value.get("cmd", value.get("input"))
    if not isinstance(command, str) or not command.strip():
        raise ToolProtocolError("functions.exec 的 shell 命令必须是非空字符串")
    if "input" in value and not _SHELL_START.match(command):
        raise ToolProtocolError("functions.exec 附带 shell 参数，但 input 不是可确认的 shell 命令；JavaScript 请仅传 input")
    for key in ("workdir", "shell"):
        if key in value and not isinstance(value[key], str):
            raise ToolProtocolError("functions.exec 的 " + key + " 必须是字符串")
    for key in ("yield_time_ms", "max_output_tokens"):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise ToolProtocolError("functions.exec 的 " + key + " 必须是非负整数")
    for key in ("login", "tty"):
        if key in value and not isinstance(value[key], bool):
            raise ToolProtocolError("functions.exec 的 " + key + " 必须是布尔值")
    arguments = {key: val for key, val in value.items() if key != "input"}
    arguments["cmd"] = command
    return "const result = await tools.exec_command(" + json.dumps(arguments, ensure_ascii=True) + "); text(result);"


_EXEC_GUIDANCE = (
    "\nDeepSeek compatibility: functions.exec input is JavaScript, never a shell command. "
    "For shell commands use: const result = await tools.exec_command({cmd: \"git status\", "
    "workdir: \".\", yield_time_ms: 30000, max_output_tokens: 12000}); text(result); "
    "When emitting DSML, pass only the input string; put shell options inside tools.exec_command."
)


def prepare_deepseek_tools(payload: bytes, *, model: str) -> bytes | None:
    if not model.casefold().startswith("deepseek"):
        return None
    try:
        root = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(root, dict):
        return None
    changed = False

    def visit(tools: Any, namespace: str = "") -> None:
        nonlocal changed
        if not isinstance(tools, list):
            return
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "namespace":
                name = tool.get("name")
                if isinstance(name, str):
                    visit(tool.get("tools"), f"{namespace}.{name}" if namespace else name)
            elif is_codex_javascript_tool(tool, namespace) and _EXEC_GUIDANCE not in tool["description"]:
                tool["description"] += _EXEC_GUIDANCE
                changed = True

    visit(root.get("tools"))
    if isinstance(root.get("input"), list):
        for item in root["input"]:
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                visit(item.get("tools"))
    return json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if changed else None


def tool_argument_field(item: Mapping[str, Any]) -> str:
    return "input" if item["type"] == "custom_tool_call" else "arguments"


def tool_event_prefix(item: Mapping[str, Any]) -> str:
    return "response.custom_tool_call_input" if item["type"] == "custom_tool_call" else "response.function_call_arguments"
