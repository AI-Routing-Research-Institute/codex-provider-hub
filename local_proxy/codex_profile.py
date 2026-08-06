"""Codex settings, UI configuration, and unified profile construction."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import httpx

from local_proxy.codex import load_proxy_providers
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
from local_proxy.server import ProxyProfile


SETTINGS_VERSION = 5
APP_DATA_DIRECTORY_NAME = ".codex-local-proxy"


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
        "port": DEFAULT_PORT,
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
    provider_id = payload.get("selected_provider_id")
    if isinstance(provider_id, str) and provider_id.strip():
        settings["selected_provider_id"] = provider_id.strip()
    port = payload.get("port")
    if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
        settings["port"] = port
    database_path = payload.get("database_path")
    if isinstance(database_path, str) and database_path.strip():
        try:
            settings["database_path"] = display_path(
                resolve_user_path(database_path.strip())
            )
        except (OSError, RuntimeError, ValueError):
            pass
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


def codex_config_fragment(port: int = DEFAULT_PORT) -> str:
    return (
        'model_provider = "local_cc_switch"\n'
        "\n"
        "[model_providers.local_cc_switch]\n"
        'name = "CC Switch Local Proxy"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\n'
        'wire_api = "responses"\n'
        "requires_openai_auth = true\n"
    )


def codex_ui_config(port: int, root: Path | None = None) -> dict[str, Any]:
    data_root = (root or data_directory()).expanduser().resolve()
    return {
        "service_id": "codex",
        "display_name": "Codex 本地中转",
        "brand_mark": "CX",
        "client_name": "Codex",
        "protocol_label": "Responses · SSE",
        "proxy_url": f"http://127.0.0.1:{port}/v1",
        "peer_console_label": "Claude Code 控制台",
        "peer_console_url": f"http://127.0.0.1:{port}/control/claude/",
        "config_endpoint": "/control/codex/api/codex-config",
        "control_base_path": "/control/codex",
        "config_button_label": "复制 Codex 配置",
        "config_location_label": "Codex 配置文件",
        "config_location_hint": "“复制 Codex 配置”生成的片段需要合并到此文件",
        "data_directory": display_path(data_root),
        "config_location": "~/.codex/config.toml",
        "restart_config_text": "端口将在退出并重新启动本地中转后生效；届时需要重新复制 Codex 配置。",
        "copy_config_success_title": "Codex 配置已复制",
        "copy_config_success_detail": "首次配置后重启一次 Codex，后续切换不再需要重启。",
        "shutdown_client_name": "Codex",
        "provider_label": "Codex API",
        "theme_storage_key": "local-proxy-theme",
        "features": {"usage_history": True, "shared_port": False},
    }


def build_codex_profile(
    *,
    database: Path,
    port: int,
    data_root: Path | None = None,
    settings_data: dict[str, Any] | None = None,
) -> ProxyProfile:
    root = (data_root or data_directory()).expanduser().resolve()
    active_settings_path = root / "settings.json"
    active_usage_path = root / "usage.sqlite3"
    settings = dict(settings_data) if settings_data is not None else load_settings(active_settings_path)
    settings_lock = threading.RLock()
    active_database_path = database.expanduser().resolve()

    def load_prepared_providers(source: Path) -> tuple:
        loaded = filter_self_referencing_providers(load_proxy_providers(source), port)
        with settings_lock:
            provider_order = tuple(settings.get("provider_order", ()))
        return order_proxy_providers(loaded, provider_order)

    def prepared_providers() -> tuple:
        with settings_lock:
            source = active_database_path
        return load_prepared_providers(source)

    providers = prepared_providers()
    router = ProviderRouter(
        providers,
        current_provider_id=settings.get("selected_provider_id"),
    )
    retry_store = RetryPolicyStore(retry_policy_from_mapping(settings.get("retry", {})))
    health_store = HealthStatusUrlStore(settings.get("health_status_url"))

    def persist(**changes: Any) -> None:
        with settings_lock:
            settings.update(changes, schema_version=SETTINGS_VERSION)
            save_settings(settings, active_settings_path)

    def runtime_snapshot() -> dict[str, Any]:
        with settings_lock:
            configured_port = int(settings.get("port", port))
            database_display = display_path(active_database_path)
        return {
            "configured_port": configured_port,
            "active_port": port,
            "restart_required": configured_port != port,
            "database_path": database_display,
            "health_status_url": health_store.get(),
            "data_directory": display_path(root),
            "settings_file": display_path(active_settings_path),
            "usage_database": display_path(active_usage_path),
            "codex_config_file": "~/.codex/config.toml",
        }

    def validate_database(value: str) -> tuple[Path, tuple]:
        source = resolve_user_path(value.strip())
        if not source.is_file():
            raise ValueError(f"未找到 CC Switch 数据库：{display_path(source)}")
        try:
            loaded = load_prepared_providers(source)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise ValueError("无法读取 CC Switch 数据库或数据库结构不兼容") from exc
        if not loaded:
            raise ValueError("数据库中没有可用的 Codex 供应商")
        return source, loaded

    def validate_runtime_database(value: str) -> dict[str, Any]:
        source, loaded = validate_database(value)
        return {
            "database_path": display_path(source),
            "provider_count": len(loaded),
            "current_provider_configured": any(
                provider.is_cc_switch_current for provider in loaded
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
        database_value = payload.get("database_path")
        if not isinstance(database_value, str) or not database_value.strip():
            raise ValueError("数据来源不能为空")
        health_url = normalize_health_status_url(payload.get("health_status_url"))
        source, loaded = validate_database(database_value)

        with settings_lock:
            candidate = dict(settings)
            candidate.update(
                schema_version=SETTINGS_VERSION,
                port=configured_port,
                database_path=display_path(source),
                health_status_url=health_url,
            )
            save_settings(candidate, active_settings_path)
            settings.clear()
            settings.update(candidate)
            active_database_path = source

        health_store.replace(health_url)
        current = router.current_provider()
        selected = router.replace_providers(
            loaded,
            preferred_id=current.provider_id if current else None,
        )
        if selected is not None and selected.provider_id != settings.get("selected_provider_id"):
            persist(selected_provider_id=selected.provider_id)
        return runtime_snapshot()

    return ProxyProfile(
        service_id="codex",
        service_name="codex-local-proxy",
        router=router,
        upstream_client=httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=None, write=120.0, pool=30.0),
            follow_redirects=False,
        ),
        reload_providers=prepared_providers,
        on_provider_selected=lambda provider_id: persist(selected_provider_id=provider_id),
        hidden_provider_ids=settings.get("hidden_provider_ids", ()),
        provider_order=settings.get("provider_order", ()),
        on_hidden_provider_ids_changed=lambda ids: persist(hidden_provider_ids=list(ids)),
        on_provider_order_changed=lambda ids: persist(provider_order=list(ids)),
        config_fragment=lambda: codex_config_fragment(port),
        retry_policy_store=retry_store,
        on_retry_policy_changed=lambda policy: persist(retry=policy.as_public_dict()),
        usage_store=UsageStore(active_usage_path),
        recovery_history_store=RecoveryHistoryStore(active_usage_path),
        health_status_url_store=health_store,
        runtime_settings_snapshot=runtime_snapshot,
        on_runtime_settings_changed=apply_runtime_settings,
        validate_runtime_database=validate_runtime_database,
        ui_config=lambda: codex_ui_config(port, root),
        config_endpoint_name="codex-config",
    )
