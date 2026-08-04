import unittest
from pathlib import Path

from codex_local_proxy_app import APP_VERSION, smoke_test

from scripts.create_local_proxy_smoke_db import create_database


ROOT = Path(__file__).resolve().parents[1]


class MacOSReleaseTests(unittest.TestCase):
    def test_packaged_smoke_test_covers_assets_icon_and_provider_loading(self) -> None:
        # The smoke test itself is platform-neutral; re-asserting it here keeps
        # the macOS release pipeline independently guarded.
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "release-smoke.db"
            create_database(database)

            result = smoke_test(database)

        self.assertEqual(result["app_version"], APP_VERSION)
        self.assertEqual(result["provider_count"], 1)
        self.assertTrue(result["current_provider_configured"])
        self.assertEqual(result["credential_count"], 0)
        self.assertEqual(result["control_asset_count"], 3)
        self.assertEqual(result["claude_provider_count"], 1)
        self.assertEqual(result["claude_compatible_provider_count"], 1)
        self.assertEqual(result["claude_control_asset_count"], 3)
        self.assertTrue(result["tray_backend_available"])
        self.assertTrue(result["claude_curl_transport_available"])
        self.assertEqual(result["icon_size"], [64, 64])

    def test_macos_spec_uses_appkit_backend_and_app_bundle(self) -> None:
        spec = (ROOT / "packaging" / "CodexLocalProxy.macos.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn('ROOT / "local_proxy_static"', spec)
        self.assertIn('ROOT / "claude_proxy_static"', spec)
        self.assertIn('collect_submodules("tiktoken_ext")', spec)
        self.assertIn('"pystray._appkit"', spec)
        self.assertIn("target_arch=\"arm64\"", spec)
        self.assertIn("name=\"CodexLocalProxy-macos-arm64\"", spec)
        self.assertIn("console=False", spec)
        self.assertIn("BUNDLE(", spec)
        self.assertIn("bundle_identifier=", spec)
        self.assertIn("CFBundleShortVersionString", spec)

    def test_macos_release_workflow_syncs_version_and_attaches_artifacts(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "macos-release.yml"
        ).read_text(encoding="utf-8")
        build_script = (
            ROOT / "scripts" / "build_local_proxy_macos.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: write", workflow)
        self.assertIn("runs-on: macos-latest", workflow)
        self.assertIn('python-version: "3.13"', workflow)
        self.assertIn("python -m unittest discover", workflow)
        self.assertIn("Sync source version to release tag", workflow)
        self.assertIn("create_local_proxy_smoke_db.py", workflow)
        # macOS only uploads to an existing Release (the Windows workflow
        # creates it with release notes); it must not race on `gh release create`.
        self.assertIn("gh release upload", workflow)
        self.assertNotIn("gh release create", workflow)

    def test_macos_build_script_emits_icns_and_zip_artifacts(self) -> None:
        build_script = (
            ROOT / "scripts" / "build_local_proxy_macos.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("--write-icon", build_script)
        self.assertIn(".icns", build_script)
        self.assertIn("CodexLocalProxy.macos.spec", build_script)
        self.assertIn(".zip", build_script)
        self.assertIn("sha256", build_script)


if __name__ == "__main__":
    unittest.main()
