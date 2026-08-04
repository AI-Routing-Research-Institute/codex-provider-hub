# Codex 与 Claude Code 双网页本地中转设计

## 最终产品形态

项目最终只发布一个 Windows 安装包或便携程序。用户启动一次程序后，程序在后台同时运行两个本地网页服务：

- Codex 控制台：`http://127.0.0.1:17890/control/`
- Claude Code 控制台：`http://127.0.0.1:17891/control/`

两个网页分别管理自己的供应商、当前选择、重试状态、熔断状态、Token 统计和运行设置。它们共享一个桌面程序和一个托盘图标，但不共享当前供应商和请求状态。

安装后仍然采用现有使用方式：启动程序、后台常驻、自动打开浏览器。托盘菜单增加“打开 Codex 控制台”和“打开 Claude Code 控制台”，同时保留重启和退出。重启或退出会同时管理两个本地服务。

现有 Codex 功能和端口保持不变。升级安装后，原有 Codex 设置、供应商选择、Token 统计和恢复记录继续有效。

## 功能范围

第一版包括：

- 从 CC Switch 读取 `app_type = "codex"` 的供应商供 Codex 网页使用。
- 从 CC Switch 读取 `app_type = "claude"` 的供应商供 Claude Code 网页使用。
- Claude Code 原生 Anthropic Messages API：`/v1/messages`。
- 普通 JSON 响应和 SSE 流式响应。
- 供应商即时切换、失败重试、退避等待、熔断和恢复记录。
- Claude 输入、输出和缓存 Token 统计。
- Claude Code 的 PowerShell 和 Bash 配置片段。
- 单进程、单托盘、单安装包和双网页。

第一版不包括：

- Claude Desktop 供应商。
- 把 OpenAI Chat Completions 格式转换成 Anthropic Messages 格式。
- 修改或回写 CC Switch 数据库。
- 监听局域网或公网地址。

## 程序组成

打包入口调整为一个统一启动器。统一启动器负责：

1. 加载 Codex 和 Claude Code 两套设置。
2. 启动 Codex 服务端口 `17890`。
3. 启动 Claude Code 服务端口 `17891`。
4. 创建一个托盘图标。
5. 同时打开 Codex 和 Claude Code 两个控制台页面。
6. 在退出、重启或异常时同时停止两个服务。

代理逻辑拆分成“共享内核”和“协议适配器”：

- `provider_proxy_core.py`：供应商路由、请求状态、重试策略、熔断、恢复记录和 Token 存储。
- `provider_proxy_codex.py`：现有 OpenAI Responses 协议的判断和解析。
- `provider_proxy_claude.py`：Anthropic Messages 协议的判断和解析。
- `codex_local_proxy.py`：Codex 数据加载、FastAPI 路由和 Codex 控制接口。
- `claude_local_proxy.py`：Claude 数据加载、FastAPI 路由和 Claude 控制接口。
- `provider_hub_app.py`：统一桌面启动、托盘、浏览器和双服务生命周期。
- `local_proxy_static/`：Codex 控制台资源。
- `claude_proxy_static/`：Claude Code 控制台资源。

现有 Codex 逻辑迁移到共享内核时，公开接口、设置格式、重试行为和测试结果必须保持不变。

## 本地数据

为保证旧版本升级兼容，现有 Codex 数据目录不迁移：

```text
~/.codex-local-proxy/
```

Claude Code 使用独立数据目录：

```text
~/.claude-local-proxy/
```

两个目录分别保存设置文件、Token 数据库和最近 24 小时的恢复记录。安装包只有一个不代表运行数据需要混在一起；分开保存可以避免统计、端口和供应商状态互相污染。

## Claude 供应商加载

Claude 服务以 SQLite 只读方式打开 `~/.cc-switch/cc-switch.db`，查询 `providers.app_type = 'claude'`，并关联同类型的 `provider_endpoints`。

当前本机数据库已经验证存在 17 条 Claude 供应商记录。Claude 配置位于 `settings_config` JSON 的 `env` 字段中，主要字段包括：

- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_MODEL`
- `ANTHROPIC_DEFAULT_OPUS_MODEL`
- `ANTHROPIC_DEFAULT_SONNET_MODEL`
- `ANTHROPIC_DEFAULT_HAIKU_MODEL`

上游地址优先使用 `ANTHROPIC_BASE_URL`，缺失时使用 `provider_endpoints.url`。地址必须是合法 HTTP 或 HTTPS 地址，不能包含用户名、密码、查询参数或片段。

认证字段按以下顺序确定：

1. `meta.apiKeyField` 明确指定的字段。
2. `ANTHROPIC_API_KEY`。
3. `ANTHROPIC_AUTH_TOKEN`。

`ANTHROPIC_API_KEY` 转为上游 `x-api-key` 请求头；`ANTHROPIC_AUTH_TOKEN` 转为 `Authorization: Bearer ...`。密钥只保存在进程内存中，不返回给网页，不写日志，不写统计数据库。

当 `meta.commonConfigEnabled` 为真时，加载 `settings.common_config_claude` 并与供应商配置合并，供应商自己的值优先。代理只读取和请求相关的环境字段，不使用 Claude Code 本地权限设置。

## 协议兼容规则

`meta.apiFormat` 为 `anthropic` 或未填写时，供应商可用于 Claude Code 原生 Messages 协议。

`meta.apiFormat = "openai_chat"` 的供应商不能直接接收 Claude Code 的 Anthropic 请求。第一版在控制台显示为“不兼容”，不允许选择，避免请求格式错误。后续如果确实需要，再单独实现协议转换。

缺少地址或缺少密钥的供应商显示为“配置不完整”，同样不能成为活动路由。

## Claude 请求流程

Claude Code 配置本地地址后，会向 `POST /v1/messages` 发送请求：

1. Claude 路由器记录当时选中的可用供应商。
2. 中转读取请求体，最大仍为 64 MiB，并提取模型名用于统计。
3. 删除客户端传入的 `x-api-key`、`authorization` 和代理相关请求头。
4. 根据当前供应商重新注入真实认证信息。
5. 请求发送到 `<供应商地址>/v1/messages`。
6. JSON 响应原样返回；SSE 流式事件不转换格式，直接流式转发。
7. 请求完成后记录聚合 Token，并结束活动请求计数。

中转默认不改写请求中的模型名。CC Switch 中保存的默认模型用于控制台展示和健康检查，Claude Code 请求中的 `model` 仍是实际发送给上游的模型。

## 重试和供应商切换

以下错误允许在尚未输出内容时重试：

- 连接失败和响应开始前的连接中断。
- HTTP `408`、`429`、`500`、`502`、`503`、`504`。
- Anthropic 表示过载的 HTTP `529`。
- 临时网关 HTML `404`。
- Anthropic SSE 中的 `overloaded_error`、`api_error`、`rate_limit_error` 或同类临时错误。

Claude SSE 在出现真正可见内容前进行短暂缓冲。只有收到文本、思考内容或工具参数等 `content_block_delta` 后，才认为响应已经开始输出。单独的 `message_start` 不算可见输出。

如果在可见输出前失败，中转关闭旧连接并按设置重试。每次重试前重新读取当前选择的供应商，因此用户在网页上切换供应商后，等待中的请求可以由新供应商接管。

如果文本、思考内容或工具参数已经发送给 Claude Code，之后发生错误时不重放请求，避免重复内容、重复工具调用和重复计费。该错误只记录为“输出后中断”。

固定或递增等待、最大尝试次数、无限重试、`Retry-After`、客户端断开和熔断逻辑与现有 Codex 功能保持一致。

## Token 统计

Claude 适配器从以下 Anthropic 事件读取 Token：

- `message_start.message.usage.input_tokens`
- `message_start.message.usage.cache_creation_input_tokens`
- `message_start.message.usage.cache_read_input_tokens`
- `message_delta.usage.output_tokens`

如果成功响应没有 usage，中转使用现有 Token 估算能力计算输入和输出。数据库只保存供应商 ID、模型、时间、状态和 Token 数量，不保存请求正文、回答正文、请求头或密钥。

Claude 控制台提供今日、近 24 小时、近 7 日、近 30 日和全部时间范围。

## 两个网页

Codex 网页继续展示：

- Codex 供应商列表和当前选择。
- Responses API 请求状态。
- Codex 重试、熔断、恢复记录和 Token 统计。
- Codex `config.toml` 配置片段。

Claude Code 网页展示：

- Claude 供应商列表和当前选择。
- 供应商是否兼容 Anthropic 协议。
- 供应商默认 Opus、Sonnet、Haiku 模型信息。
- Messages API 请求状态。
- Claude 重试、熔断、恢复记录和 Token 统计。
- PowerShell 和 Bash 配置片段。

两个页面顶部提供互相跳转的入口，但操作只影响当前页面对应的服务。

## Claude Code 配置

PowerShell 配置片段：

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:17891"
$env:ANTHROPIC_API_KEY = "local-claude-proxy"
```

Bash 配置片段：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:17891"
export ANTHROPIC_API_KEY="local-claude-proxy"
```

本地占位 Key 不会发送到上游。Claude 中转会删除它，然后使用当前供应商的真实密钥。

## 托盘和浏览器行为

统一程序只创建一个托盘图标。菜单包含：

- 打开 Codex 控制台。
- 打开 Claude Code 控制台。
- 重启本地中转。
- 退出本地中转。

重复启动程序时不创建第二组服务，而是打开已有控制台。程序退出时同时停止两个端口，避免留下后台进程。

端口修改分别保存在各自设置中。任一端口修改都需要重启统一程序；重启后两个服务一起重新启动。

## 打包和升级

PyInstaller 继续构建一个 Windows 可执行文件，打包内容增加 Claude 服务代码和 `claude_proxy_static/`。构建、校验和发布流程仍只产生一套程序与 SHA-256 文件。

为了兼容现有用户，继续保留当前 `CodexLocalProxy-win-x64.exe` 文件名和快捷方式名称。产品页面和托盘菜单体现双服务能力，但不改变旧自动化使用的下载地址。

升级后第一次启动：

1. 按原逻辑加载 Codex 设置和统计。
2. Claude 数据目录不存在时自动创建默认设置。
3. 加载 CC Switch 中的 Claude 供应商。
4. 同时启动两个端口。
5. 不修改 `~/.claude/settings.json` 或 CC Switch 数据库。

## 错误处理和安全边界

- 两个服务都只能监听回环地址。
- CC Switch 数据库始终只读。
- 控制写操作继续要求本地自定义请求头并校验 Host。
- Claude 客户端认证头在转发前删除，再注入供应商认证。
- 密钥从状态接口、异常摘要、恢复记录和日志中脱敏。
- 不兼容、缺地址、缺密钥和指向本地中转自身的供应商不能成为活动路由。
- 一个服务启动失败时，统一程序显示明确错误并停止另一服务，避免进入只启动一半但用户不知情的状态。

## 测试和验收

自动测试覆盖：

- 现有 Codex 全部测试保持通过。
- Claude 数据库真实结构加载，但测试不比较或输出密钥值。
- API Key 和 Auth Token 两种认证头注入。
- 普通和流式 `/v1/messages` 转发。
- HTTP `408/429/5xx/529` 和 SSE 内嵌错误重试。
- 输出前切换供应商、输出后禁止重放、熔断和客户端断开。
- Claude 输入、输出和缓存 Token 统计。
- 两个网页的状态、切换、刷新、配置复制和跳转。
- 单程序同时启动两个端口，重启和退出时同时停止。
- PyInstaller 包含两套网页资源和所有运行模块。

手工验收：

1. 从安装包启动程序。
2. 验证一个托盘图标和两个控制台页面。
3. 在 Claude 页面加载本地 CC Switch Claude 供应商。
4. 使用配置片段启动 Claude Code。
5. 切换供应商后验证新请求使用新供应商。
6. 模拟临时失败，验证重试和恢复记录。
7. 验证 Codex 原有功能没有变化。

## 实施顺序

1. 为现有 Codex 行为补足特征测试，抽取协议无关的共享内核。
2. 让 Codex 使用共享内核并跑完整回归测试。
3. 增加 Claude 供应商加载器和 Anthropic 协议适配器。
4. 增加 Claude FastAPI 服务和独立网页。
5. 增加统一桌面启动器、单托盘和双服务生命周期管理。
6. 更新 PyInstaller、快捷方式、测试和中文使用文档。
7. 完成双端口本地冒烟测试和安装包验证。
