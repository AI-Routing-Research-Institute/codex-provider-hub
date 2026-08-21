import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectDocumentationTests(unittest.TestCase):
    def test_license_contains_standard_agpl_v3_terms(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 19 November 2007", license_text)
        self.assertIn("interacting with it remotely through a computer network", license_text)
        self.assertIn("Corresponding Source", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)

    def test_notice_preserves_project_origin(self) -> None:
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")

        self.assertIn("Codex Provider Hub", notice)
        self.assertIn("Copyright (c) 2026 Codex Provider Hub contributors", notice)
        self.assertIn(
            "https://github.com/AI-Routing-Research-Institute/codex-provider-hub",
            notice,
        )
        self.assertIn("LICENSE", notice)
        self.assertIn("NOTICE", notice)

    def test_commercial_license_keeps_agpl_commercial_rights(self) -> None:
        commercial = (ROOT / "COMMERCIAL-LICENSE.md").read_text(encoding="utf-8")

        self.assertIn("闭源商业授权", commercial)
        self.assertIn("AGPL-3.0-or-later", commercial)
        self.assertIn("无需购买商业许可证", commercial)
        self.assertIn("GitHub Issue", commercial)
        self.assertIn("不构成商业许可证", commercial)
        self.assertNotIn("所有商业使用都必须付费", commercial)

    def test_readme_is_a_cc_switch_first_user_guide(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        required_sections = (
            "## 适合谁",
            "## 五分钟快速开始",
            "## 推荐搭配 CC Switch",
            "## 配置 Codex",
            "## 配置 Claude Code",
            "## 管理和切换供应商",
            "## 请求传输方式",
            "## 常见问题",
            "## 开源与商业授权",
        )

        for section in required_sections:
            with self.subTest(section=section):
                self.assertIn(section, readme)

        self.assertIn("http://127.0.0.1:17890/control/codex/", readme)
        self.assertIn("http://127.0.0.1:17890/control/claude/", readme)
        self.assertIn('ANTHROPIC_BASE_URL": "http://127.0.0.1:17890"', readme)
        self.assertIn("curl_cffi", readme)
        self.assertIn("COMMERCIAL-LICENSE.md", readme)
        self.assertNotIn("当前仓库尚未附带开源许可证", readme)
        self.assertNotIn("所有商业使用都必须付费", readme)


if __name__ == "__main__":
    unittest.main()
