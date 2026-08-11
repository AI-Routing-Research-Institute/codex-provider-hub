+++
id = "2026-08-11-tray-open-console-label"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 统一托盘控制台入口样式

## 目标

将托盘首项精简为“打开控制台”，将悬浮名称统一为“模型路由服务”，并使菜单字体一致，同时保持默认打开 Codex 控制台。

## 现状

托盘首项显示“打开 Codex 控制台”且被标记为系统默认菜单项。Windows 会将默认项自动显示为粗体，因此它与下方 Claude Code 控制台入口视觉不一致。

## 设计范围

- 首项文案改为“打开控制台”。
- 托盘悬浮名称改为“模型路由服务”。
- 移除菜单项的系统默认粗体标记，使所有菜单项字体一致。
- 保留首项点击打开 Codex 控制台的行为。
- 保留托盘图标默认激活动作打开 Codex 控制台。
- 同步托盘菜单测试。

## 非目标

- 不合并或移除 Claude Code 控制台入口。
- 不调整控制台 URL、端口、开机自启、重启或退出行为。
- 不自定义绘制 Windows 原生托盘菜单。

## 兼容性

仅修改托盘菜单文案和默认样式标记，不影响配置、接口与数据。属于向后兼容界面修正，版本选择 `patch`。

## 风险

- 移除 `default=True` 后，部分平台的双击行为可能变化；通过给托盘图标保留独立默认动作覆盖。
- pystray 各平台菜单实现不同；测试同时验证菜单文字、样式标记和默认动作。

## 测试计划

- 验证首项文案为“打开控制台”且不带默认粗体标记。
- 验证首项动作和托盘默认激活动作都打开 Codex 控制台。
- 运行相关 Python 测试及完整回归验证。

## 实际改动

- `local_proxy/application.py` 将可见首项改为“打开控制台”且不设默认样式，并增加不可见的 Codex 默认动作，保证托盘默认激活仍打开 Codex 控制台。
- 托盘悬浮名称由“Codex 与 Claude Code 本地中转”改为“模型路由服务”。
- `tests/test_local_proxy_app.py` 验证可见菜单顺序、首项非粗体标记、隐藏默认动作、悬浮名称，以及两种入口均打开 Codex 控制台。

## 验证结果

- `\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：386 项通过。
- `node --test <tests/*.test.js>`：35 项通过。
- `node --check proxy_static/app.js`：通过。
- `\.venv\Scripts\python.exe -m compileall -q local_proxy tests`：通过。
- `git diff --check`：通过，仅有仓库既有 LF/CRLF 转换提示。

## PR

pending
