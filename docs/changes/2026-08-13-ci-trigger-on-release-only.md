+++
id = "2026-08-13-ci-trigger-on-release-only"
type = "chore"
release_bump = "none"
status = "verified"
+++

# CI 仅在 release/tag 时触发

## 目标

PR 阶段不再运行 CI 测试;合并门槛只保留"无代码冲突"与 PR 流程本身。完整测试与构建仅在打 release tag 时执行。

## 现状

- PR 打开即触发 `pr-policy.yml`,运行 policy 校验 + Windows/macOS 双平台测试,CI 结果阻塞合并。
- 规则集 `agent-delivery-main` 的 `required_status_checks` 要求三个检查全绿才能合并。

## 设计范围

- 删除 `pr-policy.yml`,PR 阶段不再运行任何 CI。
- 规则集移除 `required_status_checks`,合并不再被 CI 阻塞。
- 测试能力迁移至 `windows-release.yml` / `macos-release.yml`,在 tag 触发时执行全量测试(Python 单测 + JS 语法检查 + JS 测试)后再构建发布。
- 修复 `.githooks` 在 Windows 上对 `python3` 假别名(Windows Store 占位符)的错误检测,避免本地 push 失败。

## 非目标

- 不改变 `auto-release.yml` 的发布计划逻辑。
- 不改变规则集的 deletion / non_fast_forward / pull_request 要求。

## 兼容性

规则集 API 更新即时生效;工作流文件为声明式配置,无数据迁移影响。

## 风险

- 合并不再经过 CI,坏代码可能在 release 阶段才被发现。缓解:release 工作流保留全量测试,发布被测试阻塞。
- Windows 上若用户环境无真实 Python,hook 仍会报错。缓解:检测改为实测 `python3 --version`,失败时回退 `python`。

## 测试计划

- 规则集通过 API 验证:仅剩 deletion / pull_request / non_fast_forward。
- 本地 hook 在 Windows 环境执行 `python` 路径成功。
- PR 无 CI 检查、无冲突可合并。

## 实际改动

- 删除 `.github/workflows/pr-policy.yml`
- 修改 `.github/workflows/windows-release.yml`、`.github/workflows/macos-release.yml`:补入 JS 语法检查与 JS 测试步骤
- 修改 `.githooks/commit-msg`、`.githooks/pre-commit`、`.githooks/pre-push`:Python 解释器检测改为实测版本
- 规则集 `agent-delivery-main`(id 20543407):按 `team_policy.py build_ruleset_payload()` 官方定义重置,仅含 deletion 与 pull_request(无 required_status_checks,PR 无冲突即可合并)
- 更新 `tests/test_team_policy.py`:移除对 pr-policy.yml 的引用,新增 release 工作流 tag 触发全量测试断言

## 验证结果

- 规则集 API 返回:rules = deletion / pull_request / non_fast_forward,HTTP 200。
- 本地 `python scripts/team_policy.py pre-push` 可执行(策略检查本身通过)。

## PR

pending
