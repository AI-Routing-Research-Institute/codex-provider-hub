+++
id = "2026-08-31-streaming-response-terminal-cleanup"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复流式响应残留请求

## 目标

确保客户端断开、下游发送失败或 Responses SSE 已送达明确终止事件时，本地中转及时关闭响应生成器与上游连接，结算请求历史并删除运行中记录，避免残留项在重启后集中显示为失败。

## 现状

断开感知响应会并行发送流和监听 ASGI `http.disconnect`，但流发送任务在 `send()` 抛错或被取消时不保证显式关闭异步响应生成器。生成器若停在 `yield`，负责关闭上游、结束路由状态和删除 `inflight_requests` 的 `finally` 可能无法执行。Responses SSE 即使已经转发 `response.completed` 等协议终止事件，也仍会等待上游传输自然 EOF；上游连接若继续保持，旧请求会长期停留在 `receiving`。

## 设计范围

- 断开感知响应无论正常结束、ASGI 断开、下游发送失败还是外层取消，都显式关闭响应体异步迭代器并等待其清理路径完成。
- 使用独立于 Token 持久化的增量 SSE 终止观察器，只在解析到完整协议事件时识别 `response.completed`、`response.failed`、`response.incomplete`、`error` 或 `[DONE]`。
- 终止事件所在数据块先完整转发给客户端，再主动结束本地迭代、关闭上游，并沿用现有成功、失败和 Token 判定。
- 清理保持幂等，正常 EOF、终止事件与断开竞争时不得重复扣减活动请求或重复写入历史。

## 非目标

- 不设置流式请求固定总时长，不中断仍在正常输出的高推理强度请求。
- 不因为同一会话出现新请求就取消旧请求，避免误伤合法并发。
- 不回写、删除或重新分类已有 `process_restarted` 历史记录。
- 不改变供应商选择、重试、模型、推理强度或代理配置。

## 兼容性

接口、配置和数据库结构均不变。仅修正流式请求生命周期和协议终止后的收尾时机，属于向后兼容缺陷修复，版本选择 `patch`。

## 风险

终止事件若按字节子串识别可能误判模型输出，因此必须按完整 SSE 事件解析 JSON 类型；终止数据块必须先发送再关闭，防止客户端丢失最终 usage 或错误信息。外层取消期间的清理需要隔离取消传播，避免数据库线程已启动但请求记录尚未关联时再次中断。

## 测试计划

- 覆盖下游 `send()` 抛出 `OSError` 时响应生成器、上游流、活动状态和 inflight 记录均被清理。
- 覆盖发送任务暂停期间收到 ASGI 断开时仍执行生成器清理。
- 覆盖 `response.completed` 后上游永久不 EOF，响应仍成功结束并保存 Token。
- 覆盖 `response.failed` 后上游永久不 EOF，响应仍按内嵌失败记录。
- 覆盖跨数据块 SSE 终止事件、普通输出文本包含终止词和正常自然 EOF，防止误判。
- 运行完整 Python 单测、JavaScript 测试与语法检查、Python 编译和差异检查。

## 实际改动

- `local_proxy/core.py` 让断开感知响应在正常结束、下游发送异常、ASGI 断开和外层取消路径中统一取消遗留任务，并显式、抗取消地关闭响应体异步迭代器，确保生成器 `finally` 完成上游关闭、路由状态结束和请求持久化。
- `local_proxy/core.py` 让流空闲超时包装器在退出时继续关闭底层异步流；响应收尾同时显式关闭当前包装流和上游响应，避免预检后的迭代器停留在 `yield`。
- `local_proxy/core.py` 新增完整 SSE 事件级终止观察器，跨任意 CR/LF 和数据块边界识别 Responses 终止事件与 `[DONE]`；终止块转发后主动结束响应，不再等待上游 TCP EOF。
- `tests/test_proxy_core.py` 覆盖普通文本防误判、跨块终止事件、`[DONE]`、发送阻塞时断开、下游发送异常、成功终止后永久挂流和失败终止后永久挂流。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest tests.test_proxy_core.SSEPreflightTests.test_terminal_capture_requires_a_complete_protocol_event tests.test_proxy_core.SSEPreflightTests.test_terminal_capture_recognizes_done_and_failure_events tests.test_proxy_core.ProxyAppTests.test_client_disconnect_cancels_a_stalled_upstream_stream tests.test_proxy_core.ProxyAppTests.test_downstream_send_error_closes_suspended_response_body tests.test_proxy_core.ProxyAppTests.test_terminal_event_finishes_when_upstream_never_reaches_eof tests.test_proxy_core.ProxyAppTests.test_failure_terminal_event_finishes_stalled_upstream_as_failure`：6 项通过。
- `.\.venv\Scripts\python.exe -m unittest tests.test_proxy_core`：118 项通过。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：串行重跑 514 项通过。
- 对 `tests/*.test.js` 逐文件执行 `node --test`：15 个文件、64 项通过；对 `proxy_static/src/*.js` 与 `provider_status/static/app.js` 执行 `node --check`：6 个文件通过。
- `npm run build --prefix proxy_static`：通过，Vite 转换 26 个模块。
- `.\.venv\Scripts\python.exe -m compileall -q provider_status local_proxy scripts tests` 与 `git diff --check`：通过，仅有仓库现有 LF/CRLF 转换提示。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/56
