+++
id = "2026-08-24-vue3-migration"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 前端迁移到 Vue 3 + Vite 架构

## 目标

将本地中转控制台迁移到 Vue 3 + Vite，并在不改变原版信息架构、视觉层级、控件、状态反馈与操作能力的前提下完整复刻原生 JavaScript 控制台。Codex 与 Claude 控制台均从统一后端路由加载构建产物和各自服务数据，本次以桌面端原版体验为验收基准。

## 现状

当前分支已提交 Vue 组件和 Vite 配置，但构建产物不可用：后端默认资源目录已指向 `proxy_static/dist`，路由实现仍重复拼接 `dist`；静态路由不接受 `assets/...` 嵌套路径；Vite base 固定为 Codex 路径，Claude 控制台会引用错误资源；入口样式为空；组件请求的 URL 与统一控制台 `/control/<service>/api/*` 契约不一致。现有说明提前标记为 `verified`，与实际状态不符。

## 设计范围

- 使用相对静态资源 base，让同一构建产物可从 `/control/codex/` 与 `/control/claude/` 加载。
- 修正统一后端对 `dist/index.html` 和 `dist/assets/*` 的安全读取，并保留缓存禁用行为。
- 建立前端控制台基址与请求辅助函数，统一调用当前服务的 `/api/*` 接口并带本机控制请求头。
- 按原版 DOM 与 CSS 契约复刻标题栏、连接条、五个页签、供应商双栏工作区、请求表、两组设置表单、监控管理、页脚、弹窗、浮层和通知。
- 让五个 Vue 页面读取真实 API 数据，恢复旧版供应商健康、Token 用量、活动会话、供应商目录管理、请求筛选、重试设置、运行设置与监控管理能力。
- 补齐主题、连接状态、加载、空数据和错误反馈，清理组件计时器。
- 完整复刻原版桌面窗口布局；保留原版 CSS 已有的响应式规则，但不额外设计或验收移动端适配。
- 增加构建产物路由及关键前端契约的自动化回归测试。

## 非目标

- 不改变现有后端 API 的请求或响应结构。
- 不引入新的图标库、CSS 框架或远程静态资源。
- 不改变远程供应商状态页和桌面托盘界面。
- 不新增或重做移动端适配。

## 兼容性

后端 API、配置和数据结构无变化。静态资源 URL 从固定 Codex 绝对路径改为相对当前控制台路径，兼容 Codex 与 Claude 两个入口；旧的单服务 `/control/` 路由继续使用原有静态文件处理。该迁移新增用户可见的 Vue 控制台，版本选择 `minor`。

## 风险

- 同一静态产物服务于两个控制台时可能串用 API；通过从 `location.pathname` 解析服务基址并增加双入口测试缓解。
- Vite 哈希资源位于子目录，路径处理不当可能返回 404 或越界读取；使用 Starlette `:path` 参数、规范化路径和目录边界检查。
- API 响应字段与简化组件预期不同可能造成空白或运行时错误；按现有测试与服务响应归一化，并覆盖空字段。
- 原版包含较多弹窗、浮层和管理操作，迁移时遗漏会形成行为回退；以 `origin/main` 的 `index.html`、`app.js`、`styles.css` 为逐项验收基准并增加契约测试。
- 响应式表格和长 URL 可能造成横向溢出；对固定格式区域设置滚动容器，对长文本设置换行或截断。

## 测试计划

- 运行 `npm run build`，确认 CSS 非空且 HTML 使用相对的 `./static/assets/` 资源。
- 运行统一服务静态资源、供应商目录和请求历史的针对性 Python 测试。
- 运行 Python 全量单元测试、前端 JavaScript 语法检查、Node 测试、`compileall` 与 `git diff --check`。
- 启动本地服务，在 Codex 与 Claude 入口检查页面和资源均为 200。
- 在桌面视口检查五个页签、浅色/深色主题、原版布局一致性、无横向页面溢出及无控制台错误。

## 实际改动

本轮完成 Vue 控制台可运行性和核心 UI 迁移：

- `proxy_static/vite.config.js` 使用相对生产资源路径并将哈希资源输出到 `dist/static/assets/`。
- `local_proxy/core.py`、`local_proxy/server.py` 统一安全读取 `dist/index.html` 与嵌套静态资源，拒绝目录穿越并保留 `no-store`。
- `proxy_static/src/api.js` 根据 Codex/Claude 当前入口生成 API 基址，统一本机控制请求头、JSON 序列化和错误处理。
- `proxy_static/src/App.vue` 及标题栏、连接条、页签组件读取 `ui-config`，保存页签和主题偏好，管理连接轮询。
- `proxy_static/src/components/ProvidersView.vue` 对接真实状态和 provider catalog API，支持供应商搜索、切换、增删改、JSON 校验和错误反馈。
- `proxy_static/src/components/RequestsView.vue`、`RuntimeView.vue`、`MonitorView.vue` 使用后端真实字段展示请求、运行参数、用量和错误，并清理自动刷新计时器。
- `proxy_static/src/components/SettingsView.vue` 提供主题、刷新间隔、端口、数据库和健康地址设置。
- `proxy_static/src/styles.css` 提供全视口桌面布局、900/680/440px 响应式断点、暗色主题、表格滚动和弹窗布局。
- `tests/test_proxy_core.py`、`tests/test_server.py`、`tests/test_claude.py` 更新为哈希静态资源和 Vue 构建契约断言；`tests/local_proxy_vue_ui.test.js` 覆盖 API helper、服务基址、轮询清理和响应式 CSS。
- 删除不再引用的旧 `ProviderCard`、`useProviders` 和 Tailwind 入口文件，避免与新页面并存的错误实现。

## 验证结果

- `python scripts/team_policy.py install-hooks`：通过。
- `git fetch origin main`：通过，`origin/main` 为 `8c45d09`，当前功能分支未落后主线。
- `npm run build`（`proxy_static`）：通过，生成 `dist/index.html`、64.59 KB CSS 和 130.66 KB JS，资源引用为 `./static/assets/...`。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：486 项全部通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：10 项 Node 测试全部通过（Vue UI 5 项，主题 5 项）。
- `node --check proxy_static/src/api.js`、`node --check proxy_static/vite.config.js`、`node --check provider_status/static/app.js`：通过。
- `.venv\\Scripts\\python.exe -m compileall -q local_proxy tests`：通过。
- `git diff --check`：通过，仅有 Windows LF/CRLF 转换提示。
- 标准端口 `17890`：Codex 与 Claude 页面和哈希 JS/CSS 均为 200，`/healthz` 返回两个服务 `ok`。
- 浏览器验证：1440x900、1024x768、768x1024、390x844 均无横向溢出；桌面双栏/平板单栏符合断点；五个页签、主题切换、供应商编辑弹窗无控制台错误。

## PR

pending
