+++
id = "2026-08-31-console-ui-switch-immediate-navigation"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复控制台界面保存后刷新

## 目标

保存新版运行设置中的控制台界面后立即进入已选择的界面，不要求用户手动刷新网页。

## 现状

界面切换判断已修正，但导航仍通过延迟定时器触发；定时器未执行或页面状态变化时，用户会看到保存成功而页面不切换。

## 设计范围

- 保存成功且界面值发生变化时，立即导航到移除 `ui` 查询参数的当前控制台入口。
- 保存失败或界面值未变化时保持现有页面和提示。

## 非目标

- 不修改运行设置 API、后端配置存储或经典界面逻辑。

## 兼容性

仅调整新版前端导航时机，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

立即导航会结束当前 Vue 页面，但仅发生在服务端确认保存成功且界面确实变化时。

## 测试计划

- 验证保存成功分支直接调用导航，不依赖 `setTimeout`。
- 运行 Vue 结构测试、控制台切换测试、Vite 构建和差异检查。

## 实际改动

- `proxy_static/src/components/RuntimeView.vue` 移除界面切换导航的延迟定时器，保存成功后立即清除 `ui` 查询参数并导航。
- `tests/local_proxy_vue_ui.test.js` 增加不依赖延迟定时器的回归断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js`：通过，18/18。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check -- proxy_static/src/components/RuntimeView.vue tests/local_proxy_vue_ui.test.js docs/changes/2026-08-31-console-ui-switch-immediate-navigation.md`：通过，仅有行尾转换提示。

## PR

pending
