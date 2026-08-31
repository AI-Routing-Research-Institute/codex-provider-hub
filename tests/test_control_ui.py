import tempfile
import unittest
from pathlib import Path

from local_proxy.control_ui import (
    CONTROL_UI_MODERN,
    control_index_path,
    normalize_control_ui,
    resolve_control_asset,
    select_control_ui,
)


class ControlUiTests(unittest.TestCase):
    def test_normalizes_settings_and_allows_valid_temporary_overrides(self) -> None:
        self.assertEqual(normalize_control_ui(None), CONTROL_UI_MODERN)
        self.assertEqual(normalize_control_ui("unknown"), CONTROL_UI_MODERN)
        self.assertEqual(select_control_ui({"console_ui": "classic"}), "classic")
        self.assertEqual(
            select_control_ui({"console_ui": "classic"}, "modern"),
            "modern",
        )
        self.assertEqual(
            select_control_ui({"console_ui": "classic"}, "unknown"),
            "classic",
        )

    def test_resolves_only_namespaced_runtime_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            classic = root / "classic"
            modern = root / "dist" / "static" / "assets"
            classic.mkdir(parents=True)
            modern.mkdir(parents=True)
            (classic / "index.html").write_text("classic", encoding="utf-8")
            (classic / "app.js").write_text("classic", encoding="utf-8")
            (modern / "bundle.js").write_text("modern", encoding="utf-8")

            self.assertEqual(
                control_index_path(root, "classic"),
                classic / "index.html",
            )
            self.assertEqual(
                resolve_control_asset(root, "app.js"),
                (classic / "app.js").resolve(),
            )
            self.assertEqual(
                resolve_control_asset(root, "assets/bundle.js"),
                (modern / "bundle.js").resolve(),
            )
            self.assertIsNone(resolve_control_asset(root, "../classic/app.js"))
            self.assertIsNone(resolve_control_asset(root, "index.html"))
            self.assertIsNone(resolve_control_asset(root, "src/main.js"))


if __name__ == "__main__":
    unittest.main()
