"""Codex Responses provider loading and protocol-specific configuration."""

from __future__ import annotations

import json
import shlex
import sqlite3
import sys
import tomllib
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

import probe_codex_cc_switch as cc_switch

from local_proxy.core import (
    DEFAULT_DATABASE,
    ProviderConfigurationError,
    ProxyProvider,
    _normalize_base_url,
)


def load_proxy_providers(db_path: Path = DEFAULT_DATABASE) -> tuple[ProxyProvider, ...]:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到 CC Switch 数据库：{path}")
    uri = path.as_uri() + "?mode=ro"
    query = """
        SELECT p.id, p.name, p.is_current, p.settings_config, p.meta, pe.url
        FROM providers AS p
        LEFT JOIN provider_endpoints AS pe
          ON pe.provider_id = p.id AND pe.app_type = p.app_type
        WHERE p.app_type = 'codex'
        ORDER BY p.sort_index IS NULL, p.sort_index, p.created_at, p.name
    """
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query).fetchall()
        common_row = connection.execute(
            "SELECT value FROM settings WHERE key = 'common_config_codex'"
        ).fetchone()
    common_config = (common_row["value"] if common_row else "") or ""
    providers: list[ProxyProvider] = []
    errors: list[str] = []
    for row in rows:
        try:
            record = _record_from_row(row)
            if not record.is_api_provider:
                continue
            providers.append(_proxy_provider(record, common_config))
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, ProviderConfigurationError) as exc:
            errors.append(f"{row['name']}: {exc}")
    if not providers and errors:
        raise ProviderConfigurationError("；".join(errors))
    return tuple(providers)


def codex_cli_launch_command(
    provider: ProxyProvider,
    *,
    platform: str | None = None,
) -> dict[str, str]:
    """Build a one-shot Codex CLI command for an upstream provider."""
    if not provider.has_credentials:
        raise ProviderConfigurationError("该供应商没有可用于启动 Codex CLI 的凭据")

    provider_id = "custom"
    settings = [
        f"model_provider={_toml_string(provider_id)}",
        f"model_providers.{provider_id}.name={_toml_string(provider.name)}",
        f"model_providers.{provider_id}.base_url={_toml_string(provider.base_url)}",
        f"model_providers.{provider_id}.wire_api={_toml_string('responses')}",
    ]
    environment_name = "CODEX_PROVIDER_API_KEY"
    if provider.api_key:
        settings.append(
            f"model_providers.{provider_id}.env_key={_toml_string(environment_name)}"
        )
    if provider.configured_headers:
        settings.append(
            f"model_providers.{provider_id}.http_headers={_toml_table(provider.configured_headers)}"
        )
    if provider.default_query:
        settings.append(
            f"model_providers.{provider_id}.query_params={_toml_table(provider.default_query)}"
        )

    arguments = ["codex"]
    for setting in settings:
        arguments.extend(("-c", setting))
    target_platform = platform or sys.platform
    if provider.api_key:
        if target_platform == "win32":
            return {
                "shell": "powershell",
                "command": _powershell_launch(arguments, environment_name, provider.api_key),
            }
        return {
            "shell": "shell",
            "command": (
                f"{environment_name}={shlex.quote(provider.api_key)} "
                f"{shlex.join(arguments)}"
            ),
        }
    if target_platform == "win32":
        return {"shell": "powershell", "command": _powershell_join(arguments)}
    return {"shell": "shell", "command": shlex.join(arguments)}


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_table(values: Mapping[str, str]) -> str:
    return "{" + ", ".join(
        f"{_toml_string(key)}={_toml_string(value)}" for key, value in values.items()
    ) + "}"


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_join(arguments: list[str]) -> str:
    return " ".join(
        arguments[:1] + [_powershell_quote(argument) for argument in arguments[1:]]
    )


def _powershell_launch(arguments: list[str], environment_name: str, api_key: str) -> str:
    command = _powershell_join(arguments)
    quoted_name = _powershell_quote(environment_name)
    quoted_key = _powershell_quote(api_key)
    return (
        "& { "
        f"$codexProviderKeyBefore = [Environment]::GetEnvironmentVariable({quoted_name}, 'Process'); "
        f"$env:{environment_name} = {quoted_key}; "
        f"try {{ {command} }} finally {{ "
        f"if ($null -eq $codexProviderKeyBefore) {{ Remove-Item Env:{environment_name} -ErrorAction SilentlyContinue }} "
        f"else {{ $env:{environment_name} = $codexProviderKeyBefore }} }} "
        "}"
    )


def _record_from_row(row: sqlite3.Row) -> cc_switch.ProviderRecord:
    payload = json.loads(row["settings_config"] or "{}")
    auth = payload.get("auth") or {}
    if not isinstance(auth, dict):
        raise ProviderConfigurationError("认证配置格式无效")
    meta = json.loads(row["meta"]) if row["meta"] else {}
    if not isinstance(meta, dict):
        raise ProviderConfigurationError("元数据格式无效")
    return cc_switch.ProviderRecord(
        provider_id=str(row["id"]),
        name=str(row["name"]),
        is_current=bool(row["is_current"]),
        endpoint_url=row["url"],
        common_config_enabled=bool(meta.get("commonConfigEnabled")),
        raw_config=str(payload.get("config") or "").strip(),
        auth=auth,
        meta=meta,
    )


def _proxy_provider(
    record: cc_switch.ProviderRecord,
    common_config: str,
) -> ProxyProvider:
    effective_text = cc_switch.build_effective_config(record, common_config)
    config = tomllib.loads(effective_text) if effective_text.strip() else {}
    provider_config = _selected_provider_config(config)
    base_url = provider_config.get("base_url") or record.endpoint_url
    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderConfigurationError("没有配置 base_url")
    normalized_url = _normalize_base_url(base_url)
    env_key = provider_config.get("env_key", "OPENAI_API_KEY")
    if not isinstance(env_key, str) or not env_key.strip():
        raise ProviderConfigurationError("env_key 格式无效")
    api_key = _nonempty_string(record.auth.get(env_key))
    if api_key is None and env_key != "OPENAI_API_KEY":
        api_key = _nonempty_string(record.auth.get("OPENAI_API_KEY"))

    headers = _string_mapping(provider_config.get("http_headers"), "http_headers")
    env_headers = _string_mapping(
        provider_config.get("env_http_headers"),
        "env_http_headers",
    )
    resolved_headers = dict(headers)
    for header_name, auth_name in env_headers.items():
        value = _nonempty_string(record.auth.get(auth_name))
        if value is not None:
            resolved_headers[header_name] = value
    query = _string_mapping(provider_config.get("query_params"), "query_params")
    wire_api = provider_config.get("wire_api", "responses")
    if not isinstance(wire_api, str):
        wire_api = "responses"
    return ProxyProvider(
        provider_id=record.provider_id,
        name=record.name,
        base_url=normalized_url,
        is_cc_switch_current=record.is_current,
        wire_api=wire_api,
        api_key=api_key,
        configured_headers=resolved_headers,
        default_query=query,
    )


def _selected_provider_config(config: Mapping[str, Any]) -> dict[str, Any]:
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return {}
    selected = config.get("model_provider")
    if isinstance(selected, str) and isinstance(providers.get(selected), dict):
        return dict(providers[selected])
    if len(providers) == 1:
        only = next(iter(providers.values()))
        return dict(only) if isinstance(only, dict) else {}
    return {}


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_mapping(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderConfigurationError(f"{field_name} 格式无效")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ProviderConfigurationError(f"{field_name} 必须只包含字符串")
        result[key] = item
    return result
