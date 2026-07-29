## Codex 本地中转 Windows 便携版

下载 `CodexLocalProxy-win-x64.exe` 后直接双击运行。程序会打开本地控制台并常驻 Windows 通知区域。

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
