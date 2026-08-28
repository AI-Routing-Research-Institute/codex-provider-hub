"""Shared runtime settings and data migration for the unified local proxy."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from local_proxy.core import (
    DEFAULT_DATABASE,
    DEFAULT_PORT,
    HealthStatusUrlStore,
    RetryPolicy,
    RetryPolicyStore,
    normalize_health_status_url,
    retry_policy_from_mapping,
)
from local_proxy.paths import display_path, resolve_user_path


APP_DATA_DIRECTORY_NAME = ".codex-local-proxy"
SHARED_SETTINGS_VERSION = 1
PROTOCOL_SETTINGS_VERSION = 1
SERVICE_IDS = ("codex", "claude")


def data_directory() -> Path:
    return Path.home() / APP_DATA_DIRECTORY_NAME


def shared_settings_path(root: Path | None = None) -> Path:
    return (root or data_directory()) / "shared-settings.json"


def protocol_settings_path(service_id: str, root: Path | None = None) -> Path:
    _validate_service_id(service_id)
    return (root or data_directory()) / f"{service_id}-settings.json"


def protocol_usage_database_path(service_id: str, root: Path | None = None) -> Path:
    _validate_service_id(service_id)
    return (root or data_directory()) / f"{service_id}-usage.sqlite3"


def protocol_provider_catalog_path(service_id: str, root: Path | None = None) -> Path:
    _validate_service_id(service_id)
    return (root or data_directory()) / f"{service_id}-providers.sqlite3"


def _validate_service_id(service_id: str) -> None:
    if service_id not in SERVICE_IDS:
        raise ValueError(f"未知协议：{service_id}")


def default_shared_settings() -> dict[str, Any]:
    return {
        "schema_version": SHARED_SETTINGS_VERSION,
        "port": DEFAULT_PORT,
        "database_path": display_path(DEFAULT_DATABASE),
        "retry": RetryPolicy().as_public_dict(),
        "health_status_url": None,
    }


def default_protocol_settings() -> dict[str, Any]:
    return {
        "schema_version": PROTOCOL_SETTINGS_VERSION,
        "selected_provider_id": None,
        "provider_order": [],
        "hidden_provider_ids": [],
        "session_provider_overrides": {},
        "show_provider_launch_command": True,
        "show_status_upload": True,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_shared_settings(path: Path | None = None) -> dict[str, Any]:
    target = path or shared_settings_path()
    payload = _read_json_object(target)
    settings = default_shared_settings()

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
        settings["retry"] = retry_policy_from_mapping(
            payload.get("retry", {})
        ).as_public_dict()
    except (TypeError, ValueError):
        pass

    try:
        settings["health_status_url"] = normalize_health_status_url(
            payload.get("health_status_url")
        )
    except ValueError:
        pass
    return settings


def _shared_settings_from_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    settings = default_shared_settings()
    port = payload.get("port")
    if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
        settings["port"] = port
    database_path = payload.get("database_path")
    if isinstance(database_path, str) and database_path.strip():
        settings["database_path"] = database_path.strip()
    try:
        settings["retry"] = retry_policy_from_mapping(
            payload.get("retry", {})
        ).as_public_dict()
    except (TypeError, ValueError):
        pass
    try:
        settings["health_status_url"] = normalize_health_status_url(
            payload.get("health_status_url")
        )
    except ValueError:
        pass
    return settings


def save_shared_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    _write_json_object(path or shared_settings_path(), _shared_settings_from_mapping(settings))


def load_protocol_settings(path: Path) -> dict[str, Any]:
    payload = _read_json_object(path)
    settings = default_protocol_settings()
    selected = payload.get("selected_provider_id")
    if isinstance(selected, str) and selected.strip():
        settings["selected_provider_id"] = selected.strip()
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
    overrides = payload.get("session_provider_overrides")
    if isinstance(overrides, dict):
        settings["session_provider_overrides"] = {
            thread_id: provider_id.strip()
            for thread_id, provider_id in list(overrides.items())[:1000]
            if isinstance(thread_id, str)
            and 1 <= len(thread_id) <= 256
            and isinstance(provider_id, str)
            and provider_id.strip()
        }
    show_launch_command = payload.get("show_provider_launch_command")
    if isinstance(show_launch_command, bool):
        settings["show_provider_launch_command"] = show_launch_command
    show_status_upload = payload.get("show_status_upload")
    if isinstance(show_status_upload, bool):
        settings["show_status_upload"] = show_status_upload
    return settings


def save_protocol_settings(settings: dict[str, Any], path: Path) -> None:
    normalized = default_protocol_settings()
    selected = settings.get("selected_provider_id")
    if isinstance(selected, str) and selected.strip():
        normalized["selected_provider_id"] = selected.strip()
    for field_name in ("provider_order", "hidden_provider_ids"):
        values = settings.get(field_name)
        if isinstance(values, Iterable) and not isinstance(values, (str, bytes)):
            normalized[field_name] = list(
                dict.fromkeys(
                    value.strip()
                    for value in values
                    if isinstance(value, str) and value.strip()
                )
            )
    overrides = settings.get("session_provider_overrides")
    if isinstance(overrides, dict):
        normalized["session_provider_overrides"] = {
            thread_id: provider_id.strip()
            for thread_id, provider_id in list(overrides.items())[-1000:]
            if isinstance(thread_id, str)
            and 1 <= len(thread_id) <= 256
            and isinstance(provider_id, str)
            and provider_id.strip()
        }
    show_launch_command = settings.get("show_provider_launch_command")
    if isinstance(show_launch_command, bool):
        normalized["show_provider_launch_command"] = show_launch_command
    show_status_upload = settings.get("show_status_upload")
    if isinstance(show_status_upload, bool):
        normalized["show_status_upload"] = show_status_upload
    _write_json_object(path, normalized)


class SharedSettingsStore:
    """Thread-safe shared settings and stores used by both profiles."""

    def __init__(
        self,
        path: Path | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.path = path or shared_settings_path()
        self._lock = threading.RLock()
        self._settings = (
            _shared_settings_from_mapping(settings)
            if settings is not None
            else load_shared_settings(self.path)
        )
        self.retry_policy_store = RetryPolicyStore(
            retry_policy_from_mapping(self._settings["retry"])
        )
        self.health_status_url_store = HealthStatusUrlStore(
            self._settings["health_status_url"]
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._settings,
                "retry": dict(self._settings["retry"]),
            }

    def replace_runtime(
        self,
        *,
        port: int,
        database_path: Path,
        health_status_url: str | None,
    ) -> None:
        with self._lock:
            candidate = dict(self._settings)
            candidate.update(
                port=port,
                database_path=display_path(database_path),
                health_status_url=health_status_url,
            )
            save_shared_settings(candidate, self.path)
            self._settings = _shared_settings_from_mapping(candidate)
            self.health_status_url_store.replace(health_status_url)

    def replace_retry(self, policy: RetryPolicy) -> None:
        with self._lock:
            candidate = dict(self._settings, retry=policy.as_public_dict())
            try:
                save_shared_settings(candidate, self.path)
            except Exception:
                self.retry_policy_store.replace(
                    retry_policy_from_mapping(self._settings["retry"])
                )
                raise
            self._settings = _shared_settings_from_mapping(candidate)
            self.retry_policy_store.replace(policy)


class SharedRuntimeCoordinator:
    """Apply shared runtime changes to both protocol profiles."""

    def __init__(
        self,
        settings_store: SharedSettingsStore,
        profiles: Iterable[Any],
        *,
        active_port: int,
    ) -> None:
        self.settings_store = settings_store
        self.active_port = active_port
        self._lock = threading.RLock()
        self.profiles = {profile.service_id: profile for profile in profiles}
        if set(self.profiles) != set(SERVICE_IDS):
            raise ValueError("共享运行设置必须同时注册 Codex 和 Claude")
        for service_id, profile in self.profiles.items():
            if (
                profile.load_runtime_database is None
                or profile.apply_runtime_database is None
                or profile.runtime_metadata is None
            ):
                raise ValueError(f"{service_id} Profile 缺少共享运行设置回调")
            profile.retry_policy_store = settings_store.retry_policy_store
            profile.health_status_url_store = settings_store.health_status_url_store
            profile.on_retry_policy_changed = settings_store.replace_retry
            profile.runtime_settings_snapshot = (
                lambda current_service_id=service_id: self.snapshot(current_service_id)
            )
            profile.on_runtime_settings_changed = (
                lambda payload, current_service_id=service_id: self.apply_runtime_settings(
                    current_service_id,
                    payload,
                )
            )
            profile.validate_runtime_database = (
                lambda value, current_service_id=service_id: self.validate_runtime_database(
                    current_service_id,
                    value,
                )
            )

    def snapshot(self, service_id: str) -> dict[str, Any]:
        profile = self.profiles[service_id]
        settings = self.settings_store.snapshot()
        return {
            "configured_port": settings["port"],
            "active_port": self.active_port,
            "restart_required": settings["port"] != self.active_port,
            "database_path": settings["database_path"],
            "health_status_url": settings["health_status_url"],
            **dict(profile.runtime_metadata()),
            "shared_settings_file": display_path(self.settings_store.path),
        }

    def _prepare_database(
        self,
        value: str,
    ) -> tuple[Path, dict[str, tuple[Any, ...]]]:
        source = resolve_user_path(value.strip())
        if not source.is_file():
            raise ValueError(f"未找到 CC Switch 数据库：{display_path(source)}")
        loaded: dict[str, tuple[Any, ...]] = {}
        try:
            for service_id, profile in self.profiles.items():
                loaded[service_id] = profile.load_runtime_database(source)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise ValueError("无法读取 CC Switch 数据库或数据库结构不兼容") from exc
        return source, loaded

    def validate_runtime_database(self, service_id: str, value: str) -> dict[str, Any]:
        source, loaded = self._prepare_database(value)
        summaries: dict[str, dict[str, Any]] = {}
        for current_service_id, providers in loaded.items():
            profile = self.profiles[current_service_id]
            summary = (
                dict(profile.database_validation_summary(providers))
                if profile.database_validation_summary is not None
                else {"provider_count": len(providers)}
            )
            summaries[current_service_id] = summary
        return {
            "database_path": display_path(source),
            **summaries[service_id],
            "protocols": summaries,
        }

    def apply_runtime_settings(
        self,
        service_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        profile = self.profiles[service_id]
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
        if (
            getattr(profile, "apply_runtime_preferences", None) is not None
            and "show_provider_launch_command" in payload
            and not isinstance(payload["show_provider_launch_command"], bool)
        ):
            raise ValueError("临时启动命令显示设置必须是布尔值")
        if (
            getattr(profile, "apply_runtime_preferences", None) is not None
            and "show_status_upload" in payload
            and not isinstance(payload["show_status_upload"], bool)
        ):
            raise ValueError("上传检测显示设置必须是布尔值")
        source, loaded = self._prepare_database(database_value)
        with self._lock:
            self.settings_store.replace_runtime(
                port=configured_port,
                database_path=source,
                health_status_url=health_url,
            )
            for current_service_id, providers in loaded.items():
                self.profiles[current_service_id].apply_runtime_database(source, providers)
            apply_preferences = getattr(profile, "apply_runtime_preferences", None)
            if apply_preferences is not None:
                apply_preferences(payload)
        return self.snapshot(service_id)


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".migrating")
    temporary.unlink(missing_ok=True)
    try:
        source_uri = source.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
            with closing(sqlite3.connect(temporary)) as destination_db:
                source_db.backup(destination_db)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_legacy_file(source: Path, *, sqlite_database: bool = False) -> None:
    source.unlink(missing_ok=True)
    if not sqlite_database:
        return
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        sidecar.unlink(missing_ok=True)


def _remove_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except (FileNotFoundError, OSError):
        pass


def migrate_runtime_data(
    *,
    target: Path | None = None,
    codex_source: Path | None = None,
    claude_source: Path | None = None,
    fallback_codex_source: Path | None = None,
) -> tuple[str, ...]:
    """Migrate legacy Codex/Claude files without overwriting new destinations."""

    destination = (target or data_directory()).expanduser().resolve()
    codex_root = (codex_source or destination).expanduser().resolve()
    claude_root = (
        claude_source or (Path.home() / ".claude-local-proxy")
    ).expanduser().resolve()
    fallback_root = (
        fallback_codex_source.expanduser().resolve()
        if fallback_codex_source is not None
        else None
    )

    codex_settings_source = codex_root / "settings.json"
    codex_usage_source = codex_root / "usage.sqlite3"
    if not codex_settings_source.is_file() and fallback_root is not None:
        codex_settings_source = fallback_root / "settings.json"
    if not codex_usage_source.is_file() and fallback_root is not None:
        codex_usage_source = fallback_root / "usage.sqlite3"
    claude_settings_source = claude_root / "settings.json"
    claude_usage_source = claude_root / "usage.sqlite3"
    codex_payload = _read_json_object(codex_settings_source)
    claude_payload = _read_json_object(claude_settings_source)
    migrated: list[str] = []

    shared_target = shared_settings_path(destination)
    if not shared_target.exists():
        shared_payload = dict(claude_payload)
        shared_payload.update(codex_payload)
        save_shared_settings(shared_payload, shared_target)
        migrated.append(shared_target.name)

    for service_id, payload in (("codex", codex_payload), ("claude", claude_payload)):
        target_path = protocol_settings_path(service_id, destination)
        if target_path.exists():
            continue
        save_protocol_settings(payload, target_path)
        migrated.append(target_path.name)

    for service_id, source in (
        ("codex", codex_usage_source),
        ("claude", claude_usage_source),
    ):
        target_path = protocol_usage_database_path(service_id, destination)
        if not source.is_file() or target_path.exists():
            continue
        _sqlite_backup(source, target_path)
        migrated.append(target_path.name)

    if shared_target.exists() and protocol_settings_path("codex", destination).exists():
        _remove_legacy_file(codex_settings_source)
    if shared_target.exists() and protocol_settings_path("claude", destination).exists():
        _remove_legacy_file(claude_settings_source)
    if protocol_usage_database_path("codex", destination).exists():
        _remove_legacy_file(codex_usage_source, sqlite_database=True)
    if protocol_usage_database_path("claude", destination).exists():
        _remove_legacy_file(claude_usage_source, sqlite_database=True)

    # Remove backups created by the short-lived intermediate migration format.
    archive_root = destination / "legacy" / "codex"
    _remove_legacy_file(archive_root / "settings.json")
    _remove_legacy_file(archive_root / "usage.sqlite3", sqlite_database=True)
    _remove_empty_directory(archive_root)
    _remove_empty_directory(destination / "legacy")

    for legacy_root in {codex_settings_source.parent, codex_usage_source.parent, claude_root}:
        if legacy_root != destination:
            _remove_empty_directory(legacy_root)
    return tuple(migrated)
