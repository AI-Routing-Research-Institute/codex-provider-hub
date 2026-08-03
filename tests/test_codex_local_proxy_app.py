import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import codex_local_proxy_app

from codex_local_proxy_app import (
    codex_config_fragment,
    data_directory,
    default_settings,
    display_path,
    load_settings,
    migrate_legacy_data_directory,
    save_settings,
    settings_path,
    usage_database_path,
)


class LocalProxySettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_corrupt_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            settings = default_settings()
            settings.update(selected_provider_id="provider-a", port=18888)
            settings["database_path"] = str(Path(temp_dir) / "cc-switch.db")
            settings["retry"]["max_attempts"] = -1
            settings["provider_order"] = ["provider-b", "provider-a"]
            settings["hidden_provider_ids"] = ["provider-c"]
            settings["health_status_url"] = (
                "https://status.example.test/api/status?window=24h"
            )

            save_settings(settings, path)

            self.assertEqual(load_settings(path)["selected_provider_id"], "provider-a")
            self.assertEqual(load_settings(path)["port"], 18888)
            self.assertEqual(
                load_settings(path)["database_path"],
                display_path(Path(temp_dir) / "cc-switch.db"),
            )
            self.assertEqual(load_settings(path)["retry"]["max_attempts"], -1)
            self.assertEqual(
                load_settings(path)["provider_order"], ["provider-b", "provider-a"]
            )
            self.assertEqual(load_settings(path)["hidden_provider_ids"], ["provider-c"])
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

    def test_provider_preferences_are_trimmed_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "provider_order": [" second ", "first", "second", 1],
                        "hidden_provider_ids": [" hidden ", "", "hidden"],
                    }
                ),
                encoding="utf-8",
            )

            settings = load_settings(path)

            self.assertEqual(settings["provider_order"], ["second", "first"])
            self.assertEqual(settings["hidden_provider_ids"], ["hidden"])

    def test_default_data_files_use_fixed_home_directory(self) -> None:
        self.assertEqual(data_directory().name, ".codex-local-proxy")
        self.assertEqual(settings_path(), data_directory() / "settings.json")
        self.assertEqual(usage_database_path(), data_directory() / "usage.sqlite3")

    def test_legacy_settings_and_usage_are_copied_without_removing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "legacy"
            destination = root / ".codex-local-proxy"
            legacy.mkdir()
            (legacy / "settings.json").write_text(
                json.dumps({"port": 18888}),
                encoding="utf-8",
            )
            with closing(sqlite3.connect(legacy / "usage.sqlite3")) as connection:
                connection.execute("CREATE TABLE marker (value TEXT)")
                connection.execute("INSERT INTO marker VALUES ('preserved')")
                connection.commit()

            migrated = migrate_legacy_data_directory(legacy, destination)

            self.assertEqual(set(migrated), {"settings.json", "usage.sqlite3"})
            self.assertTrue((legacy / "settings.json").is_file())
            self.assertTrue((legacy / "usage.sqlite3").is_file())
            self.assertEqual(load_settings(destination / "settings.json")["port"], 18888)
            with closing(sqlite3.connect(destination / "usage.sqlite3")) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM marker").fetchone()[0],
                    "preserved",
                )


class CodexConfigTests(unittest.TestCase):
    def test_fragment_uses_fixed_loopback_responses_provider(self) -> None:
        fragment = codex_config_fragment(17891)

        self.assertIn('model_provider = "local_cc_switch"', fragment)
        self.assertIn('base_url = "http://127.0.0.1:17891/v1"', fragment)
        self.assertIn('wire_api = "responses"', fragment)
        self.assertIn("requires_openai_auth = true", fragment)
        self.assertNotIn("api_key", fragment.casefold())

    def test_replacement_process_uses_saved_settings_in_frozen_app(self) -> None:
        executable = str(Path(sys.executable).resolve())
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", executable),
            mock.patch.object(subprocess, "Popen") as popen,
        ):
            codex_local_proxy_app.launch_replacement_process()

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
            codex_local_proxy_app.launch_replacement_process()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "codex_local_proxy_app.py")
        self.assertEqual(command[-2:], ["--tray", "--no-browser"])

    def test_tray_restart_stops_server_and_returns_restart_request(self) -> None:
        menu_labels: list[str] = []

        class FakeMenuItem:
            def __init__(self, label, action, default=False):
                self.label = label
                self.action = action
                self.default = default
                menu_labels.append(label)

        class FakeMenu:
            SEPARATOR = object()

            def __init__(self, *items):
                self.items = items

        class FakeIcon:
            def __init__(self, name, image, title, menu):
                self.menu = menu
                self.stopped = False

            def run(self):
                self.menu.items[2].action(self, self.menu.items[2])

            def stop(self):
                self.stopped = True

        fake_pystray = mock.Mock(Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon)
        fake_pystray.Menu.SEPARATOR = FakeMenu.SEPARATOR
        codex_server = mock.Mock()
        claude_server = mock.Mock()
        tray_holder: dict[str, object] = {}
        with (
            mock.patch.dict(sys.modules, {"pystray": fake_pystray}),
            mock.patch.object(codex_local_proxy_app, "create_app_icon", return_value=object()),
        ):
            restart_requested = codex_local_proxy_app._run_tray(
                (codex_server, claude_server),
                "http://127.0.0.1:17890/control/",
                "http://127.0.0.1:17891/control/",
                tray_holder,
            )

        self.assertTrue(restart_requested)
        self.assertTrue(tray_holder["icon"].stopped)
        codex_server.request_stop.assert_called_once_with()
        claude_server.request_stop.assert_called_once_with()
        self.assertEqual(
            menu_labels,
            ["打开 Codex 控制台", "打开 Claude Code 控制台", "重启本地中转", "退出本地中转"],
        )

    def test_run_servers_opens_both_consoles_and_stops_both(self) -> None:
        codex_server = mock.Mock(running=False)
        claude_server = mock.Mock(running=False)
        with mock.patch.object(codex_local_proxy_app.webbrowser, "open") as browser:
            result = codex_local_proxy_app.run_hub_servers(
                codex_server,
                claude_server,
                codex_control_url="http://127.0.0.1:17890/control/",
                claude_control_url="http://127.0.0.1:17891/control/",
                open_browser=True,
                tray=False,
            )

        self.assertEqual(result, 0)
        codex_server.start.assert_called_once_with()
        claude_server.start.assert_called_once_with()
        browser.assert_any_call("http://127.0.0.1:17890/control/")
        browser.assert_any_call("http://127.0.0.1:17891/control/")
        codex_server.stop.assert_called_once_with()
        claude_server.stop.assert_called_once_with()

    def test_existing_proxy_url_accepts_expected_service_name(self) -> None:
        response = mock.Mock(status_code=200)
        response.json.return_value = {"service": "claude-local-proxy"}
        with mock.patch.object(codex_local_proxy_app.httpx, "get", return_value=response):
            url = codex_local_proxy_app.existing_proxy_url(
                17891,
                service_name="claude-local-proxy",
            )

        self.assertEqual(url, "http://127.0.0.1:17891/control/")

    def test_shortcut_targets_browser_app_launcher(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "install_local_proxy_shortcut.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn('"codex_local_proxy_app.py"', script)
        self.assertIn("--tray", script)
        self.assertIn("IconLocation", script)
        self.assertNotIn("codex_local_proxy_gui.py", script)


if __name__ == "__main__":
    unittest.main()
