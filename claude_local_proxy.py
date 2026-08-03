from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codex_local_proxy import (
    CONTROL_ASSET_DIR,
    DEFAULT_DATABASE,
    ProviderRouter,
    ProxyProvider,
    ProviderConfigurationError,
    create_proxy_app,
)
from provider_proxy_protocol import ClaudeMessagesProtocol


CLAUDE_CONTROL_ASSET_DIR = Path(__file__).resolve().parent / "claude_proxy_static"


@dataclass(frozen=True)
class ClaudeProxyProvider(ProxyProvider):
    credential_kind: str = "api_key"
    api_format: str = "anthropic"
    compatible: bool = True
    default_models: Mapping[str, str] = field(default_factory=dict)


def load_claude_proxy_providers(
    db_path: Path = DEFAULT_DATABASE,
) -> tuple[ClaudeProxyProvider, ...]:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到 CC Switch 数据库：{path}")
    uri = path.as_uri() + "?mode=ro"
    query = """
        SELECT p.id, p.name, p.is_current, p.settings_config, p.meta, pe.url
        FROM providers AS p
        LEFT JOIN provider_endpoints AS pe
          ON pe.provider_id = p.id AND pe.app_type = p.app_type
        WHERE p.app_type = 'claude'
        ORDER BY p.sort_index IS NULL, p.sort_index, p.created_at, p.name
    """
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query).fetchall()
        common_row = connection.execute(
            "SELECT value FROM settings WHERE key = 'common_config_claude'"
        ).fetchone()
    common_config = _json_object(common_row["value"] if common_row else None)
    common_env = _string_mapping(common_config.get("env"))
    providers: list[ClaudeProxyProvider] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for row in rows:
        try:
            provider_id = str(row["id"])
            if provider_id in seen_ids:
                raise ProviderConfigurationError("包含重复的供应商 ID")
            seen_ids.add(provider_id)
            providers.append(_claude_provider_from_row(row, common_env))
        except (json.JSONDecodeError, ProviderConfigurationError) as exc:
            errors.append(f"{row['name']}: {exc}")
    if not providers and errors:
        raise ProviderConfigurationError("；".join(errors))
    return tuple(providers)


def create_claude_proxy_app(router: ProviderRouter, **kwargs: Any):
    asset_dir = CLAUDE_CONTROL_ASSET_DIR if CLAUDE_CONTROL_ASSET_DIR.is_dir() else CONTROL_ASSET_DIR
    return create_proxy_app(
        router,
        protocol_adapter=ClaudeMessagesProtocol(),
        service_name="claude-local-proxy",
        control_asset_dir=asset_dir,
        allowed_proxy_paths=frozenset({"messages", "messages/count_tokens"}),
        provider_selectable=lambda provider: bool(
            getattr(provider, "compatible", False) and provider.has_credentials
        ),
        provider_public_fields=lambda provider: {
            "compatible": bool(getattr(provider, "compatible", False)),
            "api_format": str(getattr(provider, "api_format", "anthropic")),
            "default_models": dict(getattr(provider, "default_models", {})),
        },
        config_endpoint_name="claude-config",
        **kwargs,
    )


def _claude_provider_from_row(
    row: sqlite3.Row,
    common_env: Mapping[str, str],
) -> ClaudeProxyProvider:
    payload = _json_object(row["settings_config"])
    meta = _json_object(row["meta"])
    provider_env = _string_mapping(payload.get("env"))
    env = dict(common_env) if meta.get("commonConfigEnabled") is True else {}
    env.update(provider_env)
    base_url = env.get("ANTHROPIC_BASE_URL") or row["url"]
    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderConfigurationError("没有配置 ANTHROPIC_BASE_URL")
    from codex_local_proxy import _normalize_base_url

    normalized_url = _normalize_base_url(base_url)
    configured_field = meta.get("apiKeyField")
    field_order = [configured_field, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]
    credential_field = next(
        (
            field_name
            for field_name in field_order
            if field_name in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
            and env.get(field_name)
        ),
        None,
    )
    api_key = env.get(credential_field) if credential_field else None
    api_format = str(meta.get("apiFormat") or "anthropic").strip().lower()
    model_fields = {
        "model": "ANTHROPIC_MODEL",
        "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    }
    default_models = {
        label: env[field]
        for label, field in model_fields.items()
        if isinstance(env.get(field), str) and env[field].strip()
    }
    return ClaudeProxyProvider(
        provider_id=str(row["id"]),
        name=str(row["name"]),
        base_url=normalized_url,
        is_cc_switch_current=bool(row["is_current"]) and api_format == "anthropic" and bool(api_key),
        wire_api="anthropic_messages",
        api_key=api_key,
        credential_kind=(
            "auth_token" if credential_field == "ANTHROPIC_AUTH_TOKEN" else "api_key"
        ),
        api_format=api_format,
        compatible=api_format == "anthropic",
        default_models=default_models,
    )


def _json_object(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ProviderConfigurationError("配置必须是 JSON 对象")
    return dict(parsed)


def _string_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderConfigurationError("env 必须是对象")
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }
