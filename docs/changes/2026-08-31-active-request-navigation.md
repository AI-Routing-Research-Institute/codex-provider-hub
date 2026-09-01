+++
id = "2026-08-31-active-request-navigation"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 活动请求按钮跳转

## 目标

点击新版供应商列表中的活动请求数量后，切换到请求页面并自动筛选被点击供应商的请求。

## 现状

新版活动请求数量仅显示为不可交互文本，无法像经典界面一样进入请求记录；请求页已有供应商筛选接口，但没有接收供应商页的导航上下文。

## 设计范围

- 活动请求按钮在非管理模式下可点击，并阻止事件冒泡。
- 供应商组件向应用层发出目标供应商 ID。
- 应用层切换到请求页并将目标供应商筛选传递给请求组件。
- 请求组件应用筛选、清理搜索词和状态筛选后重新加载，保留当前时间范围。

## 非目标

- 不修改后端 API 或请求数据结构。
- 不改变经典界面的行为。
- 不改变供应商切换、Token 详情或活动会话提示逻辑。

## 兼容性

复用现有 `/api/requests?provider_id=...` 查询参数，无配置、数据库或迁移影响，选择 `patch` 版本提升。

## 风险

事件冒泡可能误触发供应商切换；通过 `.stop` 和结构测试防护。请求组件若未正确应用目标筛选，可能展示错误供应商记录；通过事件链测试和实际请求参数检查验证。

## 测试计划

- Vue 结构测试覆盖按钮点击、事件传递、视图切换和筛选应用。
- 运行 JavaScript 测试、Vite 构建和 `git diff --check`。
- 浏览器检查点击活动按钮后请求页显示对应供应商筛选且无横向溢出。

## 实际改动

- `proxy_static/src/components/ProvidersView.vue` 将活动请求数量改为可点击按钮，管理模式禁用并通过 `navigate-requests` 事件传出供应商 ID。
- `proxy_static/src/App.vue` 接收导航事件，切换请求页并向请求组件传递目标供应商筛选。
- `proxy_static/src/components/RequestsView.vue` 接收导航供应商 ID，清理搜索词和状态筛选后按供应商重新加载请求记录。
- `tests/local_proxy_vue_ui.test.js` 增加活动请求按钮、事件链和筛选重置回归断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js`：通过，18/18。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check`：通过，仅有行尾转换提示。

## PR

pending
