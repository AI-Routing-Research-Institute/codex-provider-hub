+++
id = "2026-08-14-agents-md-gate-sync"
type = "chore"
release_bump = "none"
status = "verified"
+++

# AGENTS.md 门禁描述与当前规则集对齐

## 目标

消除 AGENTS.md 与仓库实际门禁策略的冲突：AGENTS.md 仍要求 `policy` / `tests-windows` / `tests-macos` 三个 required checks 配置并全部成功后才允许合并，而远端规则集已按 2026-08-12/13 变更移除这些检查，导致 Agent 读取指令后自我硬停止、无法交付。

## 现状

- 远端规则集 `agent-delivery-main` 仅含 `deletion` + `pull_request`（0 审批、无 required status checks，PR 无冲突即可合并）。
- `pr-policy.yml` 已删除（PR #29），PR 阶段不运行 CI；全量测试移至 release tag 时由 `windows-release.yml` / `macos-release.yml` 执行。
- AGENTS.md「Git 与 PR」「硬停止条件」仍引用三个 required checks 与 clean fallback 流程，与现状冲突；实际合并方式为 `gh pr merge --squash`（规则集未开放 `allow_auto_merge`）。

## 设计范围

- AGENTS.md 门禁描述与当前规则集/CI 策略对齐：
  - PR 合并门槛改为「PR 存在且无冲突」，明确 PR 阶段无 CI、无 required checks。
  - 测试验证责任明确为 release tag 全量执行 + 本地测试必须通过。
  - 合并方式明确为 `gh pr merge --squash`（基于准确 head SHA，合并后验证目标提交）。
  - 硬停止条件移除「required checks 未配置」，保留「本地测试未运行或失败」「Ruleset 未验证」等。

## 非目标

- 不修改 `scripts/team_policy.py` 的规则集模板（#22 已与远端一致）。
- 不重新启用 PR 阶段 CI 或 required status checks。
- 不调整规则集的 deletion / pull_request 内容。

## 兼容性

无接口、配置或数据影响；仅仓库约束文档文本更新。

## 风险

- 若未来重新启用 CI 门禁，需同步回 AGENTS.md —— 低风险，随规则集变更一并处理。
- 移除 checks 描述后 Agent 可能误以为无需任何验证 —— 缓解：保留「本地测试未运行或失败必须停止」硬停止条件。

## 测试计划

- `git diff --check` 通过。
- `python scripts/team_policy.py pre-commit` / `commit-msg` / `pre-push` 校验通过。
- `python -m unittest discover -s tests -p "test_team_policy.py"` 无回归。
- 远端规则集与 `build_ruleset_payload()` 一致（verify-ruleset 通过）。

## 实际改动

- `AGENTS.md`：禁止理由（L3）、合并门槛与合并方式（L34-38）、跟踪项（L45）、硬停止条件（L49）与当前门禁对齐。
- `.agents/skills/git-commit-helper/SKILL.md`：step 9 合并门槛与合并方式、step 10 报告项、Stop Conditions、理由映射表同步对齐。
- `tests/test_team_policy.py`：`test_clean_auto_merge_fallback_is_strictly_bounded` 重写为 `test_merge_policy_matches_ruleset_gates`，断言新门禁（无 tests-windows/tests-macos、无冲突即可合并、release tag 全量测试）。
- 新增本变更说明。

## 验证结果

- `git diff --check`：通过。
- team_policy.py pre-commit / commit-msg / pre-push：通过。
- test_team_policy.py：规则集相关 25 项通过（本机 curl_cffi 缺失导致的无关失败除外，与 #22 记录一致）。
- verify-ruleset：`{"name": "agent-delivery-main", "id": 20543407, "verified": true}`

## PR

pending
