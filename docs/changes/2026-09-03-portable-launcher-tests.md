+++
id = "2026-09-03-portable-launcher-tests"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 跨平台启动器测试

## 目标

修复 macOS 发布工作流中因 Windows 专用环境变量和隐藏启动器断言导致的测试失败，同时保留 Windows 源码模式隐藏启动行为的覆盖。

## 现状

启动器产品逻辑已经按平台区分：Windows 且隐藏脚本可用时通过 `wscript.exe` 启动，其他平台使用直接 Python 命令。新增测试却无条件断言 `WINDIR` 和 `wscript.exe`，导致 macOS 上出现两个环境变量错误和一个错误断言。

## 设计范围

- 让源码模式自动启动命令测试按当前平台和可用运行时断言。
- 在 Windows 环境继续验证 `wscript.exe`、隐藏 VBS 和 `pythonw.exe` 参数。
- 在 macOS/Linux 环境验证直接 Python 启动命令，不读取 Windows 专用环境变量。
- 保持启动器产品代码和 Windows 快捷方式行为不变。

## 非目标

- 不修改本地代理请求处理、托盘功能或启动器产品逻辑。
- 不修改 GitHub Actions 的平台矩阵或发布流程。
- 不删除已有启动器测试，只修正其平台假设。

## 兼容性

仅影响测试代码及其永久变更说明，不改变运行时配置、数据库、接口和用户启动方式。Windows、macOS 和 Linux 均可执行对应平台断言。

## 风险

平台条件写得过宽可能导致 Windows 隐藏启动路径失去覆盖，或在非 Windows 环境误测 Windows 行为。通过同时保留 Windows 专用断言和非 Windows 直接命令断言，并运行本机全量测试降低风险。

## 测试计划

- 运行启动器相关 Python 单测。
- 运行 Python 全量单测和 JavaScript 全量测试。
- 运行 Python 编译、Node 语法检查、Vue 构建和 `git diff --check`。
- 确认 macOS 发布工作流的 Python 测试不再因为 `WINDIR` 失败。

## 自审

- 测试只读取产品现有的 `_hidden_source_proxy_command` 平台分支，不引入新的运行时分支。
- Windows 断言仍覆盖隐藏启动脚本，非 Windows 断言覆盖当前实际直接启动命令。
- 版本 bump 使用 `patch`，因为这是发布测试兼容性修复。

## 实际改动

- `tests/test_local_proxy_app.py` 使用明确的平台分支构造期望启动命令。
- Windows 环境继续断言 `wscript.exe`、隐藏 VBS 和 `pythonw.exe` 参数。
- macOS/Linux 环境断言直接 Python 启动命令，不读取 `WINDIR`。

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest tests.test_local_proxy_app`：25 项通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：541 项通过，耗时 76.358 秒。
- `Get-ChildItem -Path tests -File -Filter *.test.js | Sort-Object Name | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }`：18 个 JavaScript 测试文件、91 项通过。
- `npm run build --prefix proxy_static`：Vite 生产构建通过，转换 29 个模块。
- `.venv\\Scripts\\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- `node --check proxy_static/classic/app.js` 及 `proxy_static/src`、`provider_status/static` 下 JavaScript 文件：通过。
- `git diff --check`：通过。

## PR

pending
