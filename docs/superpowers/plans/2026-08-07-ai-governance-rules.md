# Project AI Governance Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add strict repository-wide AI instructions for release tagging, GitHub CI/CD follow-up, and detailed commit messages.

**Architecture:** Create one root-level `AGENTS.md` so its rules cover the complete repository. Keep the change documentation-only; verify the mandatory clauses structurally, commit only the governance files, and push `main` without creating a release tag because this change is not a user-facing release milestone.

**Tech Stack:** Markdown, Git, GitHub Actions

---

### Task 1: Add And Publish Repository AI Governance

**Files:**
- Create: `AGENTS.md`
- Create: `docs/superpowers/plans/2026-08-07-ai-governance-rules.md`
- Reference: `docs/superpowers/specs/2026-08-07-ai-governance-rules-design.md`

- [x] **Step 1: Create the root project instructions**

Create `AGENTS.md` with the approved mandatory rules:

```markdown
# 项目级 AI 强制约束

本文件适用于整个仓库。所有 AI Agent 必须遵守以下规则；不得以“建议”“通常”“应当”等弱化措辞规避。任一前置条件无法验证时，必须停止对应操作并向用户报告阻塞原因，禁止猜测、静默跳过或使用过期证据。

## 发布里程碑检查

AI 在完成以下任一事项后，必须主动执行发布门槛检查，不得等待用户提醒：

- 一个可独立交付的完整功能；
- 一组目标一致、可作为一个版本交付的相关修复；
- 用户要求提交代码，且本次提交可能完成上述功能或修复批次。

只有同时满足以下全部条件，AI 才能向用户建议发布：

1. 功能或修复已经闭环，范围内不存在已知未完成事项。
2. 针对拟发布的准确目标提交，刚刚运行过仓库完整自动化测试且全部通过；历史测试、局部测试或推测不得替代。
3. 本次计划发布的变更已全部提交。
4. 目标提交位于 `main`，并且已推送到 `origin/main`。
5. 工作区不存在非预期的 tracked 或 untracked 变更。
6. 已准备完整发布说明，包含用户可见变化、兼容性影响和验证证据。
7. 拟使用的语义化版本标签在本地和远端均不存在。

任一条件不满足或无法确认时，AI 必须停止发布流程，明确列出阻塞项，禁止创建或推送标签。

## 标签授权与 GitHub CI/CD

请求发布授权前，AI 必须向用户完整展示：

- 拟创建的 `vX.Y.Z` 标签及语义化版本选择理由；
- 标签指向的完整 commit hash；
- 自上一个版本标签以来的提交范围；
- 完整发布说明；
- 实际执行的验证命令及结果；
- 推送标签后将触发的 Windows 和 macOS GitHub Actions 工作流。

“提交代码”“推送代码”“继续”“可以”或“准备好就发布”等泛化表述不构成标签授权。只有用户针对具体版本号和具体目标提交作出明确发布确认后，AI 才能创建并推送该标签。授权仅限一次，禁止复用于其他版本号或其他提交。

获得明确授权后，AI 只能创建并推送单个轻量标签 `vX.Y.Z`。禁止使用 `git push --tags` 或 `git push --all`。

标签推送后，AI 必须检查 Windows 与 macOS 两个发布工作流，并向用户报告每个工作流的 URL、状态以及失败时的具体 job 和 step。禁止仅报告“已触发 CI/CD”。

## Git 提交要求

每次提交前，AI 必须检查完整 diff，按独立关注点拆分提交，并排除无关修改、无关未跟踪文件、生成产物、凭据和本地状态。禁止为追求单次提交而混入不相关变更。

每个 commit 必须同时满足：

1. 标题采用明确的 Conventional Commit 格式，准确描述发生变化的行为，禁止使用“更新代码”“修复问题”“调整逻辑”等无法还原修改内容的笼统描述。
2. 正文必须包含以下三个完全一致的章节标题：`功能修改`、`影响范围`、`验证结果`。

正文内容必须符合：

- `功能修改`：逐项描述用户可见行为或内部逻辑的具体变化。
- `影响范围`：说明受影响的模块、接口、配置、兼容性、迁移要求和已知风险；没有影响的项目也必须明确写明“无”。
- `验证结果`：列出实际运行的每一条验证命令及结果；未运行的测试必须如实写明原因，禁止声称未执行的测试已通过。

提交完成后，AI 必须向用户报告 commit hash 和完整提交摘要。
```

- [x] **Step 2: Verify the mandatory clauses**

Run:

```powershell
rg -n "必须主动执行发布门槛检查|只有用户针对具体版本号和具体目标提交|禁止使用.*git push --tags|Windows 与 macOS|功能修改|影响范围|验证结果" AGENTS.md
git diff --check
```

Expected: every mandatory clause is found and `git diff --check` exits with code 0.

- [x] **Step 3: Inspect scope and stage only governance files**

Run:

```powershell
git status --short
git diff -- AGENTS.md docs/superpowers/plans/2026-08-07-ai-governance-rules.md
git add AGENTS.md docs/superpowers/plans/2026-08-07-ai-governance-rules.md
git diff --cached --check
```

Expected: only `AGENTS.md` and this plan are newly staged; existing `.tmp-dist*` and `.zcode/` directories remain untracked and unstaged.

- [ ] **Step 4: Commit with the required detailed body**

Create a Conventional Commit whose body has the exact sections `功能修改`, `影响范围`, and `验证结果`. The body must state that this is documentation-only and list the structural verification commands actually run.

- [ ] **Step 5: Push and verify synchronization**

Run:

```powershell
git push origin main
git rev-list --left-right --count origin/main...HEAD
git status --short --branch
```

Expected: push succeeds, divergence is `0 0`, tracked files are clean, and existing local artifact directories remain untracked.
