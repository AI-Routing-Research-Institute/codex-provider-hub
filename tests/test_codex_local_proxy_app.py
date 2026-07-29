import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

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
