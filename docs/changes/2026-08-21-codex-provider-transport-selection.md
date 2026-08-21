+++
id = "2026-08-21-codex-provider-transport-selection"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# Codex 供应商级请求传输方式

## 目标

为 Codex 本地供应商增加可选的请求传输方式：默认使用 `httpx`，只有明确选择兼容模式的供应商使用 `curl_cffi`。

## 现状

Codex 当前所有供应商统一使用 `curl_cffi`。该方案解决了部分中转的 Cloudflare 403，但改变了所有正常供应商的既有网络行为，影响范围过大。

## 设计范围

- 新增供应商级 `transport` 配置，允许 `httpx` 和 `curl_cffi`。
- 核心转发在每次请求尝试前按当前供应商选择客户端。
- 本地供应商编辑接口与页面支持读取、校验和保存请求传输方式。
- Codex profile 同时管理标准客户端和兼容客户端的生命周期。

## 非目标

- 不修改重试次数、退避、熔断、供应商切换、状态上传和 Claude 协议行为。
- 不增加自动切换供应商逻辑。
- 不改变供应商真实返回的 503、504、429、401 等业务错误。

## 兼容性

缺少 `transport` 字段的已有供应商按 `httpx` 处理；非法手工配置也回退到 `httpx`，页面提交的非法值会被拒绝。Claude 继续使用 `curl_cffi`。

## 风险

同时维护两个异步客户端会增加资源关闭路径；统一应用生命周期会关闭 Codex 标准客户端、兼容客户端和 Claude 客户端，并按对象去重。

## 测试计划

- ProviderCatalog 持久化、默认值和非法值测试。
- Codex profile 客户端选择测试。
- 核心请求和统一服务按 provider 选择客户端测试。
- 完整 Python 与 JavaScript 测试、PyInstaller 冒烟和独立端口启动验证。
- 使用本地真实供应商配置验证 Cloudflare 403 消失。

## 实际改动

- `ProxyProvider` 增加默认值为 `httpx` 的 `transport` 字段。
- Codex profile 恢复 `httpx.AsyncClient` 为标准客户端，并按供应商选择共享 `CurlClient`。
- 核心转发在每次尝试时调用客户端选择器，保留重试期间手动切换供应商的原有语义。
- 本地供应商目录和编辑页面支持传输方式持久化与回读。
- 本地 `ai.hybgzs.com/v1` 的 3 条供应商记录已设为兼容模式，其他供应商未修改。

## 验证结果

- `python -m unittest discover -s tests`：476 项通过。
- `node --check proxy_static/app.js`：通过。
- `tests/*.test.js`：全部通过。
- `python scripts/team_policy.py pre-commit`：通过。
- 本地真实 `https://ai.hybgzs.com/v1` 配置经供应商选择器使用 `curl_cffi`，返回 HTTP 200 `text/event-stream`，未返回 Cloudflare HTML。
- PyInstaller 测试包 `--smoke-test`：通过。
- 测试包在独立端口 `17894` 启动，Codex 与 Claude 健康状态均为 `ok`；供应商编辑接口回读 `curl_cffi`，页面包含传输选择及保存逻辑；验证后测试进程正常退出。

测试包：`.tmp-dist-provider-transport-selection/CodexLocalProxy-win-x64.exe`。

SHA-256：`2442cefb567bf08aba1e73782e528019f8f3afdbc54016595d9429cabbdeda8c`。

## PR

pending
