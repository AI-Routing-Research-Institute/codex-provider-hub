## Codex 本地中转 Windows 便携版

下载 `CodexLocalProxy-win-x64.exe` 后直接双击运行。程序会静默启动并常驻 Windows 通知区域，不会自动打开网页；需要时可从托盘菜单分别打开 Codex 和 Claude Code 控制台。托盘菜单支持为当前用户开启或关闭“开机自启”。

使用要求：

- Windows x64
- 已安装并配置 CC Switch
- CC Switch 数据库位于当前用户的 `~/.cc-switch/cc-switch.db`

安全说明：

- EXE 不包含供应商数据库、API Key、本机设置、检测地址或用量数据
- 设置和用量保存在 `%LOCALAPPDATA%\CodexLocalProxy`
- 可使用随附的 `.sha256` 文件验证下载完整性
- 当前版本尚未进行商业代码签名，Windows SmartScreen 可能显示“未知发布者”

本版本包含供应商切换、Token 统计、自动重试与熔断、服务器可用性摘要，以及输出前 `response.failed/upstream_error` 自动恢复。

## Codex 本地中转 macOS 便携版

下载 `CodexLocalProxy-macos-arm64.zip` 后解压得到 `CodexLocalProxy-macos-arm64.app`，拖入「应用程序」文件夹（可选）即可使用。程序会静默启动并常驻 macOS 菜单栏，需要时可从菜单栏手动打开 Codex 或 Claude Code 控制台。仅支持 Apple Silicon（M 系列芯片）机型。

使用要求：

- macOS 11.0 或更高版本，Apple Silicon（ARM64）
- 已安装并配置 CC Switch
- CC Switch 数据库位于当前用户的 `~/.cc-switch/cc-switch.db`

首次打开说明（重要）：

- 当前版本未经 Apple 代码签名与公证，直接双击会被 Gatekeeper 拦截
- **首次打开方式**：右键点击 `.app` → 选择「打开」→ 在「无法验证开发者」对话框中点击「打开」，之后即可正常双击启动
- 或在终端执行：`xattr -dr com.apple.quarantine /路径/到/CodexLocalProxy-macos-arm64.app`

安全说明：

- App 不包含供应商数据库、API Key、本机设置、检测地址或用量数据
- 共享设置与两套协议数据统一保存在 `~/.codex-local-proxy`，Codex 和 Claude Code 的选择与用量文件彼此独立
- 可使用随附的 `.sha256` 文件验证下载完整性
