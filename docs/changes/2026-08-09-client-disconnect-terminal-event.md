+++
id = "2026-08-09-client-disconnect-terminal-event"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修正响应终止后的客户端断开误报

## 目标

当本地中转已经收到上游成功终止事件时，即使客户端随即关闭连接，也应记录为成功；真正发生在终止事件之前的客户端取消应以中性短摘要展示。

## 现状

当前流式请求只有在上游异步迭代器自然耗尽后才设置完成状态。Codex 收到 `response.completed` 后立即关闭连接时，响应生成器可能在下一次迭代前被取消，从而误记为“客户端在响应完成前断开连接”。这会污染失败数量，并在请求列表产生大量醒目的长红色错误。

## 设计范围

- 在流观察器中记录成功、失败和未完成等终止事件。
- 已收到成功终止事件且没有流级错误时，将请求记录为成功，不依赖迭代器是否自然耗尽。
- 未收到成功终止事件的客户端断开继续保留为非成功记录，但使用“客户端取消”短摘要。
- 请求列表将客户端取消使用中性警示层级，完整原因仍通过悬停标题提供。
- 增加终止事件后取消、真正提前取消和展示分类测试。

## 非目标

- 不对客户端已取消的请求执行重试。
- 不回写或重分类已有历史数据库记录。
- 不改变上游流级错误、HTTP 错误和 Token 统计规则。

## 兼容性

不修改配置和数据库结构；现有历史记录保持不变。仅修正新请求的完成判定和展示，属于向后兼容缺陷修复，版本选择 `patch`。

## 风险

- 若终止事件识别过宽，可能把失败流误记为成功；通过只接受明确成功终止事件且优先保留内嵌失败来规避。
- 客户端取消仍可能消耗上游 Token；保留 Token 与非成功结果，避免隐藏实际消耗。
- 不同协议终止事件不同；由协议流观察器统一识别已支持的 Responses 终止事件，并保持 Claude 现有自然耗尽逻辑。

## 测试计划

- 测试 `response.completed` 已送达后客户端取消不会误报。
- 测试终止事件前取消仍记录为客户端取消。
- 测试内嵌失败事件优先于成功/连接状态。
- 运行完整 Python、Node 测试、JavaScript 语法、Python compileall 和 diff 检查。
- 人工重启后观察新请求记录，不修改既有历史记录。

## 实际改动

- `local_proxy/core.py` 的 `UsageCapture` 记录明确的 `response.completed` 成功终止事件；请求成功和 usage 成功判定接受“迭代自然耗尽”或“已看到成功终止事件”，流级错误仍优先。
- 真正发生在终止事件前的连接关闭继续记录 `client_disconnected`，错误摘要缩短为“客户端取消”，保留非成功状态与实际 Token。
- `proxy_static/app.js` 与 `proxy_static/styles.css` 将新旧 `client_disconnected` 统一映射为“取消”及橙色层级，悬停标题保留完整说明；同步提升静态资源缓存版本。
- `tests/test_proxy_core.py` 增加终止后关闭与提前关闭的集成测试，`tests/local_proxy_requests.test.js` 覆盖历史长摘要的短标签映射。

## 验证结果

- `\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：369 项通过。
- `node --test <tests/*.test.js>`：27 项通过。
- `node --check proxy_static/app.js`：通过。
- `\.venv\Scripts\python.exe -m compileall -q local_proxy tests`：通过。
- `git diff --check`：通过，仅出现仓库既有的 LF/CRLF 转换提示。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/8
