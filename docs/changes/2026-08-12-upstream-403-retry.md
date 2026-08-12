+++
id = "2026-08-12-upstream-403-retry"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 上游 403 重试与压缩错误响应解码

## 目标

上游返回 HTTP 403 时使用本地中转已有的自动重试策略；上游返回 gzip、deflate 等压缩响应时，由本地中转解压正文并移除旧的压缩响应头，避免 Codex 将压缩字节显示为乱码。

## 现状

本地中转当前只重试 HTTP 500、502、503、504、429 以及部分按语义识别的 400/404 响应，普通 403 会直接返回客户端。Cloudflare Challenge 等 403 响应经常带有 `Content-Encoding: gzip`，代理使用原始字节流透传后，部分客户端会把压缩正文直接作为错误文本展示。

## 设计范围

- 将上游 HTTP 403 纳入现有状态码重试分类。
- 403 重试继续遵守已有的启用开关、最大尝试次数、退避、供应商路由和熔断配置。
- 对带 `Content-Encoding` 的上游响应使用 HTTPX 解码后的响应流，并在返回客户端时移除 `Content-Encoding`。
- 保持未压缩响应、业务状态码、响应内容类型和其余非逐跳响应头不变。
- 增加压缩 403 透传、403 重试成功和重试耗尽回归测试。

## 非目标

- 不绕过 Cloudflare Challenge、WAF 或供应商鉴权策略。
- 不改变用户现有重试配置，也不为 403 增加独立次数或延迟设置。
- 不把本地控制接口自身返回的 403 纳入上游重试。

## 兼容性

无配置、API 或数据库迁移。行为变化仅限上游 403 现在可能触发重试，以及压缩响应返回客户端时不再保留 `Content-Encoding`。版本选择 `patch`，因为这是现有代理错误处理和重试行为的缺陷修复。

## 风险

- 永久性 403 在无限重试配置下会持续重试；沿用现有策略和客户端断开检测，不改变用户明确配置的语义。
- 解压流可能改变压缩响应的分块边界；客户端消费的是等价正文，且代理已删除失效的编码和长度头。
- HTTPX 不支持的编码可能在流读取阶段报错；通过覆盖常见 gzip 场景和现有流中断测试控制回归风险。

## 测试计划

- 运行新增的 403 重试与 gzip 403 响应回归测试。
- 运行完整 Python 测试套件。
- 运行完整 Node 前端测试套件。
- 运行 Python 编译、JavaScript 语法检查和 `git diff --check`。

## 实际改动

- `local_proxy/core.py` 将 403 加入 Codex 上游可重试状态码，并对所有带 `Content-Encoding` 的响应使用 HTTPX 解码流，向客户端返回时移除失效的编码头。
- `local_proxy/protocols/claude_messages.py` 将 403 加入 Claude Messages 协议的可重试状态码，保持统一服务两种协议行为一致。
- `tests/test_proxy_core.py` 覆盖 Codex 403 重试恢复、有限次数耗尽和关闭重试时 gzip 403 明文透传。
- `tests/test_claude.py` 覆盖 Claude Messages 403 重试恢复。

## 验证结果

- `.venv/Scripts/python.exe -m unittest tests.test_proxy_core.ProxyAppTests.test_http_403_is_retried_before_reaching_client tests.test_proxy_core.ProxyAppTests.test_http_403_exhausts_configured_attempts tests.test_proxy_core.ProxyAppTests.test_gzip_http_403_is_decoded_when_retry_is_disabled tests.test_proxy_core.ProxyAppTests.test_gzip_permanent_http_400_is_decoded_before_pass_through tests.test_claude.ClaudeProtocolTests.test_http_403_is_retried`：5 项通过。
- `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"`：423 项通过。
- `node --check proxy_static/app.js` 与 `node --check provider_status/static/app.js`：通过。
- `node --test tests/*.test.js`：40 项通过。
- `.venv/Scripts/python.exe -m compileall -q local_proxy provider_status probe_codex_cc_switch.py local_proxy_app.py`：通过。
- `git diff --check` 与冲突标记扫描：通过。

## PR

pending
