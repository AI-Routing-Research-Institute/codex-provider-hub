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
from pathlib import Path
from typing import Any, Callable

# pythonw.exe does not provide standard streams, while Uvicorn initializes
# logging against them even when access logging is disabled.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import httpx

from local_proxy.core import (
    CONTROL_ASSET_DIR,
    DEFAULT_DATABASE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    LocalProxyServer,
    ProviderRouter,
    create_proxy_app,
    filter_self_referencing_providers,
)
from local_proxy.codex import load_proxy_providers
from local_proxy.codex_profile import (
    build_codex_profile,
    codex_config_fragment,
    codex_ui_config,
    load_settings as load_codex_settings,
)
from local_proxy.diagnostics import DiagnosticLog
from local_proxy.paths import display_path, resolve_user_path
from local_proxy.status_upload import StatusUploadManager
from local_proxy.shared_settings import (
    SharedRuntimeCoordinator,
    SharedSettingsStore,
    data_directory,
    default_shared_settings as default_settings,
    load_shared_settings as load_settings,
    migrate_runtime_data,
    save_shared_settings as save_settings,
    shared_settings_path as settings_path,
)
from local_proxy.version import resolve_app_version


APP_VERSION = "0.1.7"
APP_VERSION = resolve_app_version(APP_VERSION)
AUTO_START_VALUE_NAME = "CodexLocalProxy"
AUTO_START_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def legacy_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".config"
    return base / "CodexLocalProxy"


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
            str(Path(__file__).resolve().parents[1] / "local_proxy_app.py"),
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


def migrate_legacy_data_directory(
    source: Path | None = None,
    target: Path | None = None,
) -> tuple[str, ...]:
    destination = (target or data_directory()).expanduser().resolve()
    return migrate_runtime_data(
        target=destination,
        codex_source=destination,
        claude_source=Path.home() / ".claude-local-proxy",
        fallback_codex_source=(source or legacy_data_directory()),
    )


def existing_proxy_url(
    port: int,
    *,
    service_name: str = "codex-provider-hub",
) -> str | None:
    url = f"http://127.0.0.1:{port}"
    try:
        response = httpx.get(f"{url}/healthz", timeout=1.0)
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if response.status_code == 200 and payload.get("service") == service_name:
        return f"{url}/control/codex/"
    return None


def smoke_test(database: Path = DEFAULT_DATABASE) -> dict[str, Any]:
    required_assets = (
        "classic/index.html",
        "classic/app.js",
        "classic/styles.css",
        "dist/index.html",
    )
    missing_assets = [
        name for name in required_assets if not (CONTROL_ASSET_DIR / name).is_file()
    ]
    modern_assets_dir = CONTROL_ASSET_DIR / "dist" / "static" / "assets"
    modern_javascript = tuple(modern_assets_dir.glob("*.js"))
    modern_styles = tuple(modern_assets_dir.glob("*.css"))
    if not modern_javascript:
        missing_assets.append("dist/static/assets/*.js")
    if not modern_styles:
        missing_assets.append("dist/static/assets/*.css")
    if missing_assets:
        raise FileNotFoundError(
            "本地中转页面资源缺失：" + "、".join(missing_assets)
        )
    from local_proxy.claude import load_claude_proxy_providers
    tray_backend_available = True
    if os.name == "nt":
        import pystray
        from pystray import _win32

        tray_backend_available = pystray is not None and _win32 is not None
    from curl_cffi import Curl

    curl = Curl()
    curl.close()
    claude_transport_available = True
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
        "service_count": 1,
        "listen_address": f"{DEFAULT_HOST}:{DEFAULT_PORT}",
        "control_paths": ["/control/codex/", "/control/claude/"],
        "proxy_paths": {
            "codex": "/v1/*",
            "claude": ["/v1/messages", "/v1/messages/count_tokens"],
        },
        "control_asset_count": len(required_assets) + len(modern_javascript) + len(modern_styles),
        "control_ui_modes": ["classic", "modern"],
        "claude_provider_count": len(claude_providers),
        "claude_compatible_provider_count": sum(
            provider.compatible and provider.has_credentials
            for provider in claude_providers
        ),
        "tray_backend_available": tray_backend_available,
        "claude_transport_available": claude_transport_available,
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
    shared_settings_data: dict[str, Any] | None = None,
) -> int:
    existing = existing_proxy_url(port)
    if existing is not None:
        if open_browser:
            webbrowser.open(existing)
            webbrowser.open(f"http://127.0.0.1:{port}/control/claude/")
        return 0

    shared_settings = dict(shared_settings_data or load_settings())
    shared_settings["database_path"] = display_path(database)
    shared_store = SharedSettingsStore(settings=shared_settings)
    from local_proxy.claude_profile import load_settings as load_claude_settings

    codex_settings = load_codex_settings()
    claude_settings = load_claude_settings()
    status_upload_manager = StatusUploadManager()
    tray_holder: dict[str, Any] = {}
    server_holder: dict[str, LocalProxyServer] = {}

    def stop_application() -> None:
        active_server = server_holder.get("server")
        if active_server is not None:
            active_server.request_stop()
        icon = tray_holder.get("icon")
        if icon is not None:
            icon.stop()

    def _on_update_ready(path: Path) -> None:
        tray_holder["update_apply_path"] = str(path)
        stop_application()

    update_controller = UpdateController(
        current_version=APP_VERSION,
        supported=update_supported(),
        updates_dir=updates_directory(),
        on_ready=_on_update_ready,
    )

    codex_profile = build_codex_profile(
        database=database,
        port=port,
        settings_data=codex_settings,
        retry_policy_store=shared_store.retry_policy_store,
        health_status_url_store=shared_store.health_status_url_store,
        status_upload_manager=status_upload_manager,
    )
    from local_proxy.claude_profile import build_claude_profile

    claude_profile = build_claude_profile(
        database=database,
        port=port,
        settings_data=claude_settings,
        retry_policy_store=shared_store.retry_policy_store,
        health_status_url_store=shared_store.health_status_url_store,
        status_upload_manager=status_upload_manager,
    )
    SharedRuntimeCoordinator(
        shared_store,
        (codex_profile, claude_profile),
        active_port=port,
    )

    application = create_proxy_app(
        codex_profile=codex_profile,
        claude_profile=claude_profile,
        on_shutdown_requested=stop_application,
        update_controller=update_controller,
        diagnostic_log=DiagnosticLog(
            data_directory() / "logs" / "proxy-diagnostics.log"
        ),
    )
    server = LocalProxyServer(
        host=DEFAULT_HOST,
        port=port,
        application=application,
    )
    server_holder["server"] = server
    return run_local_proxy_server(
        server,
        codex_control_url=f"http://127.0.0.1:{port}/control/codex/",
        claude_control_url=f"http://127.0.0.1:{port}/control/claude/",
        open_browser=open_browser,
        tray=tray,
        tray_holder=tray_holder,
    )


def run_local_proxy_server(
    server: LocalProxyServer,
    *,
    codex_control_url: str,
    claude_control_url: str,
    open_browser: bool,
    tray: bool,
    tray_holder: dict[str, Any] | None = None,
) -> int:
    started = False
    active_tray_holder = tray_holder if tray_holder is not None else {}
    restart_requested = False
    try:
        server.start()
        started = True
        if open_browser:
            webbrowser.open(codex_control_url)
            webbrowser.open(claude_control_url)
        if tray:
            restart_requested = _run_tray(
                server,
                codex_control_url,
                active_tray_holder,
            )
        else:
            while server.running:
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if started:
            server.stop()
    update_apply_path = active_tray_holder.get("update_apply_path")
    if update_apply_path:
        launch_update_helper(Path(update_apply_path))
    elif restart_requested:
        launch_replacement_process()
    return 0


def _run_tray(
    server: LocalProxyServer,
    codex_control_url: str,
    tray_holder: dict[str, Any],
) -> bool:
    try:
        import pystray
    except ImportError as exc:
        raise RuntimeError("托盘模式需要安装 pystray 和 Pillow") from exc

    image = create_app_icon()
    restart_requested = threading.Event()
    update_state: dict[str, Any] = {"info": None, "busy": False}

    def _notify(icon: Any, message: str) -> None:
        try:
            icon.notify(message, "Codex 本地中转")
        except (AttributeError, NotImplementedError, OSError):
            pass

    def open_codex_console(icon: Any = None, item: Any = None) -> None:
        webbrowser.open(codex_control_url)

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

    def restart_proxy(icon: Any, item: Any = None) -> None:
        if restart_requested.is_set():
            return
        restart_requested.set()
        server.request_stop()
        icon.stop()

    def exit_proxy(icon: Any, item: Any = None) -> None:
        server.request_stop()
        icon.stop()

    def update_menu_text(item: Any = None) -> str:
        if update_state["busy"]:
            return "正在更新…"
        info = update_state["info"]
        if info is not None and info.has_update:
            return f"更新到 {info.latest_version}"
        return "检查更新"

    def update_menu_enabled(item: Any = None) -> bool:
        return not update_state["busy"]

    def _run_update_check(icon: Any, *, announce: bool) -> None:
        from local_proxy import updater

        try:
            info = updater.check_for_update(APP_VERSION)
        except updater.UpdateError as exc:
            if announce:
                _notify(icon, f"检查更新失败：{exc}")
            return
        update_state["info"] = info
        icon.update_menu()
        if info.has_update:
            _notify(icon, f"发现新版本 {info.latest_version}，点击托盘菜单更新。")
        elif announce:
            _notify(icon, "当前已是最新版本。")

    def _run_update_apply(icon: Any) -> None:
        from local_proxy import updater

        info = update_state["info"]
        if info is None or not info.has_update:
            return
        if not update_supported():
            webbrowser.open(info.release_url)
            _notify(icon, "当前平台请手动下载安装最新版本。")
            return
        update_state["busy"] = True
        icon.update_menu()
        try:
            new_executable = updater.download_asset(info, updates_directory())
        except updater.UpdateError as exc:
            update_state["busy"] = False
            icon.update_menu()
            _notify(icon, f"更新失败：{exc}")
            return
        tray_holder["update_apply_path"] = str(new_executable)
        _notify(icon, "更新已就绪，正在重启到新版本…")
        server.request_stop()
        icon.stop()

    def on_update_clicked(icon: Any, item: Any = None) -> None:
        if update_state["busy"]:
            return
        info = update_state["info"]
        if info is not None and info.has_update:
            threading.Thread(
                target=_run_update_apply, args=(icon,), daemon=True
            ).start()
        else:
            threading.Thread(
                target=_run_update_check,
                args=(icon,),
                kwargs={"announce": True},
                daemon=True,
            ).start()

    menu_items = [
        pystray.MenuItem(
            "默认打开 Codex 控制台",
            open_codex_console,
            default=True,
            visible=False,
        ),
        pystray.MenuItem("打开控制台", open_codex_console),
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
            pystray.MenuItem(
                update_menu_text,
                on_update_clicked,
                enabled=update_menu_enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("重启本地中转", restart_proxy),
            pystray.MenuItem("退出本地中转", exit_proxy),
        )
    )

    icon = pystray.Icon(
        "codex-local-proxy",
        image,
        "模型路由服务",
        menu=pystray.Menu(*menu_items),
    )
    tray_holder["icon"] = icon

    def _startup_check() -> None:
        time.sleep(3)
        _run_update_check(icon, announce=False)

    threading.Thread(target=_startup_check, daemon=True).start()
    icon.run()
    return restart_requested.is_set()


def launch_replacement_process() -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--no-browser"]
        working_directory = Path(sys.executable).resolve().parent
    else:
        script = Path(__file__).resolve().parents[1] / "local_proxy_app.py"
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


def update_supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def updates_directory() -> Path:
    return data_directory() / "updates"


class UpdateController:
    def __init__(
        self,
        *,
        current_version: str,
        supported: bool,
        updates_dir: Path,
        on_ready: Callable[[Path], None],
    ) -> None:
        self.current_version = current_version
        self.supported = supported
        self._updates_dir = updates_dir
        self._on_ready = on_ready
        self._lock = threading.Lock()
        self._last_info: Any = None
        self._downloaded: Path | None = None

    def status(self) -> dict[str, Any]:
        from local_proxy.updater import RELEASES_PAGE

        info = self._last_info
        payload: dict[str, Any] = {
            "supported": self.supported,
            "current_version": self.current_version,
            "has_update": bool(info is not None and info.has_update),
            "latest_version": info.latest_version if info is not None else None,
            "release_url": info.release_url if info is not None else RELEASES_PAGE,
            "notes": info.notes if info is not None else "",
        }
        return payload

    def check(self) -> dict[str, Any]:
        from local_proxy import updater

        info = updater.check_for_update(self.current_version)
        with self._lock:
            self._last_info = info
            self._downloaded = None
        return self.status()

    def download(self) -> Path:
        from local_proxy import updater

        with self._lock:
            info = self._last_info
        if info is None or not info.has_update:
            raise updater.UpdateError("请先检查更新")
        if not self.supported:
            raise updater.UpdateError("当前平台不支持就地更新")
        path = updater.download_asset(info, self._updates_dir)
        with self._lock:
            self._downloaded = path
        return path

    def finalize(self) -> None:
        with self._lock:
            path = self._downloaded
        if path is not None:
            self._on_ready(path)


def _spawn_detached(command: list[str], working_directory: Path) -> None:
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


def _process_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True
    import ctypes

    still_active = 259
    query_limited = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(query_limited, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def launch_update_helper(new_executable: Path) -> None:
    """Hand the running new-version binary the swap-then-relaunch job."""

    target = Path(sys.executable).resolve()
    command = [
        str(new_executable),
        "--finalize-update",
        "--target",
        str(target),
        "--wait-pid",
        str(os.getpid()),
    ]
    _spawn_detached(command, new_executable.parent)


def finalize_update(target: Path, wait_pid: int) -> int:
    """Wait for the old process to exit, swap in this binary, then relaunch it."""

    deadline = time.time() + 60
    while wait_pid > 0 and time.time() < deadline and _process_alive(wait_pid):
        time.sleep(0.5)
    source = Path(sys.executable).resolve()
    target = target.resolve()
    backup = target.with_name(target.name + ".bak")
    try:
        if target.exists():
            shutil.copy2(target, backup)
        shutil.copy2(source, target)
    except OSError as exc:
        if backup.exists():
            try:
                shutil.copy2(backup, target)
            except OSError:
                pass
        show_startup_error(f"更新失败，已保留原版本：{exc}")
        _relaunch_target(target)
        return 1
    _relaunch_target(target)
    backup.unlink(missing_ok=True)
    return 0


def _relaunch_target(target: Path) -> None:
    _spawn_detached([str(target), "--no-browser", "--tray"], target.parent)


def create_app_icon() -> Any:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("图标生成需要安装 Pillow") from exc
    canvas = 64
    margin = 3
    stroke_width = 3
    target = canvas - 2 * margin
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
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
        # Measure the stroked bbox at each size and keep the largest one that
        # fits the canvas margin, so the glyphs stay big without clipping on
        # any platform's font metrics.
        for font_size in range(target, 8, -1):
            try:
                trial = ImageFont.truetype(candidate, font_size)
            except OSError:
                break
            box = draw.textbbox((0, 0), "CX", font=trial, stroke_width=stroke_width)
            if box[2] - box[0] <= target and box[3] - box[1] <= target:
                font = trial
                break
        if font is not None:
            break
    if font is None:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), "CX", font=font, stroke_width=stroke_width)
    x = (canvas - (box[2] - box[0])) / 2 - box[0]
    y = (canvas - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "CX", font=font, fill="white", stroke_fill="#146c73", stroke_width=stroke_width)
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
    parser.add_argument("--finalize-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--target", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=APP_VERSION)
    args = parser.parse_args(argv)
    if args.finalize_update:
        if args.target is None:
            print("--finalize-update 需要 --target", file=sys.stderr)
            return 1
        return finalize_update(args.target, args.wait_pid or 0)
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
            shared_settings_data=settings,
        )
    except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
        show_startup_error(str(exc))
        return 1
