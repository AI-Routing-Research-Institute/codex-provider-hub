import tempfile
import unittest
from pathlib import Path

from local_proxy_app import APP_VERSION, smoke_test
from scripts.create_local_proxy_smoke_db import create_database


ROOT = Path(__file__).resolve().parents[1]


class WindowsReleaseTests(unittest.TestCase):
    def test_packaged_smoke_test_covers_assets_icon_and_provider_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "release-smoke.db"
            create_database(database)

            result = smoke_test(database)

        self.assertEqual(result["app_version"], APP_VERSION)
        self.assertEqual(result["provider_count"], 1)
        self.assertTrue(result["current_provider_configured"])
        self.assertEqual(result["credential_count"], 0)
        self.assertEqual(result["service_count"], 1)
        self.assertEqual(result["listen_address"], "127.0.0.1:17890")
        self.assertEqual(
            result["control_paths"],
            ["/control/codex/", "/control/claude/"],
        )
        self.assertGreaterEqual(result["control_asset_count"], 6)
        self.assertEqual(result["control_ui_modes"], ["classic", "modern"])
        self.assertEqual(result["claude_provider_count"], 1)
        self.assertEqual(result["claude_compatible_provider_count"], 1)
        self.assertTrue(result["tray_backend_available"])
        self.assertTrue(result["claude_transport_available"])
        self.assertEqual(result["icon_size"], [64, 64])

    def test_pyinstaller_spec_includes_runtime_assets_and_dynamic_modules(self) -> None:
        spec = (ROOT / "packaging" / "CodexLocalProxy.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn('ROOT / "proxy_static"', spec)
        self.assertIn('ROOT / "proxy_static" / "classic"', spec)
        self.assertIn('ROOT / "proxy_static" / "dist"', spec)
        self.assertNotIn('ROOT / "local_proxy_static"', spec)
        self.assertNotIn('ROOT / "claude_proxy_static"', spec)
        self.assertIn('collect_submodules("tiktoken_ext")', spec)
        self.assertIn('"pystray._win32"', spec)
        self.assertIn('name="CodexLocalProxy-win-x64"', spec)
        self.assertIn("console=False", spec)

    def test_release_dependencies_include_claude_transport(self) -> None:
        requirements = (ROOT / "requirements-status.txt").read_text(encoding="utf-8")

        self.assertIn("curl_cffi", requirements)


if __name__ == "__main__":
    unittest.main()
