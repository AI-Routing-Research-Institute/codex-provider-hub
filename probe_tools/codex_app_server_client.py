from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO


class AppServerProtocolError(RuntimeError):
    """Codex app-server 未按预期协议响应。"""


class AppServerTimeoutError(TimeoutError):
    """Codex app-server 请求超过截止时间。"""


@dataclass(frozen=True)
class AppServerTurnResult:
    output_text: str
    turn_status: str
    error_text: str
    diagnostics: str
    timed_out: bool
    http_status_code: int | None
    user_agent: str

    @property
    def returncode(self) -> int:
        return 0 if self.turn_status == "completed" and not self.timed_out else 1


@dataclass(frozen=True)
class _StreamFailure:
    error: Exception


@dataclass(frozen=True)
class _StreamClosed:
    stream_name: str


PopenFactory = Callable[..., subprocess.Popen[str]]
ProcessTerminator = Callable[[subprocess.Popen[str]], None]


def _extract_http_status(value: Any) -> int | None:
    if isinstance(value, dict):
        direct = value.get("httpStatusCode")
        if isinstance(direct, int):
            return direct
        for child in value.values():
            found = _extract_http_status(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _extract_http_status(child)
            if found is not None:
                return found
    return None


def _extract_error_message(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    message = value.get("message")
    return message.strip() if isinstance(message, str) else ""


def _append_unique(values: list[str], value: str) -> None:
    normalized = " ".join(value.split())
    if normalized and normalized not in values:
        values.append(normalized)


def _default_terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            process_group_id = os.getpgid(process.pid)
        except OSError:
            process_group_id = None
        if process_group_id is not None:
            try:
                os.killpg(process_group_id, signal.SIGTERM)
                process.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                if process.poll() is None:
                    try:
                        os.killpg(
                            process_group_id,
                            getattr(signal, "SIGKILL", 9),
                        )
                    except OSError:
                        process.kill()
            return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            if process.poll() is None:
                process.kill()


class CodexAppServerClient:
    """通过 stdio JSON-RPC 驱动一个隔离的 Codex app-server。"""

    def __init__(
        self,
        *,
        codex_bin: str,
        env: dict[str, str],
        workspace: Path,
        sandbox: str,
        model: str,
        reasoning_effort: str,
        model_provider: str | None,
        client_version: str | None = None,
        popen_factory: PopenFactory = subprocess.Popen,
        process_terminator: ProcessTerminator = _default_terminate_process_tree,
    ) -> None:
        self.codex_bin = codex_bin
        self.env = dict(env)
        self.workspace = workspace
        self.sandbox = sandbox
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.model_provider = model_provider
        self.client_version = client_version
        self._popen_factory = popen_factory
        self._process_terminator = process_terminator
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | _StreamFailure | _StreamClosed] = queue.Queue()
        self._backlog: deque[dict[str, Any]] = deque()
        self._stderr_lines: list[str] = []
        self._next_request_id = 1
        self._write_lock = threading.Lock()
        self._user_agent = ""
        self._active_thread_id: str | None = None
        self._active_turn_id: str | None = None

    def __enter__(self) -> "CodexAppServerClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _resolve_client_version(self) -> str:
        if self.client_version:
            return self.client_version
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [self.codex_bin, "--version"],
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AppServerProtocolError(f"无法读取 Codex 版本：{exc}") from exc
        combined = " ".join((completed.stdout, completed.stderr)).strip()
        matched = re.search(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b", combined)
        if completed.returncode != 0 or not matched:
            raise AppServerProtocolError(f"无法解析 Codex 版本：{combined or '无输出'}")
        self.client_version = matched.group(1)
        return self.client_version

    def _start(self, deadline: float) -> None:
        if self._process is not None:
            return
        version = self._resolve_client_version()
        platform_options: dict[str, Any]
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            platform_options = {"creationflags": creation_flags}
        else:
            platform_options = {"start_new_session": True}
        try:
            self._process = self._popen_factory(
                [self.codex_bin, "app-server", "--stdio"],
                env=self.env,
                cwd=str(self.workspace),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **platform_options,
            )
        except OSError as exc:
            raise AppServerProtocolError(f"无法启动 Codex app-server：{exc}") from exc
        if self._process.stdout is None or self._process.stderr is None or self._process.stdin is None:
            self.close()
            raise AppServerProtocolError("Codex app-server 标准输入输出管道不可用")

        threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            name="codex-app-server-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            name="codex-app-server-stderr",
            daemon=True,
        ).start()

        initialized = self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex_desktop",
                    "title": "Codex Desktop",
                    "version": version,
                },
                "capabilities": {"experimentalApi": True},
            },
            deadline,
        )
        user_agent = initialized.get("userAgent")
        self._user_agent = user_agent if isinstance(user_agent, str) else ""
        if "(codex_desktop;" not in self._user_agent:
            raise AppServerProtocolError(
                "app-server 未返回 codex_desktop 客户端标识"
                + (f"：{self._user_agent}" if self._user_agent else "")
            )
        self._notify("initialized")

    def _read_stdout(self, stream: TextIO) -> None:
        try:
            while True:
                line = stream.readline()
                if line == "":
                    self._messages.put(_StreamClosed("stdout"))
                    return
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._messages.put(
                        _StreamFailure(AppServerProtocolError(f"app-server 返回无效 JSON：{exc}"))
                    )
                    return
                if not isinstance(message, dict):
                    self._messages.put(
                        _StreamFailure(AppServerProtocolError("app-server 返回的 JSON 不是对象"))
                    )
                    return
                self._messages.put(message)
        except Exception as exc:
            self._messages.put(_StreamFailure(exc))

    def _read_stderr(self, stream: TextIO) -> None:
        try:
            while True:
                line = stream.readline()
                if line == "":
                    return
                self._stderr_lines.append(line.rstrip())
        except Exception as exc:
            self._stderr_lines.append(f"stderr 读取失败：{exc}")

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise AppServerProtocolError("app-server 尚未启动")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._write_lock:
                process.stdin.write(payload)
                process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise AppServerProtocolError(f"写入 app-server 失败：{exc}") from exc

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def _request(self, method: str, params: dict[str, Any], deadline: float) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._write({"id": request_id, "method": method, "params": params})
        while True:
            message = self._next_message(deadline, include_backlog=False)
            if message.get("id") != request_id:
                self._backlog.append(message)
                continue
            error = message.get("error")
            if error is not None:
                raise AppServerProtocolError(
                    f"app-server {method} 失败：{json.dumps(error, ensure_ascii=False)}"
                )
            result = message.get("result")
            if not isinstance(result, dict):
                raise AppServerProtocolError(f"app-server {method} 缺少 result 对象")
            return result

    def _next_message(self, deadline: float, *, include_backlog: bool = True) -> dict[str, Any]:
        if include_backlog and self._backlog:
            return self._backlog.popleft()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppServerTimeoutError("等待 app-server 响应超时")
        try:
            item = self._messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise AppServerTimeoutError("等待 app-server 响应超时") from exc
        if isinstance(item, _StreamFailure):
            raise AppServerProtocolError(str(item.error)) from item.error
        if isinstance(item, _StreamClosed):
            diagnostics = self.diagnostics
            suffix = f"：{diagnostics}" if diagnostics else ""
            raise AppServerProtocolError(f"app-server {item.stream_name} 已关闭{suffix}")
        return item

    @property
    def diagnostics(self) -> str:
        return "\n".join(line for line in self._stderr_lines if line).strip()

    def run_turn(self, prompt: str, *, timeout: float) -> AppServerTurnResult:
        deadline = time.monotonic() + timeout
        errors: list[str] = []
        http_status_code: int | None = None
        output_text = ""
        try:
            self._start(deadline)
            thread_params: dict[str, Any] = {
                "model": self.model,
                "cwd": str(self.workspace),
                "approvalPolicy": "never",
                "sandbox": self.sandbox,
                "ephemeral": True,
                "historyMode": "legacy",
                "runtimeWorkspaceRoots": [str(self.workspace)],
                "serviceTier": "default",
                "sessionStartSource": "startup",
                "threadSource": "vscode",
            }
            if self.model_provider:
                thread_params["modelProvider"] = self.model_provider
            thread_result = self._request("thread/start", thread_params, deadline)
            thread = thread_result.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(thread_id, str) or not thread_id:
                raise AppServerProtocolError("app-server thread/start 未返回 thread.id")
            self._active_thread_id = thread_id

            turn_result = self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "model": self.model,
                    "effort": self.reasoning_effort,
                    "summary": "none",
                    "cwd": str(self.workspace),
                    "serviceTier": "default",
                },
                deadline,
            )
            turn = turn_result.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise AppServerProtocolError("app-server turn/start 未返回 turn.id")
            self._active_turn_id = turn_id

            while True:
                message = self._next_message(deadline)
                method = message.get("method")
                params = message.get("params")
                if not isinstance(params, dict):
                    continue
                if method == "item/completed" and params.get("turnId") == turn_id:
                    item = params.get("item")
                    if isinstance(item, dict) and item.get("type") == "agentMessage":
                        text = item.get("text")
                        if isinstance(text, str):
                            output_text = text
                elif method == "error" and params.get("turnId") == turn_id:
                    error = params.get("error")
                    _append_unique(errors, _extract_error_message(error))
                    http_status_code = http_status_code or _extract_http_status(error)
                elif method == "turn/completed":
                    completed_turn = params.get("turn")
                    if not isinstance(completed_turn, dict) or completed_turn.get("id") != turn_id:
                        continue
                    turn_error = completed_turn.get("error")
                    _append_unique(errors, _extract_error_message(turn_error))
                    http_status_code = http_status_code or _extract_http_status(turn_error)
                    if not output_text:
                        items = completed_turn.get("items")
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, dict) and item.get("type") == "agentMessage":
                                    text = item.get("text")
                                    if isinstance(text, str):
                                        output_text = text
                    status = completed_turn.get("status")
                    turn_status = status if isinstance(status, str) else "failed"
                    self._active_turn_id = None
                    return AppServerTurnResult(
                        output_text=output_text,
                        turn_status=turn_status,
                        error_text="\n".join(errors),
                        diagnostics=self.diagnostics,
                        timed_out=False,
                        http_status_code=http_status_code,
                        user_agent=self._user_agent,
                    )
        except AppServerTimeoutError as exc:
            _append_unique(errors, str(exc))
            self.interrupt_active_turn()
            return AppServerTurnResult(
                output_text=output_text,
                turn_status="interrupted",
                error_text="\n".join(errors),
                diagnostics=self.diagnostics,
                timed_out=True,
                http_status_code=http_status_code,
                user_agent=self._user_agent,
            )

    def interrupt_active_turn(self) -> None:
        if not self._active_thread_id or not self._active_turn_id:
            return
        try:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._write(
                {
                    "id": request_id,
                    "method": "turn/interrupt",
                    "params": {
                        "threadId": self._active_thread_id,
                        "turnId": self._active_turn_id,
                    },
                }
            )
        except AppServerProtocolError:
            pass
        self._active_turn_id = None

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
        self._process_terminator(process)
