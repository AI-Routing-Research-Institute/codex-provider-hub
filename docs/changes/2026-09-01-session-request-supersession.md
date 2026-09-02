+++
id = "2026-09-01-session-request-supersession"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 同会话请求接管

## 目标

同一真实 Codex `thread_id` 发起新请求时，主动终止该会话仍未结束的旧请求，关闭其上游响应流，并将旧请求记录为“已由同会话新请求接管”，避免旧流长期残留并在进程重启后集中显示为异常失败。

## 现状

本地中转仅按内部 `request_id` 跟踪活动请求。同一 `thread_id` 的后续请求可以正常完成，但旧请求若持续收到保活数据或未触发现有断流清理条件，会继续保留在路由状态和 `inflight_requests` 中，最终在进程重启时被恢复为 `process_restarted` 失败记录。

## 设计范围

- 对非空 `thread_id` 实施单活动请求约束，新请求接管并取消同会话旧请求。
- 取消旧请求的实际 ASGI 响应任务，使响应迭代器及底层上游流执行关闭逻辑。
- 旧请求尚未观察到协议终止事件时，以 `session_superseded` 写入历史，页面按取消状态展示。
- 旧请求已完成协议终止时保留原成功或失败结果，避免竞态导致结果降级或重复记录。
- 不按会话显示名称匹配，不影响不同 `thread_id`、无 `thread_id` 请求及单个请求内部的供应商重试。

## 非目标

- 不删除已有的历史异常记录。
- 不改变供应商切换、重试次数、SSE 空闲超时和模型映射策略。
- 不限制不同 Codex 会话之间的并发请求。

## 兼容性

不修改外部 HTTP API、配置格式或数据库表结构。新增历史错误类型 `session_superseded`，旧版页面仍可将其作为普通非成功记录展示；新版页面会明确显示为取消。

版本选择 `patch`：这是现有请求生命周期清理行为的缺陷修复，不新增或破坏外部接口。

## 风险

- 新旧请求并发启动时可能发生取消与协议完成竞态；通过请求级状态和幂等清理保证只记录一次，并优先保留已观察到的终止结果。
- 取消若只移除内存状态会继续占用上游连接；协调器必须取消实际响应任务，由既有 `finally` 关闭响应迭代器和上游流。
- 错误地按显示名称匹配会中断无关会话；仅使用非空 `thread_id` 作为键。

## 测试计划

- Python 回归测试覆盖同 `thread_id` 新请求终止旧流、关闭上游、清理活动状态和 SQLite inflight 记录。
- 覆盖不同 `thread_id`、相同显示名称、无 `thread_id` 请求保持并发。
- 覆盖协议终止与接管并发时不降级成功结果、不重复写历史。
- JavaScript 测试覆盖现代和经典页面将 `session_superseded` 展示为取消。
- 运行 Python 全量测试、JavaScript 语法与测试、Vue 构建、Python 编译及 `git diff --check`。

## 实际改动

- `local_proxy/core.py` 新增应用级 `SessionRequestCoordinator`，按非空真实 `thread_id` 注册活动 ASGI 请求；同会话新请求会标记并取消旧请求，等待旧请求完成资源清理后再继续。
- `local_proxy/core.py` 为流式响应绑定请求租约，接管、客户端断开、发送失败和协议终止均通过同一生成器收尾路径关闭上游流、更新路由状态并删除 SQLite inflight 记录。
- `local_proxy/core.py` 增加预响应阶段的接管清理，覆盖连接、首包等待、SSE 预检和重试等待阶段；旧请求写入 `session_superseded`、`cancelled` 和“已由同会话新请求接管”。
- `local_proxy/server.py` 为统一中转的 Codex、Claude 服务分别创建请求协调器，避免跨服务相互影响；没有 `thread_id` 的请求保持原有并发行为。
- `proxy_static/src/components/RequestsView.vue` 和 `proxy_static/classic/app.js` 将 `session_superseded` 展示为取消状态及“同会话新请求接管”。
- `tests/test_proxy_core.py` 覆盖接收中流、SSE 预检、不同线程同名、无线程并发及终止事件竞态；两个前端契约测试覆盖新取消类型。

## 验证结果

- `python -m unittest -v` 定向执行 5 个同会话接管用例：通过，覆盖接收中、SSE 预检、不同线程、无线程和协议终止竞态。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：通过，532 项测试全部成功。
- 对 `tests/*.test.js` 逐文件执行 `node --test`：通过，16 个 JavaScript 测试文件、34 个测试全部成功。
- `.\.venv\Scripts\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- `node --check` 检查状态页、经典控制台和共享前端 JavaScript：通过。
- `npm run build --prefix proxy_static`：通过，Vite 转换 29 个模块并生成包含接管状态展示的新生产资源。
- `git diff --check`：通过，仅有仓库现有的 LF/CRLF 转换提示；冲突标记、常见乱码和新增凭据扫描无命中。

## PR

pending
