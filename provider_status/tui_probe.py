from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from provider_status.codex_diagnostics import (
    CodexDiagnostic,
    read_codex_diagnostic,
)


class TuiProtocolError(RuntimeError):
    """Codex TUI 未按预期启动或生成会话记录。"""


class TuiTimeoutError(TimeoutError):
    """Codex TUI 探测超过截止时间。"""


@dataclass(frozen=True)
class RolloutSnapshot:
    output_text: str = ""
    error_text: str = ""
    http_status_code: int | None = None
    originator: str = ""
    complete: bool = False


@dataclass(frozen=True)
class TuiTurnResult:
    output_text: str
    turn_status: str
    error_text: str
    diagnostics: str
    timed_out: bool
    http_status_code: int | None
    originator: str
    error_code: str | None = None

    @property
    def returncode(self) -> int:
        return 0 if self.turn_status == "completed" and not self.timed_out else 1


class PtyAdapter(Protocol):
    def open(self) -> tuple[int, int]: ...

    def set_window_size(self, fd: int, rows: int, columns: int) -> None: ...

    def set_nonblocking(self, fd: int) -> None: ...

    def wait_readable(self, fd: int, timeout: float) -> bool: ...

    def read(self, fd: int, size: int) -> bytes: ...

    def write(self, fd: int, value: bytes) -> int: ...

    def close(self, fd: int) -> None: ...


class _PosixPtyAdapter:
    def open(self) -> tuple[int, int]:
        import pty

        return pty.openpty()

    def set_window_size(self, fd: int, rows: int, columns: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def set_nonblocking(self, fd: int) -> None:
        os.set_blocking(fd, False)

    def wait_readable(self, fd: int, timeout: float) -> bool:
        import select

        readable, _, _ = select.select([fd], [], [], timeout)
        return bool(readable)

    def read(self, fd: int, size: int) -> bytes:
        return os.read(fd, size)

    def write(self, fd: int, value: bytes) -> int:
        return os.write(fd, value)

    def close(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass


ProcessFactory = Callable[..., subprocess.Popen[Any]]
ProcessTerminator = Callable[[subprocess.Popen[Any]], None]
RolloutScanner = Callable[[Path, str], RolloutSnapshot]
Clock = Callable[[], float]
DiagnosticReader = Callable[..., CodexDiagnostic | None]


def _default_terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        process_group_id = os.getpgid(process.pid)
    except OSError:
        process_group_id = None
    if process_group_id is None:
        process.terminate()
        return
    for signum, wait_seconds in (
        (signal.SIGINT, 2.0),
        (signal.SIGTERM, 2.0),
        (getattr(signal, "SIGKILL", signal.SIGTERM), 0.0),
    ):
        try:
            os.killpg(process_group_id, signum)
        except OSError:
            return
        if wait_seconds <= 0:
            return
        try:
            process.wait(timeout=wait_seconds)
            return
        except subprocess.TimeoutExpired:
            continue


class CodexTuiClient:
    """在真实 POSIX PTY 中驱动一次隔离的 Codex TUI turn。"""

    _TRUST_PROMPT_NORMALIZED = "doyoutrustthecontentsofthisdirectory"
    _DIAGNOSTIC_LIMIT = 32_768

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
        pty_adapter: PtyAdapter | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
        process_terminator: ProcessTerminator = _default_terminate_process_tree,
        rollout_scanner: RolloutScanner | None = None,
        base_url: str | None = None,
        diagnostic_reader: DiagnosticReader | None = None,
        clock: Clock = time.monotonic,
        platform_name: str = os.name,
    ) -> None:
        self.codex_bin = codex_bin
        self.env = dict(env)
        self.workspace = Path(workspace)
        self.sandbox = sandbox
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.model_provider = model_provider
        self._pty = pty_adapter or _PosixPtyAdapter()
        self._process_factory = process_factory
        self._process_terminator = process_terminator
        self._rollout_scanner = rollout_scanner or scan_rollouts
        self._base_url = base_url
        self._diagnostic_reader = diagnostic_reader or read_codex_diagnostic
        self._clock = clock
        self._platform_name = platform_name
        self._process: subprocess.Popen[Any] | None = None
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._diagnostics = bytearray()
        self._trust_confirmed = False
        self._diagnostic_first_seen_at: float | None = None
        self._diagnostic_kind: str | None = None
        self._diagnostic_last_read_at: float | None = None

    def __enter__(self) -> "CodexTuiClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _command(self, prompt: str) -> list[str]:
        return [
            self.codex_bin,
            "--no-alt-screen",
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "-m",
            self.model,
            "-s",
            self.sandbox,
            "-a",
            "never",
            "-C",
            str(self.workspace),
            prompt,
        ]

    def _start(self, prompt: str) -> Path:
        if self._platform_name != "posix":
            raise TuiProtocolError("Codex TUI 状态探测仅支持 POSIX PTY")
        codex_home_value = self.env.get("CODEX_HOME", "").strip()
        if not codex_home_value:
            raise TuiProtocolError("Codex TUI 状态探测缺少 CODEX_HOME")

        master_fd, slave_fd = self._pty.open()
        self._master_fd = master_fd
        self._slave_fd = slave_fd
        try:
            self._pty.set_window_size(slave_fd, 40, 120)
            self._pty.set_nonblocking(master_fd)
            child_env = dict(self.env)
            child_env.setdefault("TERM", "xterm-256color")
            self._process = self._process_factory(
                self._command(prompt),
                env=child_env,
                cwd=str(self.workspace),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            self.close()
            raise
        self._pty.close(slave_fd)
        self._slave_fd = None
        return Path(codex_home_value)

    def _append_diagnostics(self, value: bytes) -> None:
        self._diagnostics.extend(value)
        overflow = len(self._diagnostics) - self._DIAGNOSTIC_LIMIT
        if overflow > 0:
            del self._diagnostics[:overflow]

    def _read_available(self) -> None:
        if self._master_fd is None:
            return
        if not self._pty.wait_readable(self._master_fd, 0.1):
            return
        try:
            value = self._pty.read(self._master_fd, 8192)
        except BlockingIOError:
            return
        if not value:
            return
        self._append_diagnostics(value)
        visible = re.sub(
            r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))",
            "",
            self._diagnostic_text(),
        )
        normalized = re.sub(r"[^a-z0-9]+", "", visible.casefold())
        if (
            not self._trust_confirmed
            and self._TRUST_PROMPT_NORMALIZED in normalized
        ):
            self._pty.write(self._master_fd, b"\r")
            self._trust_confirmed = True

    def _diagnostic_text(self) -> str:
        return bytes(self._diagnostics).decode("utf-8", errors="replace")

    def run_turn(self, prompt: str, *, timeout: float) -> TuiTurnResult:
        deadline = self._clock() + timeout
        codex_home = self._start(prompt)
        try:
            while True:
                now = self._clock()
                if now >= deadline:
                    diagnostic = self._read_diagnostic(codex_home, now, force=True)
                    if diagnostic is not None:
                        return self._diagnostic_result(diagnostic)
                    return TuiTurnResult(
                        output_text="",
                        turn_status="failed",
                        error_text="等待 Codex TUI 响应超时",
                        diagnostics=self._diagnostic_text(),
                        timed_out=True,
                        http_status_code=None,
                        originator="",
                    )

                self._read_available()
                snapshot = self._rollout_scanner(codex_home, prompt)
                if snapshot.complete:
                    if not snapshot.output_text and not snapshot.error_text:
                        diagnostic = self._read_diagnostic(
                            codex_home,
                            now,
                            force=True,
                        )
                        if diagnostic is not None:
                            return self._diagnostic_result(diagnostic)
                    return TuiTurnResult(
                        output_text=snapshot.output_text,
                        turn_status="completed" if snapshot.output_text else "failed",
                        error_text=(
                            snapshot.error_text
                            or ("Codex TUI 完成但没有输出" if not snapshot.output_text else "")
                        ),
                        diagnostics=self._diagnostic_text(),
                        timed_out=False,
                        http_status_code=snapshot.http_status_code,
                        originator=snapshot.originator,
                        error_code=(
                            None
                            if snapshot.output_text or snapshot.error_text
                            else "invalid_output"
                        ),
                    )

                diagnostic = self._read_diagnostic(codex_home, now)
                if diagnostic is not None:
                    if not diagnostic.retryable:
                        return self._diagnostic_result(diagnostic)
                    if self._diagnostic_first_seen_at is None:
                        self._diagnostic_first_seen_at = now
                        self._diagnostic_kind = diagnostic.kind
                    elif diagnostic.kind != self._diagnostic_kind:
                        self._diagnostic_first_seen_at = now
                        self._diagnostic_kind = diagnostic.kind
                    if (
                        diagnostic.occurrences >= 2
                        and self._diagnostic_first_seen_at is not None
                        and now - self._diagnostic_first_seen_at >= 12.0
                    ):
                        return self._diagnostic_result(diagnostic)

                if self._process is not None and self._process.poll() is not None:
                    return TuiTurnResult(
                        output_text="",
                        turn_status="failed",
                        error_text=f"Codex TUI 提前退出，rc={self._process.returncode}",
                        diagnostics=self._diagnostic_text(),
                        timed_out=False,
                        http_status_code=None,
                        originator=snapshot.originator,
                    )
        finally:
            self.close()

    def _read_diagnostic(
        self,
        codex_home: Path,
        now: float,
        *,
        force: bool = False,
    ) -> CodexDiagnostic | None:
        if self._base_url is None:
            return None
        if (
            not force
            and self._diagnostic_last_read_at is not None
            and now - self._diagnostic_last_read_at < 1.0
        ):
            return None
        self._diagnostic_last_read_at = now
        return self._diagnostic_reader(
            codex_home,
            base_url=self._base_url,
            model=self.model,
        )

    def _diagnostic_result(self, diagnostic: CodexDiagnostic) -> TuiTurnResult:
        return TuiTurnResult(
            output_text="",
            turn_status="failed",
            error_text=diagnostic.message,
            diagnostics=self._diagnostic_text(),
            timed_out=False,
            http_status_code=diagnostic.http_status_code,
            originator="",
            error_code=diagnostic.kind,
        )

    def close(self) -> None:
        if self._process is not None:
            process = self._process
            self._process = None
            self._process_terminator(process)
        for attribute in ("_master_fd", "_slave_fd"):
            fd = getattr(self, attribute)
            if fd is not None:
                setattr(self, attribute, None)
                self._pty.close(fd)


def _jsonl_events(path: Path) -> Iterator[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _message_texts(payload: dict[str, Any]) -> list[str]:
    content = payload.get("content")
    if not isinstance(content, list):
        return []
    values: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            values.append(text)
    return values


def _extract_http_status(value: Any) -> int | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.replace("_", "").casefold()
            if normalized == "httpstatuscode" and isinstance(child, int):
                return child
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


def _is_retryable(payload: dict[str, Any]) -> bool:
    return payload.get("willRetry") is True or payload.get("will_retry") is True


def _error_message(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    error = payload.get("error")
    if isinstance(error, dict):
        nested = error.get("message")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def scan_rollouts(codex_home: Path, prompt: str) -> RolloutSnapshot:
    root = Path(codex_home)
    try:
        paths = sorted(
            root.rglob("*.jsonl"),
            key=lambda path: (path.stat().st_mtime_ns, str(path)),
        )
    except OSError:
        paths = []

    originator = ""
    latest = RolloutSnapshot()
    expected_prompt = prompt.strip()
    for path in paths:
        seen_prompt = False
        for event in _jsonl_events(path):
            event_type = event.get("type")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue

            if event_type == "session_meta":
                value = payload.get("originator")
                if isinstance(value, str):
                    originator = value
                continue

            if event_type == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                texts = _message_texts(payload)
                if role == "user" and any(
                    text.strip() == expected_prompt for text in texts
                ):
                    seen_prompt = True
                    latest = RolloutSnapshot(originator=originator)
                    continue
                if seen_prompt and role == "assistant" and texts:
                    return RolloutSnapshot(
                        output_text="\n".join(texts).strip(),
                        originator=originator,
                        complete=True,
                    )

            if not seen_prompt or event_type != "event_msg":
                continue
            payload_type = payload.get("type")
            if payload_type == "task_complete":
                return RolloutSnapshot(originator=originator, complete=True)
            if not isinstance(payload_type, str) or "error" not in payload_type.casefold():
                continue
            if _is_retryable(payload):
                continue
            message = _error_message(payload)
            if not message:
                continue
            return RolloutSnapshot(
                error_text=message,
                http_status_code=_extract_http_status(payload),
                originator=originator,
                complete=True,
            )

    if originator and not latest.originator:
        latest = RolloutSnapshot(originator=originator)
    return latest


__all__ = [
    "RolloutSnapshot",
    "TuiProtocolError",
    "TuiTimeoutError",
    "TuiTurnResult",
    "scan_rollouts",
]
