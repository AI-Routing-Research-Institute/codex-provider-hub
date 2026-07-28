from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from probe_codex_cc_switch import build_env
from provider_status.config import ProviderConfig
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


ClientFactory = Callable[..., CodexTuiClient]
Clock = Callable[[], float]


class CodexHealthProbe:
    def __init__(
        self,
        codex_bin: str | Path,
        temp_root: Path,
        client_factory: ClientFactory = CodexTuiClient,
        clock: Clock = time.monotonic,
    ) -> None:
        self._codex_bin = str(codex_bin)
        self._temp_root = Path(temp_root)
        self._client_factory = client_factory
        self._clock = clock

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
                return self._failure(
                    latency_ms,
                    turn.error_code,
                    _turn_summary(turn),
                    api_key,
                )
            if turn.timed_out:
                return self._failure(
                    latency_ms,
                    "timeout",
                    _turn_summary(turn),
                    api_key,
                )
            if turn.turn_status != "completed":
                summary = _turn_summary(turn)
                return self._failure(
                    latency_ms,
                    _classify_error(turn.http_status_code, summary),
                    summary,
                    api_key,
                )
            if not _is_valid_output(turn.output_text):
                return self._failure(
                    latency_ms,
                    "invalid_output",
                    turn.output_text or "model returned no output",
                    api_key,
                )
            return HealthProbeResult(
                success=True,
                latency_ms=latency_ms,
                error_code=None,
                error_summary=None,
            )
        except Exception as exc:
            latency_ms = self._elapsed_ms(started_at)
            summary = str(exc) or type(exc).__name__
            error_code = (
                "timeout"
                if isinstance(exc, (TuiTimeoutError, TimeoutError))
                else _classify_error(None, summary)
            )
            return self._failure(latency_ms, error_code, summary, api_key)
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

    @staticmethod
    def _failure(
        latency_ms: int,
        error_code: str,
        summary: str,
        api_key: str,
    ) -> HealthProbeResult:
        return HealthProbeResult(
            success=False,
            latency_ms=latency_ms,
            error_code=error_code,
            error_summary=sanitize_error_summary(
                summary,
                sensitive_values=(api_key,),
            ),
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
    if http_status_code in {502, 503, 504} or any(
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


__all__ = ["CodexHealthProbe", "HEALTH_PROMPT", "HealthProbeResult"]
