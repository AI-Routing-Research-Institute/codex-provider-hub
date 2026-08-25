+++
id = "2026-08-25-import-codex-proxy-to-ccswitch"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 将本地中转导入 CC Switch

## 目标

将 Codex 和 Claude Code 控制台页脚的复制配置入口替换为“导入到 CCS”，通过 CC Switch 深链直接注册对应的本地中转供应商。

## 现状

用户需要复制 TOML 片段并手工合并到 Codex 配置；Sub2API 已使用 `ccswitch://v1/import` 协议将服务地址和密钥直接导入 CC Switch。

## 设计范围

- 两个控制台页脚均显示“导入到 CCS”，点击后打开 `ccswitch://v1/import` 深链。
- Codex 导入资源的应用为 `codex`，endpoint 使用当前本地中转 `/v1` 地址，默认模型设置为 `gpt-5.6-sol`。
- Claude Code 导入资源的应用为 `claude`，endpoint 使用不含 `/v1` 的本地中转地址，不强制设置默认模型。
- 两种导入均使用各自的非敏感本地占位认证值。
- CC Switch 未安装或协议未注册时显示错误提示。

## 非目标

- 不修改供应商管理页从 CC Switch 导入本地目录的现有功能。
- 不向深链增加本项目不支持的余额查询脚本。
- 不删除后端 Codex 配置片段接口。

## 兼容性

两个控制台的页脚入口均由手工复制配置改为导入 CC Switch；控制接口和现有配置文件保持不变。该功能需要本机安装并注册 CC Switch 协议处理程序。

## 风险

未安装 CC Switch 时自定义协议无法打开；通过与 Sub2API 一致的焦点检测显示提示。用户若将导入到 CC Switch 的本地中转供应商再次反向导入本项目并选中，可能形成自循环，因此文档明确禁止该操作。

## 测试计划

- 增加深链参数单元测试和 Vue 模板行为契约测试。
- 运行前端生产构建、定向 Node 测试和 `git diff --check`。
- 浏览器检查 Codex 和 Claude Code 页的按钮、深链参数与错误提示。

## 实际改动

- 新增 `proxy_static/src/ccswitch.js`，按服务类型生成 CC Switch 供应商导入深链；Codex 默认模型为 `gpt-5.6-sol`，Claude 不写模型参数。
- `proxy_static/src/App.vue` 将两个控制台的页脚配置操作改为打开导入深链，并在协议处理程序不可用时显示错误通知。
- 页脚导入入口使用与“保存设置”一致的主题色主按钮，并通过 `UiIcon` 显示上传图标。
- 页脚“退出中转”按钮同步为 34px 高和 13px 字号，与导入按钮保持相同控件尺寸。
- `local_proxy/codex_profile.py`、`local_proxy/claude_profile.py` 和兼容 profile 配置统一返回“导入到 CCS”文案及端口变更提示；原配置片段接口继续保留。
- `tests/local_proxy_vue_ui.test.js` 增加两种深链的参数测试，profile 测试增加按钮文案断言。
- 更新中英文 README 和本地中转文档，说明两种导入流程、等价配置和自循环保护。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：9 项通过。
- `.\.venv\Scripts\python.exe -m unittest tests.test_codex_profile tests.test_claude_profile tests.test_local_proxy_app tests.test_project_documentation tests.test_server.UnifiedProxyAppTests.test_control_views_share_assets_and_keep_service_state_separate tests.test_claude.ClaudeProxyAppTests.test_control_assets_and_claude_config_endpoint`：38 项通过。
- `npm run build`（`proxy_static`）：Vite 生产构建通过，24 个模块转换成功。
- `git diff --check`：通过，仅输出既有换行符提示。
- 重启本地中转后浏览器检查：Codex 与 Claude Code 页脚均显示带上传图标的主题色“导入到 CCS”按钮；浅色和深色主题下均为白字、无横向溢出；未实际触发外部协议写入。

## PR

pending
