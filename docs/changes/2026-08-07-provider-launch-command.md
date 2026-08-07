+++
id = "2026-08-07-provider-launch-command"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 复制供应商 Codex CLI 临时启动命令

## 目标

在 Codex 控制台的每个供应商条目中提供“复制临时启动命令”操作，生成一条使用该供应商地址和认证信息直接启动 Codex CLI 的单次命令，并且不修改用户现有的 `~/.codex/config.toml`。

## 现状

控制台只能复制指向本地中转的长期 Codex 配置。需要绕过本地中转、临时用某个供应商直接启动 Codex CLI 时，用户必须手工组合 URL、Key 和 Codex provider 配置，容易遗漏字段或产生 shell 转义错误。

## 设计范围

- Codex 供应商列表每行显示“复制临时启动命令”按钮，缺少凭据时禁用。
- 按当前操作系统生成 PowerShell 或 POSIX shell 命令。
- 使用固定的 `model_provider = "custom"` 和 Codex `-c key=value` 单次覆盖注入供应商名称、URL、Responses 协议、Key、静态请求头和查询参数。
- Key 只通过要求本地控制请求头且禁止缓存的按需接口返回，不进入普通状态、统计或配置接口。
- Windows 命令在 Codex CLI 退出后恢复原有临时环境变量；POSIX 命令只为本次进程设置变量。
- Claude Code 控制台不启用该功能。

## 非目标

- 不修改或追加 `~/.codex/config.toml`。
- 不自动执行复制出的命令。
- 不为 Claude Code 供应商生成启动命令。
- 不隐藏命令已包含认证信息这一事实，也不承诺终端历史不会记录用户执行的命令。

## 兼容性

新增一个仅本地控制台使用的 POST 接口和一个 UI feature flag；现有代理协议、配置文件、数据库结构及供应商选择行为不变，无迁移要求。功能依赖 Codex CLI 支持 `-c` 配置覆盖和自定义 `model_providers` 字段。语义化版本选择 `minor`，因为增加了新的用户可见操作和控制 API。

## 风险

- 复制出的命令包含 Key 或认证请求头，可能被用户保存到公共脚本或终端历史；界面和文档必须明确提示按密钥处理。
- 供应商名称、Key、请求头或查询参数可能包含 shell 特殊字符；命令生成器必须分别执行 TOML、PowerShell 和 POSIX shell 转义。
- 若接口响应被缓存或普通状态接口泄漏 Key，会扩大凭据暴露面；接口必须要求本地控制请求头并返回 `Cache-Control: no-store`，普通接口测试必须继续验证不泄漏。
- 新按钮可能挤压窄屏供应商行；必须验证桌面和移动端无横向溢出或文字重叠。

## 测试计划

- Python 单元测试覆盖 Windows/POSIX 命令、特殊字符、请求头、查询参数和缺失凭据。
- 统一控制 API 测试覆盖本地控制请求头、Codex/Claude 能力隔离、禁止缓存和普通接口不泄漏。
- Node 测试覆盖成功复制、URL 编码、请求头和后端错误提示。
- 运行完整 Python 与 Node 自动化测试，以及两份前端脚本语法检查。
- 使用真实 CC Switch 数据库启动本地服务，验证桌面、390px 窄屏布局、点击提示和生成命令结构。
- 使用已安装 Codex CLI 的 `--help` 路径验证 `-c` 参数可解析。

## 实际改动

- `local_proxy/codex.py` 增加跨平台 Codex CLI 命令生成器，固定使用 `custom` provider ID，并覆盖 provider URL、Responses 协议、Key、静态请求头、查询参数和 shell 转义。
- `local_proxy/server.py` 和 `local_proxy/codex_profile.py` 增加 Codex 专用按需启动命令接口，要求本地控制头并禁止缓存；普通公开状态继续脱敏。
- `proxy_static/app.js`、`styles.css`、`index.html` 增加按供应商复制按钮、复制反馈、禁用状态和缓存版本更新。
- `README.md`、`docs/codex-local-proxy.md` 同步复制临时启动命令的用法和 Key 风险边界。
- `tests/test_local_proxy_app.py`、`tests/test_server.py`、`tests/test_proxy_core.py` 和 `tests/local_proxy_copy_command.test.js` 覆盖命令生成、接口权限、能力隔离、缓存、剪贴板行为和资源版本。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：rebase 到最新 `origin/main` 后重新运行并通过，共 359 项测试。
- `node --check proxy_static/app.js`：通过。
- `node --check provider_status/static/app.js`：通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }`：通过，共 21 项测试。
- `git diff --check`：通过。
- 使用真实 CC Switch 数据库在 `127.0.0.1:17890` 启动服务并调用按需接口：功能开关已启用，生成命令包含 `model_provider="custom"` 与 `model_providers.custom.*`，不包含旧 provider ID；`17891` 未监听。
- 1280px 桌面和 390px 窄屏浏览器检查：22 个“复制临时启动命令”按钮均正常渲染，页面、供应商行和按钮无横向溢出。
- 将 `--help` 注入按需接口生成的内部 Codex CLI 调用：退出码为 0，帮助信息正常输出，生成命令中的 `-c` 参数可被解析。

## PR

pending
