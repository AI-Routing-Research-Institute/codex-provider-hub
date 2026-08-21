+++
id = "2026-08-21-source-runtime-version"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复源码运行版本标识

## 目标

从 Git 工作树启动本地中转时，控制台和更新接口展示当前代码对应的最近正式版本，不再长期显示过期的开发默认版本。

## 现状

`local_proxy/application.py` 将 `APP_VERSION` 固定为 `0.1.7`。发布工作流会在打包前按标签临时改写该常量，因此便携版版本正确；直接运行最新源码时不会经过该步骤，更新接口仍把 `0.1.7` 当作当前版本并错误提示存在跨多个版本的更新。

## 设计范围

- 源码运行且仓库元数据可用时，从当前提交最近可达的严格 `v<major>.<minor>.<patch>` 标签解析版本。
- 打包运行或 Git 信息不可用、标签格式无效、命令失败时，继续使用内置版本作为安全回退。
- 保持现有纯三段 SemVer 输出，兼容在线更新比较逻辑和发布工作流的版本注入。
- 增加自动化测试覆盖标签解析、回退和打包运行边界。

## 非目标

- 不改变发布标签生成、自动发版或便携包在线更新流程。
- 不在版本号中加入提交哈希、分支名或脏工作树标记。
- 不自动重启已经运行的源码进程。

## 兼容性

无接口、配置、数据库或迁移变化。更新接口的 `current_version` 仍为三段 SemVer 字符串。选择 `patch`，因为这是源码运行场景下版本识别错误的缺陷修复。

## 风险

运行环境可能没有 Git、没有标签或不在完整仓库中；实现必须捕获这些情况并回退到内置版本，避免版本检测阻止程序启动。浅克隆只要包含最近标签即可解析，否则同样安全回退。

## 测试计划

- 新增版本解析单元测试，覆盖有效标签、无效输出、命令失败和打包环境。
- 运行 Python 全量单元测试。
- 运行 JavaScript 语法检查和 JavaScript 全量测试。
- 运行源码 `--version` 并核对更新接口相关测试。

## 实际改动

- `local_proxy/version.py`：新增源码版本解析，非打包环境从当前提交最近可达的严格 SemVer Git 标签取版本；打包环境、Git 命令失败和无效标签均回退到注入版本。
- `local_proxy/application.py`：保留发布工作流可替换的 `APP_VERSION` 常量，并在源码运行时通过统一解析函数校正版本。
- `tests/test_version.py`：覆盖源码标签解析、打包环境不调用 Git、无效标签和 Git 失败回退。

## 验证结果

- `.venv\\Scripts\\python.exe -m unittest tests.test_version tests.test_windows_release tests.test_macos_release`：12 tests passed。
- `.venv\\Scripts\\python.exe local_proxy_app.py --version`：输出 `0.13.2`。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：480 tests passed。
- `node --check proxy_static/app.js`：通过。
- `node --check provider_status/static/app.js`：通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：全部 JavaScript 测试通过。

## PR

pending
