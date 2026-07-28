import json
import tempfile
import unittest
from pathlib import Path

from codex_local_proxy_app import (
    codex_config_fragment,
    default_settings,
    load_settings,
    save_settings,
)


class LocalProxySettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_corrupt_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            settings = default_settings()
            settings.update(selected_provider_id="provider-a", port=18888)
            settings["retry"]["max_attempts"] = -1
            settings["provider_order"] = ["provider-b", "provider-a"]
            settings["hidden_provider_ids"] = ["provider-c"]
            settings["health_status_url"] = (
                "https://status.example.test/api/status?window=24h"
            )

            save_settings(settings, path)

            self.assertEqual(load_settings(path)["selected_provider_id"], "provider-a")
            self.assertEqual(load_settings(path)["port"], 18888)
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
