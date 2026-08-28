import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_proxy import claude_profile
from local_proxy.claude_profile import (
    claude_config_snippets,
    claude_ui_config,
    data_directory,
    default_settings,
    load_settings,
    settings_path,
    usage_database_path,
)


class ClaudeLocalProxySettingsTests(unittest.TestCase):
    def test_status_upload_visibility_defaults_on_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "claude-data"
            with (
                mock.patch.object(
                    claude_profile,
                    "load_claude_proxy_providers",
                    return_value=(),
                ),
                mock.patch.object(claude_profile, "ClaudeCurlClient"),
            ):
                profile = claude_profile.build_claude_profile(
                    database=Path(temp_dir) / "cc-switch.db",
                    port=17890,
                    data_root=root,
                )

                self.assertTrue(profile.runtime_metadata()["show_status_upload"])
                profile.apply_runtime_preferences({"show_status_upload": False})
                self.assertFalse(profile.runtime_metadata()["show_status_upload"])
                self.assertFalse(
                    claude_profile.load_settings(root / "claude-settings.json")[
                        "show_status_upload"
                    ]
                )
                with self.assertRaisesRegex(ValueError, "上传检测"):
                    profile.apply_runtime_preferences({"show_status_upload": "no"})

    def test_ui_config_uses_unified_port_and_peer_console_path(self) -> None:
        config = claude_ui_config(19000, Path("C:/claude-local-proxy"))
        self.assertEqual(config["config_button_label"], "导入到 CCS")
        self.assertEqual(config["proxy_url"], "http://127.0.0.1:19000")
        self.assertEqual(config["peer_console_url"], "http://127.0.0.1:19000/control/codex/")
        self.assertEqual(config["config_endpoint"], "/control/claude/api/claude-config")
        self.assertTrue(config["features"]["status_upload"])

    def test_defaults_use_shared_data_directory_and_protocol_files(self) -> None:
        self.assertNotIn("port", default_settings())
        self.assertEqual(data_directory().name, ".codex-local-proxy")
        self.assertEqual(settings_path(), data_directory() / "claude-settings.json")
        self.assertEqual(usage_database_path(), data_directory() / "claude-usage.sqlite3")

    def test_settings_round_trip_ignores_legacy_port(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "port": 18891,
                        "selected_provider_id": "claude-a",
                        "provider_order": ["claude-b", "claude-a"],
                    }
                ),
                encoding="utf-8",
            )

            settings = load_settings(path)

        self.assertNotIn("port", settings)
        self.assertEqual(settings["selected_provider_id"], "claude-a")
        self.assertEqual(settings["provider_order"], ["claude-b", "claude-a"])

    def test_config_snippets_use_placeholder_key_and_local_base_url(self) -> None:
        snippets = claude_config_snippets(18891)

        self.assertIn(
            '$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:18891"',
            snippets["powershell"],
        )
        self.assertIn(
            '$env:ANTHROPIC_API_KEY = "local-claude-proxy"',
            snippets["powershell"],
        )
        self.assertIn(
            'export ANTHROPIC_BASE_URL="http://127.0.0.1:18891"',
            snippets["bash"],
        )
        self.assertNotIn("upstream", str(snippets).casefold())

    def test_build_profile_uses_unified_port_and_messages_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "cc-switch.db"
            with (
                mock.patch.object(
                    claude_profile,
                    "load_claude_proxy_providers",
                    return_value=(),
                ),
                mock.patch.object(claude_profile, "ClaudeCurlClient") as client_class,
            ):
                profile = claude_profile.build_claude_profile(
                    database=database,
                    port=17890,
                    data_root=Path(temp_dir) / "claude-data",
                )

        self.assertEqual(profile.service_id, "claude")
        self.assertEqual(profile.allowed_proxy_paths, frozenset({"messages", "messages/count_tokens"}))
        client_class.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
