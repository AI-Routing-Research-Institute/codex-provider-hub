+++
id = "2026-08-11-local-proxy-request-stall"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 本地中转请求记录卡顿修复

## 目标

降低请求记录查询耗时，避免控制台轮询阻塞本地中转的健康接口和流式转发，并让运行中请求的耗时以稳定节奏刷新。

## 现状

请求记录查询使用 `request_history.usage_id` 排除已迁移的旧用量记录，但该字段缺少索引。随着本地历史数据增长，关联子查询会反复扫描请求历史。控制接口又在异步事件循环中直接执行同步 SQLite 查询，请求页每秒轮询时会暂停同一进程中的其他接口和流式响应。前端每轮状态刷新还会先重绘请求列表，再在请求记录返回后再次重绘，造成运行耗时在一秒内多次跳动。

## 设计范围

- 为 `request_history.usage_id` 增加兼容旧数据库的索引迁移。
- 将控制台的同步数据库读取移到工作线程，避免阻塞 ASGI 事件循环。
- 批量解析请求历史中的会话名称，减少重复文件状态检查。
- 限制状态轮询重入，页面不可见时暂停轮询，恢复可见时立即刷新。
- 请求列表只在请求数据更新后重绘，消除同一轮轮询中的重复耗时刷新。

## 非目标

- 不改变上游供应商路由、重试策略或流式协议。
- 不改变请求记录保留周期、筛选语义、分页格式或耗时计算口径。
- 不修改用户现有供应商、会话路由或运行设置。

## 兼容性

现有 SQLite 数据库会在启动时自动创建新增索引，无需数据重建。HTTP 接口字段和前端配置保持不变，旧数据库及现有请求历史继续可读。该改动为向后兼容的缺陷修复，因此发布版本选择 `patch`。

## 风险

首次启动创建索引会产生一次短暂数据库写入；通过 `CREATE INDEX IF NOT EXISTS` 保证重复启动安全。数据库读取移到工作线程后仍由现有锁串行保护，避免并发连接改变数据一致性。页面隐藏时暂停刷新可能让后台标签显示旧状态，恢复可见时立即刷新以消除该差异。

## 测试计划

- 验证新建和旧版 SQLite 数据库都会创建 `request_history_usage_id` 索引。
- 验证请求记录控制接口执行慢速同步读取时，健康接口仍可响应。
- 验证请求历史会话名称采用批量解析且返回行为不变。
- 运行前端 Node 测试，验证轮询重入、页面可见性和请求列表单次渲染行为。
- 运行完整 Python、Node、语法检查和编译检查。

## 实际改动

- `local_proxy/core.py` 为已有和新建用量数据库增加 `request_history_usage_id` 索引，并将兼容控制入口的状态、恢复历史、Token 历史、请求历史及会话目录读取移入工作线程。
- `local_proxy/server.py` 将生产统一中转的对应控制接口移入工作线程，避免同步 SQLite 和会话索引读取阻塞流式转发事件循环。
- `local_proxy/core.py` 对请求历史中的线程 ID 批量解析会话名称，取消逐行重复读取会话索引。
- `proxy_static/app.js` 串行化状态轮询，后台标签停止轮询并在恢复可见时立即刷新；静默请求记录刷新不再预先重绘，状态渲染也不再重复重绘请求列表。
- `tests/test_proxy_core.py`、`tests/test_server.py` 和 `tests/local_proxy_requests.test.js` 覆盖索引迁移、查询计划、事件循环响应、批量会话解析和前端轮询渲染行为。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest tests.test_proxy_core.UsageTests tests.test_server`：19 项通过。
- `node --test tests\local_proxy_requests.test.js`：10 项通过。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：392 项通过。
- `node --test tests\*.test.js`：37 项通过。
- `node --check proxy_static\app.js`：通过。
- `.\.venv\Scripts\python.exe -m compileall -q provider_status local_proxy tests`：通过。
- `git diff --check`：通过，仅输出工作区既有的 LF/CRLF 转换提示。
- 使用当前真实数据库的 SQLite 备份复测：索引迁移约 18 ms，请求历史查询由修复前约 710 ms 降至 12–15 ms。

## PR

pending
