+++
id = "2026-08-20-input-item-id-repair"
type = "feature"
release_bump = "patch"
status = "verified"
+++

# Responses 输入项 ID 兼容修复

## 目标

当上游明确拒绝 `input[N].id` 时，代理删除该输入项的顶层 `id` 并对同一供应商进行一次受限重试；同一会话后续再次路由到该供应商时沿用兼容处理，避免长会话因旧供应商生成的 ID 持续失败。

## 现状

代理会将 `invalid_request_error` 视为永久错误并直接透传，Responses 请求体也会原样发送。长会话在供应商切换后可能携带当前上游不接受的输入项 ID。

## 设计范围

- 只识别 HTTP 400 且 `code=invalid_value`、`type=invalid_request_error`、`param=input[N].id` 的错误。
- 只删除顶层 `input[]` 对象中被明确指出的 `id`，不生成新 ID。
- 保留 `call_id`、`previous_response_id` 和其他字段。
- 修复重试独立于普通临时错误重试，单次请求设置严格上限。
- 按会话和供应商记录短期内存兼容状态；状态不保存真实 ID，不新增数据库迁移。
- 被指出的索引不是对象、没有顶层字符串 `id`、越界或属于 `item_reference` 时保留原错误。

## 非目标

- 不修复其他参数错误、上下文超限、权限错误或模型错误。
- 不删除 `call_id`、`previous_response_id`、工具参数中的嵌套 ID。
- 不修改 Codex 原始会话文件或客户端请求内容。
- 不修改供应商服务端数据。

## 兼容性

请求转发接口保持不变。修复只影响命中精确错误的 Responses 请求；兼容记忆为进程内存并带 TTL，重启后自动重新学习。无数据库结构变更。

## 风险

错误匹配过宽可能误改合法请求，因此采用严格字段匹配和 `item_reference` 排除。删除普通输入项顶层 `id` 是针对跨供应商兼容问题的工程推断，尚未通过真实上游成功/失败请求对照证明语义等价，因此兼容记忆仅按会话与供应商生效并设置 24 小时 TTL。重复坏 ID 可能形成循环，因此设置单请求修复上限并在失败时透传最后一次上游错误。

## 测试计划

- 精确识别 HTTP 400 的 `input[N].id` 错误并删除指定字段。
- 修复后同供应商重试成功，且保留 `call_id` 与 `previous_response_id`。
- 会话/供应商兼容状态只作用于匹配供应商，并在后续请求生效。
- 普通永久 400、非法 JSON、越界索引和不安全输入项保持原样。
- 多个坏 ID、上限、普通重试关闭及请求体不可变场景。

## 实际改动

- `local_proxy/core.py` 增加 `input[N].id` 精确错误识别和安全请求体派生，只删除非 `item_reference` 输入项的顶层字符串 `id`。
- 修复重试固定在返回错误的供应商，不占用普通重试次数、不触发普通路由切换，单请求最多修复 8 个不同索引。
- 新增按会话哈希和供应商 ID 隔离的 24 小时内存兼容状态，最多保留 4096 项；同一会话后续请求自动删除安全的输入项 ID。
- `local_proxy/server.py` 为 Codex 协议配置独立兼容状态，Claude 协议不启用该处理。
- `tests/test_proxy_core.py` 覆盖精确匹配、字段保留、TTL、普通重试关闭、供应商切换、修复上限、后续请求和非 Responses 路径。

## 验证结果

- `python -m py_compile local_proxy/core.py local_proxy/server.py tests/test_proxy_core.py`：通过。
- `python -m unittest discover -s tests -p "test_*.py"`：469 项通过。
- `node --check proxy_static/app.js`：通过。
- `node --check provider_status/static/app.js`：通过。
- 对 `tests/*.test.js` 逐文件执行 `node --test`：10 个文件、45 项通过。
- `git diff --check`：通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/39
