import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from local_proxy import application as local_proxy_app

from local_proxy.application import (
    codex_config_fragment,
    codex_ui_config,
    data_directory,
    default_settings,
    display_path,
    load_settings,
    migrate_legacy_data_directory,
    save_settings,
    settings_path,
)
from local_proxy.codex import codex_cli_launch_command
from local_proxy.core import ProviderConfigurationError, ProxyProvider
from local_proxy.codex_profile import load_settings as load_codex_settings
from local_proxy.shared_settings import (
    migrate_runtime_data,
    protocol_provider_catalog_path,
    protocol_settings_path,
    protocol_usage_database_path,
)


class LocalProxySettingsTests(unittest.TestCase):
    def test_ui_config_uses_unified_port_and_peer_console_path(self) -> None:
        config = codex_ui_config(19000)
        self.assertEqual(config["config_button_label"], "导入到 CCS")
        self.assertEqual(config["proxy_url"], "http://127.0.0.1:19000/v1")
        self.assertEqual(config["peer_console_url"], "http://127.0.0.1:19000/control/claude/")
        self.assertEqual(config["config_endpoint"], "/control/codex/api/codex-config")
        self.assertTrue(config["features"]["provider_launch_command"])
        self.assertTrue(config["features"]["provider_catalog"])

    def test_shared_settings_round_trip_and_corrupt_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "shared-settings.json"
            settings = default_settings()
            settings["port"] = 18888
            settings["database_path"] = str(Path(temp_dir) / "cc-switch.db")
            settings["retry"]["max_attempts"] = -1
            settings["health_status_url"] = (
                "https://status.example.test/api/status?window=24h"
            )
            save_settings(settings, path)

            self.assertEqual(load_settings(path)["port"], 18888)
            self.assertEqual(
                load_settings(path)["database_path"],
                display_path(Path(temp_dir) / "cc-switch.db"),
            )
            self.assertEqual(load_settings(path)["retry"]["max_attempts"], -1)
            self.assertEqual(
                load_settings(path)["health_status_url"],
                "https://status.example.test/api/status?window=24h",
            )
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(load_settings(path), default_settings())

    def test_invalid_settings_values_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "selected_provider_id": "",
                        "port": 70000,
                        "health_status_url": "file:///private/status.json",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(load_settings(path), default_settings())

    def test_codex_provider_preferences_are_trimmed_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "provider_order": [" second ", "first", "second", 1],
                        "hidden_provider_ids": [" hidden ", "", "hidden"],
                        "session_provider_overrides": {
                            "thread-fixture": " provider-b ",
                        },
                    }
                ),
                encoding="utf-8",
            )

            settings = load_codex_settings(path)

            self.assertEqual(settings["provider_order"], ["second", "first"])
            self.assertEqual(settings["hidden_provider_ids"], ["hidden"])
            self.assertEqual(
                settings["session_provider_overrides"],
                {"thread-fixture": "provider-b"},
            )

    def test_default_data_files_use_fixed_home_directory(self) -> None:
        self.assertEqual(data_directory().name, ".codex-local-proxy")
        self.assertEqual(settings_path(), data_directory() / "shared-settings.json")
        for service_id in ("codex", "claude"):
            self.assertEqual(
                protocol_settings_path(service_id),
                data_directory() / f"{service_id}-settings.json",
            )
            self.assertEqual(
                protocol_usage_database_path(service_id),
                data_directory() / f"{service_id}-usage.sqlite3",
            )
            self.assertEqual(
                protocol_provider_catalog_path(service_id),
                data_directory() / f"{service_id}-providers.sqlite3",
            )

    def test_fallback_legacy_data_is_split_and_removed_after_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "legacy"
            destination = root / ".codex-local-proxy"
            claude_legacy = root / ".claude-local-proxy"
            legacy.mkdir()
            claude_legacy.mkdir()
            (legacy / "settings.json").write_text(
                json.dumps({"port": 18888, "selected_provider_id": "codex-a"}),
                encoding="utf-8",
            )
            with closing(sqlite3.connect(legacy / "usage.sqlite3")) as connection:
                connection.execute("CREATE TABLE marker (value TEXT)")
                connection.execute("INSERT INTO marker VALUES ('preserved')")
                connection.commit()

            migrated = migrate_runtime_data(
                target=destination,
                codex_source=destination,
                claude_source=claude_legacy,
                fallback_codex_source=legacy,
            )

            self.assertEqual(
                set(migrated),
                {
                    "shared-settings.json",
                    "codex-settings.json",
                    "claude-settings.json",
                    "codex-usage.sqlite3",
                },
            )
            self.assertFalse((legacy / "settings.json").exists())
            self.assertFalse((legacy / "usage.sqlite3").exists())
            self.assertEqual(load_settings(destination / "shared-settings.json")["port"], 18888)
            self.assertEqual(
                load_codex_settings(destination / "codex-settings.json")["selected_provider_id"],
                "codex-a",
            )
            with closing(sqlite3.connect(destination / "codex-usage.sqlite3")) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM marker").fetchone()[0],
                    "preserved",
                )


class CodexConfigTests(unittest.TestCase):
    def test_fragment_uses_fixed_loopback_responses_provider(self) -> None:
        fragment = codex_config_fragment(18888)

        self.assertIn('model_provider = "local_cc_switch"', fragment)
        self.assertIn('base_url = "http://127.0.0.1:18888/v1"', fragment)
        self.assertIn('wire_api = "responses"', fragment)
        self.assertIn("requires_openai_auth = true", fragment)
        self.assertNotIn("api_key", fragment.casefold())

    def test_cli_command_preserves_provider_values_for_supported_shells(self) -> None:
        provider = ProxyProvider(
            provider_id="quoted",
            name="A 'quoted' provider",
            base_url="https://api.example.test/v1",
            is_cc_switch_current=True,
            api_key="secret ' value",
            configured_headers={"X-Trace": 'a"b'},
            default_query={"mode": "fast"},
        )

        powershell = codex_cli_launch_command(provider, platform="win32")
        shell = codex_cli_launch_command(provider, platform="linux")

        self.assertEqual(powershell["shell"], "powershell")
        self.assertIn("$env:CODEX_PROVIDER_API_KEY = 'secret '' value'", powershell["command"])
        self.assertIn("model_provider=\"custom\"", powershell["command"])
        self.assertIn("model_providers.custom.base_url=\"https://api.example.test/v1\"", powershell["command"])
        self.assertIn("model_providers.custom.http_headers={\"X-Trace\"=\"a\\\"b\"}", powershell["command"])
        self.assertIn("Remove-Item Env:CODEX_PROVIDER_API_KEY", powershell["command"])
        self.assertEqual(shell["shell"], "shell")
        self.assertTrue(shell["command"].startswith("CODEX_PROVIDER_API_KEY="))
        self.assertIn("model_providers.custom.query_params={\"mode\"=\"fast\"}", shell["command"])

    def test_cli_command_rejects_provider_without_credentials(self) -> None:
        provider = ProxyProvider(
            provider_id="missing",
            name="Missing",
            base_url="https://api.example.test",
            is_cc_switch_current=False,
        )

        with self.assertRaises(ProviderConfigurationError):
            codex_cli_launch_command(provider, platform="linux")

    def test_auto_start_commands_always_disable_browser_opening(self) -> None:
        executable = str(Path(sys.executable).resolve())
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", executable),
        ):
            frozen_command = local_proxy_app.auto_start_command()
        with (
            mock.patch.object(sys, "frozen", False, create=True),
            mock.patch.object(sys, "executable", executable),
        ):
            source_command = local_proxy_app.auto_start_command()

        self.assertEqual(
            frozen_command,
            subprocess.list2cmdline([executable, "--no-browser"]),
        )
        self.assertIn("local_proxy_app.py", source_command)
        self.assertIn("--tray", source_command)
        self.assertTrue(source_command.endswith("--no-browser"))

    def test_source_auto_start_prefers_pythonw_for_silent_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "python.exe"
            pythonw = Path(temp_dir) / "pythonw.exe"
            executable.touch()
            pythonw.touch()
            with (
                mock.patch.object(sys, "frozen", False, create=True),
                mock.patch.object(sys, "executable", str(executable)),
            ):
                command = local_proxy_app.auto_start_command()

        self.assertEqual(
            command,
            subprocess.list2cmdline(
                [
                    str(pythonw.resolve()),
                    str(Path(local_proxy_app.__file__).resolve().parents[1] / "local_proxy_app.py"),
                    "--tray",
                    "--no-browser",
                ]
            ),
        )

    def test_auto_start_state_and_changes_use_current_user_value(self) -> None:
        with (
            mock.patch.object(local_proxy_app, "auto_start_supported", return_value=True),
            mock.patch.object(local_proxy_app, "auto_start_command", return_value="current command"),
            mock.patch.object(local_proxy_app, "_read_auto_start_value", return_value="CURRENT COMMAND"),
        ):
            self.assertTrue(local_proxy_app.is_auto_start_enabled())

        with (
            mock.patch.object(local_proxy_app, "auto_start_supported", return_value=True),
            mock.patch.object(local_proxy_app, "auto_start_command", return_value="current command"),
            mock.patch.object(local_proxy_app, "_read_auto_start_value", return_value="old command"),
        ):
            self.assertFalse(local_proxy_app.is_auto_start_enabled())

        with (
            mock.patch.object(local_proxy_app, "auto_start_supported", return_value=True),
            mock.patch.object(local_proxy_app, "auto_start_command", return_value="current command"),
            mock.patch.object(local_proxy_app, "_write_auto_start_value") as write_value,
            mock.patch.object(local_proxy_app, "_delete_auto_start_value") as delete_value,
        ):
            local_proxy_app.set_auto_start_enabled(True)
            local_proxy_app.set_auto_start_enabled(False)

        write_value.assert_called_once_with("current command")
        delete_value.assert_called_once_with()

    def test_main_defaults_to_silent_start_and_allows_explicit_browser_open(self) -> None:
        settings = default_settings()
        with (
            mock.patch.object(local_proxy_app, "migrate_legacy_data_directory"),
            mock.patch.object(local_proxy_app, "load_settings", return_value=settings),
            mock.patch.object(local_proxy_app, "run_application", return_value=0) as run,
        ):
            self.assertEqual(local_proxy_app.main([]), 0)
            self.assertFalse(run.call_args.kwargs["open_browser"])
            self.assertEqual(local_proxy_app.main(["--open-browser"]), 0)
            self.assertTrue(run.call_args.kwargs["open_browser"])
            self.assertEqual(local_proxy_app.main(["--no-browser"]), 0)
            self.assertFalse(run.call_args.kwargs["open_browser"])

    def test_replacement_process_uses_saved_settings_in_frozen_app(self) -> None:
        executable = str(Path(sys.executable).resolve())
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", executable),
            mock.patch.object(subprocess, "Popen") as popen,
        ):
            local_proxy_app.launch_replacement_process()

        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(command, [executable, "--no-browser"])
        self.assertEqual(options["cwd"], str(Path(executable).parent))
        self.assertNotIn("--port", command)
        self.assertNotIn("--database", command)

    def test_replacement_process_preserves_tray_mode_from_source(self) -> None:
        with (
            mock.patch.object(sys, "frozen", False, create=True),
            mock.patch.object(subprocess, "Popen") as popen,
        ):
            local_proxy_app.launch_replacement_process()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "local_proxy_app.py")
        self.assertEqual(command[-2:], ["--tray", "--no-browser"])

    def test_tray_restart_stops_server_and_returns_restart_request(self) -> None:
        menu_labels: list[str] = []

        menu_items_by_label: dict[str, object] = {}

        class FakeMenuItem:
            def __init__(
                self,
                label,
                action,
                default=False,
                checked=None,
                visible=True,
                enabled=None,
            ):
                display = label() if callable(label) else label
                self.label = display
                self.action = action
                self.default = default
                self.checked = checked
                self.visible = visible
                self.enabled = enabled
                if visible:
                    menu_labels.append(display)
                menu_items_by_label[display] = self

        class FakeMenu:
            SEPARATOR = object()

            def __init__(self, *items):
                self.items = items

        class FakeIcon:
            def __init__(self, name, image, title, menu):
                self.menu = menu
                self.title = title
                self.stopped = False

            def run(self):
                restart_item = menu_items_by_label["重启本地中转"]
                restart_item.action(self, restart_item)

            def stop(self):
                self.stopped = True

            def update_menu(self):
                pass

        fake_pystray = mock.Mock(Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon)
        fake_pystray.Menu.SEPARATOR = FakeMenu.SEPARATOR
        server = mock.Mock()
        tray_holder: dict[str, object] = {}
        with (
            mock.patch.dict(sys.modules, {"pystray": fake_pystray}),
            mock.patch.object(local_proxy_app, "create_app_icon", return_value=object()),
            mock.patch.object(local_proxy_app, "auto_start_supported", return_value=True),
            mock.patch.object(local_proxy_app, "is_auto_start_enabled", return_value=True),
            mock.patch.object(local_proxy_app.webbrowser, "open") as browser,
        ):
            restart_requested = local_proxy_app._run_tray(
                server,
                "http://127.0.0.1:17890/control/codex/",
                tray_holder,
            )
            auto_start_item = menu_items_by_label["开机自启"]
            auto_start_checked = auto_start_item.checked(auto_start_item)
            visible_open_item = menu_items_by_label["打开控制台"]
            default_item = menu_items_by_label["默认打开 Codex 控制台"]
            visible_open_item.action(tray_holder["icon"], visible_open_item)
            default_item.action(tray_holder["icon"], default_item)

        self.assertTrue(restart_requested)
        self.assertTrue(tray_holder["icon"].stopped)
        server.request_stop.assert_called_once_with()
        self.assertEqual(
            menu_labels,
            ["打开控制台", "开机自启", "检查更新", "重启本地中转", "退出本地中转"],
        )
        self.assertNotIn("打开 Claude Code 控制台", menu_items_by_label)
        self.assertTrue(auto_start_checked)
        self.assertFalse(visible_open_item.default)
        self.assertTrue(default_item.default)
        self.assertFalse(default_item.visible)
        self.assertEqual(tray_holder["icon"].title, "模型路由服务")
        self.assertEqual(
            browser.call_args_list,
            [
                mock.call("http://127.0.0.1:17890/control/codex/"),
                mock.call("http://127.0.0.1:17890/control/codex/"),
            ],
        )

    def test_run_server_opens_both_views_and_stops_once(self) -> None:
        server = mock.Mock(running=False)
        with mock.patch.object(local_proxy_app.webbrowser, "open") as browser:
            result = local_proxy_app.run_local_proxy_server(
                server,
                codex_control_url="http://127.0.0.1:17890/control/codex/",
                claude_control_url="http://127.0.0.1:17890/control/claude/",
                open_browser=True,
                tray=False,
            )

        self.assertEqual(result, 0)
        server.start.assert_called_once_with()
        browser.assert_any_call("http://127.0.0.1:17890/control/codex/")
        browser.assert_any_call("http://127.0.0.1:17890/control/claude/")
        server.stop.assert_called_once_with()

    def test_run_servers_stays_silent_when_browser_opening_is_disabled(self) -> None:
        server = mock.Mock(running=False)
        with mock.patch.object(local_proxy_app.webbrowser, "open") as browser:
            result = local_proxy_app.run_local_proxy_server(
                server,
                codex_control_url="http://127.0.0.1:17890/control/codex/",
                claude_control_url="http://127.0.0.1:17890/control/claude/",
                open_browser=False,
                tray=False,
            )

        self.assertEqual(result, 0)
        browser.assert_not_called()

    def test_existing_proxy_url_accepts_expected_service_name(self) -> None:
        response = mock.Mock(status_code=200)
        response.json.return_value = {"service": "codex-provider-hub"}
        with mock.patch.object(local_proxy_app.httpx, "get", return_value=response):
            url = local_proxy_app.existing_proxy_url(
                17890,
            )

        self.assertEqual(url, "http://127.0.0.1:17890/control/codex/")

    def test_existing_unified_instance_is_reused(self) -> None:
        with (
            mock.patch.object(
                local_proxy_app,
                "existing_proxy_url",
                return_value="http://127.0.0.1:17890/control/codex/",
            ),
            mock.patch.object(local_proxy_app.webbrowser, "open") as browser,
        ):
            result = local_proxy_app.run_application(
                database=Path("cc-switch.db"),
                port=17890,
                open_browser=True,
                tray=False,
            )

        self.assertEqual(result, 0)
        browser.assert_any_call("http://127.0.0.1:17890/control/codex/")
        browser.assert_any_call("http://127.0.0.1:17890/control/claude/")

    def test_run_application_constructs_one_server_for_both_protocols(self) -> None:
        from local_proxy import claude_profile

        server = mock.Mock()
        codex_profile_instance = mock.Mock()
        claude_profile_instance = mock.Mock()
        shared_settings = default_settings()
        codex_settings = {"selected_provider_id": "codex-a"}
        claude_settings = {"selected_provider_id": "claude-a"}
        shared_store = mock.Mock()
        shared_store.retry_policy_store = object()
        shared_store.health_status_url_store = object()
        diagnostic_log = mock.Mock()
        with (
            mock.patch.object(local_proxy_app, "existing_proxy_url", return_value=None),
            mock.patch.object(local_proxy_app, "load_settings", return_value=shared_settings),
            mock.patch.object(
                local_proxy_app,
                "load_codex_settings",
                return_value=codex_settings,
            ),
            mock.patch.object(
                local_proxy_app,
                "SharedSettingsStore",
                return_value=shared_store,
            ),
            mock.patch.object(local_proxy_app, "SharedRuntimeCoordinator") as coordinator,
            mock.patch.object(
                local_proxy_app,
                "build_codex_profile",
                return_value=codex_profile_instance,
            ) as codex_profile_builder,
            mock.patch.object(
                claude_profile,
                "load_settings",
                return_value=claude_settings,
            ),
            mock.patch.object(
                claude_profile,
                "build_claude_profile",
                return_value=claude_profile_instance,
            ) as claude_profile_builder,
            mock.patch.object(
                local_proxy_app,
                "DiagnosticLog",
                return_value=diagnostic_log,
            ) as diagnostic_log_factory,
            mock.patch.object(local_proxy_app, "create_proxy_app", return_value=object()) as app_factory,
            mock.patch.object(
                local_proxy_app,
                "LocalProxyServer",
                return_value=server,
            ) as server_class,
            mock.patch.object(
                local_proxy_app,
                "run_local_proxy_server",
                return_value=0,
            ) as runner,
        ):
            result = local_proxy_app.run_application(
                database=Path("cc-switch.db"),
                port=17890,
                open_browser=False,
                tray=False,
            )

        self.assertEqual(result, 0)
        codex_profile_builder.assert_called_once_with(
            database=Path("cc-switch.db"),
            port=17890,
            settings_data=codex_settings,
            retry_policy_store=shared_store.retry_policy_store,
            health_status_url_store=shared_store.health_status_url_store,
            status_upload_manager=mock.ANY,
        )
        claude_profile_builder.assert_called_once_with(
            database=Path("cc-switch.db"),
            port=17890,
            settings_data=claude_settings,
            retry_policy_store=shared_store.retry_policy_store,
            health_status_url_store=shared_store.health_status_url_store,
            status_upload_manager=mock.ANY,
        )
        self.assertIs(
            codex_profile_builder.call_args.kwargs["status_upload_manager"],
            claude_profile_builder.call_args.kwargs["status_upload_manager"],
        )
        coordinator.assert_called_once_with(
            shared_store,
            (codex_profile_instance, claude_profile_instance),
            active_port=17890,
        )
        app_factory.assert_called_once_with(
            codex_profile=codex_profile_instance,
            claude_profile=claude_profile_instance,
            on_shutdown_requested=mock.ANY,
            update_controller=mock.ANY,
            diagnostic_log=diagnostic_log,
        )
        diagnostic_log_factory.assert_called_once_with(
            local_proxy_app.data_directory() / "logs" / "proxy-diagnostics.log"
        )
        server_class.assert_called_once_with(
            host="127.0.0.1",
            port=17890,
            application=app_factory.return_value,
        )
        runner.assert_called_once_with(
            server,
            codex_control_url="http://127.0.0.1:17890/control/codex/",
            claude_control_url="http://127.0.0.1:17890/control/claude/",
            open_browser=False,
            tray=False,
            tray_holder=mock.ANY,
        )

    def test_shortcut_targets_browser_app_launcher(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "install_local_proxy_shortcut.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn('"local_proxy_app.py"', script)
        self.assertIn("--tray", script)
        self.assertIn("--no-browser", script)
        self.assertIn("IconLocation", script)
        self.assertNotIn("codex_local_proxy_gui.py", script)

    def test_app_icon_has_transparent_background(self) -> None:
        icon = local_proxy_app.create_app_icon()
        self.assertEqual(icon.mode, "RGBA")
        self.assertEqual(icon.size, (64, 64))
        alpha = icon.getchannel("A")
        histogram = alpha.histogram()
        opaque = sum(histogram[254:])
        self.assertGreater(opaque, 0)
        # The transparent area must cover the outer frame, not just a sliver.
        # The "CX" glyphs are opaque, the surrounding background is transparent.
        self.assertGreater(histogram[0], 64 * 64 // 4)
        for corner in ((0, 0), (63, 0), (0, 63), (63, 63)):
            self.assertEqual(alpha.getpixel(corner), 0)

    def test_app_icon_glyphs_fill_canvas(self) -> None:
        icon = local_proxy_app.create_app_icon()
        bbox = icon.getchannel("A").getbbox()
        self.assertIsNotNone(bbox)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        # Glyphs must fill most of the 64px canvas instead of shrinking to a
        # small wordmark floating on the transparent background. Width is the
        # binding dimension for "CX" (two caps side by side), so the height
        # floor is intentionally lower to stay portable across font metrics.
        self.assertGreaterEqual(width, 44)
        self.assertGreaterEqual(height, 28)


if __name__ == "__main__":
    unittest.main()
