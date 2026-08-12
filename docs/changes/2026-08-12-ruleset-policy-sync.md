+++
id = "2026-08-12-ruleset-policy-sync"
type = "chore"
release_bump = "none"
status = "implemented"
+++

# 同步 Ruleset 校验与门禁配置

## 目标

让 `scripts/team_policy.py` 的规则集期望模板与远端 `agent-delivery-main` 实际配置一致，消除协作者提交时被 pre-push 钩子拦截的误报。

## 现状

远端 `agent-delivery-main` 规则集已调整为仅保留 `deletion` + `pull_request`（合并前不再检测 CI、允许管理员强制推送），但 `team_policy.py` 的 `build_ruleset_payload()` 仍期望包含 `non_fast_forward` 和 `required_status_checks`（policy / tests-windows / tests-macos），导致 `verify-ruleset` 校验失败，协作者推送被拦截并提示：

```
远端 agent-delivery-main Ruleset 缺少: non_fast_forward, required_status_checks, policy, tests-windows, tests-macos
```

## 设计范围

- 从 `build_ruleset_payload()` 移除 `non_fast_forward` 与 `required_status_checks` 两条规则
- 保留 `deletion` 与 `pull_request` 规则
- 不动 `verify_ruleset` 的校验逻辑（`expected_types.issubset(actual_types)` 子集检查天然兼容）

## 非目标

- 不修改远端规则集（维持当前门禁：合并前不查 CI、admin 可强制推送）
- 不调整 commit/PR 其他策略

## 兼容性

- `configure-ruleset` 与 `verify-ruleset` 命令的接口不变
- 校验逻辑（target/enforcement/conditions 精确比对 + 规则类型子集检查）不变
- 本地钩子行为不变，仅期望模板与远端对齐

## 风险

- 若未来有人重新启用 CI 门禁，需同步恢复脚本中的两条规则 —— 低风险，可在规则集变更时一并处理

## 测试计划

- 运行 `python scripts/team_policy.py verify-ruleset --repo AI-Routing-Research-Institute/codex-provider-hub` 确认校验通过
- 运行 Python unittest 确认无回归

## 实际改动

- `scripts/team_policy.py`：`build_ruleset_payload()` 移除 `non_fast_forward` 与 `required_status_checks`

## 验证结果

- `python scripts/team_policy.py verify-ruleset --repo AI-Routing-Research-Institute/codex-provider-hub` → `{"name": "agent-delivery-main", "id": 20543407, "verified": true}`
- `python -m unittest discover -s tests -p "test_team_policy.py"` → 25 个测试全部通过（规则集相关断言已同步为无 CI 门禁）
- 完整测试套件仅剩 7 个 `curl_cffi` 依赖缺失错误（与本次改动无关，本机 pip 源无法安装该包）

## PR

pending
