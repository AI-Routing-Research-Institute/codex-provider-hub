+++
id = "2026-08-24-split-token-and-request-columns"
type = "feature"
release_bump = "none"
status = "verified"
+++

# 拆分供应商 Token 与请求次数列

## 目标

让供应商表格中的 Token 用量和请求次数分别占用独立列，提升数据可读性。

## 现状

Token 用量单元格同时显示 Token 总量和请求次数，导致两种统计混在同一列。

## 设计范围

- 请求列显示供应商请求次数，并保留活动请求提示。
- Token 用量列只显示 Token 总量及已有详情入口。

## 非目标

- 不修改统计接口、数据计算、筛选或详情弹窗行为。

## 兼容性

无接口、配置和数据兼容性影响。

## 风险

仅改变表格展示；通过构建和 Vue 契约测试验证。

## 测试计划

- 运行 `npm run build`。
- 运行 `node --test tests/local_proxy_vue_ui.test.js`。
- 运行 `git diff --check`。

## 实际改动

已在 `ProvidersView.vue` 将请求次数移入“请求次数”列，并让 Token 用量列只显示 Token 总量和详情入口。

## 验证结果

已验证：`npm run build` 通过；`node --test tests/local_proxy_vue_ui.test.js` 的 5 项测试全部通过；`git diff --check` 通过。

## PR

pending
