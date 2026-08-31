# Codex 本地中转

统一程序只启动一个监听 `127.0.0.1:17890` 的本地中转，从 `~/.cc-switch/cc-switch.db` 只读加载两套供应商。Codex 控制台地址：

```text
http://127.0.0.1:17890/control/codex/
```

```text
Claude Code: http://127.0.0.1:17890/control/claude/
```

## Windows 便携版

从 GitHub Releases 下载 `CodexLocalProxy-win-x64.exe` 后直接双击运行。便携版自带 Python 和运行依赖，通过一个图标常驻 Windows 通知区域，不需要安装项目虚拟环境。托盘菜单可以分别打开两个控制台视图。

便携版仍然从当前用户的 `~/.cc-switch/cc-switch.db` 读取供应商，因此需要先安装并配置 CC Switch。共享配置与两套协议数据统一放在 `~/.codex-local-proxy/`，Codex 和 Claude Code 的供应商选择、统计及恢复记录仍互不混用。

## Claude Code 接入

在 Claude Code 控制台点击“导入到 CCS”，通过 CC Switch 注册本地中转。若需要绕过 CC Switch，也可以在当前终端执行等价配置：

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:17890"
$env:ANTHROPIC_API_KEY = "local-claude-proxy"
claude
```

统一服务把 `/v1/messages` 和 `/v1/messages/count_tokens` 路由到 Claude 协议，其余 `/v1/*` 路由到 Codex 协议；不提供无 `/v1` 前缀的 `/messages`。Claude 协议读取 CC Switch 的 `app_type = "claude"` 供应商，支持 `ANTHROPIC_API_KEY` 和 `ANTHROPIC_AUTH_TOKEN`。它原样转发 Anthropic Messages 请求，并在可见输出开始前处理网络错误、HTTP `408/429/500/502/503/504/529` 和 Anthropic SSE 临时错误。输出开始后不会重放请求。

`meta.apiFormat = "openai_chat"` 的条目会在页面显示为协议不兼容。第一版不进行 OpenAI Chat Completions 到 Anthropic Messages 的转换。

从旧版本首次启动时，程序会拆分 `~/.codex-local-proxy/settings.json`、`~/.codex-local-proxy/usage.sqlite3` 和旧 Claude 目录中的协议数据。SQLite 数据通过备份接口迁移，已存在的新文件不会被覆盖；确认新文件存在后会删除旧文件和空目录，不额外保留备份。更早版本位于 `%LOCALAPPDATA%\CodexLocalProxy` 的 Codex 数据也会作为兼容来源读取。

可以使用同一 Release 中的 `.sha256` 文件校验下载内容。首个未签名版本可能触发 Windows SmartScreen 的“未知发布者”提示。

## 启动

```powershell
.\.venv\Scripts\python.exe local_proxy_app.py
```

安装桌面快捷方式：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_local_proxy_shortcut.ps1
```

桌面快捷方式使用 `pythonw.exe` 在后台启动。首次启动会打开控制台并在 Windows 通知区域显示常驻图标；双击图标或右键选择“打开控制台”可再次打开页面，选择“退出本地中转”会停止服务。重复启动快捷方式只会打开已有控制台，不会创建第二个服务进程。

## 运行设置

Codex 和 Claude Code 控制台的“运行设置”页都可以修改统一端口、供应商数据源、服务器检测地址和控制台界面。控制台可选择“经典界面”或“新版界面”，两种协议共用该选择，保存后页面自动刷新生效，不会重启或中断中转请求。若选中的界面无法加载，可在控制台 URL 后临时添加 `?ui=classic` 或 `?ui=modern` 进入另一套界面恢复设置，查询参数不会修改持久化选择。

数据源保存前会以只读方式同时验证两套加载器，保存后立即替换两种协议的新请求供应商列表；某一种协议没有供应商时，另一种协议仍可正常使用。检测地址和重试设置会立即对两种协议生效。端口修改需要退出并重新启动本地中转，随后将两种本地中转重新导入到 CCS。

本地数据目录固定为 `~/.codex-local-proxy/`，页面只展示该位置，不允许修改：

```text
~/.codex-local-proxy/
├── shared-settings.json
├── codex-settings.json
├── codex-usage.sqlite3
├── claude-settings.json
└── claude-usage.sqlite3
```

`shared-settings.json` 保存端口、CC Switch 数据库路径、检测地址、重试策略和控制台界面。两个 `*-settings.json` 只保存各自的供应商选择、排序和隐藏状态，两个 `*-usage.sqlite3` 分别保存 Token 聚合数据与最近 24 小时的脱敏恢复记录。Codex 自身的配置文件仍是 `~/.codex/config.toml`，本地中转不会自动覆盖它。

## 首次接入 Codex

在控制台点击“导入到 CCS”，通过 `ccswitch://v1/import` 将本地中转注册为 Codex 供应商，默认模型为 `gpt-5.6-sol`；随后在 CC Switch 中切换到该供应商。此后 Codex 始终连接本机地址，切换本项目中的上游供应商不再需要重启。

每个请求的首次尝试会使用当时选中的供应商。切换后，新请求立即使用新供应商；尚未输出内容的旧请求如果再次遇到可重试错误，下一次尝试会由最新供应商接管，并重新应用新供应商的地址、认证头和默认参数。已经开始输出的流式请求继续使用原供应商直到完成，不会跨供应商重放。

## 供应商列表与 Token 统计

- “管理列表”模式可以拖动供应商调整顺序，也可以隐藏和恢复供应商。当前正在使用的供应商需要先切换后才能隐藏。
- Codex 列表中每个供应商都有“复制临时启动命令”按钮。点击后会按当前操作系统生成一条直接启动 Codex CLI 的单次命令，通过 `-c` 临时覆盖供应商地址、Responses 协议、Key、请求头和查询参数，不修改 `~/.codex/config.toml`；缺少凭据时按钮不可用。Windows 命令会在 Codex 退出后恢复原来的临时环境变量，macOS/Linux 只为本次进程设置环境变量。
- Codex 与 Claude Code 的排序和隐藏状态分别保存在 `codex-settings.json` 和 `claude-settings.json`，不会修改 CC Switch 数据库。
- 指向当前本地中转监听端口的回环供应商会在加载时排除，避免把“Codex 本地中转”自身显示为可选上游。
- Token 数据优先读取上游 Responses 终止事件或非流式响应中的 `usage`；上游没有返回 `usage` 时，才使用与模型匹配的 `tiktoken` 编码估算输入和可见输出。
- 用量分别保存在 `codex-usage.sqlite3` 和 `claude-usage.sqlite3`。Token 记录只包含供应商 ID、模型、时间、状态和 Token 数值，不保存请求正文、回答正文或 Key。
- 控制台支持今日、近 24 小时、近 7 日（严格 `7 × 24` 小时）、近 30 日和全部时间范围。

## 自动恢复

- 建连错误、首个响应数据块前的流中断以及 HTTP `500/502/503/504` 会自动重试。
- HTTP `429` 总是进入重试；`Retry-After` 仅作为等待提示，并受本地最大等待时间限制。
- Responses SSE 在可见输出前出现内嵌 `429`、模型容量已满或临时上游错误时会静默重试；推理、状态和工具参数事件不会被误判为可见输出。正文已经输出后不会重放，避免客户端收到重复内容或工具调用。
- 默认最多尝试 4 次，等待 1 秒后按递增间隔重试，单次等待最长 30 秒。
- 控制台的“重试设置”页可以启用或关闭自动重试，设置最大尝试次数、首次等待、等待策略、最大等待和熔断参数。
- 最大尝试次数选择“无限重试”时内部保存为 `-1`；只对尚未输出内容的临时错误持续重试，Codex 断开或中转退出时停止。
- 多个请求独立并发转发和计数，一个请求等待重试不会阻塞其他请求。
- 供应商切换发生在重试前时，旧供应商的失败记录仍归属于旧供应商，活动请求计数会迁移到实际接管的新供应商。
- 一旦响应内容已经转发给 Codex，中转不会重放整个请求，以免重复文本、工具调用或计费。
- 同一供应商连续 3 个请求在重试后仍失败时会熔断 30 秒。
- 最近 24 小时的恢复记录会持久化到对应协议的 `*-usage.sqlite3`，包括等待重试、重试耗尽、客户端断开以及输出后失败未重放；过期记录会自动清理，控制台最多加载最新 500 条。
- 恢复记录只保存供应商、时间、尝试次数、阶段、结果和脱敏后的错误摘要，不保存请求正文、响应正文或认证信息。

## 安全边界

- 服务只允许监听回环地址。
- CC Switch 数据库使用 SQLite 只读连接。
- 普通状态、统计和配置 API 不返回上游 Key。只有用户点击 Codex 供应商的“复制临时启动命令”时，页面才会通过要求本地控制请求头且禁止缓存的接口读取一次 Key 并写入剪贴板；Key 不会渲染到页面或写入访问日志。复制出的命令包含认证信息，必须按密钥处理，避免保存到公共脚本或共享终端历史。
- 转发时会移除 Codex 传入的认证头，再应用当前供应商认证。
- 控制写操作要求同源自定义请求头，并校验 Host。
- 请求体上限为 64 MiB，响应保持流式转发。
