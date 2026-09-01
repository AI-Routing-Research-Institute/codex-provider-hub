+++
id = "2026-08-31-modern-console-ui-switch"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复新版控制台界面切换

## 目标

在新版运行设置中选择经典界面并保存后，自动刷新并进入经典界面。

## 现状

新版把用户已经修改的单选值当作保存前界面进行比较，服务端返回相同值后不会触发刷新；配置虽然已保存，当前页面仍停留在新版。

## 设计范围

- 以当前实际渲染的新版界面作为已生效界面基准。
- 保存成功后比较服务端返回值与当前界面；选择经典界面时清除 `ui` 查询覆盖并刷新页面。
- 增加前端结构测试，防止再次使用表单当前值作为保存前状态。

## 非目标

- 不修改经典界面切换逻辑。
- 不修改运行设置 API、配置格式或服务端界面选择规则。

## 兼容性

仅修正新版前端刷新判断，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

服务端保存失败时不得刷新页面；继续沿用现有错误提示。

## 测试计划

- 运行 Vue 结构测试，验证已生效界面状态的加载、比较和更新。
- 运行 Vite 构建与 `git diff --check`。

## 实际改动

- `proxy_static/src/components/RuntimeView.vue` 以当前实际渲染的 `modern` 界面作为比较基准；服务端保存结果为 `classic` 时清除 `ui` 查询参数并刷新。
- `tests/local_proxy_vue_ui.test.js` 增加比较基准和错误旧逻辑的回归断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：通过，17/17。
- `node --test tests/local_proxy_console_ui.test.js`：通过，1/1。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check -- proxy_static/src/components/RuntimeView.vue tests/local_proxy_vue_ui.test.js docs/changes/2026-08-31-modern-console-ui-switch.md`：通过；仅输出工作区行尾转换提示，无空白错误。

## PR

pending
