+++
id = "2026-09-04-request-lifecycle-cancellation-cleanup"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 请求生命周期取消清理

## 目标

确保请求在连接、首包等待、SSE 预检、流式转发和协议终止后的任意取消时机都能可靠结束：内存活动计数归零、会话租约释放、`inflight_requests` 删除且请求历史只记录一次；可选的请求调试持久化不得阻止核心收尾。

## 现状

客户端在响应对象创建前取消请求时，旧进程只处理同会话接管取消，普通取消可能留下 `inflight_requests`，随后在进程重启时被批量恢复为 `process_restarted`。请求调试留存接入后，流式响应的收尾又先等待调试 SQLite 写入，再调用 `router.finish_request()` 和用量持久化；若任务在调试写入期间被取消，已经收到 `response.completed` 的请求仍会留在“运行中”。

## 设计范围

- 为请求生命周期建立单一、幂等的核心收尾入口，统一成功、失败、客户端断开、同会话接管和异常路径。
- 核心收尾优先结束路由活动状态，并以抗取消方式写入请求历史、删除 `inflight_requests`；只有核心清理完成后才标记生命周期已清理。
- 在连接、首包等待和 SSE 预检等响应返回前阶段识别客户端断开，立即关闭上游并按 `client_disconnected` 收尾。
- 调试 attempt 和 request 收尾移动到核心清理之后，保持尽力写入、幂等和失败隔离。
- 保留真实进程中断恢复：只有上次进程确实未收尾的行才在启动时生成 `process_restarted`。
- 为开启请求调试时的预检取消、终止事件竞态、调试写入延迟/失败和重复收尾增加回归测试。

## 非目标

- 不删除、重写或重新分类已有的 `process_restarted` 历史记录。
- 不改变供应商选择、无限重试设置、模型映射、推理强度或 DSML 转换语义。
- 不增加请求固定总时长，也不取消仍在正常运行且客户端保持连接的高推理请求。
- 不增加 Agent Router 专项测试；使用通用 Responses 供应商覆盖协议行为。
- 本次不提交、推送、创建 PR 或重启用户当前运行的本地中转。

## 兼容性

不修改外部 HTTP API、配置文件和现有 SQLite 表结构。历史记录字段与控制台接口保持兼容；只修正请求收尾和残留恢复的准确性。版本选择 `patch`，因为这是现有生命周期保证的缺陷修复。

## 风险

取消与协议终止可能同时发生，若幂等边界不正确会重复扣减活动数或写入重复历史；通过单一生命周期锁和终态快照避免。直接屏蔽取消可能拖慢请求任务退出；仅对必须完成的核心持久化采用抗取消等待，调试写入保持可失败。并发读取 ASGI `receive` 可能消费流式响应阶段需要的断开事件；预检监听器必须在返回响应前停止并移交监听权。

## 测试计划

- 验证连接、首包等待和 SSE 预检阶段的普通客户端取消会关闭上游、清空路由状态和 `inflight_requests`。
- 验证同 thread id 新请求接管旧预检请求，开启和关闭请求调试时结果一致。
- 验证 `response.completed` 已转发后立即断开，即使调试写入延迟或失败也不会留下运行中记录。
- 验证重复取消、终止与接管竞态只生成一条请求历史。
- 连续执行多轮完成/取消后重新初始化 `UsageStore`，确认不会生成虚假 `process_restarted`；同时保留真正未收尾行的恢复覆盖。
- 运行相关 Python 单测、完整 Python 测试、JavaScript 语法和测试、前端构建、Python 编译以及 `git diff --check`。

## 自审

- 修复作用于共享请求生命周期，Codex 和 Claude 配置都会受益，但 Responses SSE 预检监听只在对应路径启用。
- 核心清理不依赖调试数据库成功，符合调试旁路不能改变请求行为的约束。
- 不通过隐藏或删除历史记录掩盖问题，启动恢复仍保留真实异常证据。
- 不改变响应内容、重试判定和 DSML 检测规则。

## 实际改动

- `local_proxy/core.py` 增加响应建立前的断开监听器，在上游连接、首块等待、SSE 预检和重试等待期间并行观察 ASGI `http.disconnect`，并在响应对象返回前停止监听、移交给既有断开感知响应。
- `local_proxy/core.py` 将普通客户端取消和同会话接管统一到幂等生命周期清理：先结束路由状态，再以抗取消方式写入请求历史并删除 `inflight_requests`，之后关闭上游并结束调试记录。
- `local_proxy/core.py` 调整流式响应收尾顺序，在调试 attempt/request 落盘前完成路由与用量数据库清理；即使终止事件后的调试写入被取消，核心状态也不会残留。
- `local_proxy/request_debug.py` 仅在初始化时设置 WAL，避免每次短连接重复切换日志模式；流包装退出时无条件刷新剩余调试缓冲。
- `tests/test_request_debug.py` 增加预检阶段断开与慢调试收尾竞态覆盖，并验证重新初始化 `UsageStore` 不会生成虚假的 `process_restarted`。

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest -v tests.test_request_debug`：5 项通过。
- `.venv\\Scripts\\python.exe -m unittest tests.test_proxy_core`：147 项通过。
- `.venv\\Scripts\\python.exe -m unittest tests.test_request_debug tests.test_deepseek_dsml tests.test_codex_profile tests.test_server`：36 项通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：576 项通过。
- JavaScript 全量测试：18 个文件、94 项通过。
- `npm ci --prefix proxy_static`：依赖安装通过；仅输出 esbuild `allow-scripts` 提示。
- `npm run build --prefix proxy_static`：Vite 生产构建通过，转换 30 个模块，产物哈希未改变。
- `.venv\\Scripts\\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- Node JavaScript 语法检查：通过。
- `git diff --check`：通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/94
