---
name: git-commit-helper
description: Use when an Agent needs to stage, commit, push, open or update a pull request, enable auto-merge, or complete Git delivery work in this repository.
---

# Git Commit Helper

## Core Rule

All repository delivery goes through a feature branch, a permanent change record, required policy checks, and a pull request. Violating the literal steps violates the workflow.

## Mandatory Workflow

1. Run `python scripts/team_policy.py install-hooks`.
2. Fetch `origin/main`. Never begin from a stale base.
3. If currently on `main`, create a short-lived branch such as `feat/<slug>` or `fix/<slug>`. 禁止直接推送 `main`。
4. Before product edits, create `docs/changes/YYYY-MM-DD-<slug>.md` from the template with `status = "planned"`; complete target, current state, scope, non-goals, compatibility, risks, and test plan.
5. Inspect `git status --short`, staged diff, and unstaged diff. Split unrelated concerns and stage explicit paths only. Never use `git add .` or `git add -A`.
6. After implementation and focused tests, update the record to `implemented`. After fresh full verification, update it to `verified` and record exact commands/results.
7. Commit with `emoji type(scope): 简体中文描述`. Every body must contain non-empty `功能修改`, `影响范围`, and `验证结果` headings. Never add AI attribution or `Co-authored-by`.
8. Rebase on current `origin/main`, rerun full verification, and push only the feature branch.
9. Create or update the PR with the record summary, risks, and verification. Attempt to enable `auto-merge` (expected to fail while the ruleset does not enable `allow_auto_merge`); do not request human approval. PR 阶段不运行 CI，无 required checks；merge 只要求 PR 存在且无冲突。 Merge with `gh pr merge --squash` or the web UI's Squash and merge (gh CLI not required) using the PR's exact head SHA (准确 head SHA) and verify the resulting merge commit; never merge by branch name, stale SHA, ambiguous ref, or an unverified state. 完整测试（Python 单测、JS 语法检查、JS 测试）由 release tag 触发的 `windows-release.yml` / `macos-release.yml` 全量执行并阻塞发布。
10. Never create or push tags manually. The main-branch release coordinator selects SemVer from verified change records and dispatches both release workflows. Report the PR URL, squash merge result, release workflow URL, and both platform workflow results.

## Commit Types

| Prefix | Use |
|---|---|
| `🎉 init` | initialization |
| `✨ feat` | feature |
| `🐞 fix` | defect fix |
| `🦄 refactor` | behavior-preserving refactor |
| `🌈 style` | UI or formatting |
| `⚡️ perf` | performance |
| `📃 docs` | documentation |
| `🧪 test` | tests |
| `🐳 chore` | maintenance |
| `🔧 build` | build and release |

## Required Message

```text
✨ feat(scope): 具体中文行为描述

功能修改
- 逐项说明行为。

影响范围
- 模块、接口、配置、兼容性、迁移和风险；无影响也明确写无。

验证结果
- 实际命令及结果；未运行项说明原因。
```

## Stop Conditions

Stop and report the blocker when the branch is stale, a changed file is not understood, a record is absent/incomplete, local tests fail or were not run, or repository administration prevents merge/ruleset enforcement. Never use `--no-verify`, force push, `git push --all`, or `git push --tags` as a workaround.

| Rationalization | Required response |
|---|---|
| "The change is too small for a record." | Every independently deliverable behavior change needs a record. |
| "Direct main push is faster." | Main only changes through pull requests without conflicts. |
| "Old tests already passed." | Verification must target the rebased HEAD. |
| "CI can replace local review." | Inspect diffs and maintain the record before CI. |
