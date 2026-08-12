+++
id = "2026-08-12-remove-claude-tray-entry"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 精简托盘控制台入口

## 目标

从系统托盘菜单中移除“打开 Claude Code 控制台”，只保留统一的“打开控制台”入口；用户进入任一控制台后继续通过页面右上角切换 Codex 与 Claude Code 页面。

## 现状

托盘菜单同时提供“打开控制台”和“打开 Claude Code 控制台”。两个页面已经提供互相切换入口，托盘中的第二个页面入口重复占用菜单空间。

## 设计范围

- 删除托盘菜单中的“打开 Claude Code 控制台”可见项。
- 删除仅供该菜单项使用的回调和 `_run_tray` Claude URL 参数。
- 保留“打开控制台”和托盘默认激活动作打开 Codex 控制台。
- 同步托盘菜单结构与浏览器调用测试。

## 非目标

- 不删除 Claude Code 控制台页面或页面间切换入口。
- 不改变显式使用 `--open-browser` 启动时打开两个控制台的行为。
- 不调整开机自启、重启、退出或托盘图标行为。

## 兼容性

仅精简托盘可见菜单，不影响接口、配置、数据库或代理流量。属于向后兼容界面修正，版本选择 `patch`。

## 风险

- 参数删除可能遗漏调用方；通过搜索全部 `_run_tray` 调用并运行完整 Python 回归验证。
- 默认托盘激活动作可能被误删；测试继续验证隐藏默认项与可见入口均打开 Codex 控制台。

## 测试计划

- 验证可见托盘菜单不再包含“打开 Claude Code 控制台”。
- 验证“打开控制台”和默认托盘激活动作仍打开 Codex 控制台。
- 运行托盘定向测试、完整 Python 与 Node 回归、语法编译和 diff 检查。

## 实际改动

- `local_proxy/application.py` 删除托盘中的“打开 Claude Code 控制台”菜单项及专用回调，并从 `_run_tray` 参数中移除不再使用的 Claude 控制台 URL。
- `tests/test_local_proxy_app.py` 将预期可见菜单更新为“打开控制台、开机自启、检查更新、重启本地中转、退出本地中转”，并明确断言 Claude Code 菜单项不存在。
- “打开控制台”和隐藏的托盘默认激活动作继续打开 Codex 控制台；`--open-browser` 启动双页面的逻辑保持不变。

## 验证结果

- `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"`：423 项通过，包含托盘菜单回归测试。
- `node --test tests/*.test.js`：40 项通过。
- `node --check proxy_static/app.js` 与 `node --check provider_status/static/app.js`：通过。
- `.venv/Scripts/python.exe -m compileall -q local_proxy provider_status probe_codex_cc_switch.py local_proxy_app.py`：通过。
- `git diff --check`：通过。
- 搜索 `local_proxy` 与 `proxy_static`：产品代码中不再包含“打开 Claude Code 控制台”。

## PR

pending
