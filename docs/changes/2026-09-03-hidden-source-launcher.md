+++
id = "2026-09-03-hidden-source-launcher"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 源码模式隐藏启动

## 目标

让 Windows 源码模式的桌面快捷方式和开机自启在后台启动本地中转，只显示托盘图标，不闪现控制台窗口。

## 现状

桌面快捷方式和开机自启直接调用虚拟环境的 `pythonw.exe`。部分 Python 版本的虚拟环境启动器会再转发到系统解释器，双击时可能短暂显示黑色控制台窗口。

## 设计范围

- 新增使用 `wscript.exe` 隐藏启动 Python 的 Windows 启动脚本。
- 修改桌面快捷方式安装脚本，目标改为 `wscript.exe`。
- 修改源码模式开机自启命令，使用同一隐藏启动链。
- 兼容并迁移已有的旧版开机自启命令。
- 增加路径引用和启动命令回归测试。

## 非目标

- 不修改打包版 exe 的启动方式。
- 不修改本地中转服务、托盘功能、请求处理或数据库逻辑。
- 不删除用户现有快捷方式之外的文件。

## 兼容性

仅影响 Windows 源码模式的桌面快捷方式和当前用户开机自启注册表项。已有配置、数据库和接口保持不变。

## 风险

VBS 参数引用错误可能导致包含空格或非 ASCII 字符的路径无法启动。通过统一转义函数、临时快捷方式测试和实际健康检查降低风险；隐藏启动脚本缺失时保留直接 `pythonw.exe` 回退路径。

## 测试计划

- Python 开机自启命令和旧命令迁移单测。
- PowerShell 快捷方式安装脚本解析及临时快捷方式断言。
- VBS 启动器文件、参数和隐藏窗口配置检查。
- Python 全量测试、JavaScript 测试、Vue 构建、Python 编译和 `git diff --check`。
- 手动重新安装快捷方式并确认双击后无黑窗、托盘出现、`/healthz` 正常。

## 实际改动

- 新增 `scripts/start_local_proxy_hidden.vbs`，通过 `wscript.exe` 的隐藏窗口模式异步启动源码版本地中转。
- 修改 `scripts/install_local_proxy_shortcut.ps1`，桌面快捷方式改为指向 `wscript.exe`，并传入绝对路径和原有托盘参数。
- 修改 `local_proxy/application.py`，源码开机自启和托盘重启使用相同隐藏启动链；检测到旧版直连 `pythonw.exe` 注册表命令时自动迁移。
- 修改 `tests/test_local_proxy_app.py`，覆盖隐藏命令、旧命令迁移、快捷方式脚本和 VBS 参数行为。
- 更新 `docs/codex-local-proxy.md` 的源码快捷方式说明。

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：540 项通过。
- `.venv\\Scripts\\python.exe -m unittest tests.test_local_proxy_app`：25 项通过。
- `cscript.exe //nologo scripts\\start_local_proxy_hidden.vbs ... --smoke-test`：返回码 0。
- `scripts\\install_local_proxy_shortcut.ps1` 临时快捷方式实际生成：目标为 `C:\\Windows\\System32\\wscript.exe`，参数包含隐藏启动脚本和 `--tray --no-browser`。
- PowerShell 安装脚本语法检查、Python 编译检查和 `git diff --check`：通过。
- `tests/*.test.js`：87 项通过。

## PR

pending
