# 项目级 AI 强制约束

本文件适用于整个仓库。所有 AI Agent 必须遵守；禁止以“小改动”“用户很着急”“hooks 可跳过”“本地测试没跑过”或“release CI 会兜底”等理由省略步骤。任一条件无法验证时，必须停止并报告阻塞，禁止猜测、静默跳过、`--no-verify`、强推或直接修改远端状态绕过门禁。

## 唯一交付入口

凡涉及暂存、commit、push、PR、auto-merge 或交付，必须使用仓库 skill：`.agents/skills/git-commit-helper/SKILL.md`。即使宿主未自动发现 skill，也必须先直接读取该文件并完整执行。

首次进入仓库必须运行：

```text
python scripts/team_policy.py install-hooks
```

## 开发前门禁

1. 必须先 fetch `origin/main`。
2. 禁止在 `main` 或 `master` 上开发和提交；必须从最新主线创建 `feat/<slug>`、`fix/<slug>` 等短生命周期功能分支。
3. 修改产品代码、配置、UI、构建或工作流前，必须先从 `docs/changes/template.md` 创建永久功能说明 `docs/changes/YYYY-MM-DD-<slug>.md`。
4. 初始状态必须是 `planned`，并先完成目标、现状、设计范围、非目标、兼容性、风险和测试计划。Agent 必须完成结构化自审，但不等待人工批准。

## 开发与说明同步

- 功能说明必须与代码同步维护；代码和测试完成后改为 `implemented`，新鲜完整验证通过后才能改为 `verified`。
- 实际改动必须列出具体行为和文件；验证结果必须记录真实命令和结果；PR 字段必须记录最终 URL。
- 已发布说明永久保留，禁止删除或改写；后续修正创建新说明。
- 禁止提交生成产物、凭据、私有配置、本地数据库或无关未跟踪文件。

## Git 与 PR

- 每次提交前必须检查完整 staged/unstaged diff，并按独立关注点拆分。
- commit 标题必须为前置 emoji 的 Conventional Commit 中文描述；正文必须包含非空的 `功能修改`、`影响范围`、`验证结果`。
- 禁止直接推送 `main` 和任何标签。功能分支必须 rebase 到最新 `origin/main` 并针对准确 HEAD 重新完整验证。
- Agent 必须创建或更新 PR、填写功能说明和验证证据，并尝试启用 auto-merge（当前规则集未开放 `allow_auto_merge`，启用失败属预期）。
- PR 阶段不运行 CI，PR 不要求人工审批、无 required status checks；合并仅要求 PR 存在且无冲突。
- 完整测试（Python 单测、JS 语法检查、JS 测试）在 release tag 时由 `windows-release.yml` / `macos-release.yml` 全量执行并阻塞发布。
- 合并一律使用 `gh pr merge --squash` 以 PR 的最新准确 head SHA 执行，合并后验证目标提交；禁止使用分支名、旧 SHA、模糊 ref 或无 SHA 合并，禁止绕过门禁直接改动远端。

## 自动版本与自动发版

- Agent 在功能说明中根据兼容性选择 `major`、`minor`、`patch` 或 `none`，并在说明中给出理由，不等待人工决定版本号。
- PR 合并后由 `auto-release` 工作流读取自上个标签以来新增且 `verified` 的说明，选择最高 bump、生成 release notes、创建唯一标签，并显式触发 Windows 与 macOS 发布工作流。
- 正常流程禁止 Agent 手工创建或推送标签。只有自动发布工作流可以执行标签操作。
- Agent 必须跟踪 PR 状态（无 CI 检查）、squash 合并、自动发布和两个平台发布结果，报告 URL、状态和具体失败步骤。

## 硬停止条件

以下任一情况必须停止：主线未同步；处于受保护分支；功能说明缺失或状态不匹配；diff 含不理解/无关/敏感文件；本地测试未运行或失败；Ruleset 未验证；合并或发布权限不足。不得声称流程完成。
