+++
id = "2026-08-24-align-health-refresh-with-provider-count"
type = "feature"
release_bump = "none"
status = "verified"
+++

# 对齐供应商检测刷新按钮

## 目标

将服务器检测刷新按钮放到供应商数量说明行末尾，减少标题区域的垂直占用。

## 现状

刷新按钮单独占据一行，与“已显示 N 个供应商”说明分离。

## 设计范围

- 将刷新按钮放入供应商数量说明段落。
- 保留按钮的刷新行为、标题和无障碍标签。

## 非目标

- 不修改供应商数据、接口、刷新逻辑或其他页面布局。

## 兼容性

无接口、配置和数据兼容性影响。

## 风险

仅改变文案与按钮的布局位置；通过构建和 Vue 契约测试验证。

## 测试计划

- 运行 `npm run build`。
- 运行 `node --test tests/local_proxy_vue_ui.test.js`。
- 运行 `git diff --check`。

## 实际改动

已将服务器检测刷新按钮移入 `ProvidersView.vue` 的供应商数量说明行，并删除原独立检测状态行及其无用样式。

## 验证结果

已验证：`npm run build` 通过；`node --test tests/local_proxy_vue_ui.test.js` 的 5 项测试全部通过；`git diff --check` 通过。

## PR

pending
