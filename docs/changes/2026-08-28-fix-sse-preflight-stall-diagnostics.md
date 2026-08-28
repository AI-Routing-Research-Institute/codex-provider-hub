+++
id = "2026-08-28-fix-sse-preflight-stall-diagnostics"
type = "fix"
release_bump = "patch"
status = "implemented"
+++

# 修复 SSE 首包预检卡死并保留故障现场

## 目标

消除高推理强度响应在首个可见文本前触发的事件循环长时间冻结；即使进程被手动重启，也能从持久日志和请求记录中识别卡住阶段及被中断请求。

## 现状

首包预检会在每个完整 SSE 事件到达后重新复制、规范化、切分并解析全部累计缓存，形成平方级 CPU 开销。事件循环冻结期间，模型请求、控制页面、健康接口和超时计时器都会同时停止推进。当前 `pythonw.exe` 将标准输出和错误输出送到空设备，Uvicorn 也关闭访问日志；请求历史只在收尾时落库，强制重启会同时丢失堆栈现场和未完成请求记录。

## 设计范围

- 使用增量 SSE 事件检查器处理首包前数据，每个完整事件只解析一次，同时保留原始字节用于首包提交，继续支持模型容量、限流和临时上游错误的首包前重试。
- 增加独立于 ASGI 事件循环的 watchdog 线程；心跳超过阈值未推进时写入轮转诊断日志，并转储所有 Python 线程堆栈。
- 日志仅记录时间、服务、请求编号、供应商编号、模型、阶段、请求体字节数和事件循环延迟，不记录请求体、认证信息、请求头或完整会话标识。
- 在请求开始时写入轻量运行中请求表，阶段变化时更新，正常收尾时删除；应用启动时将残留项转为 `process_restarted` 失败记录。
- 在健康接口中暴露最近 watchdog 事件和日志路径，便于页面失去响应后通过文件定位。

## 非目标

- 不调整供应商选择、会话路由、模型、推理强度、代理环境或认证配置。
- 不修改用户当前的无限重试次数、固定重试间隔和供应商切换规则。
- 不记录或持久化请求正文、响应正文、Key、Cookie、Authorization 或其他敏感内容。
- 不尝试在进程已经被操作系统终止后继续原请求。

## 兼容性

SSE 转发内容和重试语义保持兼容。SQLite 自动新增运行中请求表，旧数据库在启动时自动迁移，无需手工操作；旧客户端可忽略健康接口新增字段。日志写入用户数据目录下的新 `logs` 子目录。该改动为向后兼容缺陷修复，版本选择 `patch`。

## 风险

增量解析必须正确处理跨数据块的 CRLF、空行和不完整事件，否则可能漏判首包前临时错误；使用分块边界、终止事件和大规模 reasoning 事件测试覆盖。watchdog 堆栈可能包含本地文件路径和函数参数表示，因此日志不主动格式化业务对象，并限制轮转大小与保留数量。运行中请求表写入放在线程池，避免再次阻塞事件循环。

## 测试计划

- 验证大量 reasoning SSE 事件只被解析一次，首包预检耗时随数据量线性增长且健康心跳可以继续推进。
- 验证跨块 SSE、可见输出、终止事件、容量错误、永久错误和达到预检字节上限时的现有行为。
- 验证 watchdog 在线程独立运行时能检测停滞、只按冷却周期记录一次并输出线程堆栈。
- 验证运行中请求开始、阶段更新、正常删除和重启恢复为失败记录。
- 运行完整 Python 单测、JavaScript 语法检查、前端测试、Python 编译和 diff 检查。

## 实际改动

- `local_proxy/core.py` 将首包前 SSE 判断改为增量事件解析；支持跨块 CRLF、裸 CR 和未完成尾部，每个完整事件只进入一次协议 decision，并继续保留容量错误提前捕获、字节上限和先输出即提交语义。
- `local_proxy/core.py` 为活动请求增加请求体字节数和 `preflighting_sse` 阶段；新增 `inflight_requests` 表，在接受、连接、等待首块、预检、重试和接收阶段更新，正常收尾原子删除，启动时把残留记录恢复成 `process_restarted`。
- `local_proxy/diagnostics.py` 新增 5 MB、3 个备份的 JSON 轮转日志，以及独立原生线程 watchdog；事件循环超过 2 秒未心跳时记录脱敏活动请求并转储全部 Python 线程堆栈，30 秒内不重复转储。
- `local_proxy/core.py`、`local_proxy/server.py` 和 `local_proxy/application.py` 将诊断组件接入单协议与统一服务生命周期；生产日志写入数据目录的 `logs/proxy-diagnostics.log`，健康接口暴露 watchdog 计数、最近停顿和日志路径。
- `tests/test_proxy_core.py`、`tests/test_diagnostics.py`、`tests/test_server.py` 和 `tests/test_local_proxy_app.py` 覆盖线性 SSE 解析、边界兼容、错误顺序、inflight 生命周期、重启恢复、持久化锁并发、日志轮转、watchdog 冷却和应用接线。

## 验证结果

- `\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：通过，495 项测试，耗时 74.106 秒。
- 对 `proxy_static/src/*.js` 和 `provider_status/static/app.js` 执行 `node --check`：通过。
- 对 `tests/*.test.js` 逐个执行 `node --test`：通过，16 项测试。
- `\.venv\Scripts\python.exe -m compileall -q provider_status local_proxy tests`：通过。
- `git diff --check`：通过。

## PR

pending
