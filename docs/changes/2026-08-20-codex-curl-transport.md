+++
id = "2026-08-20-codex-curl-transport"
type = "bugfix"
release_bump = "patch"
status = "verified"
+++

# Codex Cloudflare 403 传输修复

## 目标

修复部分中转供应商拒绝 Python httpx 客户端并返回 Cloudflare HTML 403 的问题，使本地 Codex 转发能够到达供应商 API。

## 现状

Codex 出站使用 `httpx.AsyncClient`。`https://ai.hybgzs.com/v1` 对相同地址和凭据会拒绝 httpx 请求，但接受 curl 客户端请求，导致直接使用 Codex 正常、接入本地中转后固定失败。

## 设计范围

- 抽取 Claude 已使用的 curl_cffi 传输为通用客户端。
- Codex 出站请求改用通用 curl 客户端。
- 保持现有请求头、Responses 协议、SSE 流式读取和异常类型兼容。

## 非目标

- 不修改重试次数、退避策略、熔断策略或供应商切换逻辑。
- 不改变供应商真实返回的 503、504、429、401 等业务错误。
- 不修改前端页面和配置格式。

## 兼容性

无配置和数据库迁移。Claude 继续通过原有 `ClaudeCurlClient` 名称使用同一实现，外部行为不变。

## 风险

主要风险是 SSE 流关闭和连接异常映射出现回归；通过复用现有 Claude 实现及完整代理测试覆盖。

## 测试计划

- 通用 curl 请求、流式读取、关闭和异常转换单测。
- Codex profile 客户端构造测试。
- 完整 Python 与 JavaScript 测试。
- 使用本地真实供应商配置验证 Cloudflare 403 消失。
- PyInstaller 冒烟及独立端口启动验证。

## 实际改动

- 新增 `local_proxy/transports/curl.py` 通用 curl_cffi 传输。
- `local_proxy/transports/claude.py` 保留兼容名称并复用通用实现。
- `local_proxy/codex_profile.py` 使用通用 curl 客户端。
- 增加通用传输和 Codex profile 回归测试。

## 验证结果

已通过：`python -m unittest discover -s tests -p "test_*.py"`（472 项）；全部 `tests/*.test.js`；`node --check proxy_static/app.js`；`git diff --check`；PyInstaller EXE `--smoke-test`。使用本地真实 `ai.hybgzs.com/v1` 配置验证，通用 curl 客户端返回 HTTP 200 `text/event-stream`，固定 Cloudflare 403 消失；未修改重试和供应商切换逻辑。

测试包：`.tmp-dist-codex-curl-fix/CodexLocalProxy-win-x64.exe`。

SHA-256：`1a6bb1f42553d29a7e3807930755479d759468470fcc8d9529afacf03dff773d`。

## PR

pending
