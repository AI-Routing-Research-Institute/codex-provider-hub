import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_SCRIPT = ROOT / "probe_codex_gui.py"


class ResultDetailTests(unittest.TestCase):
    def test_compact_detail_keeps_only_first_note_and_truncates_long_text(self) -> None:
        from probe_codex_gui import compact_result_detail

        self.assertEqual(
            compact_result_detail("鉴权失败：API Key 无效；Codex 出现重连"),
            "鉴权失败：API Key 无效",
        )
        self.assertEqual(len(compact_result_detail("很长" * 30)), 48)
        self.assertTrue(compact_result_detail("很长" * 30).endswith("…"))


class AttemptProgressTests(unittest.TestCase):
    def test_fast_defaults_and_remaining_timeout_text(self) -> None:
        from probe_codex_gui import (
            DEFAULT_ATTEMPTS,
            DEFAULT_TIMEOUT_SECONDS,
            format_attempt_progress,
        )

        self.assertEqual(DEFAULT_ATTEMPTS, 1)
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 90)
        self.assertEqual(
            format_attempt_progress("Provider Alpha", "gpt-5.5", 1, 1, 12, 90),
            "Provider Alpha | gpt-5.5 | 第 1/1 次尝试 | 已用 12s，剩余 78s",
        )
        self.assertEqual(
            format_attempt_progress("Provider Alpha", "gpt-5.5", 1, 1, 91, 90),
            "Provider Alpha | gpt-5.5 | 第 1/1 次尝试 | 已达到 90s 超时，正在终止子进程...",
        )


class GuiSmokeTests(unittest.TestCase):
    @unittest.skipIf(
        os.environ.get("CI") == "true",
        "smoke 模式依赖本机真实的 cc-switch 数据库与已安装的 codex 二进制，"
        "在 CI 全新环境上不具备这些真实数据，故跳过。",
    )
    def test_smoke_mode_reports_desktop_dependencies_without_opening_window(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(GUI_SCRIPT), "--smoke-test"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["title"], "Codex 供应商探测")
        self.assertGreaterEqual(payload["provider_count"], 1)
        self.assertGreaterEqual(payload["model_count"], 3)
        self.assertTrue(payload["codex_binary_found"])
        self.assertTrue(payload["settings_path"].endswith("settings.json"))


if __name__ == "__main__":
    unittest.main()
