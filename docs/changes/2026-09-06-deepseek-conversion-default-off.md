+++
id = "2026-09-06-deepseek-conversion-default-off"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# DeepSeek 兼容转换默认关闭

## 目标

按用户要求，将 DeepSeek 兼容转换开关的默认值改为关闭。

## 现状

协议设置默认值、Codex 运行时回退值以及现代和经典界面默认开启转换。连续工具调用的思考回传兼容问题尚未解决，用户要求改为默认关闭。

## 设计范围

- 未保存该字段、首次启动或字段非法而回退默认值时关闭转换。
- 同步后端默认值、运行时适配器选择、现代界面初始值与经典界面加载行为。
- 保留用户已明确保存的 true/false 选择；同步已有测试并重新构建本地界面。

## 非目标

不修改当前运行服务或用户私有设置，不重启，不提交 Git；不实现此前讨论的原生双向工具适配方案。

## 兼容性

字段与接口不变，仅默认行为改变。显式选择继续生效；模型映射独立生效。选择 patch，因为这是现有功能默认设置的调整。

## 风险

需同时调整后端和界面，避免展示状态与实际转换状态不一致。已有配置若明确保存为 true，仍然开启，可在运行设置中手动关闭。

## 测试计划

更新现有设置测试验证默认关闭、非法值回退关闭、显式启用可持久化以及前端初始关闭。按仓库要求执行完整 Python 单测、JavaScript 语法检查与测试、npm ci、前端构建和 diff 检查。

## 结构化自审

用户明确授权修改默认值；当前功能分支与已 fetch 的 origin/main 一致；不覆盖已有选择和其他未提交工作；不需新增审批。

## 实际改动

- `local_proxy/shared_settings.py`：协议默认值为 false。
- `local_proxy/codex_profile.py`：运行设置展示与适配器选择缺失字段时均回退 false。
- `proxy_static/src/components/RuntimeView.vue`、`proxy_static/classic/app.js`、`proxy_static/classic/index.html`：现代初始值为 false；经典默认不勾选，仅显式 true 才勾选。
- 更新已有后端默认值与持久化测试、前端运行设置行为测试。

## 验证结果

- `.venv\Scripts\python.exe -m unittest tests.test_deepseek_runtime_conversion tests.test_codex_profile tests.test_shared_settings -q`：28 项通过。
- 只读确认当前用户配置尚无该字段，手动重启后会使用关闭的默认值。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：612 项全部通过，51.040 秒，日志 `%TEMP%/codex-default-off-python.log`。
- 10 个 JavaScript 文件 `node --check` 通过；对 `tests/*.test.js` 逐文件运行 `node --test`：18 文件、95 项通过。
- `npm ci --prefix proxy_static`、`npm run build --prefix proxy_static` 成功，30 个模块，生成本地 `index-BYejZO5X.js`，未暂存或提交构建产物。
- `.venv\Scripts\python.exe -m compileall -q local_proxy tests` 与 `git diff --check` 通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/94
