+++
id = "2026-08-24-vue-main-feature-compatibility"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# Vue 控制台兼容主线代理功能

## 目标

保留当前 Vue 3 + Vite 控制台作为唯一前端，同时兼容主线新增的上游超时、流式卡顿保护、请求阶段和运行时诊断功能。

## 现状

当前功能分支已将本地控制台迁移到 Vue 3 + Vite；主线随后在后端和原生控制台中增加了上游响应超时、流空闲超时、事件循环诊断、请求阶段字段和控制请求超时。直接合并会在原生前端入口、静态资源和相关测试处冲突。

## 设计范围

- 以当前 Vue/Vite 源码、入口、构建资源和 UI 样式覆盖主线原生前端。
- 将主线后端超时、流式收尾、请求阶段、事件循环诊断和异步化处理移植到当前架构。
- 将控制请求 8 秒超时、请求阶段文案和新增重试类型接入 Vue 请求页面。
- 将主线新增后端测试适配到当前 Vue 静态资源测试体系，并保留已有永久变更说明。

## 非目标

- 不恢复或继续维护主线原生 `proxy_static/app.js` UI。
- 不改变当前 Vue 控制台的主题、下拉框、图标、布局、字号和统计列设计。
- 不修改供应商配置、凭据、会话路由、用户重试策略或远程状态服务。
- 不推送远端、不创建标签、不在本次工作中合并主分支。

## 兼容性

新增后端字段仅扩展健康和控制接口，旧客户端可以忽略；现有请求数据库无需迁移。最终分支将 rebase 到最新 `origin/main`，确保 PR 合并时无未解决冲突。版本 bump 选择 `patch`，因为这是对主线代理稳定性的兼容性修复。

## 风险

- 主线原生前端与当前 Vue 入口结构差异较大，直接覆盖可能丢失后端功能；通过逐项移植和接口测试缓解。
- 长时间无输出的上游可能被误判；沿用主线 120 秒按块空闲阈值，并验证首包前重试、输出后不重放。
- rebase 可能再次引入静态资源冲突；保留 Vue 入口并检查完整 diff，禁止恢复原生 UI。

## 测试计划

- 运行上游响应头超时、流空闲超时、请求阶段、诊断和控制请求超时相关 Python/Node 测试。
- 运行 `npm run build`、JavaScript 语法检查、Python 全量单测、Node 全量测试、`compileall` 和 `git diff --check`。
- rebase 到最新主线后重新执行完整验证，并检查 Vue 控制台资源和请求状态文案。

## 实际改动

- rebase 到 `origin/main` 并保留当前 Vue/Vite 入口、组件、主题样式和构建资源；继续删除主线原生 `proxy_static/app.js` 及其旧测试。
- `proxy_static/src/api.js`：所有控制请求增加 8 秒 AbortController 超时，并统一显示本地中转响应超时文案。
- `proxy_static/src/components/RequestsView.vue`：显示连接上游、等待首包、接收中、重试次数，以及响应头/流空闲超时错误文案。
- `tests/local_proxy_vue_ui.test.js`：覆盖控制请求超时行为和 Vue 请求阶段/错误文案。
- 主线的 `local_proxy/core.py`、`local_proxy/server.py` 超时、流式收尾、请求阶段和运行时诊断实现随 rebase 保留。

## 验证结果

验证结果：

- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：486 项通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：12 项通过。
- `npm run build`（`proxy_static`）：Vite 构建通过。
- `.venv\Scripts\python.exe -m compileall -q local_proxy provider_status tests`：通过。
- `node --check proxy_static/src/api.js`、`node --check proxy_static/vite.config.js`、`node --check provider_status/static/app.js`：通过。
- `git diff --check`：通过，仅有 Git 的 LF/CRLF 转换提示。

## PR

pending
