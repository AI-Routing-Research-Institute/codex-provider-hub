+++
id = "2026-09-03-portable-launcher-path-tests"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 启动器测试路径兼容性修复

## 目标

修复源码模式静默启动测试在不同操作系统和 CI 运行器上因路径别名或规范化差异产生的误报，确保发布工作流的全量测试稳定通过。

## 现状

启动器实现使用 `Path.resolve()` 生成 Python 运行时路径。测试辅助函数在构造期望命令时没有对临时 Python 路径做同样的解析，导致 Windows 短文件名与长文件名、macOS `/private` 前缀等等价路径比较失败。

## 设计范围

- 让测试期望命令先按产品实现的规则解析可执行文件路径。
- 保留 Windows 隐藏启动器和非 Windows 直接启动命令的现有断言。
- 在本地和 CI 使用的路径形式不同的情况下比较同一语义命令。

## 非目标

- 不修改产品启动器逻辑、快捷方式、托盘行为或代理请求处理。
- 不修改发布工作流平台矩阵或构建配置。

## 兼容性

仅影响测试代码和交付说明，无运行时接口、配置、数据库或迁移变化。

## 风险

如果测试解析规则与产品实现不一致，可能隐藏真实的启动命令回归。通过复用相同的 `Path.resolve()` 语义，并保留平台专用命令结构断言降低风险。

## 测试计划

- 运行启动器相关 Python 单测。
- 运行 Python 全量单测、JavaScript 测试、Node 语法检查、Python 编译和 Vue 构建。
- 运行 `git diff --check` 和仓库 pre-push 门禁。
- 确认 Windows 与 macOS 发布工作流不再因临时路径格式失败。

## 实际改动

- `tests/test_local_proxy_app.py` 先解析测试注入的 Python 可执行文件路径，再构造 `pythonw.exe`、脚本和平台启动器命令。
- 保留 Windows 隐藏启动链和非 Windows 直接 Python 启动命令的结构断言。

## 验证结果

- `python -m unittest tests.test_local_proxy_app`：25 项通过。
- `python -m unittest discover -s tests -p "test_*.py"`：541 项通过，耗时 80.339 秒。
- JavaScript 测试：18 个文件、91 项通过。
- `npm run build --prefix proxy_static`：Vite 生产构建通过，转换 29 个模块。
- `python -m compileall -q provider_status local_proxy scripts tests`：通过。
- Node JavaScript 语法检查：通过。
- `git diff --check`：通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/71
