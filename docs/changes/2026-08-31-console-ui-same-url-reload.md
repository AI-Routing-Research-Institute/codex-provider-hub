+++
id = "2026-08-31-console-ui-same-url-reload"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复控制台切换同地址刷新

## 目标

在当前 URL 没有 `ui` 覆盖参数时，保存界面切换仍能触发页面重新加载。

## 现状

保存成功后目标入口 URL 可能与当前 URL完全相同，直接赋值 `window.location.href` 不一定产生导航，导致用户必须手动刷新。

## 设计范围

- 比较当前 URL 与清除 `ui` 后的目标 URL。
- 地址相同时调用 `window.location.reload()`，地址不同时设置 `window.location.href`。

## 非目标

- 不修改后端接口或界面配置存储。

## 兼容性

仅调整两套前端的保存后刷新行为，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

仅在保存成功且目标界面不同于当前实际界面时触发刷新。

## 测试计划

- Vue 与经典控制台结构测试覆盖同地址 reload 和不同地址导航分支。
- 运行 Vite 构建和差异检查。

## 实际改动

- `proxy_static/src/components/RuntimeView.vue` 与 `proxy_static/classic/app.js` 在目标 URL 与当前 URL相同时调用 `reload()`，否则直接导航。
- 对应控制台结构测试增加同地址刷新断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js`：通过，18/18。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check -- proxy_static/src/components/RuntimeView.vue proxy_static/classic/app.js tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js docs/changes/2026-08-31-console-ui-same-url-reload.md`：通过，仅有行尾转换提示。

## PR

pending
