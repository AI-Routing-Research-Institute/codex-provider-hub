"""Claude settings, UI configuration, and protocol profile construction."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from local_proxy.claude import load_claude_proxy_providers
from local_proxy.core import (
    DEFAULT_PORT,
    HealthStatusUrlStore,
    ProviderRouter,
    RecoveryHistoryStore,
    RetryPolicyStore,
    UsageStore,
    filter_self_referencing_providers,
    order_proxy_providers,
)
from local_proxy.paths import display_path
from local_proxy.protocols.claude_messages import ClaudeMessagesProtocol
from local_proxy.server import ProxyProfile
from local_proxy.shared_settings import (
    PROTOCOL_SETTINGS_VERSION,
    data_directory,
    default_protocol_settings,
    load_protocol_settings,
    protocol_settings_path,
    protocol_usage_database_path,
    save_protocol_settings,
)
from local_proxy.transports.claude import ClaudeCurlClient


SETTINGS_VERSION = PROTOCOL_SETTINGS_VERSION


def settings_path() -> Path:
    return protocol_settings_path("claude")


def usage_database_path() -> Path:
    return protocol_usage_database_path("claude")


def default_settings() -> dict[str, Any]:
    return default_protocol_settings()


def load_settings(path: Path | None = None) -> dict[str, Any]:
    return load_protocol_settings(path or settings_path())


def save_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    save_protocol_settings(settings, path or settings_path())


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


def claude_ui_config(port: int, root: Path | None = None) -> dict[str, Any]:
    data_root = (root or data_directory()).expanduser().resolve()
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
        "data_directory": display_path(data_root),
        "config_location": "~/.claude/settings.json",
        "restart_config_text": "端口将在退出并重新启动本地中转后生效；届时需要重新复制 Claude Code 配置。",
        "copy_config_success_title": "Claude Code 配置已复制",
        "copy_config_success_detail": "在当前终端运行配置后启动 Claude Code。",
        "shutdown_client_name": "Claude Code",
        "provider_label": "Claude Code",
        "theme_storage_key": "local-proxy-theme",
        "features": {"usage_history": True},
    }


def build_claude_profile(
    *,
    database: Path,
    port: int,
    data_root: Path | None = None,
    settings_data: dict[str, Any] | None = None,
    retry_policy_store: RetryPolicyStore | None = None,
    health_status_url_store: HealthStatusUrlStore | None = None,
) -> ProxyProfile:
    root = (data_root or data_directory()).expanduser().resolve()
    active_settings_path = protocol_settings_path("claude", root)
    active_usage_path = protocol_usage_database_path("claude", root)
    settings = (
        dict(settings_data)
        if settings_data is not None
        else load_settings(active_settings_path)
    )
    settings_lock = threading.RLock()
    active_database_path = database.expanduser().resolve()

    def load_prepared_providers(source: Path) -> tuple:
        loaded = filter_self_referencing_providers(
            load_claude_proxy_providers(source),
            port,
        )
        with settings_lock:
            provider_order = tuple(settings.get("provider_order", ()))
        return order_proxy_providers(loaded, provider_order)

    def prepared_providers() -> tuple:
        with settings_lock:
            source = active_database_path
        return load_prepared_providers(source)

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

    def persist(**changes: Any) -> None:
        with settings_lock:
            settings.update(changes, schema_version=SETTINGS_VERSION)
            save_settings(settings, active_settings_path)

    def apply_database(source: Path, loaded: tuple) -> None:
        nonlocal active_database_path
        with settings_lock:
            active_database_path = source
        current = router.current_provider()
        selected = router.replace_providers(
            loaded,
            preferred_id=current.provider_id if current else None,
        )
        if selected is not None and not (selected.compatible and selected.has_credentials):
            compatible = next(
                (
                    provider
                    for provider in loaded
                    if provider.compatible and provider.has_credentials
                ),
                None,
            )
            selected = router.select(compatible.provider_id) if compatible is not None else None
        selected_id = selected.provider_id if selected is not None else None
        if selected_id != settings.get("selected_provider_id"):
            persist(selected_provider_id=selected_id)

    def runtime_metadata() -> dict[str, Any]:
        return {
            "data_directory": display_path(root),
            "settings_file": display_path(active_settings_path),
            "usage_database": display_path(active_usage_path),
            "claude_config_file": "~/.claude/settings.json",
        }

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
        retry_policy_store=retry_policy_store or RetryPolicyStore(),
        usage_store=UsageStore(active_usage_path),
        recovery_history_store=RecoveryHistoryStore(active_usage_path),
        health_status_url_store=health_status_url_store or HealthStatusUrlStore(),
        load_runtime_database=load_prepared_providers,
        apply_runtime_database=apply_database,
        database_validation_summary=lambda loaded: {
            "provider_count": len(loaded),
            "compatible_provider_count": sum(
                provider.compatible and provider.has_credentials for provider in loaded
            ),
        },
        runtime_metadata=runtime_metadata,
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
