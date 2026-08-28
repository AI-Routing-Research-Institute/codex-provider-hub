+++
id = "2026-08-28-toggle-status-upload-visibility"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 上传检测按钮可见性设置

## 目标

在运行设置中增加“供应商上传检测”开关，允许用户像控制供应商临时启动命令一样，选择是否在供应商卡片中显示“上传检测”按钮。

## 现状

供应商临时启动命令已有共享可见性设置，运行设置页面可以即时控制供应商卡片按钮；上传检测按钮只根据供应商和服务端是否支持上传决定是否渲染，无法由用户隐藏，导致不使用状态上传功能时供应商名称区域仍被按钮占用。

## 设计范围

- 新增协议级布尔设置 `show_status_upload`，默认值为 `true`，保持升级前界面行为。
- Codex 与 Claude 各自的运行设置快照和保存接口读取、校验并持久化该字段，与现有临时启动命令偏好保持相同作用域。
- 运行设置页在服务支持状态上传时显示“供应商上传检测”开关，保存后立即更新当前页面。
- 供应商列表同时满足服务支持、供应商支持和用户开关开启时才显示“上传检测”按钮；隐藏按钮不关闭上传接口、监控任务或服务器检测状态。

## 非目标

- 不删除或禁用状态上传后端接口、SSH 初始化、监控管理和已上传配置。
- 不改变供应商健康状态、自动检测、上传内容或上传权限判断。
- 不调整供应商卡片其他字段和布局。

## 兼容性

旧配置缺少 `show_status_upload` 时自动使用 `true`，无需迁移；运行设置接口增加可选布尔字段，旧前端和旧配置仍可工作。该改动提供新的用户可配置能力且向后兼容，版本选择 `minor`。

## 风险

若能力字段与用户偏好混用，可能误隐藏整个检测状态或在不支持的协议中显示无效开关；实现中将功能能力与可见性偏好分开，并用组件和共享设置测试覆盖。

## 测试计划

- 验证默认配置显示上传检测按钮，新设置默认开启并可持久化关闭。
- 验证运行设置 API 接受布尔值、拒绝非布尔值，并在 Codex 与 Claude 快照间同步。
- 验证运行设置页只在支持状态上传时显示开关，保存后向供应商列表发出即时可见性更新。
- 验证供应商按钮同时受服务能力、供应商能力和用户设置控制。
- 运行完整 Python 单测、JavaScript 语法检查、JavaScript 测试、Python 编译与差异检查。

## 实际改动

- `local_proxy/shared_settings.py` 为协议设置新增默认开启的 `show_status_upload` 字段，并在加载与保存时只接受布尔值。
- `local_proxy/codex_profile.py` 和 `local_proxy/claude_profile.py` 将上传检测能力加入 UI 配置，在各自运行设置快照中返回按钮偏好，并校验、持久化当前协议的修改。
- `local_proxy/server.py` 将 `status_upload` 加入公开 UI 功能白名单，未知功能字段仍不会透传给前端。
- `proxy_static/src/App.vue`、`RuntimeView.vue` 和 `ProvidersView.vue` 增加上传检测可见性状态、运行设置开关和即时事件同步；供应商卡片按钮同时受服务能力、用户偏好、供应商上传能力及管理模式约束。
- 重新生成本地生产静态资源用于手动测试；生成产物保持忽略状态，不作为后续 Git 交付内容。
- Python 与 JavaScript 测试新增默认值、协议独立持久化、错误类型拒绝、UI 功能白名单和组件显隐链路覆盖。

## 验证结果

- `\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：rebase 到最新 `origin/main` 后通过，502 项测试，耗时 72.547 秒。
- 对 `proxy_static/src/*.js` 和 `provider_status/static/app.js` 执行 `node --check`：通过。
- 对 `tests/*.test.js` 逐个执行 `node --test`：通过，18 项测试。
- `npm run build --prefix proxy_static`：通过，Vite 转换 26 个模块，生产包包含 `show_status_upload` 设置；构建产物仅用于验证，未纳入提交。
- `\.venv\Scripts\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- `git diff --check`：通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/52
