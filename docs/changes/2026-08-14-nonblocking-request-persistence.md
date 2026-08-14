+++
id = "2026-08-14-nonblocking-request-persistence"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 本地中转请求持久化阻塞修复

## 目标

消除请求历史读取与请求结束落库之间的锁竞争对 ASGI 事件循环的阻塞，确保数据库繁忙或历史查询较慢时，正常中转请求、健康接口和控制接口仍能继续推进。

## 现状

请求历史接口虽然已将同步查询移到工作线程，但 `UsageStore.request_history()` 会在持有共享锁期间先执行过期记录删除，再查询历史。正常中转请求结束时仍在 ASGI 事件循环线程同步写入用量和请求记录；当历史查询线程持有共享锁并等待 SQLite 写锁时，请求结束落库会在事件循环中等待同一把锁，进而暂停整个本地代理。此时不仅请求历史接口卡住，新的 `/v1` 中转请求也无法发往上游，用户只能手动重启进程恢复。

初版异步持久化将 Token 和请求历史拆成两个连续的 `asyncio.to_thread()` 调用。客户端断开检测在第一个调用期间取消流任务时，Token 写线程仍会完成，但第二个请求历史调用不会启动，产生无法关联会话、耗时和推理强度的孤立 Token 记录。现场数据库在 09:56 重启加载初版修改后初查已有 48 条此类记录；旧进程继续运行后增长到 125 条，时间范围为 09:58:39 至 12:42:03。页面将它们按兼容旧记录显示为“未知会话”。

## 设计范围

- 将请求历史保留期清理改为节流执行，普通历史读取不再每次启动删除写事务。
- 将中转请求结束阶段的用量、请求和恢复记录持久化移出 ASGI 事件循环线程。
- 将单次流请求的恢复、Token 和请求历史写入合并到同一个线程任务，避免流任务取消造成半写入。
- 保证路由请求状态的释放不依赖数据库写入及时完成，数据库记录失败不阻止代理继续处理请求。
- 增加确定性并发回归测试，覆盖历史查询占用持久化锁时正常中转和健康接口仍可推进。
- 增加清理节流测试，验证连续历史读取不会重复执行过期数据删除。

## 非目标

- 不改变上游供应商选择、重试、会话绑定或模型切换逻辑。
- 不改变请求历史接口字段、分页、筛选语义和保留期限。
- 不重构 SQLite 数据结构，不修改用户现有供应商或运行配置。
- 不在交付后自动重启用户当前运行的本地代理进程。

## 兼容性

HTTP 接口、配置和 SQLite 表结构保持不变，无需数据迁移。历史记录仍按现有保留期限清理，仅降低清理频率。该改动为向后兼容的运行时阻塞修复，因此发布版本选择 `patch`。

## 风险

异步持久化可能改变请求结束时内部操作顺序；通过先释放路由状态、在线程中完成数据库操作并保持单次记录内容不变来降低风险。节流清理会让刚超过保留期限的记录最多延迟一个清理周期删除，但查询时间窗口仍会排除这些记录，不影响接口结果。并发测试使用可控事件和超时，避免依赖机器时序。

## 测试计划

- 新增测试模拟请求历史线程持有持久化锁，验证流式请求结束不会阻塞事件循环和后续中转。
- 验证数据库持久化完成后用量、请求历史和恢复记录内容不变。
- 验证请求历史清理按周期触发，连续读取不重复开启删除事务。
- 运行相关 Python 单元测试、完整 Python 测试、Node 测试、JavaScript 语法检查、Python 编译检查和 `git diff --check`。

## 实际改动

- `local_proxy/core.py` 为请求历史清理增加一小时节流；请求写入和历史读取共享清理状态，普通查询不再每次执行 `DELETE` 写事务。
- `local_proxy/core.py` 将用量、请求和恢复事件持久化统一移到 `asyncio.to_thread()`，并在数据库持久化前关闭上游响应、释放路由请求状态。
- `local_proxy/core.py` 将流请求的恢复记录、Token 和请求历史组成一个连续后台任务；任务启动后即使外层流被取消，也会完成 `usage_id` 关联写入。
- `local_proxy/core.py` 和 `local_proxy/server.py` 将会话路由回查中的 UsageStore 与会话键解析移到工作线程，避免控制操作重新引入同类事件循环阻塞。
- `tests/test_proxy_core.py` 增加锁竞争回归测试，验证历史查询占锁且首个请求等待持久化时，第二个正常中转仍会到达上游；增加请求历史清理节流测试和流任务取消期间 Token/请求历史完整关联测试。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest tests.test_proxy_core.UsageTests.test_request_history_cleanup_is_throttled tests.test_proxy_core.ProxyAppTests.test_persistence_lock_does_not_block_following_upstream_request tests.test_proxy_core.ProxyAppTests.test_stream_usage_is_persisted_for_final_provider tests.test_proxy_core.ProxyAppTests.test_request_api_hides_thread_id_and_persists_session_route`：4 项通过。
- `.\.venv\Scripts\python.exe -m unittest tests.test_proxy_core.ProxyAppTests.test_cancelled_persistence_keeps_usage_and_request_history_linked`：旧实现按预期失败，修复后通过。
- `.\.venv\Scripts\python.exe -m unittest tests.test_proxy_core.UsageTests tests.test_proxy_core.RecoveryHistoryTests tests.test_proxy_core.ProxyAppTests tests.test_server`：91 项通过。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：435 项通过。
- `node --test tests\*.test.js`：40 项通过。
- `node --check proxy_static\app.js`：通过。
- `.\.venv\Scripts\python.exe -m compileall -q local_proxy tests`：通过。
- `.\.venv\Scripts\python.exe scripts\team_policy.py pre-commit`：通过。
- `git diff --check`：通过，仅输出工作区既有的 LF/CRLF 转换提示。
- `.\.venv\Scripts\python.exe scripts\team_policy.py verify-ruleset --repo loongkkk/codex-provider-hub`：通过，规则集 `agent-delivery-main`（ID `20543407`）验证成功。
- 功能分支已 rebase 到 `origin/main` 的 `398bf87`，并针对 rebased HEAD 重新运行上述完整 Python、Node、语法、编译和门禁验证，结果全部通过。
- 尚未重启当前本地代理进行真实运行验证；本次仅允许本地提交，等待手动重启测试后再进入 push 和 PR 交付流程。

## PR

pending
