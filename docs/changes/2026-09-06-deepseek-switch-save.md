+++
id = "2026-09-06-deepseek-switch-save"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复 DeepSeek 转换开关保存后回退

## 目标

修复运行设置勾选 DeepSeek 兼容转换后保存自动取消的问题，保持默认关闭和显式选择可保存。

## 现状

Codex profile 声明了 deepseek_compatibility 功能，但 control/ui-config 的 UI_FEATURE_FIELDS 白名单没有该字段。只读检查运行中的接口确认功能标志缺失，运行配置为 false。经典界面保存函数仅在功能标志为 true 时发送 deepseek_compatibility_enabled，导致漏发；保存返回旧 false 后复位。现代界面也受功能字段过滤影响。

## 设计范围

- 补齐公开 UI 功能字段，确保 Codex 界面收到支持该开关的标志，Claude 仍不声明该能力。
- 核对经典界面 hidden 属性实际生效，避免显示不支持的开关。
- 添加经过真实 UI 配置接口及保存接口的验证，覆盖 false → true → false、重新读取和持久化，验证经典表单携带选中值。

## 非目标

不改为默认开启，不改变协议转换逻辑；不修改用户当前运行配置或自动重启，不提交 Git。

## 兼容性

只补充已有功能的公开标识，字段与保存接口保持兼容，选择 patch。

## 风险

功能白名单仍应过滤其他未声明的私有字段；后端发布功能标志和前端构建需同步，用户手动重启后刷新页面加载。

## 测试计划

先复现功能字段被过滤与表单漏发，再验证真实接口返回能力、保存持久化和重新读取，以及前端表单布尔值。执行完整 Python/JavaScript 测试、JS 语法、npm ci/build、编译和 diff 检查。

## 结构化自审

用户明确要求查到后修复；功能分支已同步最新 origin/main；先创建说明，保留其他未提交工作；不修改私有配置和运行服务。

## 实际改动

- `local_proxy/server.py`：UI_FEATURE_FIELDS 加入 deepseek_compatibility，Codex 公开功能标志，Claude 不声明该标志。
- `proxy_static/classic/styles.css`：补充 .setting-row[hidden] 隐藏规则，防止 display:grid 覆盖 hidden 造成不支持的开关仍可见。
- `tests/test_server.py`：真实控制台配置接口保留 DeepSeek 功能标志、过滤私有字段并隔离 Claude。
- `tests/test_deepseek_runtime_conversion.py`：真实 Codex profile、运行设置协调器与 HTTP 接口串联测试，验证关闭→开启→关闭的保存返回值、重新读取、文件持久化和实际适配器选择。
- `tests/local_proxy_console_ui.test.js`：执行经典表单取值函数，验证布尔值提交及不支持时不发送字段。

## 验证结果

- 修复前新增断言复现 UI 配置接口丢失字段：test_control_views_share_assets_and_keep_service_state_separate 报 KeyError: deepseek_compatibility；补充白名单后通过。
- `.venv\Scripts\python.exe -m unittest tests.test_server.UnifiedProxyAppTests.test_control_views_share_assets_and_keep_service_state_separate tests.test_deepseek_runtime_conversion -q`：18 项通过。
- `node --test tests/local_proxy_console_ui.test.js tests/local_proxy_vue_ui.test.js`：31 项通过。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：完整运行 613 项，132.022 秒；612 项通过，既有 test_failure_terminal_event_finishes_stalled_upstream_as_failure 出现 PreResponseClientDisconnected/CancelledError 时序错误。日志 `%TEMP%/codex-switch-save-python.log`。
- `.venv\Scripts\python.exe -m unittest tests.test_proxy_core.ProxyAppTests.test_failure_terminal_event_finishes_stalled_upstream_as_failure -q`：单独复跑通过，1.186 秒，日志 `%TEMP%/codex-switch-save-rerun.log`。按仓库偶发失败复跑规则通过验证，不声称完整首次运行全部通过。
- 对 `tests/*.test.js` 逐文件执行 `node --test`：18 个文件、96 项全部通过；现有 9 个源文件加生成 JS 共 10 个 `node --check` 通过。
- `npm ci --prefix proxy_static`、`npm run build --prefix proxy_static` 成功，30 个模块；`.venv\Scripts\python.exe -m compileall -q local_proxy tests` 与 `git diff --check` 通过。
- 保持默认 false，未暂存、提交、修改用户私有配置或重启服务。用户需要手动重启本地中转，再刷新页面读取补齐后的功能配置。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/94
