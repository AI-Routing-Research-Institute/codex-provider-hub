+++
id = "2026-09-04-request-debug-retention"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 请求调试上下文留存

## 目标

为每个逻辑中转请求保留可定位问题的完整调试上下文，使卡住、重试、接管、上游报错和进程重启后的请求都能通过 request id、会话、供应商和时间还原现场。

## 现状

现有 SQLite 只保存请求统计、Token、阶段和错误摘要；请求原文、实际发送给上游的请求体、请求/响应头和原始响应内容没有持久化。程序卡住或重启后只能看到一条摘要，无法确认具体是哪条请求和哪一次上游尝试。

## 设计范围

- 新增独立的请求调试 SQLite 存储，按逻辑请求记录原始下游请求和按尝试记录上游请求/响应。
- 保存阶段、供应商、模型映射、重试、状态码、错误、时间戳和进程 run id，并支持流式响应分段刷盘。
- 默认保留最近 300 个逻辑请求；启动时将上次未收尾记录标记为进程中断，并保留已写入的部分响应。
- 请求头中的 Authorization、Key、Token、Cookie、Secret 等敏感值脱敏；请求和响应正文保留原始内容用于问题定位。
- 增加本地控制接口读取最近调试记录和单条完整详情，并在运行信息中显示调试数据库路径。
- 调试留存故障不得阻塞或改变正常中转请求。

## 非目标

- 不把完整请求正文展示在普通请求列表中。
- 不将调试记录上传到状态服务器或任何外部服务。
- 不保存 API Key、Cookie 或自定义认证头的原始值。
- 不改变现有重试、路由和 Responses/Claude 协议行为。

## 兼容性

新增独立 SQLite 文件，不修改现有用量数据库结构；旧安装首次启动时自动创建调试数据库。调试接口仅监听本地控制台已有地址。磁盘占用受保留条数和单条正文上限约束。版本选择 `minor`，因为这是新增可使用的诊断能力。

## 风险

完整正文可能包含用户会话内容并占用磁盘；通过本地文件权限、请求头脱敏、记录数上限和单字段大小上限缓解。高频流式响应若逐块同步写 SQLite 可能拖慢请求；采用内存缓冲、分段异步刷盘和失败隔离。调试写入失败时主请求继续执行，但该条记录可能不完整。

## 测试计划

- 验证请求开始、请求体、重试尝试、流式输出、完成和错误都能落盘。
- 验证进程重启恢复、保留数量、敏感头脱敏、正文编码和详情接口。
- 验证调试存储故障不会阻塞请求处理。
- 运行相关 Python 单测、服务器接口单测、JavaScript 检查、Python 编译和 `git diff --check`。

## 实际改动

- 新增 `local_proxy/request_debug.py`，创建独立的本地 SQLite 调试库，默认保留最近 300 个逻辑请求，单个正文上限 16MB、总正文上限 512MB，并在启动时恢复上次遗留的运行中记录。
- 在 `local_proxy/core.py` 接入请求开始、下游原始请求体、模型映射后的上游请求体、脱敏请求头、每次尝试、上游响应、已转发响应、流式中间片段、重试、取消、同会话接管和最终收尾状态。
- 在 `local_proxy/server.py` 增加按服务隔离的调试记录列表和详情接口；在 Codex/Claude profile 中显示对应调试数据库路径。
- 新增 `tests/test_request_debug.py` 覆盖详情保存、响应保存、脱敏、保留策略、重启恢复和控制接口。

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest -v tests.test_request_debug`：5 项通过，覆盖调试详情、脱敏、保留/恢复以及取消收尾竞态。
- `.venv\\Scripts\\python.exe -m unittest tests.test_request_debug tests.test_deepseek_dsml tests.test_codex_profile tests.test_server`：36 项通过。
- `.venv\\Scripts\\python.exe -m unittest tests.test_proxy_core`：147 项通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：576 项通过。
- JavaScript 全量测试：18 个文件、94 项通过。
- `npm ci --prefix proxy_static`：依赖安装通过；仅输出 esbuild `allow-scripts` 提示。
- `npm run build --prefix proxy_static`：Vite 生产构建通过，转换 30 个模块，产物哈希未改变。
- `.venv\\Scripts\\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- Node JavaScript 语法检查：通过。
- `git diff --check`：通过。

## PR

pending
