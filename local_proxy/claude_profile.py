from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from local_proxy.claude import load_claude_proxy_providers
from local_proxy.transports.claude import ClaudeCurlClient
from local_proxy.core import (
    DEFAULT_DATABASE,
    DEFAULT_PORT,
    HealthStatusUrlStore,
    ProviderRouter,
    RecoveryHistoryStore,
    RetryPolicy,
    RetryPolicyStore,
    UsageStore,
    filter_self_referencing_providers,
    normalize_health_status_url,
    order_proxy_providers,
    retry_policy_from_mapping,
)
from local_proxy.paths import display_path, resolve_user_path
from local_proxy.protocols.claude_messages import ClaudeMessagesProtocol
from local_proxy.server import ProxyProfile


APP_DATA_DIRECTORY_NAME = ".claude-local-proxy"
SETTINGS_VERSION = 2


def data_directory() -> Path:
    return Path.home() / APP_DATA_DIRECTORY_NAME


def settings_path() -> Path:
    return data_directory() / "settings.json"


def usage_database_path() -> Path:
    return data_directory() / "usage.sqlite3"


def default_settings() -> dict[str, Any]:
    return {
        "schema_version": SETTINGS_VERSION,
        "selected_provider_id": None,
        "database_path": display_path(DEFAULT_DATABASE),
        "retry": RetryPolicy().as_public_dict(),
        "provider_order": [],
        "hidden_provider_ids": [],
        "health_status_url": None,
    }


def load_settings(path: Path | None = None) -> dict[str, Any]:
    target = path or settings_path()
    settings = default_settings()
    if not target.is_file():
        return settings
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if not isinstance(payload, dict):
        return settings
    selected = payload.get("selected_provider_id")
    if isinstance(selected, str) and selected.strip():
        settings["selected_provider_id"] = selected.strip()
    database_path = payload.get("database_path")
    if isinstance(database_path, str) and database_path.strip():
        settings["database_path"] = database_path.strip()
    try:
        settings["retry"] = retry_policy_from_mapping(payload.get("retry", {})).as_public_dict()
    except ValueError:
        pass
    for field_name in ("provider_order", "hidden_provider_ids"):
        values = payload.get(field_name)
        if isinstance(values, list):
            settings[field_name] = list(
                dict.fromkeys(
                    value.strip()
                    for value in values
                    if isinstance(value, str) and value.strip()
                )
            )
    try:
        settings["health_status_url"] = normalize_health_status_url(
            payload.get("health_status_url")
        )
    except ValueError:
        pass
    return settings


def save_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def claude_config_snippets(port: int = DEFAULT_PORT) -> dict[str, str]:
    base_url = f"http://127.0.0.1:{port}"
    return {
        "powershell": (
            f'$env:ANTHROPIC_BASE_URL = "{base_url}"\n'
            '$env:ANTHROPIC_API_KEY = "local-claude-proxy"\n'
        ),
        "bash": (
            f'export ANTHROPIC_BASE_URL="{base_url}"\n'
            'export ANTHROPIC_API_KEY="local-claude-proxy"\n'
        ),
    }


def claude_ui_config(port: int, root: Path) -> dict[str, Any]:
    return {
        "service_id": "claude",
        "display_name": "Claude Code 本地中转",
        "brand_mark": "CC",
        "client_name": "Claude Code",
        "protocol_label": "Messages · SSE",
        "proxy_url": f"http://127.0.0.1:{port}",
        "peer_console_label": "Codex 控制台",
        "peer_console_url": f"http://127.0.0.1:{port}/control/codex/",
        "config_endpoint": "/control/claude/api/claude-config",
        "control_base_path": "/control/claude",
        "config_button_label": "复制 Claude 配置",
        "config_location_label": "Claude Code 配置位置",
        "config_location_hint": "配置片段用于启动 Claude Code",
        "data_directory": display_path(root),
        "config_location": "~/.claude/settings.json",
        "restart_config_text": "端口将在退出并重新启动本地中转后生效；届时需要重新复制 Claude Code 配置。",
        "copy_config_success_title": "Claude Code 配置已复制",
        "copy_config_success_detail": "在当前终端运行配置后启动 Claude Code。",
        "shutdown_client_name": "Claude Code",
        "provider_label": "Claude Code",
        "theme_storage_key": "local-proxy-theme",
        "features": {"usage_history": True, "shared_port": True},
    }


def build_claude_profile(
    *,
    database: Path,
    port: int,
    data_root: Path | None = None,
) -> ProxyProfile:
    root = (data_root or data_directory()).expanduser().resolve()
    active_settings_path = root / "settings.json"
    active_usage_path = root / "usage.sqlite3"
    settings = load_settings(active_settings_path)
    settings_lock = threading.RLock()
    active_database_path = database.expanduser().resolve()

    def prepared_providers():
        loaded = filter_self_referencing_providers(
            load_claude_proxy_providers(active_database_path),
            port,
        )
        return order_proxy_providers(loaded, settings.get("provider_order", ()))

    providers = prepared_providers()
    selectable = tuple(
        provider for provider in providers if provider.compatible and provider.has_credentials
    )
    preferred_id = settings.get("selected_provider_id")
    if preferred_id not in {provider.provider_id for provider in selectable}:
        preferred_id = next(
            (provider.provider_id for provider in selectable if provider.is_cc_switch_current),
            selectable[0].provider_id if selectable else None,
        )
    router = ProviderRouter(providers, current_provider_id=preferred_id)
    retry_store = RetryPolicyStore(retry_policy_from_mapping(settings.get("retry", {})))
    health_store = HealthStatusUrlStore(settings.get("health_status_url"))

    def persist(**changes: Any) -> None:
        with settings_lock:
            settings.update(changes, schema_version=SETTINGS_VERSION)
            save_settings(settings, active_settings_path)

    def runtime_snapshot() -> dict[str, Any]:
        return {
            "configured_port": port,
            "active_port": port,
            "restart_required": False,
            "database_path": display_path(active_database_path),
            "health_status_url": health_store.get(),
            "data_directory": display_path(root),
            "settings_file": display_path(active_settings_path),
            "usage_database": display_path(active_usage_path),
            "claude_config_file": "~/.claude/settings.json",
        }

    def validate_database(value: str) -> tuple[Path, tuple]:
        source = resolve_user_path(value.strip())
        if not source.is_file():
            raise ValueError(f"未找到 CC Switch 数据库：{display_path(source)}")
        try:
            loaded = filter_self_referencing_providers(
                load_claude_proxy_providers(source), port
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise ValueError("无法读取 CC Switch 数据库或数据库结构不兼容") from exc
        compatible = tuple(
            provider for provider in loaded if provider.compatible and provider.has_credentials
        )
        if not compatible:
            raise ValueError("数据库中没有兼容 Anthropic Messages 的 Claude 供应商")
        return source, loaded

    def validate_runtime_database(value: str) -> dict[str, Any]:
        source, loaded = validate_database(value)
        return {
            "database_path": display_path(source),
            "provider_count": len(loaded),
            "compatible_provider_count": sum(
                provider.compatible and provider.has_credentials for provider in loaded
            ),
        }

    def apply_runtime_settings(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal active_database_path
        configured_port = payload.get("port")
        if (
            isinstance(configured_port, bool)
            or not isinstance(configured_port, int)
            or not 1024 <= configured_port <= 65535
        ):
            raise ValueError("端口必须是 1024 到 65535 之间的整数")
        if configured_port != port:
            raise ValueError("统一端口请在 Codex 控制台修改")
        database_value = payload.get("database_path")
        if not isinstance(database_value, str) or not database_value.strip():
            raise ValueError("数据来源不能为空")
        health_url = normalize_health_status_url(payload.get("health_status_url"))
        source, loaded = validate_database(database_value)
        active_database_path = source
        current = router.current_provider()
        router.replace_providers(loaded, preferred_id=current.provider_id if current else None)
        health_store.replace(health_url)
        persist(
            database_path=display_path(source),
            health_status_url=health_url,
        )
        return runtime_snapshot()

    return ProxyProfile(
        service_id="claude",
        service_name="claude-local-proxy",
        router=router,
        upstream_client=ClaudeCurlClient(),
        protocol_adapter=ClaudeMessagesProtocol(),
        allowed_proxy_paths=frozenset({"messages", "messages/count_tokens"}),
        reload_providers=prepared_providers,
        on_provider_selected=lambda provider_id: persist(selected_provider_id=provider_id),
        hidden_provider_ids=settings.get("hidden_provider_ids", ()),
        provider_order=settings.get("provider_order", ()),
        on_hidden_provider_ids_changed=lambda ids: persist(hidden_provider_ids=list(ids)),
        on_provider_order_changed=lambda ids: persist(provider_order=list(ids)),
        config_fragment=lambda: json.dumps(claude_config_snippets(port), ensure_ascii=False),
        retry_policy_store=retry_store,
        on_retry_policy_changed=lambda policy: persist(retry=policy.as_public_dict()),
        usage_store=UsageStore(active_usage_path),
        recovery_history_store=RecoveryHistoryStore(active_usage_path),
        health_status_url_store=health_store,
        runtime_settings_snapshot=runtime_snapshot,
        on_runtime_settings_changed=apply_runtime_settings,
        validate_runtime_database=validate_runtime_database,
        ui_config=lambda: claude_ui_config(port, root),
        provider_selectable=lambda provider: bool(
            getattr(provider, "compatible", False) and provider.has_credentials
        ),
        provider_public_fields=lambda provider: {
            "compatible": bool(getattr(provider, "compatible", False)),
            "api_format": str(getattr(provider, "api_format", "anthropic")),
            "default_models": dict(getattr(provider, "default_models", {})),
        },
        config_endpoint_name="claude-config",
    )
