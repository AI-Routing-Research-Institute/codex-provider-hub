+++
id = "2026-08-07-auto-merge-clean-fallback"
type = "fix"
release_bump = "none"
status = "verified"
+++

# 收紧 clean 状态自动合并 fallback

## 目标

明确 GitHub 在 required checks 全部通过后拒绝启用 auto-merge 时的唯一安全处理方式，避免 Agent 将任意 API 错误误判为可直接合并。

## 现状

交付约束要求 Agent 启用 auto-merge，但 GitHub 在 PR 已处于 `clean` 状态时可能返回 `Pull request is in clean status`。现有文档没有规定该特殊状态的可验证 fallback，也没有明确禁止其他错误触发直接合并。

## 设计范围

- 仅允许在 auto-merge 启用失败原因为 `clean` 时进入 fallback。
- fallback 前必须查询 PR 的准确 head SHA，并确认 `policy`、`tests-windows`、`tests-macos` 三个 required checks 全部成功。
- fallback 必须使用该准确 head SHA 执行 squash merge，并在合并后验证目标提交。
- 将上述约束同步到根目录 Agent 规则、仓库 commit helper 和治理测试。

## 非目标

不改变 GitHub Ruleset、required checks、PR 审批数量、版本选择或自动发版工作流；不实现新的 GitHub API 客户端。

## 兼容性

无运行时影响，仅收紧 Agent 交付文档和对应治理测试；`release_bump = "none"`，不产生产品版本发布。

## 风险

GitHub API 错误文本或检查状态查询不完整时，Agent 将硬停止并要求人工排查，可能延迟合并，但不会绕过 required checks。合并 API 权限不足或 head SHA 发生变化时必须停止，禁止使用模糊 ref 重试。

## 测试计划

- 增加治理测试，检查 AGENTS.md 和 git-commit-helper 明确 clean-only、准确 head SHA、三个 checks 全部成功、squash merge 和其他错误硬停止。
- 运行 Python 全量单元测试、Node 语法检查和 Node 测试。
- 在 rebase 后运行仓库 pre-push 完整验证，并在 PR 上确认三个 required checks。

## 实际改动

- `AGENTS.md` 将 auto-merge 失败的 `clean` fallback 限定为唯一可恢复路径，要求准确 head SHA、三个 required checks 全成功、squash merge 和合并后验证。
- `.agents/skills/git-commit-helper/SKILL.md` 同步相同的硬停止条件，并要求记录 PR、checks、auto-merge/fallback 和发布结果。
- `tests/test_team_policy.py` 增加治理测试，防止移除上述限制。

## 验证结果

- `python -m unittest tests.test_team_policy.RepositoryGovernanceAssetTests.test_clean_auto_merge_fallback_is_strictly_bounded`：通过，1 项。
- `python -m unittest discover -s tests -p "test_*.py"`：通过，357 项。
- `node --check proxy_static/app.js`：通过。
- `node --check provider_status/static/app.js`：通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：通过，全部 JavaScript 测试通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/3
