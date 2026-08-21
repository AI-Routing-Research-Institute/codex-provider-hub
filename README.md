# Codex Provider Hub

<div align="center">

在一个本地程序中管理 Codex 与 Claude Code 的多个 API 供应商，支持网页切换、失败重试、请求记录、Token 统计和远程健康监控。

![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-4b5563?style=flat-square)
[![License](https://img.shields.io/badge/License-AGPL--3.0--or--later-663399?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/github/v/release/AI-Routing-Research-Institute/codex-provider-hub?style=flat-square)](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest)

</div>

## 目录

- [适合谁](#适合谁)
- [五分钟快速开始](#五分钟快速开始)
- [推荐搭配 CC Switch](#推荐搭配-cc-switch)
- [配置 Codex](#配置-codex)
- [配置 Claude Code](#配置-claude-code)
- [管理和切换供应商](#管理和切换供应商)
- [请求传输方式](#请求传输方式)
- [重试、统计与监控](#重试统计与监控)
- [常见问题](#常见问题)
- [其他安装方式](#其他安装方式)
- [开发与部署](#开发与部署)
- [安全边界](#安全边界)
- [开源与商业授权](#开源与商业授权)

## 适合谁

Codex Provider Hub 适合以下用户：

- 同时使用多个 Codex API 或 Claude Code API 中转，希望在网页中即时切换。
- 已经使用 CC Switch 管理供应商，希望 Codex 与 Claude Code 共用一个本地程序。
- 需要输出前自动重试、请求记录、Token 统计、供应商监控和故障诊断。
- 部分供应商会拒绝普通 Python HTTP 客户端，需要单独启用 `curl_cffi` 兼容传输。

它不是 API 供应商，不提供 API Key，也不是用于公网多租户部署的通用网关。你需要准备自己的合法供应商地址和凭据。

程序默认只监听 `127.0.0.1:17890`。同一个进程提供两套独立控制台：

```text
Codex       http://127.0.0.1:17890/control/codex/
Claude Code http://127.0.0.1:17890/control/claude/
```

## 五分钟快速开始

### 1. 准备 CC Switch

推荐先安装并配置 CC Switch，至少添加一个 Codex 或 Claude Code 供应商。默认数据库位置为：

```text
~/.cc-switch/cc-switch.db
```

当前统一程序会同时构建 Codex 和 Claude Code 控制台，因此启动前应保证该数据库存在且可以读取。

### 2. 下载程序

Windows x64：

- [下载 CodexLocalProxy-win-x64.exe](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-win-x64.exe)
- [下载 SHA-256 校验文件](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-win-x64.exe.sha256)

macOS Apple Silicon：

- [下载 CodexLocalProxy-macos-arm64.zip](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-macos-arm64.zip)
- [下载 SHA-256 校验文件](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-macos-arm64.zip.sha256)

### 3. 启动程序

Windows 直接双击 EXE。程序静默启动并常驻通知区域，不会自动打开网页。右键托盘图标可以打开 Codex 控制台、Claude Code 控制台、设置开机自启或退出程序。

macOS 解压后得到 `.app`。当前发布包未进行 Apple 签名和公证，首次启动需要右键应用并选择“打开”。也可以移除隔离标记：

```bash
xattr -dr com.apple.quarantine /路径/到/CodexLocalProxy-macos-arm64.app
```

### 4. 检查供应商

打开 Codex 控制台，确认列表已经出现供应商。首次运行时，Codex 供应商会从 CC Switch 初始化导入到本地独立目录。

再打开 Claude Code 控制台。Claude Code 供应商直接来自当前 CC Switch 数据源，协议不兼容或缺少凭据的记录会显示但不能选择。

### 5. 配置客户端

在两个控制台中分别点击“复制 Codex 配置”和“复制 Claude 配置”，按照下面两节完成一次客户端接入。之后切换供应商不需要重复修改客户端配置。

## 推荐搭配 CC Switch

CC Switch 负责集中维护供应商，本工具负责本地转发、切换、重试和统计，两者的关系如下：

- **Codex**：本地目录为空时从 CC Switch 初始化导入。导入后由 Codex Provider Hub 独立管理，网页编辑不会反向修改 CC Switch。
- **Claude Code**：继续从 CC Switch 数据源读取供应商。修改 Claude 供应商时，建议先在 CC Switch 中修改，再在控制台刷新。
- **再次同步 Codex**：进入“供应商”，点击“管理”，选择“仅新增”或“覆盖已有”，再点击“导入 CCS”。
- **仅新增**：保留本地已有供应商，只导入新的 CC Switch 记录。
- **覆盖已有**：用 CC Switch 中的同 ID 记录覆盖本地副本，本地单独修改的内容也会被覆盖。

如果只管理少量 Codex 供应商，也可以在本工具的管理模式中直接新增。批量维护 Codex 和 Claude Code 时仍建议搭配 CC Switch 使用。

## 配置 Codex

### 通过控制台复制

1. 打开 `http://127.0.0.1:17890/control/codex/`。
2. 点击页面底部“复制 Codex 配置”。
3. 将片段合并到 `~/.codex/config.toml`。
4. 第一次配置后重启一次 Codex。

默认配置片段如下：

```toml
model_provider = "local_cc_switch"

[model_providers.local_cc_switch]
name = "CC Switch Local Proxy"
base_url = "http://127.0.0.1:17890/v1"
wire_api = "responses"
requires_openai_auth = true
```

此后 Codex 始终连接本地地址，网页中选择的新供应商会立即用于新请求，不需要再次修改 `config.toml`。

### 临时绕过本地中转

Codex 供应商行中的“复制临时启动命令”会生成一条直接使用该供应商启动 Codex CLI 的单次命令，不修改 `config.toml`。该命令包含供应商认证信息，应按密钥处理，不要放入公共脚本、Issue 或共享终端历史。

## 配置 Claude Code

### 通过控制台复制

1. 打开 `http://127.0.0.1:17890/control/claude/`。
2. 点击页面底部“复制 Claude 配置”。
3. 在准备启动 Claude Code 的终端中执行复制出的命令。
4. 从同一个终端启动 `claude`。

Windows PowerShell 示例：

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:17890"
$env:ANTHROPIC_API_KEY = "local-claude-proxy"
claude
```

macOS/Linux 示例：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:17890"
export ANTHROPIC_API_KEY="local-claude-proxy"
claude
```

也可以合并到 `~/.claude/settings.json`：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:17890",
    "ANTHROPIC_API_KEY": "local-claude-proxy"
  }
}
```

注意：

- `ANTHROPIC_BASE_URL` 后面不要手工添加 `/v1`，程序会转发 Claude Code 的 `/v1/messages`。
- `local-claude-proxy` 是非空的本地占位值，不是上游真实 Key。
- 不要把 `ANTHROPIC_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN` 配置为空字符串，否则 Claude Code 可能直接提示登录。
- 本地中转会移除客户端认证头，并使用网页当前选择的 Claude 供应商认证信息。

## 管理和切换供应商

### 日常切换

在供应商列表中点击目标供应商。切换只影响后续请求，已经输出内容的流不会被中断或重新发送。

### 管理 Codex 供应商

在 Codex 控制台点击“管理”后，可以：

- 拖动调整顺序。
- 隐藏暂时不用的供应商。
- 新增本地供应商。
- 编辑名称、Base URL、API Key、请求头、查询参数和请求传输方式。
- 删除非当前供应商。
- 从 CC Switch 仅新增或覆盖导入。

编辑供应商并保存后会立即刷新路由。普通状态接口和页面不会返回已保存的完整 Key。

### 管理 Claude Code 供应商

Claude Code 供应商来自 CC Switch。请在 CC Switch 中维护地址、认证和协议格式，然后回到 Claude Code 控制台刷新。当前只允许选择兼容 Anthropic Messages 协议并且具有凭据的供应商。

## 请求传输方式

该设置位于 Codex 控制台的“供应商 → 管理 → 编辑 → 请求传输方式”。

- **标准模式（httpx）**：默认选项，适合正常供应商，保持原有网络行为。
- **兼容模式（curl_cffi）**：适合相同地址和 Key 在 Codex 中正常，但通过本地中转固定返回 Cloudflare HTML 403 的供应商。

只为确实受到客户端指纹拦截的供应商启用兼容模式。`curl_cffi` 不会修复真实的认证失败、额度不足或上游无可用渠道。

Claude Code 上游统一使用 `curl_cffi`，Claude 控制台没有这个选择项。

## 重试、统计与监控

### 自动重试

- 建连失败、首个输出前的流中断，以及 HTTP `429/500/502/503/504` 可以自动重试。
- Claude Code 另外覆盖 `408` 和 `529`。
- 支持固定等待、递增等待、最大等待、无限重试和熔断。
- 默认不会自动轮换到其他供应商。等待重试时，如果用户手动切换当前供应商，下一次尚未输出的尝试会跟随新供应商。
- 已经向客户端输出内容后不会跨供应商重放，避免重复回答、工具调用和计费。

### 请求与 Token 统计

- 请求页面显示运行中和最近 24 小时记录。
- Token 优先使用上游 `usage`，缺失时使用 `tiktoken` 估算。
- 支持今日、近 24 小时、近 7 日、近 30 日和自定义时间范围。
- 本地数据库不保存请求正文和回答正文。

### 远程监控

Codex 供应商可以通过受限 SSH 导入器上传到独立状态服务，并在“监控管理”中查看服务器上的全部监控配置、排序、立即检测或删除。服务器部署模板位于 `deploy/`，公开示例位于 `config/providers.example.toml`。

该功能用于可用性监控，不代表当前本地目录使用的是同一份 Key，也不代表指定模型在本地请求时一定可用。

## 常见问题

### Claude Code 提示 `Not logged in · Please run /login`

1. 确认从配置了本地环境变量的同一个终端启动 Claude Code。
2. 确认 `ANTHROPIC_BASE_URL` 是 `http://127.0.0.1:17890`，不要追加 `/v1`。
3. 确认 `ANTHROPIC_API_KEY` 是非空占位值，不要同时留下空的 `ANTHROPIC_AUTH_TOKEN`。
4. 打开 Claude Code 控制台，确认当前供应商可选择且有认证信息。

### 返回 Cloudflare HTML 403

如果错误正文包含 `@font-face`、`cf-fonts` 或 Cloudflare 页面，而相同 Key 直接使用 Codex 正常，只为该 Codex 供应商启用 `curl_cffi` 兼容模式。

### 返回 HTTP 401

401 表示当前请求使用的认证未被上游接受。检查网页当前供应商、本地目录中保存的 Key、自定义 `Authorization`/`X-API-Key` 请求头以及 Base URL。服务器监控成功不等于本地当前记录一定使用相同凭据。

### 返回 HTTP 503 或“模型无可用渠道”

这是上游供应商的业务状态。切换 `httpx/curl_cffi` 不会解决真实的模型缺失、分组无渠道、额度不足或供应商维护。可以手动切换到支持该模型的供应商，再由原有重试逻辑接管尚未输出的请求。

### 控制台页面打不开

1. 查看系统托盘或菜单栏中程序是否仍在运行。
2. 访问 `http://127.0.0.1:17890/healthz`。
3. 检查 `17890` 是否被其他程序占用。
4. 不要通过结束所有同名进程的方式排查，以免中断正在使用本地中转的会话。

### 修改后没有生效

- 确认使用的是最新 Release，而不是旧的临时测试包。
- 供应商编辑需要点击“保存”，必要时刷新页面。
- Codex/Claude 供应商使用独立的当前选择，不要在错误的控制台中切换。
- 端口修改需要退出并重新启动程序，然后重新复制两套客户端配置。
- Codex 首次配置后需要重启一次 Codex；之后切换供应商无需重启。

## 其他安装方式

### macOS Intel

当前 Release 只提供 Apple Silicon 包。Intel Mac 可以按“从源码运行”操作。

### Windows 桌面快捷方式

从源码检出仓库后可以安装当前用户快捷方式：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_local_proxy_shortcut.ps1
```

### 从源码运行

需要 Python 3.11 或更高版本。使用 `uv`：

```powershell
uv venv --clear .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-status.txt
uv run --python .venv\Scripts\python.exe local_proxy_app.py
```

macOS/Linux：

```bash
uv venv --clear .venv
uv pip install --python .venv/bin/python -r requirements-status.txt
uv run --python .venv/bin/python local_proxy_app.py
```

使用原生 venv：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-status.txt
.\.venv\Scripts\python.exe local_proxy_app.py
```

追加 `--open-browser` 可以在启动时打开两套控制台。

## 开发与部署

### 供应商探测工具

```powershell
.\.venv\Scripts\python.exe probe_codex_cc_switch.py --list-providers
.\.venv\Scripts\python.exe probe_codex_cc_switch.py --current-only --json
```

### 状态服务

复制示例配置：

```bash
cp config/providers.example.toml config/providers.toml
```

单次运行 Worker：

```bash
./.venv/bin/python -m provider_status.worker \
  --config config/providers.toml \
  --control-database var/control/manual-probes.sqlite3 \
  --once
```

启动状态页：

```bash
./.venv/bin/python -m provider_status.web \
  --database var/public/status.sqlite3 \
  --control-database var/control/manual-probes.sqlite3 \
  --host 127.0.0.1 \
  --port 8000
```

生产用 systemd 和 Nginx 模板位于 `deploy/`。示例域名和供应商均为占位内容，不能直接用于生产。

### 项目结构

```text
.
├── local_proxy_app.py         统一启动入口
├── local_proxy/               本地中转、协议、供应商目录与应用生命周期
├── proxy_static/              Codex 与 Claude Code 共享控制台
├── probe_tools/               供应商探测工具
├── provider_status/           健康监测 Worker、存储和状态页
├── config/                    公开示例配置
├── deploy/                    systemd 与 Nginx 模板
├── scripts/                   构建、快捷方式和仓库策略脚本
├── tests/                     Python、PowerShell 和 JavaScript 测试
└── docs/                      设计、变更记录和详细说明
```

### 测试

```powershell
python -m unittest discover -s tests
node --check proxy_static/app.js
node --check provider_status/static/app.js
Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }
```

## 安全边界

- 本地中转只允许监听回环地址。
- CC Switch SQLite 数据源使用只读连接；Codex 导入后的本地副本保存在 `~/.codex-local-proxy/codex-providers.sqlite3`。
- 普通状态、统计和供应商编辑接口不会返回完整上游 Key。
- “复制临时启动命令”会把当前 Codex 供应商认证写入剪贴板，应按密钥处理。
- 转发前移除客户端认证头，再应用当前供应商认证配置。
- Token 统计和请求记录不保存请求正文或回答正文。
- 私有配置、数据库、日志、探测报告、虚拟环境、证书和密钥默认不纳入版本控制。
- `config/providers.example.toml` 只包含示例域名，不包含真实供应商或凭据。

## 开源与商业授权

Codex Provider Hub 采用 [GNU Affero General Public License v3.0 or later](LICENSE)，SPDX 标识为 `AGPL-3.0-or-later`。

- 个人和组织可以在包括商业场景在内的环境中使用、复制、修改和分发本项目。
- 分发或提供修改版网络服务时，需要按 AGPL 履行对应源码、许可证和版权义务。
- 二次开发和再分发必须保留 [NOTICE](NOTICE) 中的版权声明和原始项目出处。
- 如果希望闭源分发、闭源提供修改版网络服务，或把代码整合进无法遵守 AGPL 的专有产品，需要申请[闭源商业授权](COMMERCIAL-LICENSE.md)。
- 完整遵守 AGPL 的商业使用无需购买商业许可证。

商业授权说明文件不是商业合同本身。请按其中流程联系维护者并另行签署正式协议。
