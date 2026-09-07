+++
id = "2026-09-04-deepseek-dsml-adapter"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# DeepSeek DSML 工具调用适配

## 目标

让返回 DSML 文本形式工具调用的供应商仍然能被 Codex 识别并执行；标准 Responses 工具调用和普通 Responses 内容继续原样转发。

## 现状

Codex 请求通过统一的 Responses SSE 转发链路发送给所有供应商。当前链路只识别标准 Responses 事件，DeepSeek 返回的 DSML 标签会被当作普通文本转发，导致工具调用无法执行，简单删除标签还会静默丢失工具调用。

真实请求缓存进一步显示，DeepSeek 会先发送一段普通说明文本，再把双竖线 DSML 标记拆成多个 `response.output_text.delta` 事件。当前适配器在看到第一段普通文本后永久进入原样透传状态，因此后续 DSML 不再参与检测；将缓存的 453181 字节响应离线重放给适配器也会逐字节原样输出。

## 设计范围

- 保留 DeepSeek DSML 转换器，并将轻量 DSML 检测接入所有 Codex Responses 供应商。
- 识别 DeepSeek 响应中的标准 Responses 事件并保持原样。
- 对 DeepSeek SSE 中的 DSML 工具调用做增量解析，支持标签和参数跨 chunk 拆分。
- 普通说明文本已经开始透传后，继续以有界前缀探测后续 DSML；只暂存可能属于 DSML 起始标记的少量事件，避免破坏普通 Responses 的流式输出。
- 将解析出的工具调用转换为标准 Responses function-call 事件，保留前置普通文本和 reasoning 内容。
- 支持 DSML 的 `name/parameters` 和 `invoke/parameter` 两种常见格式、全角及半角竖线。
- 只有检测到 DSML 标记时才进入转换；普通 Responses 原样透传。
- DSML 解析失败时返回明确的协议错误，不删除或泄漏原始 DSML，也不进入无限重试。

## 非目标

- 不修改任何供应商的请求头、地址和重试行为。
- 不改变官方 DeepSeek 已经返回的标准 Responses 工具调用。
- 本次不实现完整 Responses 与 Chat Completions 的通用双向协议转换。
- 不保存 API Key、完整会话内容或工具参数到诊断日志。

## 兼容性

现有 provider 配置格式不变；DeepSeek 通过官方域名或 provider 名称识别并启用自动适配。数据库无需迁移。版本选择 `patch`，因为这是对已有 DeepSeek 工具调用兼容性的修复。

## 风险

DSML 格式可能存在未覆盖变体，或普通文本中偶然出现相似标签。适配器只在 Responses 响应路径上运行，要求完整工具块并校验工具名称；解析失败返回协议错误，避免把不确定内容当成工具执行。流式缓冲设置上限，避免异常上游无限占用内存。

## 测试计划

- 验证任意 Responses provider 都能进入轻量 DSML 检测。
- 验证标准 Responses 文本和 function-call 事件原样通过。
- 验证完整 DSML、跨 chunk DSML、两种 DSML 结构和多工具调用转换。
- 使用“普通前言 + 跨多个 SSE delta 拆分的双竖线 DSML”回归场景，验证前言只出现一次、原始 DSML 不泄漏且工具调用成功转换。
- 将真实请求调试缓存离线重放给修复后的适配器，验证输出不再与原始响应相同并包含标准 function-call 事件。
- 验证工具调用 ID、参数 JSON、前置 reasoning/文本和完成事件正确输出。
- 验证未闭合或未知工具 DSML 返回协议错误且不泄漏原文。
- 运行 Python 单测、JavaScript 检查、Python 编译和 `git diff --check`。

## 实际改动

- `local_proxy/protocols/deepseek_dsml.py` 新增 DeepSeek DSML 协议适配器：识别官方常见的 `function_calls`、`tool_calls`、`invoke`、`parameter`、`name/parameters` 和 JSON `tool_call` 形式，支持全角/半角竖线、跨 SSE chunk 标签和自闭合参数，并生成 Codex Responses function-call 事件。
- DSML 标签归一化兼容竖线重复、竖线与 `DSML` 之间带空格，以及结束标签中 `<` 与 `/` 之间带空格的实际渲染形式。
- Responses 状态机在普通前言已经开始转发后仍保留有界 DSML 起始探测；可能属于跨事件标签的短后缀暂存到确认完成，普通 `<` 文本在排除 DSML 后仍按原始字节顺序转发。
- 发生后置 DSML 转换时复用并正确结束已经开始的消息项，保留原 response id、sequence number、output index 和前言文本；reasoning delta 不再混入可见消息。
- 带 `function_calls`/`tool_calls` 外层容器的响应必须等待容器闭合后再解析，避免第一个 `invoke` 完成时提前截断其余并行工具调用。
- `local_proxy/core.py` 增加 provider 级协议适配器 resolver；每次请求和重试依据当前实际 provider 重新选择适配器，接入流式与非流式响应转换，并同时关闭转换流、原始上游流和 HTTP 响应。
- `local_proxy/server.py` 在 `ProxyProfile` 和统一代理调用链中传递 provider 级 resolver。
- `local_proxy/codex_profile.py` 为所有 Codex Responses provider 接入轻量 DSML 检测，同时保留官方 DeepSeek provider 的专用识别。
- `tests/test_codex_profile.py` 增加通用 Responses provider 适配器选择测试。
- `tests/test_deepseek_dsml.py` 增加解析、跨 chunk、普通前言后的多调用 DSML、reasoning 隔离、标准 Responses 原样透传、非流式转换、provider 识别和 ASGI 转发集成测试。
- `local_proxy/core.py` 将转换后产生的错误/不完整终止事件记录为非成功，避免协议错误被统计为成功请求。

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest tests.test_deepseek_dsml -v`：10 项通过。
- `.venv\\Scripts\\python.exe -m unittest tests.test_proxy_core tests.test_server -v`：165 项通过。
- `.venv\\Scripts\\python.exe -m unittest tests.test_request_debug tests.test_deepseek_dsml tests.test_codex_profile tests.test_server`：36 项通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：577 项通过，耗时 40.444 秒。
- JavaScript 全量测试：18 个文件、94 项通过。
- `npm ci --prefix proxy_static`：依赖安装通过；仅输出 esbuild `allow-scripts` 提示。
- `npm run build --prefix proxy_static`：Vite 生产构建通过，转换 30 个模块，产物哈希未改变。
- `.venv\\Scripts\\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- Node JavaScript 语法检查：通过。
- `git diff --check`：通过。
- 将调试记录 `5e57ec10e4d049978602aafdbd3be58e:3` 的 453181 字节真实 DeepSeek 响应离线重放：前言 delta/done 完全一致、可见文本不含 DSML，4 个 `exec` 全部转换，保留 1277 个 reasoning 事件，只生成 1 个 `response.completed`，无 error 事件。

## PR

pending
