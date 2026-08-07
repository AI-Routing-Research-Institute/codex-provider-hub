# Codex Provider Hub v0.1.7

## 用户可见变化

- Codex Responses 与 Claude Messages 合并为一个本地服务，默认只监听 `127.0.0.1:17890`。
- Codex 与 Claude Code 控制台共用一套前端资源，分别通过 `/control/codex/` 和 `/control/claude/` 访问。
- 共享端口、CC Switch 数据库路径、重试策略和检测地址；供应商选择、排序、隐藏及用量数据仍按协议隔离。
- 新增最近 24 小时请求记录、运行中请求、筛选、分页和请求结果详情。
- Codex 支持显示会话名称并为指定会话固定供应商；Claude 请求记录可用，但不启用会话路由。
- 加固模型容量错误识别，超长错误文本仍可在输出前进入自动重试。

## 数据与兼容性

运行数据统一保存在 `~/.codex-local-proxy/`：

```text
shared-settings.json
codex-settings.json
codex-usage.sqlite3
claude-settings.json
claude-usage.sqlite3
```

- 首次启动会自动拆分旧 Codex/Claude 配置和 SQLite 数据；确认新目标存在后删除旧文件，不额外保留备份。
- 不再监听旧 Claude 独立端口 `17891`。Claude Code 继续以本地根地址作为 `ANTHROPIC_BASE_URL`，实际请求 `/v1/messages`。
- SQLite 会自动增加请求历史表；旧 Token 和恢复记录保持兼容，无需手动迁移。
- EXE/App 不包含 CC Switch 数据库、API Key、本机配置或用量数据。

## 下载与运行

- Windows x64：下载 `CodexLocalProxy-win-x64.exe`。当前版本未进行商业代码签名，SmartScreen 可能显示“未知发布者”。
- macOS：下载 `CodexLocalProxy-macos-arm64.zip`，仅支持 macOS 11+ Apple Silicon。首次运行需右键选择“打开”，或移除 Gatekeeper 隔离属性。
- 两个平台均需先安装并配置 CC Switch，并确保 `~/.cc-switch/cc-switch.db` 存在。
- 可使用随附的 `.sha256` 文件验证下载完整性。

## 验证证据

- Python 完整测试：335 项通过。
- Node 前端测试：19 项通过。
- 源码 smoke test：通过，单服务、两个控制台、两套协议及共享静态资源均已覆盖。
