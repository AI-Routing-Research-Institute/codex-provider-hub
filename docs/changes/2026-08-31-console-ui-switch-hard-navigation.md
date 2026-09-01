+++
id = "2026-08-31-console-ui-switch-hard-navigation"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复控制台切换强制导航

## 目标

确保新版保存控制台界面后立即完成页面导航，无需手动刷新。

## 现状

保存成功分支已移除延迟，但使用 `location.replace()` 在部分运行环境中未触发可见导航。

## 设计范围

- 保存成功且目标界面不同于当前界面时，直接设置 `window.location.href` 导航到移除 `ui` 覆盖参数的入口。
- 保存失败或目标界面未变化时不导航。

## 非目标

- 不修改后端接口、界面配置存储或经典界面逻辑。

## 兼容性

仅调整新版前端导航 API，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

导航只发生在服务端确认保存成功且界面值变化时；普通运行设置保存不受影响。

## 测试计划

- 验证切换分支使用 `window.location.href`。
- 运行 Vue 测试、控制台切换测试、Vite 构建和差异检查。

## 实际改动

- `proxy_static/src/components/RuntimeView.vue` 使用 `window.location.href` 执行界面切换导航。
- `tests/local_proxy_vue_ui.test.js` 验证新版切换分支使用直接导航。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js`：通过，18/18。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check -- proxy_static/src/components/RuntimeView.vue tests/local_proxy_vue_ui.test.js docs/changes/2026-08-31-console-ui-switch-hard-navigation.md`：通过，仅有行尾转换提示。

## PR

pending
