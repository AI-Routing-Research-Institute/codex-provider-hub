+++
id = "2026-08-24-sub2api-color-and-type-scale"
type = "feature"
release_bump = "none"
status = "verified"
+++

# 应用 Sub2API 色板与字号层级

## 目标

将控制台的颜色和主要文字层级调整为接近 sub2api-frontend 的暖色设计体系，提升桌面端可读性。

## 现状

当前主题使用冷灰蓝背景和青绿色主色，表格、辅助标签和按钮字号偏小，视觉密度高。

## 设计范围

- 将浅色主题映射到 sub2api 的暖白、米色、陶土橙、炭黑和柔和状态色。
- 将深色主题调整为暖炭黑背景与陶土橙主色，保留现有主题切换。
- 提高标题、标签、表格、按钮、下拉选项和状态文字的字号层级。

## 非目标

- 不修改业务逻辑、接口、信息架构或图标组件。
- 不引入远程字体或 CDN 资源，不新增移动端专属布局。

## 兼容性

无接口、配置和数据兼容性影响；仅改变视觉主题和字号。

## 风险

字号增加可能影响局部横向空间；通过构建、契约测试和桌面页面检查验证。

## 测试计划

- 运行 `npm run build`。
- 运行 `node --test tests/local_proxy_vue_ui.test.js`。
- 运行 `git diff --check`。

## 实际改动

已在 `styles.css` 将浅色和深色主题变量映射到 sub2api 的暖白/米色/陶土橙色板，并提高主要标题、标签、表格、按钮、下拉和状态文字字号。

## 验证结果

已验证：`npm run build` 通过；`node --test tests/local_proxy_vue_ui.test.js` 的 5 项测试全部通过；`git diff --check` 通过；浏览器检查确认深色主题使用暖炭色背景、陶土橙主色，body 字号为 14px、页面标题为 17px、标签页为 13px。

## PR

pending
