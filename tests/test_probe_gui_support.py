import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import probe_codex_cc_switch as backend


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_corrupt_file_fallback(self) -> None:
        from probe_tools import probe_gui_support as support

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            expected = {
                "schema_version": 1,
                "selected_provider_ids": ["provider-1"],
                "selected_models": ["gpt-5.4", "gpt-5.6-sol"],
                "custom_models": ["custom-model"],
            }
            support.save_settings(expected, path)
            self.assertEqual(support.load_settings(path), expected)

            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(support.load_settings(path), support.default_settings())


class ModelCatalogTests(unittest.TestCase):
    def make_provider(
        self,
        provider_id: str,
        name: str,
        model: str,
        *,
        current: bool = False,
    ) -> backend.ProviderRecord:
        return backend.ProviderRecord(
            provider_id=provider_id,
            name=name,
            is_current=current,
            endpoint_url="https://example.test/v1",
            common_config_enabled=False,
            raw_config=f'model = "{model}"\n',
            auth={},
            meta={},
        )

    def test_catalog_and_first_run_defaults(self) -> None:
        from probe_tools import probe_gui_support as support

        providers = [
            self.make_provider("p1", "One", "gpt-5.5"),
            self.make_provider("p2", "Current", "gpt-5.6-sol", current=True),
        ]

        catalog = support.build_model_catalog(
            providers,
            ["gpt-5.6-sol", "custom-model"],
        )
        provider_ids, models = support.default_selection(providers)

        self.assertEqual(
            catalog,
            [
                "gpt-5.4",
                "gpt-5.5",
                "gpt-5.6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "custom-model",
            ],
        )
        self.assertEqual(provider_ids, ["p2"])
        self.assertEqual(models, ["gpt-5.4", "gpt-5.6-sol"])


class CommandTests(unittest.TestCase):
    def test_build_probe_command_contains_exact_selections_and_defaults(self) -> None:
        from probe_tools import probe_gui_support as support

        command = support.build_probe_command(
            python_executable=Path(r"C:\Python314\python.exe"),
            backend_script=Path(r"D:\code\codex_provider_probe\probe_codex_cc_switch.py"),
            provider_ids=["provider-a", "provider-b"],
            models=["gpt-5.4", "gpt-5.6-sol"],
            codex_binary=Path(r"C:\Codex\codex.exe"),
            output_path=Path(r"D:\code\codex_provider_probe\reports\result.json"),
        )

        self.assertEqual(command.count("--provider"), 2)
        self.assertIn("provider-a", command)
        self.assertIn("provider-b", command)
        self.assertIn("gpt-5.4,gpt-5.6-sol", command)
        self.assertIn("2", command)
        self.assertIn("240", command)
        self.assertIn("high", command)
        self.assertIn("read-only", command)

    def test_resolve_codex_binary_uses_first_runnable_candidate(self) -> None:
        from probe_tools import probe_gui_support as support

        denied = Path(r"C:\WindowsApps\codex.exe")
        working = Path(r"C:\npm\codex.exe")

        def fake_run(command, **kwargs):
            if Path(command[0]) == denied:
                raise PermissionError("denied")
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.144.1", "")

        with patch("probe_tools.probe_gui_support.subprocess.run", side_effect=fake_run):
            resolved = support.resolve_codex_binary([denied, working])

        self.assertEqual(resolved, working)


class ReportTests(unittest.TestCase):
    def test_report_rows_include_status_elapsed_and_key_information(self) -> None:
        from probe_tools import probe_gui_support as support

        report = {
            "results": [
                {
                    "provider_name": "Code Link",
                    "model_runs": [
                        {
                            "model": "gpt-5.6-sol",
                            "status": "network_error",
                            "attempts": [
                                {
                                    "elapsed_seconds": 12.5,
                                    "error_summary": [
                                        "连接异常：响应完成前连接已断开（https://example.test/v1/responses）"
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        rows = support.report_rows(report)

        self.assertEqual(
            rows,
            [
                {
                    "provider": "Code Link",
                    "model": "gpt-5.6-sol",
                    "status": "network_error",
                    "status_label": "连接异常",
                    "elapsed": "12.5s",
                    "detail": "连接异常：响应完成前连接已断开（https://example.test/v1/responses）",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
