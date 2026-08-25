+++
id = "2026-08-25-space-runtime-update-panel"
type = "fix"
release_bump = "none"
status = "verified"
+++

# 调整运行设置更新面板间距

## 目标

让运行设置页的更新面板与上方设置表单保持 10px 的垂直间距。

## 现状

`.settings-form update-panel` 当前没有独立上边距，与上方 `.settings-form` 紧贴显示。

## 设计范围

- 仅为 `.settings-form.update-panel` 增加 `margin-top: 10px`。
- 不修改其他表单、颜色、圆角、内容或功能。

## 非目标

- 不调整运行设置页其他间距或移动端布局。

## 兼容性

无接口、配置、数据或功能影响，仅调整视觉间距。

## 风险

风险低，仅影响更新面板与上方表单的垂直布局。

## 测试计划

- 运行 Vue UI 契约测试和前端生产构建。
- 运行 `git diff --check`。

## 实际改动

- `proxy_static/src/styles.css` 为 `.settings-form.update-panel` 增加 10px 上边距。
- `tests/local_proxy_vue_ui.test.js` 增加更新面板间距契约断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：10 项通过。
- `npm run build`：Vite 生产构建通过。
- `git diff --check -- proxy_static/src/styles.css tests/local_proxy_vue_ui.test.js docs/changes/2026-08-25-space-runtime-update-panel.md`：通过。
- 17890 页面已加载本次构建生成的新 CSS 资源。

## PR

pending
