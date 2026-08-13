from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from probe_codex_cc_switch import build_env
from provider_status.config import (
    PROBE_CLIENT_CLAUDE,
    PROBE_CLIENT_CODEX,
    ProviderConfig,
    Resolver,
    validate_public_https_endpoint,
)
from provider_status.store import sanitize_error_summary
from provider_status.tui_probe import (
    CodexTuiClient,
    TuiTimeoutError,
    TuiTurnResult,
)


HEALTH_PROMPT = (
    "Do not call tools. Return only this JSON object with exactly these fields: "
    '{"status":"ok","check":"codex-provider-health"}'
)
_EXPECTED_OUTPUT = {
    "status": "ok",
    "check": "codex-provider-health",
}
_MARKDOWN_FENCE_RE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class HealthProbeResult:
    success: bool
    latency_ms: int
    error_code: str | None
    error_summary: str | None
    http_status_code: int | None = None
    failure_stage: str | None = None
    diagnostic_source: str | None = None


@dataclass(frozen=True)
class DirectDiagnosticResult:
    error_code: str
    error_summary: str
    http_status_code: int | None
    failure_stage: str


ClientFactory = Callable[..., CodexTuiClient]
Clock = Callable[[], float]
DiagnosticRunner = Callable[..., DirectDiagnosticResult]


class ProviderHealthProbe:
    def __init__(
        self,
        codex_probe: Any,
        claude_probe: Any | None = None,
        *,
        resolver: Resolver = socket.getaddrinfo,
    ) -> None:
        self._codex_probe = codex_probe
        self._claude_probe = claude_probe
        self._resolver = resolver

    def run(
        self,
        provider: ProviderConfig,
        model: str,
        api_key: str,
    ) -> HealthProbeResult:
        client = provider.probe_client(model)
        endpoint = (
            provider.claude_base_url
            if client == PROBE_CLIENT_CLAUDE
            else provider.base_url
        )
        if endpoint:
            try:
                validate_public_https_endpoint(
                    endpoint,
                    provider.provider_id,
                    self._resolver,
                    field_name=(
                        "claude_base_url"
                        if client == PROBE_CLIENT_CLAUDE
                        else "base_url"
                    ),
                )
            except ValueError as exc:
                return HealthProbeResult(
                    success=False,
                    latency_ms=0,
                    error_code="network_error",
                    error_summary=str(exc),
                    failure_stage="probe_setup",
                    diagnostic_source="probe_router",
                )
        if client == PROBE_CLIENT_CODEX:
            return self._codex_probe.run(provider, model, api_key)
        if client == PROBE_CLIENT_CLAUDE and self._claude_probe is not None:
            return self._claude_probe.run(provider, model, api_key)
        return HealthProbeResult(
            success=False,
            latency_ms=0,
            error_code="unknown_error",
            error_summary="configured probe client is unavailable",
            failure_stage="probe_setup",
            diagnostic_source="probe_router",
        )


class CodexHealthProbe:
    def __init__(
        self,
        codex_bin: str | Path,
        temp_root: Path,
        client_factory: ClientFactory = CodexTuiClient,
        clock: Clock = time.monotonic,
        diagnostic_runner: DiagnosticRunner | None = None,
    ) -> None:
        self._codex_bin = str(codex_bin)
        self._temp_root = Path(temp_root)
        self._client_factory = client_factory
        self._clock = clock
        self._diagnostic_runner = diagnostic_runner or _run_direct_diagnostic

    def run(
        self,
        provider: ProviderConfig,
        model: str,
        api_key: str,
    ) -> HealthProbeResult:
        started_at = self._clock()
        run_directory: Path | None = None
        client: Any | None = None
        try:
            run_directory, codex_home, workspace = self._prepare_run(
                provider,
                model,
                api_key,
            )
            client = self._client_factory(
                codex_bin=self._codex_bin,
                env=build_env(codex_home),
                workspace=workspace,
                sandbox="read-only",
                model=model,
                reasoning_effort="low",
                model_provider="custom",
                base_url=provider.base_url,
            )
            turn = client.run_turn(
                HEALTH_PROMPT,
                timeout=provider.timeout_seconds,
            )
            latency_ms = self._elapsed_ms(started_at)
            if turn.error_code:
                return self._resolve_failure(
                    started_at,
                    provider,
                    model,
                    api_key,
                    turn.error_code,
                    _turn_summary(turn),
                    turn.http_status_code,
                )
            if turn.timed_out:
                return self._failure(
                    latency_ms,
                    "timeout",
                    _turn_summary(turn),
                    api_key,
                    http_status_code=turn.http_status_code,
                    failure_stage="codex_tui",
                    diagnostic_source="codex_tui",
                )
            if turn.turn_status != "completed":
                summary = _turn_summary(turn)
                return self._resolve_failure(
                    started_at,
                    provider,
                    model,
                    api_key,
                    _classify_error(turn.http_status_code, summary),
                    summary,
                    turn.http_status_code,
                )
            if not _is_valid_output(turn.output_text):
                return self._failure(
                    latency_ms,
                    "invalid_output",
                    turn.output_text or "model returned no output",
                    api_key,
                    failure_stage="response_validation",
                    diagnostic_source="codex_tui",
                )
            return HealthProbeResult(
                success=True,
                latency_ms=latency_ms,
                error_code=None,
                error_summary=None,
                diagnostic_source="codex_tui",
            )
        except Exception as exc:
            latency_ms = self._elapsed_ms(started_at)
            summary = str(exc) or type(exc).__name__
            error_code = (
                "timeout"
                if isinstance(exc, (TuiTimeoutError, TimeoutError))
                else _classify_error(None, summary)
            )
            if error_code == "network_error":
                return self._resolve_failure(
                    started_at,
                    provider,
                    model,
                    api_key,
                    error_code,
                    summary,
                    None,
                )
            return self._failure(
                latency_ms,
                error_code,
                summary,
                api_key,
                failure_stage="codex_tui",
                diagnostic_source="codex_tui",
            )
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            if run_directory is not None:
                shutil.rmtree(run_directory, ignore_errors=True)

    def _prepare_run(
        self,
        provider: ProviderConfig,
        model: str,
        api_key: str,
    ) -> tuple[Path, Path, Path]:
        self._temp_root.mkdir(parents=True, exist_ok=True)
        run_directory = Path(
            tempfile.mkdtemp(prefix="provider-probe-", dir=self._temp_root)
        )
        try:
            codex_home = run_directory / "codex-home"
            workspace = run_directory / "workspace"
            codex_home.mkdir()
            workspace.mkdir()

            config_path = codex_home / "config.toml"
            auth_path = codex_home / "auth.json"
            config_path.write_text(
                _render_config(provider, model),
                encoding="utf-8",
            )
            auth_path.write_text(
                json.dumps(
                    {"OPENAI_API_KEY": api_key},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)
            os.chmod(auth_path, stat.S_IRUSR | stat.S_IWUSR)
            return run_directory, codex_home, workspace
        except Exception:
            shutil.rmtree(run_directory, ignore_errors=True)
            raise

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, round((self._clock() - started_at) * 1000))

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
        if not _is_ambiguous_stream_failure(summary):
            return self._failure(
                self._elapsed_ms(started_at),
                error_code,
                summary,
                api_key,
                http_status_code=http_status_code,
                failure_stage="codex_tui",
                diagnostic_source="codex_tui",
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
                error_code="network_error",
                error_summary="direct /responses diagnostic failed",
                http_status_code=None,
                failure_stage="network",
            )
        return self._failure(
            self._elapsed_ms(started_at),
            diagnostic.error_code,
            diagnostic.error_summary,
            api_key,
            http_status_code=diagnostic.http_status_code,
            failure_stage=diagnostic.failure_stage,
            diagnostic_source="direct_responses",
        )

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


def _run_direct_diagnostic(
    *,
    provider: ProviderConfig,
    model: str,
    api_key: str,
    timeout_seconds: float,
) -> DirectDiagnosticResult:
    endpoint = f"{provider.base_url.rstrip('/')}/responses"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        ) as client:
            with client.stream(
                "POST",
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "input": HEALTH_PROMPT,
                    "max_output_tokens": 64,
                    "stream": False,
                },
            ) as response:
                status_code = response.status_code
    except httpx.TimeoutException:
        return DirectDiagnosticResult(
            "network_error",
            "direct /responses diagnostic timed out",
            None,
            "network",
        )
    except httpx.ConnectError:
        return DirectDiagnosticResult(
            "network_error",
            "direct /responses diagnostic could not connect",
            None,
            "network",
        )
    except httpx.TransportError:
        return DirectDiagnosticResult(
            "network_error",
            "direct /responses diagnostic transport failed",
            None,
            "network",
        )

    return DirectDiagnosticResult(
        error_code=_classify_direct_status(status_code),
        error_summary=f"HTTP {status_code}",
        http_status_code=status_code,
        failure_stage=("codex_stream" if status_code == 200 else "provider_response"),
    )


def _classify_direct_status(status_code: int) -> str:
    if status_code == 200:
        return "stream_interrupted"
    if status_code == 401:
        return "auth_failed"
    if status_code == 403:
        return "client_blocked"
    if status_code == 429:
        return "rate_limited"
    if status_code in {502, 503, 504} or 520 <= status_code <= 526:
        return "upstream_unavailable"
    return "unknown_error"


def _is_ambiguous_stream_failure(summary: str) -> bool:
    normalized = summary.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "stream disconnected",
            "upstream request failed",
            "response stream disconnected",
            "响应流中断",
        )
    )


def _render_config(provider: ProviderConfig, model: str) -> str:
    return (
        f"model = {_toml_string(model)}\n"
        'model_provider = "custom"\n'
        "\n"
        "[model_providers.custom]\n"
        f"name = {_toml_string(provider.name)}\n"
        f"base_url = {_toml_string(provider.base_url)}\n"
        'wire_api = "responses"\n'
        "requires_openai_auth = true\n"
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _strip_outer_markdown_fence(value: str) -> str:
    stripped = value.strip()
    match = _MARKDOWN_FENCE_RE.fullmatch(stripped)
    return match.group("body").strip() if match else stripped


def _is_valid_output(value: str) -> bool:
    try:
        parsed = json.loads(_strip_outer_markdown_fence(value))
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed == _EXPECTED_OUTPUT


def _turn_summary(turn: TuiTurnResult) -> str:
    details = [part.strip() for part in (turn.error_text, turn.diagnostics) if part.strip()]
    if turn.http_status_code is not None:
        details.append(f"HTTP {turn.http_status_code}")
    return "\n".join(details) or f"turn status: {turn.turn_status}"


def _classify_error(http_status_code: int | None, summary: str) -> str:
    normalized = summary.casefold()
    if any(
        phrase in normalized
        for phrase in (
            "model_not_found",
            "model not found",
            "unsupported model",
            "unknown model",
            "model unavailable",
            "model does not exist",
            "model not supported",
            "供应商未开放该模型",
        )
    ) or ("does not exist" in normalized and "model" in normalized):
        return "model_unavailable"
    if http_status_code == 401 or any(
        phrase in normalized
        for phrase in (
            "authentication failed",
            "authentication failure",
            "authentication required",
            "auth failed",
            "auth error",
            "unauthorized",
            "invalid api key",
            "incorrect api key",
        )
    ):
        return "auth_failed"
    if http_status_code == 403 and (
        "channel" in normalized or "client" in normalized
    ):
        return "client_blocked"
    if http_status_code == 429 or any(
        phrase in normalized
        for phrase in (
            "usage limit",
            "rate limit",
            "too many requests",
            "quota exceeded",
        )
    ):
        return "rate_limited"
    if "no available channel" in normalized:
        return "no_channel"
    if http_status_code in {502, 503, 504} or (
        http_status_code is not None and 520 <= http_status_code <= 526
    ) or any(
        phrase in normalized
        for phrase in (
            "service unavailable",
            "上游服务暂时不可用",
        )
    ):
        return "upstream_unavailable"
    if any(
        phrase in normalized
        for phrase in (
            "stream disconnected",
            "dns",
            "tls",
            "connect",
            "error sending request",
        )
    ):
        return "network_error"
    return "unknown_error"


__all__ = [
    "CodexHealthProbe",
    "DirectDiagnosticResult",
    "HEALTH_PROMPT",
    "HealthProbeResult",
    "ProviderHealthProbe",
]
