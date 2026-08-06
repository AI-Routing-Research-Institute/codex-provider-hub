import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_local_proxy_app
from claude_local_proxy_app import (
    DEFAULT_CLAUDE_PORT,
    claude_config_snippets,
    claude_ui_config,
    data_directory,
    default_settings,
    load_settings,
    settings_path,
    usage_database_path,
)


class ClaudeLocalProxySettingsTests(unittest.TestCase):
    def test_ui_config_uses_active_and_peer_ports(self) -> None:
        config = claude_ui_config(19001, 19000, Path("C:/claude-local-proxy"))
        self.assertEqual(config["proxy_url"], "http://127.0.0.1:19001")
        self.assertEqual(config["peer_console_url"], "http://127.0.0.1:19000/control/")
        self.assertEqual(config["config_endpoint"], "/control/api/claude-config")

    def test_defaults_use_independent_port_and_data_directory(self) -> None:
        self.assertEqual(DEFAULT_CLAUDE_PORT, 17891)
        self.assertEqual(default_settings()["port"], 17891)
        self.assertEqual(data_directory().name, ".claude-local-proxy")
        self.assertEqual(settings_path(), data_directory() / "settings.json")
        self.assertEqual(usage_database_path(), data_directory() / "usage.sqlite3")

    def test_settings_round_trip_uses_claude_defaults(self) -> None:
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

        self.assertEqual(settings["port"], 18891)
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

    def test_build_server_uses_claude_app_and_port(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "cc-switch.db"
            with (
                mock.patch.object(
                    claude_local_proxy_app,
                    "load_claude_proxy_providers",
                    return_value=(),
                ),
                mock.patch.object(claude_local_proxy_app, "LocalProxyServer") as server_class,
            ):
                claude_local_proxy_app.build_claude_server(
                    database=database,
                    port=18891,
                    data_root=Path(temp_dir) / "claude-data",
                )

        self.assertEqual(server_class.call_args.kwargs["port"], 18891)
        self.assertEqual(server_class.call_args.kwargs["app_factory"].__name__, "create_claude_proxy_app")


if __name__ == "__main__":
    unittest.main()
