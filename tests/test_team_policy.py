import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.team_policy as team_policy
from scripts.team_policy import (
    PolicyError,
    ChangeRecord,
    bump_version,
    build_ruleset_payload,
    build_release_plan,
    highest_bump,
    parse_change_record,
    validate_branch_name,
    validate_change_record,
    validate_commit_identities,
    validate_commit_message,
    validate_staged_paths,
    render_release_notes,
    diff_new_failures,
    parse_node_failures,
    parse_unittest_failures,
    read_baseline_cache,
    write_baseline_cache,
    _coauthor_names,
    _extract_name,
)

ROOT = Path(__file__).resolve().parents[1]


VALID_BODY = """+++
id = "2026-08-07-agent-delivery-pipeline"
type = "feature"
release_bump = "minor"
status = "planned"
+++

# Agent delivery

## 目标

建立 Agent 自驱交付流水线。

## 现状

当前只有 Markdown 约束。

## 设计范围

策略与交付自动化。

## 非目标

不部署外部服务。

## 兼容性

无运行时影响。

## 风险

远端规则需要管理权限。

## 测试计划

运行策略和全量测试。

## 实际改动

等待实施。

## 验证结果

等待验证。

## PR

pending
"""


class ChangeRecordTests(unittest.TestCase):
    def write_record(self, content: str = VALID_BODY) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "record.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_parses_toml_frontmatter_and_required_sections(self) -> None:
        record = parse_change_record(self.write_record())

        self.assertEqual(record.metadata["release_bump"], "minor")
        self.assertEqual(record.sections["目标"], "建立 Agent 自驱交付流水线。")
        validate_change_record(record, required_status="planned")

    def test_rejects_invalid_metadata_and_empty_sections(self) -> None:
        invalid_bump = VALID_BODY.replace('release_bump = "minor"', 'release_bump = "huge"')
        with self.assertRaisesRegex(PolicyError, "release_bump"):
            validate_change_record(
                parse_change_record(self.write_record(invalid_bump)),
                required_status="planned",
            )

        empty_goal = VALID_BODY.replace("建立 Agent 自驱交付流水线。", "")
        with self.assertRaisesRegex(PolicyError, "目标"):
            validate_change_record(
                parse_change_record(self.write_record(empty_goal)),
                required_status="planned",
            )

    def test_rejects_status_below_required_state(self) -> None:
        record = parse_change_record(self.write_record())

        with self.assertRaisesRegex(PolicyError, "verified"):
            validate_change_record(record, required_status="verified")

    def test_rejects_malformed_frontmatter(self) -> None:
        with self.assertRaisesRegex(PolicyError, "frontmatter"):
            parse_change_record(self.write_record("# no frontmatter\n"))


class SemanticVersionTests(unittest.TestCase):
    def test_bumps_strict_semantic_versions(self) -> None:
        self.assertEqual(bump_version("v0.1.7", "patch"), "v0.1.8")
        self.assertEqual(bump_version("v0.1.7", "minor"), "v0.2.0")
        self.assertEqual(bump_version("v0.1.7", "major"), "v1.0.0")

    def test_rejects_invalid_tags_and_bumps(self) -> None:
        with self.assertRaisesRegex(PolicyError, "semantic version"):
            bump_version("release-1", "patch")
        with self.assertRaisesRegex(PolicyError, "bump"):
            bump_version("v1.2.3", "huge")


class GitPolicyTests(unittest.TestCase):
    def test_accepts_detailed_chinese_emoji_commit(self) -> None:
        validate_commit_message(
            "✨ feat(policy): 新增交付门禁\n\n"
            "功能修改\n- 新增策略。\n\n"
            "影响范围\n- 开发流程。\n\n"
            "验证结果\n- 单元测试通过。\n"
        )

    def test_rejects_missing_or_empty_commit_sections(self) -> None:
        with self.assertRaisesRegex(PolicyError, "功能修改"):
            validate_commit_message("✨ feat(policy): 新增交付门禁\n")
        with self.assertRaisesRegex(PolicyError, "影响范围"):
            validate_commit_message(
                "✨ feat(policy): 新增交付门禁\n\n"
                "功能修改\n- 新增策略。\n\n"
                "影响范围\n\n"
                "验证结果\n- 测试通过。\n"
            )

    def test_rejects_noncompliant_commit_title(self) -> None:
        with self.assertRaisesRegex(PolicyError, "emoji"):
            validate_commit_message(
                "feat(policy): add policy\n\n"
                "功能修改\n- 新增。\n\n影响范围\n- 无。\n\n验证结果\n- 通过。\n"
            )

    def test_rejects_protected_branches(self) -> None:
        validate_branch_name("feat/agent-delivery-governance")
        for branch in ("main", "master"):
            with self.assertRaisesRegex(PolicyError, branch):
                validate_branch_name(branch)

    def test_rejects_generated_or_sensitive_staged_paths(self) -> None:
        validate_staged_paths(
            ["local_proxy/core.py", "docs/changes/2026-08-07-feature.md"]
        )
        for path in ("dist/app.exe", ".env", "private/token.txt", "state.sqlite3"):
            with self.subTest(path=path), self.assertRaisesRegex(
                PolicyError, "禁止提交"
            ):
                validate_staged_paths([path])

    def test_accepts_whitelisted_commit_identities(self) -> None:
        validate_commit_identities(
            [("author", "moye12325"), ("committer", "GitHub")]
        )
        validate_commit_identities(
            [
                ("author", "loongkkk"),
                ("committer", "LOONGKKK\\loong"),
                ("co-author", "Gao Yiheng"),
            ]
        )

    def test_rejects_any_identity_outside_whitelist(self) -> None:
        with self.assertRaisesRegex(PolicyError, "BY250013"):
            validate_commit_identities(
                [("author", "moye12325"), ("committer", "BY250013")]
            )
        with self.assertRaisesRegex(PolicyError, "BY250013"):
            validate_commit_identities(
                [
                    ("author", "moye12325"),
                    ("committer", "moye12325"),
                    ("co-author", "BY250013"),
                ]
            )

    def test_extracts_name_and_coauthor_names(self) -> None:
        self.assertEqual(
            _extract_name("moye12325 <gengkang12325@gmail.com> 1700000000 +0800"),
            "moye12325",
        )
        self.assertEqual(_extract_name("LOONGKKK\\loong <loongk@vip.qq.com>"), "LOONGKKK\\loong")
        self.assertEqual(
            _coauthor_names(
                "✨ feat(policy): 门禁\n\n功能修改\n- x\n\n"
                "Co-authored-by: Gao Yiheng <gaoyiheng2021@gmail.com>\n"
                "Co-authored-by: BY250013 <gkk@originqc.com>\n"
            ),
            ["Gao Yiheng", "BY250013"],
        )


class RepositoryGovernanceAssetTests(unittest.TestCase):
    def test_repository_skill_enforces_delivery_workflow(self) -> None:
        skill = (ROOT / ".agents/skills/git-commit-helper/SKILL.md").read_text(
            encoding="utf-8"
        )
        metadata = (
            ROOT / ".agents/skills/git-commit-helper/agents/openai.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("name: git-commit-helper", skill)
        self.assertIn("Use when", skill)
        self.assertIn("docs/changes/", skill)
        self.assertIn("禁止直接推送 `main`", skill)
        self.assertIn("auto-merge", skill)
        self.assertIn("$git-commit-helper", metadata)

    def test_agents_requires_repository_skill_and_agent_owned_delivery(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn(".agents/skills/git-commit-helper/SKILL.md", agents)
        self.assertIn("功能分支", agents)
        self.assertIn("docs/changes/", agents)
        self.assertIn("Windows", agents)
        self.assertIn("macOS", agents)
        self.assertIn("auto-merge", agents)
        self.assertIn("不再自动发版", agents)
        self.assertIn("人工确认发版", agents)
        self.assertIn("gh workflow run release.yml", agents)
        self.assertNotIn("只有用户针对具体版本号", agents)

    def test_merge_policy_matches_ruleset_gates(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        skill = (ROOT / ".agents/skills/git-commit-helper/SKILL.md").read_text(
            encoding="utf-8"
        )

        for content in (agents, skill):
            self.assertIn("准确 head SHA", content)
            self.assertIn("squash", content)
            self.assertIn("无冲突", content)
            self.assertIn("禁止", content)
            self.assertNotIn("tests-windows", content)
            self.assertNotIn("tests-macos", content)
        self.assertIn("不强制 gh CLI", agents)
        self.assertIn("gh CLI not required", skill)
        self.assertIn("PR 阶段不运行 CI", agents)
        self.assertIn("release tag", agents)
        self.assertIn("基线之外的新增失败", agents)
        self.assertIn("基线相同的既有失败直接放行", agents)

    def test_hook_wrappers_are_versioned_and_call_policy(self) -> None:
        for hook_name in ("pre-commit", "commit-msg", "pre-push"):
            content = (ROOT / ".githooks" / hook_name).read_text(encoding="utf-8")
            self.assertIn("scripts/team_policy.py", content)
            self.assertIn("#!/bin/sh", content)

    def test_feature_record_template_contains_all_required_sections(self) -> None:
        template = (ROOT / "docs/changes/template.md").read_text(encoding="utf-8")
        self.assertIn('status = "planned"', template)
        for section in (
            "目标", "现状", "设计范围", "非目标", "兼容性", "风险",
            "测试计划", "实际改动", "验证结果", "PR",
        ):
            self.assertIn(f"## {section}", template)

    def test_release_workflows_run_full_test_suite_on_tag(self) -> None:
        for name in ("windows-release.yml", "macos-release.yml"):
            workflow = (ROOT / ".github/workflows" / name).read_text(
                encoding="utf-8"
            )
            self.assertIn("push:", workflow)
            self.assertIn("tags:", workflow)
            self.assertIn("python -m unittest discover", workflow)
            self.assertIn("npm run build --prefix proxy_static", workflow)
            self.assertIn("node --check proxy_static/classic/app.js", workflow)
            self.assertIn("Get-ChildItem proxy_static/src", workflow)
            self.assertIn("node --check provider_status/static/app.js", workflow)

    def test_ruleset_payload_protects_main_without_approvals(self) -> None:
        payload = build_ruleset_payload()

        self.assertEqual(payload["name"], "agent-delivery-main")
        self.assertEqual(payload["target"], "branch")
        self.assertEqual(payload["enforcement"], "active")
        self.assertEqual(
            payload["conditions"]["ref_name"]["include"],
            ["refs/heads/main"],
        )
        rules = {rule["type"]: rule for rule in payload["rules"]}
        self.assertIn("deletion", rules)
        self.assertNotIn("non_fast_forward", rules)
        self.assertNotIn("required_status_checks", rules)
        self.assertEqual(
            rules["pull_request"]["parameters"]["required_approving_review_count"],
            0,
        )
        self.assertEqual(
            set(payload["rules"][i]["type"] for i in range(len(payload["rules"]))),
            {"deletion", "pull_request"},
        )

    def test_release_workflow_is_manual_dispatch_and_dispatches_both_platforms(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertFalse((ROOT / ".github/workflows/auto-release.yml").exists())
        self.assertNotIn("branches:", workflow)
        self.assertIn("release-main", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("actions: write", workflow)
        self.assertIn("windows-release.yml", workflow)
        self.assertIn("macos-release.yml", workflow)
        self.assertIn("workflow run", workflow)

    def test_release_workflows_render_notes_from_records(self) -> None:
        for name in ("windows-release.yml", "macos-release.yml"):
            workflow = (ROOT / ".github/workflows" / name).read_text(
                encoding="utf-8"
            )
            if name == "windows-release.yml":
                self.assertIn("release-notes", workflow)


class VerificationBaselineTests(unittest.TestCase):
    def test_parses_unittest_failure_ids_including_rerun_timing_suffix(self) -> None:
        output = (
            "======================================================================\n"
            "FAIL: test_alpha (tests.test_proxy_core.UsageTests.test_alpha) [0.001s]\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "AssertionError: 1 != 0\n"
            "\n"
            "======================================================================\n"
            "ERROR: test_beta (tests.test_status_config.ConfigTests.test_beta)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "ValueError: boom\n"
            "\n"
            "----------------------------------------------------------------------\n"
            "Ran 2 tests in 0.1s\n"
            "\n"
            "FAILED (failures=1, errors=1)\n"
        )

        self.assertEqual(
            parse_unittest_failures(output),
            [
                "tests.test_proxy_core.UsageTests.test_alpha",
                "tests.test_status_config.ConfigTests.test_beta",
            ],
        )

    def test_parses_unittest_failures_deduplicates_repeated_ids(self) -> None:
        output = (
            "ERROR: test_dup (tests.mod.Class.test_dup)\n"
            "FAIL: test_dup (tests.mod.Class.test_dup)\n"
        )

        self.assertEqual(parse_unittest_failures(output), ["tests.mod.Class.test_dup"])

    def test_parses_node_tap_failure_names_and_ignores_passing_lines(self) -> None:
        output = (
            "TAP version 13\n"
            "ok 1 - passing case\n"
            "    not ok 2 - failing case # duration\n"
            "        not ok 3 - nested failure\n"
            "1..3\n"
            "# fail 2\n"
        )

        self.assertEqual(
            parse_node_failures(output), ["failing case", "nested failure"]
        )

    def test_diff_new_failures_passes_baseline_subsets_and_flags_new_entries(self) -> None:
        baseline = {
            "python": ["tests.old.EnvTests.test_dns"],
            "node": {"tests/a.test.js": ["old flaky"]},
        }

        subset_head = {
            "python": ["tests.old.EnvTests.test_dns"],
            "node": {"tests/a.test.js": ["old flaky"]},
        }
        self.assertEqual(diff_new_failures(subset_head, baseline), [])

        new_head = {
            "python": ["tests.old.EnvTests.test_dns", "tests.new.BugTests.test_regression"],
            "node": {"tests/a.test.js": ["old flaky"], "tests/b.test.js": ["broken case"]},
        }
        self.assertEqual(
            diff_new_failures(new_head, baseline),
            [
                "python tests.new.BugTests.test_regression",
                "node tests/b.test.js :: broken case",
            ],
        )

    def test_diff_new_failures_flags_suite_sentinels(self) -> None:
        baseline = {"python": [], "node": {}}

        self.assertEqual(
            diff_new_failures({"python": ["<python-suite-exit-nonzero>"], "node": {}}, baseline),
            ["python <python-suite-exit-nonzero>"],
        )
        self.assertEqual(
            diff_new_failures(
                {"python": [], "node": {"tests/c.test.js": ["<node-suite-exit-nonzero>"]}},
                baseline,
            ),
            ["node tests/c.test.js :: <node-suite-exit-nonzero>"],
        )

    def test_baseline_cache_roundtrip_and_stale_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "new-base.json"
            (Path(temp) / "old-base.json").write_text("{}", encoding="utf-8")

            def fake_path(merge_base: str) -> Path:
                return Path(temp) / f"{merge_base}.json"

            self.assertIsNone(read_baseline_cache("new-base"))
            with mock.patch.object(team_policy, "_baseline_cache_path", fake_path):
                write_baseline_cache("new-base", {"python": ["tests.x.Y.test_z"], "node": {}})
                loaded = read_baseline_cache("new-base")

            self.assertEqual(loaded, {"python": ["tests.x.Y.test_z"], "node": {}})
            self.assertTrue(cache.exists())
            self.assertFalse((Path(temp) / "old-base.json").exists())


class ReleasePlanningTests(unittest.TestCase):
    def make_record(self, bump: str, title: str) -> ChangeRecord:
        return ChangeRecord(
            path=Path(f"docs/changes/{title}.md"),
            metadata={"id": title, "type": "feature", "release_bump": bump, "status": "verified"},
            sections={
                "目标": title,
                "现状": "old",
                "设计范围": "scope",
                "非目标": "none",
                "兼容性": "none",
                "风险": "none",
                "测试计划": "tests",
                "实际改动": "changed",
                "验证结果": "passed",
                "PR": "https://github.com/example/pr/1",
            },
        )

    def test_selects_highest_release_bump_and_next_tag(self) -> None:
        records = [self.make_record("patch", "fix-one"), self.make_record("minor", "feature-two")]

        self.assertEqual(highest_bump([record.metadata["release_bump"] for record in records]), "minor")
        plan = build_release_plan("v0.1.7", records)
        self.assertTrue(plan["release"])
        self.assertEqual(plan["tag"], "v0.2.0")

    def test_none_records_do_not_release_and_notes_include_verified_changes(self) -> None:
        record = self.make_record("none", "docs-only")
        plan = build_release_plan("v0.1.7", [record])

        self.assertFalse(plan["release"])
        notes = render_release_notes("v0.1.8", [record])
        self.assertIn("docs-only", notes)
        self.assertIn("验证结果", notes)


if __name__ == "__main__":
    unittest.main()
