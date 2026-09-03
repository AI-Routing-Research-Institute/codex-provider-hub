+++
id = "2026-09-03-suppress-git-console-window"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 源码版本查询隐藏控制台

## 目标

消除 Windows 源码模式启动时执行 Git 版本查询产生的瞬时控制台窗口。

## 现状

本地中转导入时通过 `git describe` 读取源码版本。虽然主进程使用 `pythonw.exe` 或隐藏启动器运行，但 Git 是控制台程序，当前子进程没有设置 Windows 的无窗口创建标志，可能短暂拉起 `conhost.exe`。

## 设计范围

- 为版本查询子进程增加 Windows `CREATE_NO_WINDOW` 创建标志。
- 保持 Git 输出捕获、超时和错误回退逻辑不变。
- 增加 Windows 参数回归测试。

## 非目标

- 不修改版本号解析规则。
- 不修改快捷方式、托盘、服务启动或请求转发逻辑。
- 不影响打包版跳过 Git 查询的行为。

## 兼容性

仅改变 Windows 下 Git 子进程的创建方式，不改变配置、数据库、接口或版本结果。

## 风险

某些测试替身可能不接受新增的 `creationflags` 参数。通过统一按平台构造参数并更新现有 runner 测试降低风险；Git 查询失败时继续返回既有 fallback 版本。

## 测试计划

- 运行版本解析相关 Python 单测。
- 运行 Python 全量单测。
- 执行 `git diff --check` 和 Python 编译检查。
- 启动源码快捷方式并确认不再出现黑色窗口。

## 实际改动

- 修改 `local_proxy/version.py`，Windows 下执行 `git describe` 时传入 `CREATE_NO_WINDOW`。
- 保持源码版本解析、2 秒超时和 fallback 版本行为不变。
- 修改 `tests/test_version.py`，验证 Windows 创建参数。

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest tests.test_version`：5 项通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：541 项通过。
- `.venv\\Scripts\\python.exe -m py_compile local_proxy\\version.py tests\\test_version.py`：通过。
- `git diff --check`：通过；仅有既有 CRLF 提示，无差异错误。
- 启动快捷方式进程扫描：`git.exe` 仍执行版本查询，但未再出现其启动链上的 `conhost.exe`、`OpenConsole.exe` 或 `WindowsTerminal.exe`。

## PR

pending
