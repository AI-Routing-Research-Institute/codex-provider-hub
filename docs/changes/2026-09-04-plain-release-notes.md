+++
id = "2026-09-04-plain-release-notes"
type = "chore"
release_bump = "patch"
headline = "版本更新说明改为一眼看懂的简短摘要：按新功能、问题修复分组，每条一句话"
status = "verified"
+++

# Release 信息改为通俗简短的用户摘要

## 目标

把 GitHub Release 正文从"变更说明全文堆叠"改为面向用户的通俗摘要：按 新功能/问题修复/其他改进 分组，每条一行（功能名 + 一句话说明），文末附变更说明链接。优化不增加任何门禁或发版耗时。

## 现状

- `render_release_notes()`（`scripts/team_policy.py`）把每条 verified 说明的目标、实际改动、兼容性、风险、验证结果、PR 六个章节全文倒进 release 正文；其中"实际改动/验证结果"是长篇技术审计（含命令、SHA、文件清单），用户看不懂也不想看。
- 发版工作流（`windows-release.yml`）经 `release-notes` CLI 生成 `.build/release-notes.md` 后作为 release body 发布。

## 设计范围

1. 重写 `render_release_notes()`：
   - 标题 `# Codex 本地中转 {tag}` + 一句导语（本次更新 N 项改进）；
   - 按 metadata.type 分组：feature → ✨ 新功能、fix → 🐞 问题修复、其余 → 🛠️ 其他改进；
   - 每条一行：`- **{功能名}**：{一句话}`；功能名取说明 H1（新增解析，缺省用 id）；一句话优先取可选 front-matter `headline` 字段，否则从"目标"章节自动提取首句（去列表符号，截断至约 60 字符）；
   - 文末"技术细节"区附每条说明的 GitHub 链接（blob/{tag}/docs/changes/...）。
2. `parse_change_record` 增补 H1 标题解析（`title` 字段，缺省回退 id）；`headline` 为可选 front-matter，**不加入必填校验**——旧记录与新记录零成本兼容，门禁不变。
3. 不改 `release.yml`、平台发版工作流、AGENTS.md 门禁与 skill 步骤——生成入口仍是同一条 `release-notes` CLI，耗时不变。

## 非目标

- 不改变更说明本身的写法与门禁要求（那是开发审计资产）；
- 不改标签/发布流程与确认机制；
- 不做多语言。

## 兼容性

- 纯渲染层重写；`release-notes` CLI 签名不变；`headline` 可选字段向后兼容。release 页面格式是用户可见变化，故 release_bump=patch。

## 风险

1. 自动摘要质量取决于"目标"首句：以历史真实发布范围（v1.8.0→v1.9.0→v1.10.0→v1.10.1）逐条迭代校验可读性；不理想的记录未来可加一行 headline 覆盖。
2. H1 缺失回退 id，不影响渲染健壮性。

## 测试计划

- `tests/test_team_policy.py`：重写 release notes 断言（分组、一句话、headline 覆盖、首句截断、链接、不再包含验证结果/实际改动手）；`parse_change_record` H1 解析用例。
- 本地以历史 tag 区间真实数据渲染 review。
- 全量验证后 PR 合并，真实发版 v1.10.2 验证线上 release 页面效果。

## 实际改动

- `scripts/team_policy.py`：
  - `ChangeRecord` 新增可选 `title` 字段；`parse_change_record` 解析正文 H1 作为功能名（缺省回退 id）；
  - `render_release_notes()` 重写：标题 + 一句导语（N 项改进），按 type 分组（✨ 新功能 / 🐞 问题修复 / 🛠️ 其他改进），每条 `- **功能名**：一句话`；摘要优先取可选 front-matter `headline`，否则自动提取"目标"首句（剥 markdown 记号/链接/本机路径、清理空括号、按全角标点断句、60 字截断且不在未闭合括号内截断、标题 30 字截断）；文末"技术细节"区附每条说明的 GitHub blob 链接；
  - `headline` 未加入必填校验（完全可选，旧记录零成本兼容）。
- `tests/test_team_policy.py`：H1 解析断言；release notes 测试重写为通俗格式断言（分组、headline 覆盖优先、无技术章节泄漏、链接计数、长标题/括号截断）。
- 工作流与门禁零改动：`release.yml`/`windows-release.yml` 仍走同一条 `release-notes` CLI。

## 验证结果

- 历史真实区间渲染迭代（2026-09-04 01:40–01:42）：v1.8.1→v1.9.0 与 v1.10.0→main 两个区间逐条 review——条目均为单行通俗摘要，无路径/markdown 记号/未闭合括号截断。
- `python -m unittest tests.test_team_policy` → 33 项通过；全量 `python -m unittest discover -s tests -p "test_*.py"` → 560 项全部通过；`node --test tests/*.test.js` 全部通过（2026-09-04 01:43）。

## PR

pending
