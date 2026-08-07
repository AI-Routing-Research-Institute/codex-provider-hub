+++
id = "2026-08-07-provider-launch-command-pr-record"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 补正供应商临时启动命令发布记录

## 目标

为已合并的供应商临时启动命令功能补充准确的 PR URL，恢复变更记录的可追溯性。

## 现状

原功能说明已随 PR #4 合并到 `main`，但其中的 `PR` 字段仍为 `pending`。功能代码、测试和发布流程已经完成，缺少的是发布记录元数据。

## 设计范围

- 新增独立的补正变更说明，记录原功能说明、PR #4、合并 commit 和验证证据之间的关系。
- release bump 固定为 `none`，不重复触发版本发布。

## 非目标

- 不修改已发布的原功能说明。
- 不修改产品代码、配置、数据库或发布标签。
- 不重新发布 `v0.2.0`。

## 兼容性

仅新增文档记录，无运行时、接口、配置、数据或迁移影响。

## 风险

若遗漏最终 PR URL，后续发布说明无法从变更记录定位完整审查证据。本记录通过固定链接、合并 commit 和已完成检查降低该风险。

## 测试计划

- 运行仓库策略检查，确认新记录结构、状态、PR URL 和 release bump 合法。
- 检查工作区 diff，不运行与文档补正无关的产品测试。

## 实际改动

- 新增本记录，关联原功能说明 `docs/changes/2026-08-07-provider-launch-command.md`、PR #4、合并 commit `af1c26c09fbcf8ae1490fe8e2ad8e91199042145` 和最终验证结果。

## 验证结果

- `python scripts/team_policy.py pre-commit`：通过。
- `git diff --cached --check`：通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/5
