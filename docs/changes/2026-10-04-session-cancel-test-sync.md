+++
id = "2026-10-04-session-cancel-test-sync"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 并发会话取消测试的下游响应同步

## 目标

修复 Windows 发布测试中会话并发取消用例的调度竞态，使取消明确发生在客户端收到首个响应体之后，并保留路由、会话归属、取消隔离和历史统计断言。

## 现状

v1.13.0 标签的 Windows 发布在 `AttributionProxyTests.test_main_summary_and_agents_are_concurrent_and_cancel_is_isolated` 失败；上游流开始事件不代表下游响应已发送，过早断开触发代理合法的响应前 `CancelledError`。macOS 测试和打包已通过，但尚无 v1.13.0 GitHub Release。

## 设计范围

- 为四个并发请求增加首个非空 ASGI 响应体已发送的同步事件，确认下游开始响应后再取消子 agent a。
- 以显式事件延迟响应发送，确定性覆盖“上游已开始但下游未响应”的窗口；不依赖固定 sleep。
- 保留上游并发、独立路由、取消隔离、历史归属和内部字段不泄漏的检查。
- 新增本说明，不改写已交付的会话归属说明；选择 patch，因为仅修复测试竞态和发布阻塞，无新接口或不兼容行为。重新发布仍包含主线已有的会话归属功能。

## 非目标

不修改代理生产代码、不放宽取消断言、不吞掉 `CancelledError`、不跳过发布测试，不删除或移动 v1.13.0 标签，不自动重启用户运行的代理，不升级无关依赖。

## 兼容性

产品接口、配置、数据和迁移影响均无；仅测试同步方式变化。

## 风险

新增等待可能在响应缺失时超时。所有等待保持有界，并继续清理请求任务和 HTTP 客户端；延迟响应的事件会在 finally 中释放，避免失败路径残留任务。

## 测试计划

- 会话归属测试模块及目标并发取消用例多次运行，覆盖确定性响应延迟窗口。
- Python 全量单测、所有源码 JS 语法检查、JS 全量测试、`npm ci`、前端构建、`git diff --check`。
- 最新 origin/main rebase 后，对准确 HEAD 执行仓库全量门禁并验证 Ruleset；通过 PR squash 合并。
- 用户已授权修复后重新发版；触发 release 工作流并跟踪 Windows/macOS 干净 runner 的完整测试、打包和 Release 附件。

## 结构化自审

- 根因：上游开始与下游响应属于不同生命周期，原测试错误地将两者等同。
- 范围：仅测试和永久说明，不变更生产取消语义。
- 证据：以客户端发送回调的首个非空响应体作为取消时机依据；主动阻塞响应发送覆盖原有竞态。
- 安全：有界等待、finally 清理、不重启服务、不修改已有标签、发版测试门禁保持完整。

## 实际改动

- `tests/test_session_attribution.py`：增加每请求 `response_sent` 事件，仅首个非空 ASGI 响应体发送时置位；所有请求下游响应后才执行取消隔离断言。
- 同一用例增加 `allow_response` 门闩，在响应发送前覆盖上游已开始的窗口并断言未发送响应体；finally 释放门闩并保留原任务清理。
- `docs/changes/2026-10-04-session-cancel-test-sync.md`：独立记录根因、方案、验证和 patch 发版依据。

## 验证结果

- `python -m unittest tests.test_session_attribution`：26 项通过。
- `python -c "import sys, unittest; suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName('tests.test_session_attribution.AttributionProxyTests.test_main_summary_and_agents_are_concurrent_and_cancel_is_isolated') for _ in range(100)); result=unittest.TextTestRunner(verbosity=1).run(suite); sys.exit(not result.wasSuccessful())"`：目标用例重复 100 次，全部通过。
- `git diff --check`：通过。
- `verify_ruleset('AI-Routing-Research-Institute/codex-provider-hub', token=...)`：`agent-delivery-main`（21697881），`verified: true`；仓库允许 squash，当前身份具备 push/admin 权限。
- `python -c "from scripts.team_policy import run_full_verification; run_full_verification()"`：通过；包含 `npm ci --prefix proxy_static`、`npm run build --prefix proxy_static`、发布源码 JS 语法检查、Python 全量单测与逐文件 JS 全量测试；基线既有失败 0 项、新增失败 0 项。本地 Python 3.14.4 / Node.js 24.18.0；发布 runner 使用 Python 3.13 / Node.js 22，仍由两平台完整测试把关。
- `rg --files -g '*.js' -g '!proxy_static/dist/**' -g '!**/node_modules/**'` 后逐文件 `node --check`：30 个文件全部通过；`node --test tests/*.test.js`：98 项通过，0 失败。
- `npm ci` 报告既有审计提示（1 moderate、1 high），锁文件未变更，本次不升级无关依赖。
- 准确提交 HEAD 推送前由 pre-push 再执行完整验证；PR URL 在创建后回填。平台发布结果由 Actions 与 GitHub Release 保留，不回写已交付说明。

## PR

pending
