+++
id = "2026-08-28-fix-changelog-consistency"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 修正变更记录状态一致性与类型表

## 目标

修复恢复自动发版后暴露出的两处变更记录一致性问题，使 `release-plan` 能正确计算版本：一是历史记录使用了非法的 `status` 值，二是 `ALLOWED_TYPES` 缺失 `style` 类型导致 UI 重构记录校验失败。

## 现状

- `docs/changes/2026-08-21-tailwind-css-integration.md` 的 status 为 `completed`，不属于 `{planned, implemented, verified}`，`release-plan` 校验失败。
- #48 起合入的 8 条 UI 变更记录使用 `type = "style"`，但 `ALLOWED_TYPES` 未包含 `style`（commit emoji 体系却允许 🌈 style），导致校验失败。
- 上述问题使 auto-release 的 `Calculate release plan` 步骤失败，发布链路中断。

## 设计范围

- 将 `2026-08-21-tailwind-css-integration.md` 的 status 修正为 `verified`。
- 在 `scripts/team_policy.py` 的 `ALLOWED_TYPES` 中增加 `style`，与 commit 类型体系对齐。
- 在恢复自动发版变更记录 `2026-08-28-restore-auto-release.md` 中补充 #49 的 PR 记录与上述适配说明。

## 非目标

- 不修改其他历史变更记录的内容与 bump 声明。
- 不调整 `release-plan` 的版本计算逻辑（最高 bump 取范围内 verified 记录的最大值，本记录 `release_bump = "none"` 不参与提升）。

## 兼容性

- 无接口、配置或数据影响；仅变更记录元数据与类型校验表。
- `ALLOWED_TYPES` 新增 `style` 为向后兼容扩展，既有类型不受影响。

## 风险

- 历史记录篡改风险 —— 缓解：仅修正 status 非法值，不动 bump 与正文；且修正后 release-plan 可完整跑通，本地已验证。
- 新增 `style` 类型被滥用 —— 缓解：该类型已在既有 8 条记录中使用，属于回归既有事实而非新开口径。

## 测试计划

- `python scripts/team_policy.py release-plan --base-tag v1.0.2 --head HEAD` 输出 release=true / minor / v1.1.0。
- `python -m unittest discover -s tests -p "test_*.py"` 全量通过。
- `git diff --check` 通过。

## 实际改动

- `docs/changes/2026-08-21-tailwind-css-integration.md`：status `completed` → `verified`。
- `scripts/team_policy.py`：`ALLOWED_TYPES` 增加 `style`。
- `docs/changes/2026-08-28-restore-auto-release.md`：PR 段补充 #49，实际改动段补充适配说明。
- 新增本变更说明。

## 验证结果

- release-plan 本地复现：`{"release": true, "bump": "minor", "tag": "v1.1.0", "records": 31 条}`。
- 全量单元测试 484 项通过。
- `git diff --check` 通过。

## PR

pending
