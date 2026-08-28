+++
id = "2026-08-28-restore-auto-release"
type = "chore"
release_bump = "patch"
status = "verified"
+++

# 恢复变更记录驱动的自动发版与发布 CI/CD

## 目标

恢复 2026-08-25 移除的自动发版链路，使 PR 合入 main 后由 `auto-release` 工作流读取变更记录自动计算版本、打标签，并触发 Windows / macOS 双平台构建发布；同时恢复配套的服务端规则集与客户端 pre-push 门禁，让 CI/CD 重新成为交付出口。

## 现状

- 2026-08-25 `#47` 移除了服务端规则集 `agent-delivery-main`、`.githooks/pre-push`、`.github/workflows/` 下 auto-release / windows-release / macos-release 三个工作流，并删除了 team_policy.py 中 staged 路径检查、变更记录、release-plan / release-notes、ruleset 配置等命令（瘦身改动当时未入库，仅留在工作区）。
- 当前仓库：服务端 0 规则集、0 分支保护、0 CI；客户端仅剩 pre-commit 与 commit-msg 两道提交级检查。
- 版本发布已退化为人工作业，无自动发版出口。

## 设计范围

- 将 `scripts/team_policy.py`、`.githooks/pre-push`、`.github/workflows/`（auto-release / windows-release / macos-release）、`AGENTS.md`、`.agents/skills/git-commit-helper/SKILL.md`、治理测试（test_team_policy / test_windows_release / test_macos_release）整体还原至 `#47` 之前的完整版本。
- 变更记录机制随之恢复：改代码必须携带 `docs/changes/YYYY-MM-DD-<slug>.md`（TOML frontmatter + 十段正文），pre-push 校验记录状态至少 `verified`。
- 自动发版恢复：`auto-release` 在 push main 时读取自上个标签以来新增且 `verified` 的变更记录，取最高 bump 生成唯一标签，并显式触发 Windows 与 macOS 发布工作流；禁止人工打标签。
- 服务端规则集 `agent-delivery-main`（`deletion` + `pull_request`，0 审批）重新启用，main 只接受 PR 合入。

## 非目标

- 不修改 `#47` 移除时保留的提交级门禁（commit message 格式、身份白名单、三段式正文）。
- 不调整既有变更记录模板与字段（id / type / release_bump / status 与十段正文）。
- 不改变发布产物形态（Windows exe + macOS ARM64 便携版，均附 sha256 / 自动生成 release notes）。

## 兼容性

- 无运行时代码影响（local_proxy / provider_status 等不动）；仅仓库治理、客户端钩子与 CI 工作流。
- 身份白名单、提交格式、分支命名等既有约束不变，协作者无需新动作（已在本地配置 core.hooksPath 的机器自动生效）。

## 风险

- 恢复后 push main 即触发 auto-release，若变更记录 release_bump 误标会导致意外发版 —— 缓解：版本号由变更记录显式声明且经 PR 审阅，auto-release 对已存在标签拒绝重复创建。
- 协作者机器未安装 hook 时仍可绕过客户端检查 —— 缓解：服务端规则集恢复 PR-only 强制，直推 main 被拦。
- CI 环境（GitHub Actions）与本地差异 —— 缓解：两个构建工作流均先跑全量测试再构建，且 smoke test 通过后才发布。

## 测试计划

- `git diff --check` 通过。
- `python -m unittest discover -s tests -p "test_*.py"` 全量通过（含恢复的 release 工作流一致性测试）。
- `python scripts/team_policy.py pre-commit` / `commit-msg` / `pre-push` 校验通过。
- 合入 main 后观察 `auto-release` 实际触发并完成 v1.0.3 标签与双平台发布。

## 实际改动

- `scripts/team_policy.py`：还原变更记录解析/校验、staged 路径检查、pre-push、validate-pr、configure-ruleset / verify-ruleset、release-plan / release-notes 等命令。
- `.githooks/pre-push`：还原（禁直推 main/tag、基线校验、validate-pr、全量验证）。
- `.github/workflows/auto-release.yml`：还原（push main → release-plan → 打 tag → dispatch 双平台）。
- `.github/workflows/windows-release.yml`：还原（测试 → PyInstaller 构建 → smoke test → GitHub Release）。
- `.github/workflows/macos-release.yml`：还原（测试 → mac ARM64 构建 → smoke test → 上传 Release）。
- `AGENTS.md` 与 `.agents/skills/git-commit-helper/SKILL.md`：还原完整门禁与自动发版流程描述。
- `tests/test_team_policy.py`、`tests/test_windows_release.py`、`tests/test_macos_release.py`：还原治理测试。
- `scripts/team_policy.py` 与两个发布工作流：JS 语法检查路径适配 `#48` 的 Vue 3 + Vite 结构（`proxy_static/src/*.js` + `provider_status/static/app.js`）。
- `scripts/team_policy.py`：`ALLOWED_TYPES` 增加 `style` 类型（与 commit emoji 的 🌈 style 对齐；#48 起合入的 UI 变更记录使用该类型）。
- 修正 `2026-08-21-tailwind-css-integration.md` 的 status 为 `verified`（原为非法值 `completed`，阻塞 auto-release 版本计算）。
- 新增本变更说明。

## 验证结果

- `git diff --check`：通过。
- 全量单元测试：通过（见测试计划执行记录）。
- team_policy.py pre-commit / commit-msg / pre-push：通过。
- 远端规则集 `agent-delivery-main` 已重新配置并 verify-ruleset 确认。

## PR

#49
