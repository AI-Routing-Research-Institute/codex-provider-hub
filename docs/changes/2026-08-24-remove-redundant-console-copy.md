+++
id = "2026-08-24-remove-redundant-console-copy"
type = "feature"
release_bump = "none"
status = "verified"
+++

# 移除控制台冗余说明文案

## 目标

删除控制台中重复且不影响操作的说明性文字，保留核心状态、数据和操作控件。

## 现状

供应商和路由侧栏包含检测同步、即时生效、转发说明及流式请求提示等辅助句子，信息密度低且与界面状态重复。

## 设计范围

- 删除红框标出的说明性文本。
- 保留刷新按钮、供应商名称与地址、恢复状态和请求状态。

## 非目标

- 不修改接口、状态计算、按钮行为或布局结构。
- 不处理移动端专属设计。

## 兼容性

无接口、配置和数据兼容性影响。

## 风险

仅减少可见文案，不改变业务流程；通过构建和 Vue 契约测试验证。

## 测试计划

- 运行 `npm run build`。
- 运行 `node --test tests/local_proxy_vue_ui.test.js`。
- 运行 `git diff --check`。

## 实际改动

已从 `ProvidersView.vue` 删除红框中的同步、即时生效、转发说明和恢复/流式提示文案，保留刷新按钮及核心状态数据。

## 验证结果

已验证：`npm run build` 通过；`node --test tests/local_proxy_vue_ui.test.js` 的 5 项测试全部通过；`git diff --check` 通过。

## PR

pending
