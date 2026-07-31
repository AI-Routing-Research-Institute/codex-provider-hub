from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import closing
from pathlib import Path
from typing import Any

# pythonw.exe does not provide standard streams, while Uvicorn initializes
# logging against them even when access logging is disabled.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import httpx

from codex_local_proxy import (
    CONTROL_ASSET_DIR,
    DEFAULT_DATABASE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    HealthStatusUrlStore,
    LocalProxyServer,
    ProviderRouter,
    RecoveryHistoryStore,
    RetryPolicy,
    RetryPolicyStore,
    UsageStore,
    filter_self_referencing_providers,
    load_proxy_providers,
    normalize_health_status_url,
    order_proxy_providers,
    retry_policy_from_mapping,
)


APP_VERSION = "0.1.0"
SETTINGS_VERSION = 5
APP_DATA_DIRECTORY_NAME = ".codex-local-proxy"


def data_directory() -> Path:
    return Path.home() / APP_DATA_DIRECTORY_NAME


def settings_path() -> Path:
    return data_directory() / "settings.json"


def legacy_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".config"
    return base / "CodexLocalProxy"


def usage_database_path() -> Path:
    return data_directory() / "usage.sqlite3"


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(Path.home().resolve())
    except ValueError:
        return str(resolved)
    return "~" if not relative.parts else f"~/{relative.as_posix()}"


def resolve_user_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def migrate_legacy_data_directory(
    source: Path | None = None,
    target: Path | None = None,
) -> tuple[str, ...]:
    legacy = (source or legacy_data_directory()).expanduser().resolve()
    destination = (target or data_directory()).expanduser().resolve()
    if legacy == destination or not legacy.is_dir():
        return ()

    migrated: list[str] = []
    for name in ("settings.json", "usage.sqlite3"):
        legacy_file = legacy / name
        destination_file = destination / name
        if not legacy_file.is_file() or destination_file.exists():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        temporary = destination / f".{name}.migrating"
        temporary.unlink(missing_ok=True)
        try:
            if name == "usage.sqlite3":
                source_uri = legacy_file.resolve().as_uri() + "?mode=ro"
                with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
                    with closing(sqlite3.connect(temporary)) as destination_db:
                        source_db.backup(destination_db)
            else:
                shutil.copy2(legacy_file, temporary)
            temporary.replace(destination_file)
        finally:
            temporary.unlink(missing_ok=True)
        migrated.append(name)
    return tuple(migrated)


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


def existing_proxy_url(port: int) -> str | None:
    url = f"http://127.0.0.1:{port}"
    try:
        response = httpx.get(f"{url}/healthz", timeout=1.0)
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if response.status_code == 200 and payload.get("service") == "codex-local-proxy":
        return f"{url}/control/"
    return None


def smoke_test(database: Path = DEFAULT_DATABASE) -> dict[str, Any]:
    asset_names = ("index.html", "app.js", "styles.css")
    missing_assets = [
        name for name in asset_names if not (CONTROL_ASSET_DIR / name).is_file()
    ]
    if missing_assets:
        raise FileNotFoundError(
            "本地中转页面资源缺失：" + "、".join(missing_assets)
        )
    icon = create_app_icon()
    providers = filter_self_referencing_providers(
        load_proxy_providers(database), DEFAULT_PORT
    )
    router = ProviderRouter(providers)
    current = router.current_provider()
    return {
        "app_version": APP_VERSION,
        "provider_count": len(providers),
        "current_provider_configured": current is not None,
        "credential_count": sum(provider.has_credentials for provider in providers),
        "listen_address": f"{DEFAULT_HOST}:{DEFAULT_PORT}",
        "control_path": "/control/",
        "control_asset_count": len(asset_names),
        "icon_size": list(icon.size),
    }


def show_startup_error(message: str) -> None:
    print(message, file=sys.stderr)
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "Codex 本地中转",
            0x10,
        )
    except (AttributeError, OSError):
        pass


def run_application(
    *,
    database: Path,
    port: int,
    open_browser: bool = True,
    tray: bool = False,
) -> int:
    existing = existing_proxy_url(port)
    if existing is not None:
        if open_browser:
            webbrowser.open(existing)
        return 0

    settings = load_settings()
    settings_lock = threading.RLock()
    active_database_path = database.expanduser().resolve()

    def load_prepared_providers(source: Path) -> tuple:
        loaded = filter_self_referencing_providers(
            load_proxy_providers(source),
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
    router = ProviderRouter(
        providers,
        current_provider_id=settings.get("selected_provider_id"),
    )
    retry_policy_store = RetryPolicyStore(
        retry_policy_from_mapping(settings.get("retry", {}))
    )
    health_status_url_store = HealthStatusUrlStore(
        settings.get("health_status_url")
    )
    local_database_path = usage_database_path()
    usage_store = UsageStore(local_database_path)
    recovery_history_store = RecoveryHistoryStore(local_database_path)

    def update_settings(**changes: Any) -> None:
        with settings_lock:
            settings.update(changes)
            settings["schema_version"] = SETTINGS_VERSION
            save_settings(settings)

    def remember_selection(provider_id: str) -> None:
        update_settings(selected_provider_id=provider_id)

    def remember_retry_policy(policy: RetryPolicy) -> None:
        update_settings(retry=policy.as_public_dict())

    def remember_hidden_provider_ids(provider_ids: tuple[str, ...]) -> None:
        update_settings(hidden_provider_ids=list(provider_ids))

    def remember_provider_order(provider_ids: tuple[str, ...]) -> None:
        update_settings(provider_order=list(provider_ids))

    def runtime_settings_snapshot() -> dict[str, Any]:
        with settings_lock:
            configured_port = int(settings.get("port", port))
            database_display = display_path(active_database_path)
        return {
            "configured_port": configured_port,
            "active_port": port,
            "restart_required": configured_port != port,
            "database_path": database_display,
            "health_status_url": health_status_url_store.get(),
            "data_directory": display_path(data_directory()),
            "settings_file": display_path(settings_path()),
            "usage_database": display_path(usage_database_path()),
            "codex_config_file": "~/.codex/config.toml",
        }

    def validate_database_source(value: str) -> tuple[Path, tuple]:
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
        source, loaded = validate_database_source(value)
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
        health_status_url = normalize_health_status_url(
            payload.get("health_status_url")
        )
        source, loaded = validate_database_source(database_value)

        with settings_lock:
            candidate = dict(settings)
            candidate.update(
                schema_version=SETTINGS_VERSION,
                port=configured_port,
                database_path=display_path(source),
                health_status_url=health_status_url,
            )
            save_settings(candidate)
            settings.clear()
            settings.update(candidate)
            active_database_path = source

        health_status_url_store.replace(health_status_url)
        current = router.current_provider()
        selected = router.replace_providers(
            loaded,
            preferred_id=current.provider_id if current else None,
        )
        if selected is not None and selected.provider_id != settings.get(
            "selected_provider_id"
        ):
            update_settings(selected_provider_id=selected.provider_id)
        return runtime_settings_snapshot()

    tray_holder: dict[str, Any] = {}

    def stop_tray() -> None:
        icon = tray_holder.get("icon")
        if icon is not None:
            icon.stop()

    server = LocalProxyServer(
        router,
        host=DEFAULT_HOST,
        port=port,
        reload_providers=prepared_providers,
        on_provider_selected=remember_selection,
        hidden_provider_ids=settings.get("hidden_provider_ids", ()),
        provider_order=settings.get("provider_order", ()),
        on_hidden_provider_ids_changed=remember_hidden_provider_ids,
        on_provider_order_changed=remember_provider_order,
        config_fragment=lambda: codex_config_fragment(port),
        retry_policy_store=retry_policy_store,
        on_retry_policy_changed=remember_retry_policy,
        on_shutdown_requested=stop_tray if tray else None,
        usage_store=usage_store,
        recovery_history_store=recovery_history_store,
        health_status_url_store=health_status_url_store,
        runtime_settings_snapshot=runtime_settings_snapshot,
        on_runtime_settings_changed=apply_runtime_settings,
        validate_runtime_database=validate_runtime_database,
    )
    server.start()
    control_url = f"http://127.0.0.1:{port}/control/"
    if open_browser:
        webbrowser.open(control_url)
    restart_requested = False
    try:
        if tray:
            restart_requested = _run_tray(server, control_url, tray_holder)
        else:
            while server.running:
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    if restart_requested:
        launch_replacement_process()
    return 0


def _run_tray(
    server: LocalProxyServer,
    control_url: str,
    tray_holder: dict[str, Any],
) -> bool:
    try:
        import pystray
    except ImportError as exc:
        raise RuntimeError("托盘模式需要安装 pystray 和 Pillow") from exc

    image = create_app_icon()
    restart_requested = threading.Event()

    def open_console(icon: Any = None, item: Any = None) -> None:
        webbrowser.open(control_url)

    def restart_proxy(icon: Any, item: Any = None) -> None:
        if restart_requested.is_set():
            return
        restart_requested.set()
        server.request_stop()
        icon.stop()

    def exit_proxy(icon: Any, item: Any = None) -> None:
        server.request_stop()
        icon.stop()

    icon = pystray.Icon(
        "codex-local-proxy",
        image,
        "Codex 本地中转",
        menu=pystray.Menu(
            pystray.MenuItem("打开控制台", open_console, default=True),
            pystray.MenuItem("重启本地中转", restart_proxy),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出本地中转", exit_proxy),
        ),
    )
    tray_holder["icon"] = icon
    icon.run()
    return restart_requested.is_set()


def launch_replacement_process() -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--no-browser"]
        working_directory = Path(sys.executable).resolve().parent
    else:
        script = Path(__file__).resolve()
        command = [sys.executable, str(script), "--tray", "--no-browser"]
        working_directory = script.parent

    options: dict[str, Any] = {
        "cwd": str(working_directory),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def create_app_icon() -> Any:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("图标生成需要安装 Pillow") from exc
    image = Image.new("RGBA", (64, 64), "#146c73")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=12, fill="#146c73")
    try:
        font = ImageFont.truetype("segoeuib.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), "CX", font=font, stroke_width=1)
    x = (64 - (box[2] - box[0])) / 2
    y = (64 - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "CX", font=font, fill="white", stroke_width=1)
    return image


def write_app_icon(path: Path) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    create_app_icon().save(target, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex CC Switch 本地中转")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--tray", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--write-icon", type=Path)
    parser.add_argument("--version", action="version", version=APP_VERSION)
    args = parser.parse_args(argv)
    if args.write_icon is not None:
        try:
            write_app_icon(args.write_icon)
        except (OSError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if args.smoke_test:
        try:
            result = smoke_test(args.database or DEFAULT_DATABASE)
        except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    try:
        migrate_legacy_data_directory()
    except (OSError, sqlite3.Error) as exc:
        show_startup_error(f"无法迁移旧版本地数据：{exc}")
        return 1
    settings = load_settings()
    port = args.port if args.port is not None else int(settings["port"])
    database = args.database or resolve_user_path(settings["database_path"])
    try:
        return run_application(
            database=database,
            port=port,
            open_browser=not args.no_browser,
            tray=args.tray or bool(getattr(sys, "frozen", False)),
        )
    except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
        show_startup_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
