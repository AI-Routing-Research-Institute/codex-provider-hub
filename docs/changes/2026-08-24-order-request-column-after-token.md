+++
id = "2026-08-24-order-request-column-after-token"
type = "feature"
release_bump = "none"
status = "verified"
+++

# 调整供应商统计列顺序

## 目标

将供应商表格的“请求次数”列放到“Token 用量”列之后。

## 现状

请求次数列位于 Token 用量之前，不符合当前数据阅读顺序。

## 设计范围

- 同步调整表头、供应商行单元格和桌面网格列定义。
- 保持统计值和交互行为不变。

## 非目标

- 不修改接口、统计计算、移动端布局或其他页面。

## 兼容性

无接口、配置和数据兼容性影响。

## 风险

仅改变列顺序；通过构建和 Vue 契约测试验证。

## 测试计划

- 运行 `npm run build`。
- 运行 `node --test tests/local_proxy_vue_ui.test.js`。
- 运行 `git diff --check`。

## 实际改动

已将 `ProvidersView.vue` 的 Token 用量单元格放在请求次数单元格之前，并同步 `--provider-grid-columns` 与表头对齐规则。

## 验证结果

已验证：`npm run build` 通过；`node --test tests/local_proxy_vue_ui.test.js` 的 5 项测试全部通过；`git diff --check` 通过。

## PR

pending
