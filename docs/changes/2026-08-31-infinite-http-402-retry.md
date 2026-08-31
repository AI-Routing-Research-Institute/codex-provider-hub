+++
id = "2026-08-31-infinite-http-402-retry"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# HTTP 402 重试策略

## 目标

将上游 HTTP 402 纳入现有供应商重试机制，使预算池临时耗尽后可以按照当前重试配置继续等待恢复；在无限重试模式下，402 请求持续重试，直到请求成功或客户端断开。

## 现状

本地中转当前只将 403、500、502、503、504 和 429 识别为可重试状态。Agent Router 返回“Budget pool quota has been exhausted”的 HTTP 402 会直接结束请求，导致配置为无限重试的请求无法等待额度恢复。

## 设计范围

- 将 HTTP 402 映射为 `http_402`，接入已有重试循环、活动请求状态和恢复记录。
- 保持现有 RetryPolicy 语义：启用且 `max_attempts = -1` 时无限重试，有限次数时遵守配置，关闭时直接返回 402。
- 重试前继续刷新当前供应商，使用户切换供应商后后续尝试可以使用新的供应商。
- 复用已有错误正文提取和脱敏逻辑，保存并显示 402 的上游错误摘要。
- 客户端断开时停止重试并完成请求清理。

## 非目标

- 不改变 HTTP 401、403 或其他状态码的现有重试策略。
- 不修改供应商额度、预算池或认证配置。
- 不新增独立的 402 专用配置项或后台任务。

## 兼容性

现有配置格式和数据库结构无需迁移；只有已经启用重试的请求会受到影响。该改动修复现有重试策略遗漏的上游状态，版本选择 `patch`。

## 风险

预算池耗尽可能长期不恢复，无限重试会保持请求活动并继续消耗重试请求。实现复用现有退避、客户端断开清理和状态展示，测试有限次数、无限次数及断开场景，避免影响其他请求。

## 测试计划

- 验证 HTTP 402 被识别为 `http_402`。
- 验证有限次数策略在 402 后按配置次数停止，并保留错误摘要。
- 验证无限次数策略在 402 后继续重试，供应商恢复后请求成功。
- 验证重试过程中切换当前供应商后使用新的供应商。
- 验证客户端断开后无限重试停止、活动请求清理且恢复记录完整。
- 运行完整 Python 单测、JavaScript 语法检查、JavaScript 测试、Python 编译与差异检查。

## 实际改动

- `local_proxy/core.py` 将 HTTP 402 加入统一可重试状态集合，使其遵循现有 RetryPolicy 的有限或无限重试配置。
- `tests/test_proxy_core.py` 覆盖 402 错误摘要、有限重试和无限重试恢复场景。

## 验证结果

- `C:\code\codex_provider_probe\.venv\Scripts\python.exe -m unittest tests.test_proxy_core.ProxyAppTests.test_http_402_is_retried_with_upstream_error_summary tests.test_proxy_core.ProxyAppTests.test_http_402_retries_indefinitely_until_provider_recovers`：通过，2 项测试。
- `C:\code\codex_provider_probe\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：通过，508 项测试，耗时 70.318 秒。
- `Get-ChildItem -Path proxy_static/src,provider_status/static,tests -File -Filter *.js | ForEach-Object { node --check $_.FullName }`：通过。
- `Get-ChildItem -Path tests -File -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：通过，全部 JavaScript 测试文件通过。
- `C:\code\codex_provider_probe\.venv\Scripts\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- `npm run build --prefix proxy_static`：通过，Vite 转换 26 个模块；构建产物仅用于验证，未纳入提交。
- `git diff --check`：通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/55
