+++
id = "2026-08-13-pr-23-delivery-record"
type = "docs"
release_bump = "none"
status = "verified"
+++

# 补充 PR #23 交付映射

## 目标

在不改写 `v0.10.0` 已发布功能说明的前提下，新增永久记录，明确 PR #23 与托盘入口精简、请求推理强度展示、上游 403 重试三项变更的交付关系。

## 现状

PR #23 已合并并由 `v0.10.0` 发布，但对应的三份已发布功能说明仍保留 `PR: pending`。仓库规则禁止发布后改写原说明，因此需要通过后续记录补充交付映射。

## 设计范围

- 保持三份 `2026-08-12` 已发布功能说明原样。
- 新增本说明，列出 PR #23、合并提交、版本与对应功能说明。
- 版本增量选择 `none`，因为本次仅补充审计文档，不改变产品行为或发布产物。

## 非目标

- 不修改已发布功能说明。
- 不修改产品代码、配置、数据库、UI 或工作流。
- 不创建新版本或重新发布 `v0.10.0`。

## 兼容性

无接口、配置、数据、运行时或发布兼容性影响。

## 风险

主要风险是交付链接或提交信息记录错误；通过 GitHub PR、合并提交和 Release 页面交叉核对降低风险。

## 测试计划

- 检查相对 `origin/main` 的完整 diff，确认仅新增本说明。
- 运行 Python、JavaScript、语法与 PR 策略全量验证。
- 确认自动发布计划因 `release_bump = "none"` 不创建新版本。

## 实际改动

新增本说明，记录 PR #23 对应以下已发布变更：

- `2026-08-12-remove-claude-tray-entry`
- `2026-08-12-request-reasoning-effort`
- `2026-08-12-upstream-403-retry`

交付信息：

- 合并提交：`c1a0478cd9872545d4aafe23564599451194fbea`
- 发布版本：`v0.10.0`
- 发布页面：https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/tag/v0.10.0

## 验证结果

- `python -m unittest discover -s tests -p "test_*.py"`：通过，423 项测试全部成功。
- `node --check proxy_static/app.js`：通过。
- `node --check provider_status/static/app.js`：通过。
- 对 `tests/*.test.js` 逐个执行 `node --test`：通过，40 项测试全部成功。
- `git diff --cached --check`：通过，无空白错误。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/25
