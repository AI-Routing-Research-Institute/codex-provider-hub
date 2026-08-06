from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import probe_codex_cc_switch as backend


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SCRIPT = PROJECT_ROOT / "probe_codex_cc_switch.py"
REPORT_DIR = PROJECT_ROOT / "reports"
BUILTIN_MODELS = (
    "gpt-5.4",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
)


def default_settings() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "selected_provider_ids": [],
        "selected_models": [],
        "custom_models": [],
    }


def settings_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "CodexProviderProbe" / "settings.json"
    return Path.home() / ".codex-provider-probe" / "settings.json"


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _natural_sort_key(
    value: str,
) -> tuple[int, list[tuple[int, int | str]]]:
    parts = [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    ]
    return (0 if value.casefold().startswith("gpt-") else 1, parts)


def _normalize_settings(payload: Any) -> dict[str, Any]:
    normalized = default_settings()
    if not isinstance(payload, dict):
        return normalized
    normalized["selected_provider_ids"] = _unique_strings(
        payload.get("selected_provider_ids", [])
    )
    normalized["selected_models"] = _unique_strings(
        payload.get("selected_models", [])
    )
    normalized["custom_models"] = _unique_strings(
        payload.get("custom_models", [])
    )
    return normalized


def load_settings(path: Path | None = None) -> dict[str, Any]:
    target = path or settings_path()
    if not target.is_file():
        return default_settings()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_settings()
    return _normalize_settings(payload)


def save_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_settings(settings)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def configured_model(provider: backend.ProviderRecord) -> str | None:
    raw_config = provider.raw_config.strip()
    if not raw_config:
        return None
    try:
        model = tomllib.loads(raw_config).get("model")
    except tomllib.TOMLDecodeError:
        matched = re.search(r'(?m)^model\s*=\s*"([^"]+)"', raw_config)
        model = matched.group(1) if matched else None
    if not isinstance(model, str) or not model.strip():
        return None
    return model.strip()


def build_model_catalog(
    providers: Sequence[backend.ProviderRecord],
    custom_models: Sequence[str],
) -> list[str]:
    values: list[str] = list(BUILTIN_MODELS)
    values.extend(
        model
        for provider in providers
        if (model := configured_model(provider)) is not None
    )
    values.extend(custom_models)
    return sorted(_unique_strings(values), key=_natural_sort_key)


def default_selection(
    providers: Sequence[backend.ProviderRecord],
) -> tuple[list[str], list[str]]:
    selected = [provider for provider in providers if provider.is_current]
    if not selected and providers:
        selected = [providers[0]]
    provider_ids = [provider.provider_id for provider in selected]
    models = ["gpt-5.4"]
    models.extend(
        model
        for provider in selected
        if (model := configured_model(provider)) is not None
    )
    return provider_ids, _unique_strings(models)


def load_api_providers(db_path: Path | None = None) -> list[backend.ProviderRecord]:
    target = db_path or backend.expand_path("~/.cc-switch/cc-switch.db")
    providers = backend.load_codex_providers(target)
    return [provider for provider in providers if provider.is_api_provider]


def codex_binary_candidates() -> list[Path]:
    candidates: list[Path] = []
    override = os.environ.get("CODEX_BIN")
    if override:
        candidates.append(Path(override))

    app_data = os.environ.get("APPDATA")
    if app_data:
        npm_root = Path(app_data) / "npm" / "node_modules" / "@openai" / "codex"
        candidates.extend(
            npm_root.glob(
                "node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"
            )
        )

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.extend(
            (Path(local_app_data) / "OpenAI" / "Codex" / "bin").glob(
                "*/codex.exe"
            )
        )

    resolved = shutil.which("codex.exe")
    if resolved:
        candidates.append(Path(resolved))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def resolve_codex_binary(candidates: Iterable[Path] | None = None) -> Path | None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for candidate in candidates if candidates is not None else codex_binary_candidates():
        path = Path(candidate)
        try:
            completed = subprocess.run(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return path
    return None


def resolve_python_console() -> Path:
    executable = Path(sys.executable)
    lowered = executable.name.casefold()
    if lowered == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.is_file():
            return console
    if lowered == "pyw.exe":
        launcher = shutil.which("py.exe")
        if launcher:
            return Path(launcher)
    return executable


def build_probe_command(
    *,
    python_executable: Path,
    backend_script: Path,
    provider_ids: Sequence[str],
    models: Sequence[str],
    codex_binary: Path,
    output_path: Path,
    attempts: int = 2,
    timeout: int = 240,
    reasoning_effort: str = "high",
    sandbox: str = "read-only",
) -> list[str]:
    command = [str(python_executable), str(backend_script)]
    for provider_id in provider_ids:
        command.extend(["--provider", provider_id])
    command.extend(
        [
            "--models",
            ",".join(models),
            "--codex-bin",
            str(codex_binary),
            "--attempts",
            str(attempts),
            "--timeout",
            str(timeout),
            "--reasoning-effort",
            reasoning_effort,
            "--sandbox",
            sandbox,
            "--output",
            str(output_path),
        ]
    )
    return command


def report_rows(report: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for provider_result in report.get("results", []):
        provider_name = str(provider_result.get("provider_name", ""))
        for model_run in provider_result.get("model_runs", []):
            attempts = model_run.get("attempts", [])
            elapsed_seconds = sum(
                float(attempt.get("elapsed_seconds") or 0) for attempt in attempts
            )
            notes: list[str] = []
            for attempt in reversed(attempts):
                for note in attempt.get("error_summary") or []:
                    text = str(note).strip()
                    if text and text not in notes:
                        notes.append(text)
            status = str(model_run.get("status", "exec_failed"))
            rows.append(
                {
                    "provider": provider_name,
                    "model": str(model_run.get("model", "")),
                    "status": status,
                    "status_label": backend.status_label(status),
                    "elapsed": f"{elapsed_seconds:g}s",
                    "detail": "；".join(notes),
                }
            )
    return rows
