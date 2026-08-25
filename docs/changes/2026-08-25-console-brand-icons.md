+++
id = "2026-08-25-console-brand-icons"
type = "style"
release_bump = "none"
status = "verified"
+++

# 替换控制台文字品牌标记

## 目标

将控制台左上角的 `CX` 和 `CC` 文字标记替换为对应的 Codex/OpenAI 与 Claude Code/Anthropic 品牌图标。

## 现状

标题栏当前直接显示后端 `brand_mark` 返回的两个字母，无法直观区分 Codex 与 Claude Code 品牌。

## 设计范围

- 参考 Sub2API `PlatformIcon.vue` 的本地内嵌 SVG 方案，不引入远程资源或图标字体。
- Codex 控制台显示 OpenAI 结形品牌标志。
- Claude Code 控制台显示 Claude/Anthropic 放射品牌标志。
- 根据现有 `service_id` 选择图标，并保留 `brand_mark` 作为兼容回退依据。
- 增加品牌图标映射和标题栏使用方式的契约测试。

## 非目标

- 不修改标题、颜色、标题栏尺寸、主题、路由或后端接口。
- 不增加新的前端依赖。

## 兼容性

无接口、配置、数据和迁移影响；现有 `brand_mark` 字段继续保留。

## 风险

内嵌 SVG 可能因 viewBox 或尺寸设置出现裁切；通过浅色、深色及两个控制台的浏览器检查验证。

## 测试计划

- 运行 Vue UI 定向测试和生产构建。
- 检查 Codex 与 Claude Code 控制台分别显示正确图标且没有裁切。
- 运行 `git diff --check`。

## 实际改动

- 新增 `proxy_static/src/components/ui/BrandIcon.vue`，内嵌 Codex/OpenAI 与 Claude Code/Anthropic SVG。
- `proxy_static/src/components/Titlebar.vue` 根据 `service_id` 和 `brand_mark` 渲染品牌图标。
- `proxy_static/src/styles.css` 固定品牌图标为 24px，并在 `tests/local_proxy_vue_ui.test.js` 增加映射和尺寸契约。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：7 项通过。
- `npm run build`（`proxy_static`）：Vite 生产构建通过。
- 浏览器检查：Codex 与 Claude Code 图标在浅色和深色主题下均正确显示、无裁切。
- `git diff --check`：通过，仅输出既有换行符提示。

## PR

pending
