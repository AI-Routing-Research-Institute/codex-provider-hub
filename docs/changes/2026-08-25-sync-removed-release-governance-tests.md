+++
id = "2026-08-25-sync-removed-release-governance-tests"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 同步已移除发布门禁的测试

## 目标

兼容主线删除 pre-push 和自动发布工作流的策略变更，使全量测试不再读取已删除文件。

## 现状

主线提交 `f68a320` 删除 `.githooks/pre-push` 及三个发布工作流，但历史 UI 分支重放后仍保留对应测试，导致全量 Python 测试出现 6 个 `FileNotFoundError`。

## 设计范围

- 删除仅验证已移除 Windows/macOS 发布工作流的测试方法。
- hook 契约仅检查仍保留的 `pre-commit` 和 `commit-msg`，并断言 `pre-push` 不存在。
- 删除 `test_team_policy.py` 中读取已移除自动发布和平台发布工作流的测试。

## 非目标

- 不恢复已被主线删除的 hooks 或 GitHub Actions 工作流。
- 不修改运行时代码、Vue UI、CCS 导入行为或现存提交门禁。

## 兼容性

无运行时、接口、配置或数据影响；仅同步测试与主线实际治理资产。

## 风险

删除过多治理断言可能降低现存门禁覆盖；通过只移除指向明确已删除文件的测试，保留其余策略测试来控制范围。

## 测试计划

- 运行全量 Python 单元测试。
- 运行全部 Node 测试、前端生产构建和语法检查。
- 运行 `git diff --check`。

## 实际改动

- `tests/test_team_policy.py` 的 hook 契约改为检查仍保留的 `pre-commit` 和 `commit-msg`，并显式断言 `pre-push` 不存在。
- 删除 `tests/test_team_policy.py` 中读取已移除自动发布和平台发布工作流的三个测试。
- 删除 macOS、Windows 测试中读取已移除发布工作流的测试方法，保留打包 smoke、spec 和构建脚本测试。

## 验证结果

- `\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：481 项通过。
- `node --test $testFiles`：14 项通过。
- `npm run build`：Vite 生产构建通过。
- `node --check proxy_static/src/ccswitch.js`、`node --check proxy_static/src/api.js`：通过。
- `\.venv\Scripts\python.exe -m py_compile local_proxy/codex_profile.py local_proxy/claude_profile.py local_proxy/core.py local_proxy/server.py local_proxy/application.py`：通过。
- `git diff --check`：通过。
- `git rev-list --left-right --count HEAD...origin/main`：`16 0`，已包含最新主线。
- `.githooks/pre-push`、`auto-release.yml`、`macos-release.yml`、`windows-release.yml` 均不存在，主线删除保持生效。

## PR

pending
