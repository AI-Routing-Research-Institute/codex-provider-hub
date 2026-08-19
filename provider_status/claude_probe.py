from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from provider_status.config import ProviderConfig
from provider_status.probe import (
    DirectDiagnosticResult,
    HEALTH_PROMPT,
    HealthProbeResult,
    _classify_error,
    _is_valid_output,
)
from provider_status.store import sanitize_error_summary


_CLAUDE_OUTPUT_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "status": {"const": "ok"},
            "check": {"const": "codex-provider-health"},
        },
        "required": ["status", "check"],
        "additionalProperties": False,
    },
    separators=(",", ":"),
)
_OUTPUT_LIMIT = 32_768


@dataclass(frozen=True)
class ClaudeProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


ClaudeRunner = Callable[..., ClaudeProcessResult]
Clock = Callable[[], float]
DiagnosticRunner = Callable[..., DirectDiagnosticResult]
ProcessFactory = Callable[..., subprocess.Popen[str]]
ProcessTerminator = Callable[[subprocess.Popen[str]], None]


class ClaudeHealthProbe:
    def __init__(
        self,
        claude_bin: str | Path,
        temp_root: Path,
        *,
        runner: ClaudeRunner | None = None,
        clock: Clock = time.monotonic,
        diagnostic_runner: DiagnosticRunner | None = None,
    ) -> None:
        self._claude_bin = str(claude_bin)
        self._temp_root = Path(temp_root)
        self._runner = runner or _run_claude_cli
        self._clock = clock
        self._diagnostic_runner = (
            diagnostic_runner or _run_direct_messages_diagnostic
        )

    def run(
        self,
        provider: ProviderConfig,
        model: str,
        api_key: str,
    ) -> HealthProbeResult:
        started_at = self._clock()
        run_directory: Path | None = None
        try:
            if not provider.claude_base_url:
                return self._failure(
                    self._elapsed_ms(started_at),
                    "unknown_error",
                    "Claude base URL is not configured",
                    api_key,
                    failure_stage="claude_cli",
                    diagnostic_source="claude_cli",
                )
            run_directory, home, workspace = self._prepare_run()
            process = self._runner(
                claude_bin=self._claude_bin,
                env=_build_claude_env(
                    home,
                    provider.claude_base_url,
                    api_key,
                    provider.credential_kind,
                ),
                workspace=workspace,
                model=model,
                prompt=HEALTH_PROMPT,
                timeout=provider.timeout_seconds,
            )
            latency_ms = self._elapsed_ms(started_at)
            if process.timed_out:
                return self._failure(
                    latency_ms,
                    "timeout",
                    _process_summary(process) or "Claude CLI timed out",
                    api_key,
                    failure_stage="claude_cli",
                    diagnostic_source="claude_cli",
                )

            envelope = _parse_claude_envelope(process.stdout)
            if envelope is None:
                summary = _process_summary(process) or "Claude CLI returned no JSON result"
                error_code = (
                    _classify_claude_error(None, summary)
                    if process.returncode != 0
                    else "invalid_output"
                )
                return self._resolve_failure(
                    started_at,
                    provider,
                    model,
                    api_key,
                    error_code,
                    summary,
                    None,
                )

            http_status_code = _http_status(envelope.get("api_error_status"))
            summary = _envelope_summary(envelope, process.stderr)
            if process.returncode != 0 or envelope.get("is_error") is True:
                return self._resolve_failure(
                    started_at,
                    provider,
                    model,
                    api_key,
                    _classify_claude_error(http_status_code, summary),
                    summary,
                    http_status_code,
                )

            output = envelope.get("structured_output")
            if isinstance(output, dict):
                output_text = json.dumps(output, ensure_ascii=False)
            else:
                result = envelope.get("result")
                output_text = result if isinstance(result, str) else ""
            if not _is_valid_output(output_text):
                return self._failure(
                    latency_ms,
                    "invalid_output",
                    output_text or summary or "model returned no output",
                    api_key,
                    http_status_code=http_status_code,
                    failure_stage="response_validation",
                    diagnostic_source="claude_cli",
                )
            return HealthProbeResult(
                success=True,
                latency_ms=latency_ms,
                error_code=None,
                error_summary=None,
                diagnostic_source="claude_cli",
            )
        except Exception as exc:
            summary = str(exc) or type(exc).__name__
            error_code = (
                "timeout" if isinstance(exc, TimeoutError) else _classify_claude_error(None, summary)
            )
            return self._resolve_failure(
                started_at,
                provider,
                model,
                api_key,
                error_code,
                summary,
                None,
            )
        finally:
            if run_directory is not None:
                shutil.rmtree(run_directory, ignore_errors=True)

    def _prepare_run(self) -> tuple[Path, Path, Path]:
        self._temp_root.mkdir(parents=True, exist_ok=True)
        run_directory = Path(
            tempfile.mkdtemp(prefix="provider-probe-", dir=self._temp_root)
        )
        try:
            home = run_directory / "home"
            workspace = run_directory / "workspace"
            home.mkdir()
            workspace.mkdir()
            return run_directory, home, workspace
        except Exception:
            shutil.rmtree(run_directory, ignore_errors=True)
            raise

    def _resolve_failure(
        self,
        started_at: float,
        provider: ProviderConfig,
        model: str,
        api_key: str,
        error_code: str,
        summary: str,
        http_status_code: int | None,
    ) -> HealthProbeResult:
        if http_status_code is not None or not _needs_direct_diagnostic(
            error_code, summary
        ):
            return self._failure(
                self._elapsed_ms(started_at),
                error_code,
                summary,
                api_key,
                http_status_code=http_status_code,
                failure_stage="claude_cli",
                diagnostic_source="claude_cli",
            )
        try:
            diagnostic = self._diagnostic_runner(
                provider=provider,
                model=model,
                api_key=api_key,
                timeout_seconds=10.0,
            )
        except Exception:
            diagnostic = DirectDiagnosticResult(
                "network_error",
                "direct /v1/messages diagnostic failed",
                None,
                "network",
            )
        return self._failure(
            self._elapsed_ms(started_at),
            diagnostic.error_code,
            diagnostic.error_summary,
            api_key,
            http_status_code=diagnostic.http_status_code,
            failure_stage=diagnostic.failure_stage,
            diagnostic_source="direct_messages",
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, round((self._clock() - started_at) * 1000))

    @staticmethod
    def _failure(
        latency_ms: int,
        error_code: str,
        summary: str,
        api_key: str,
        *,
        http_status_code: int | None = None,
        failure_stage: str | None = None,
        diagnostic_source: str | None = None,
    ) -> HealthProbeResult:
        return HealthProbeResult(
            success=False,
            latency_ms=latency_ms,
            error_code=error_code,
            error_summary=sanitize_error_summary(
                summary,
                sensitive_values=(api_key,),
            ),
            http_status_code=http_status_code,
            failure_stage=failure_stage,
            diagnostic_source=diagnostic_source,
        )


def _build_claude_env(
    home: Path,
    base_url: str,
    api_key: str,
    credential_kind: str = "api_key",
) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ANTHROPIC_") and not key.startswith("CLAUDE_")
    }
    env.update(
        {
            "HOME": str(home),
            "ANTHROPIC_BASE_URL": base_url.rstrip("/"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    env["ANTHROPIC_AUTH_TOKEN" if credential_kind == "auth_token" else "ANTHROPIC_API_KEY"] = api_key
    return env


def _run_claude_cli(
    *,
    claude_bin: str,
    env: Mapping[str, str],
    workspace: Path,
    model: str,
    prompt: str,
    timeout: float,
    process_factory: ProcessFactory = subprocess.Popen,
    process_terminator: ProcessTerminator | None = None,
) -> ClaudeProcessResult:
    command = [
        claude_bin,
        "--bare",
        "--safe-mode",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--tools",
        "",
        "--model",
        model,
        "--effort",
        "low",
        "--output-format",
        "json",
        "--json-schema",
        _CLAUDE_OUTPUT_SCHEMA,
        "-p",
        prompt,
    ]
    platform_options: dict[str, Any]
    if os.name == "nt":
        platform_options = {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        }
    else:
        platform_options = {"start_new_session": True}
    process = process_factory(
        command,
        cwd=str(workspace),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **platform_options,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return ClaudeProcessResult(
            returncode=int(process.returncode or 0),
            stdout=_bounded(stdout),
            stderr=_bounded(stderr),
        )
    except subprocess.TimeoutExpired as exc:
        (process_terminator or _terminate_process_tree)(process)
        stdout, stderr = process.communicate()
        return ClaudeProcessResult(
            returncode=int(process.returncode or 1),
            stdout=_bounded(_timeout_stream(exc.stdout) + stdout),
            stderr=_bounded(_timeout_stream(exc.stderr) + stderr),
            timed_out=True,
        )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
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
                        os.killpg(process_group_id, getattr(signal, "SIGKILL", 9))
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


def _run_direct_messages_diagnostic(
    *,
    provider: ProviderConfig,
    model: str,
    api_key: str,
    timeout_seconds: float,
) -> DirectDiagnosticResult:
    if not provider.claude_base_url:
        return DirectDiagnosticResult(
            "unknown_error",
            "Claude base URL is not configured",
            None,
            "claude_cli",
        )
    endpoint = f"{provider.claude_base_url.rstrip('/')}/v1/messages"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        ) as client:
            with client.stream(
                "POST",
                endpoint,
                headers={
                    ("Authorization" if provider.credential_kind == "auth_token" else "x-api-key"):
                    f"Bearer {api_key}" if provider.credential_kind == "auth_token" else api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 64,
                    "messages": [{"role": "user", "content": HEALTH_PROMPT}],
                },
            ) as response:
                status_code = response.status_code
    except httpx.TimeoutException:
        return DirectDiagnosticResult(
            "network_error",
            "direct /v1/messages diagnostic timed out",
            None,
            "network",
        )
    except httpx.ConnectError:
        return DirectDiagnosticResult(
            "network_error",
            "direct /v1/messages diagnostic could not connect",
            None,
            "network",
        )
    except httpx.TransportError:
        return DirectDiagnosticResult(
            "network_error",
            "direct /v1/messages diagnostic transport failed",
            None,
            "network",
        )
    return DirectDiagnosticResult(
        _classify_direct_status(status_code),
        f"HTTP {status_code}",
        status_code,
        "claude_cli" if status_code == 200 else "provider_response",
    )


def _classify_direct_status(status_code: int) -> str:
    if status_code == 200:
        return "client_blocked"
    if status_code in {401, 403}:
        return "auth_failed"
    if status_code == 404:
        return "model_unavailable"
    if status_code == 429:
        return "rate_limited"
    if status_code in {502, 503, 504} or 520 <= status_code <= 526:
        return "upstream_unavailable"
    return "unknown_error"


def _classify_claude_error(http_status_code: int | None, summary: str) -> str:
    normalized = summary.casefold()
    if http_status_code in {401, 403}:
        if "client" in normalized or "channel" in normalized:
            return "client_blocked"
        return "auth_failed"
    if "failed to authenticate" in normalized:
        return "auth_failed"
    return _classify_error(http_status_code, summary)


def _needs_direct_diagnostic(error_code: str, summary: str) -> bool:
    if error_code not in {"network_error", "unknown_error"}:
        return False
    normalized = summary.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "api error",
            "fetch failed",
            "request failed",
            "stream disconnected",
            "upstream request failed",
        )
    )


def _parse_claude_envelope(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _envelope_summary(envelope: Mapping[str, Any], stderr: str) -> str:
    result = envelope.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    error = envelope.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return stderr.strip() or "Claude CLI request failed"


def _process_summary(process: ClaudeProcessResult) -> str:
    return "\n".join(
        part.strip() for part in (process.stderr, process.stdout) if part.strip()
    )


def _http_status(value: Any) -> int | None:
    return value if isinstance(value, int) and 100 <= value <= 599 else None


def _timeout_stream(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def _bounded(value: str | None) -> str:
    return (value or "")[-_OUTPUT_LIMIT:]


__all__ = [
    "ClaudeHealthProbe",
    "ClaudeProcessResult",
]
