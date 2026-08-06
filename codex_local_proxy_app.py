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


APP_VERSION = "0.1.1"
SETTINGS_VERSION = 5
APP_DATA_DIRECTORY_NAME = ".codex-local-proxy"
AUTO_START_VALUE_NAME = "CodexLocalProxy"
AUTO_START_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


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


def auto_start_supported() -> bool:
    return os.name == "nt"


def auto_start_command() -> str:
    executable_path = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        command = [str(executable_path), "--no-browser"]
    else:
        pythonw_path = executable_path.with_name("pythonw.exe")
        if executable_path.name.casefold() == "python.exe" and pythonw_path.is_file():
            executable_path = pythonw_path
        command = [
            str(executable_path),
            str(Path(__file__).resolve()),
            "--tray",
            "--no-browser",
        ]
    return subprocess.list2cmdline(command)


def _read_auto_start_value() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            AUTO_START_RUN_KEY,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, AUTO_START_VALUE_NAME)
    except FileNotFoundError:
        return None
    if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return None
    return str(value)


def _write_auto_start_value(value: str) -> None:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        AUTO_START_RUN_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, AUTO_START_VALUE_NAME, 0, winreg.REG_SZ, value)


def _delete_auto_start_value() -> None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            AUTO_START_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, AUTO_START_VALUE_NAME)
    except FileNotFoundError:
        pass


def is_auto_start_enabled() -> bool:
    if not auto_start_supported():
        return False
    value = _read_auto_start_value()
    if not value:
        return False
    return os.path.expandvars(value).strip().casefold() == auto_start_command().casefold()


def set_auto_start_enabled(enabled: bool) -> None:
    if not auto_start_supported():
        raise RuntimeError("当前系统不支持 Windows 开机自启设置")
    if enabled:
        _write_auto_start_value(auto_start_command())
    else:
        _delete_auto_start_value()


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


def codex_ui_config(port: int, claude_port: int) -> dict[str, Any]:
    return {
        "service_id": "codex",
        "display_name": "Codex 本地中转",
        "brand_mark": "CX",
        "client_name": "Codex",
        "protocol_label": "Responses · SSE",
        "proxy_url": f"http://127.0.0.1:{port}/v1",
        "peer_console_label": "Claude Code 控制台",
        "peer_console_url": f"http://127.0.0.1:{claude_port}/control/",
        "config_endpoint": "/control/api/codex-config",
        "config_button_label": "复制 Codex 配置",
        "config_location_label": "Codex 配置文件",
        "config_location_hint": "“复制 Codex 配置”生成的片段需要合并到此文件",
        "data_directory": display_path(data_directory()),
        "config_location": "~/.codex/config.toml",
        "restart_config_text": "端口将在退出并重新启动本地中转后生效；届时需要重新复制 Codex 配置。",
        "copy_config_success_title": "Codex 配置已复制",
        "copy_config_success_detail": "首次配置后重启一次 Codex，后续切换不再需要重启。",
        "shutdown_client_name": "Codex",
        "provider_label": "Codex API",
        "theme_storage_key": "local-proxy-theme",
        "features": {"usage_history": True},
    }


def existing_proxy_url(
    port: int,
    *,
    service_name: str = "codex-local-proxy",
) -> str | None:
    url = f"http://127.0.0.1:{port}"
    try:
        response = httpx.get(f"{url}/healthz", timeout=1.0)
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if response.status_code == 200 and payload.get("service") == service_name:
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
    from claude_local_proxy import load_claude_proxy_providers
    tray_backend_available = True
    if os.name == "nt":
        import pystray
        from pystray import _win32

        tray_backend_available = pystray is not None and _win32 is not None
    from curl_cffi import Curl

    curl = Curl()
    curl.close()
    claude_curl_transport_available = True
    icon = create_app_icon()
    providers = filter_self_referencing_providers(
        load_proxy_providers(database), DEFAULT_PORT
    )
    router = ProviderRouter(providers)
    current = router.current_provider()
    claude_providers = load_claude_proxy_providers(database)
    return {
        "app_version": APP_VERSION,
        "provider_count": len(providers),
        "current_provider_configured": current is not None,
        "credential_count": sum(provider.has_credentials for provider in providers),
        "listen_address": f"{DEFAULT_HOST}:{DEFAULT_PORT}",
        "control_path": "/control/",
        "control_asset_count": len(asset_names),
        "claude_provider_count": len(claude_providers),
        "claude_compatible_provider_count": sum(
            provider.compatible and provider.has_credentials
            for provider in claude_providers
        ),
        "tray_backend_available": tray_backend_available,
        "claude_curl_transport_available": claude_curl_transport_available,
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
    open_browser: bool = False,
    tray: bool = False,
) -> int:
    from claude_local_proxy_app import load_settings as load_claude_settings

    claude_settings = load_claude_settings()
    claude_port = int(claude_settings["port"])
    existing = existing_proxy_url(port)
    existing_claude = existing_proxy_url(
        claude_port,
        service_name="claude-local-proxy",
    )
    if existing is not None and existing_claude is not None:
        if open_browser:
            webbrowser.open(existing)
            webbrowser.open(existing_claude)
        return 0
    if existing is not None or existing_claude is not None:
        raise RuntimeError(
            "检测到旧版或不完整的本地中转实例，请先从托盘退出旧版本地中转后重新启动。"
        )

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
    hub_servers: list[LocalProxyServer] = []

    def stop_hub() -> None:
        for active_server in tuple(hub_servers):
            active_server.request_stop()
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
        on_shutdown_requested=stop_hub,
        usage_store=usage_store,
        recovery_history_store=recovery_history_store,
        health_status_url_store=health_status_url_store,
        runtime_settings_snapshot=runtime_settings_snapshot,
        on_runtime_settings_changed=apply_runtime_settings,
        validate_runtime_database=validate_runtime_database,
        ui_config=lambda: codex_ui_config(port, claude_port),
    )
    from claude_local_proxy_app import build_claude_server

    claude_database = resolve_user_path(
        claude_settings.get("database_path") or str(database)
    )
    claude_server = build_claude_server(
        database=claude_database,
        port=claude_port,
        codex_port=port,
        on_shutdown_requested=stop_hub,
    )
    hub_servers.extend((server, claude_server))
    return run_hub_servers(
        server,
        claude_server,
        codex_control_url=f"http://127.0.0.1:{port}/control/",
        claude_control_url=f"http://127.0.0.1:{claude_port}/control/",
        open_browser=open_browser,
        tray=tray,
        tray_holder=tray_holder,
    )


def run_hub_servers(
    codex_server: LocalProxyServer,
    claude_server: LocalProxyServer,
    *,
    codex_control_url: str,
    claude_control_url: str,
    open_browser: bool,
    tray: bool,
    tray_holder: dict[str, Any] | None = None,
) -> int:
    servers = (codex_server, claude_server)
    started: list[LocalProxyServer] = []
    active_tray_holder = tray_holder if tray_holder is not None else {}
    restart_requested = False
    try:
        for server in servers:
            server.start()
            started.append(server)
        if open_browser:
            webbrowser.open(codex_control_url)
            webbrowser.open(claude_control_url)
        if tray:
            restart_requested = _run_tray(
                servers,
                codex_control_url,
                claude_control_url,
                active_tray_holder,
            )
        else:
            while any(server.running for server in servers):
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for server in reversed(started):
            server.stop()
    if restart_requested:
        launch_replacement_process()
    return 0


def _run_tray(
    servers: tuple[LocalProxyServer, LocalProxyServer],
    codex_control_url: str,
    claude_control_url: str,
    tray_holder: dict[str, Any],
) -> bool:
    try:
        import pystray
    except ImportError as exc:
        raise RuntimeError("托盘模式需要安装 pystray 和 Pillow") from exc

    image = create_app_icon()
    restart_requested = threading.Event()

    def open_codex_console(icon: Any = None, item: Any = None) -> None:
        webbrowser.open(codex_control_url)

    def open_claude_console(icon: Any = None, item: Any = None) -> None:
        webbrowser.open(claude_control_url)

    def auto_start_checked(item: Any = None) -> bool:
        try:
            return is_auto_start_enabled()
        except OSError:
            return False

    def toggle_auto_start(icon: Any, item: Any = None) -> None:
        try:
            enabled = not is_auto_start_enabled()
            set_auto_start_enabled(enabled)
            icon.update_menu()
            try:
                icon.notify(
                    "开机自启已开启。" if enabled else "开机自启已关闭。",
                    "Codex 本地中转",
                )
            except (AttributeError, NotImplementedError, OSError):
                pass
        except (OSError, RuntimeError) as exc:
            show_startup_error(f"修改开机自启失败：{exc}")

    def stop_servers() -> None:
        for server in servers:
            server.request_stop()

    def restart_proxy(icon: Any, item: Any = None) -> None:
        if restart_requested.is_set():
            return
        restart_requested.set()
        stop_servers()
        icon.stop()

    def exit_proxy(icon: Any, item: Any = None) -> None:
        stop_servers()
        icon.stop()

    menu_items = [
        pystray.MenuItem("打开 Codex 控制台", open_codex_console, default=True),
        pystray.MenuItem("打开 Claude Code 控制台", open_claude_console),
        pystray.Menu.SEPARATOR,
    ]
    if auto_start_supported():
        menu_items.extend(
            (
                pystray.MenuItem(
                    "开机自启",
                    toggle_auto_start,
                    checked=auto_start_checked,
                ),
                pystray.Menu.SEPARATOR,
            )
        )
    menu_items.extend(
        (
            pystray.MenuItem("重启本地中转", restart_proxy),
            pystray.MenuItem("退出本地中转", exit_proxy),
        )
    )

    icon = pystray.Icon(
        "codex-local-proxy",
        image,
        "Codex 与 Claude Code 本地中转",
        menu=pystray.Menu(*menu_items),
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
    # "segoeuib.ttf" only resolves on Windows; pick a platform-appropriate
    # bold font so the tray/bundle icon keeps crisp glyphs on every OS.
    font_candidates = (
        ["segoeuib.ttf", "segoeui.ttf"]
        if sys.platform == "win32"
        else ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc"]
    )
    font = None
    for candidate in font_candidates:
        try:
            font = ImageFont.truetype(candidate, 26)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), "CX", font=font, stroke_width=1)
    x = (64 - (box[2] - box[0])) / 2
    y = (64 - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "CX", font=font, fill="white", stroke_width=1)
    return image


def write_app_icon(path: Path) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    # The .ico container is Windows-specific; macOS app bundles use .icns.
    # Decide the output format from the file extension when it is explicit,
    # otherwise fall back to the running platform's native container.
    if suffix == ".icns":
        image_format = "ICNS"
    elif suffix == ".ico":
        image_format = "ICO"
    elif sys.platform == "darwin":
        image_format = "ICNS"
    else:
        image_format = "ICO"
    create_app_icon().save(target, format=image_format, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex CC Switch 本地中转")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--port", type=int)
    browser_group = parser.add_mutually_exclusive_group()
    browser_group.add_argument("--open-browser", action="store_true")
    browser_group.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
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
            open_browser=args.open_browser,
            tray=args.tray or bool(getattr(sys, "frozen", False)),
        )
    except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
        show_startup_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
