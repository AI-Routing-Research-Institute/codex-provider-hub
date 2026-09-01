+++
id = "2026-08-31-classic-console-ui-switch-navigation"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复经典控制台界面切换

## 目标

从经典控制台保存新版界面设置后同样立即完成页面导航。

## 现状

经典界面保存成功后仍依赖延迟定时器调用导航，导致部分浏览器环境需要手动刷新。

## 设计范围

- 经典界面保存成功且界面值变化时立即设置当前入口 URL，移除 `ui` 覆盖参数。
- 保存失败或界面未变化时保持当前页面。

## 非目标

- 不修改运行设置 API、配置存储或界面选择规则。

## 兼容性

仅调整经典前端导航时机，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

仅在服务端保存成功且界面值变化时导航。

## 测试计划

- 运行经典控制台结构测试、Vue 测试、Vite 构建和差异检查。

## 实际改动

- `proxy_static/classic/app.js` 移除界面切换导航的延迟定时器，保存成功后立即使用 `window.location.href` 导航。
- `tests/local_proxy_console_ui.test.js` 增加直接导航和无延迟跳转回归断言。

## 验证结果

- `node --test tests/local_proxy_console_ui.test.js tests/local_proxy_vue_ui.test.js`：通过，18/18。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check -- proxy_static/classic/app.js tests/local_proxy_console_ui.test.js docs/changes/2026-08-31-classic-console-ui-switch-navigation.md`：通过，仅有行尾转换提示。

## PR

pending
