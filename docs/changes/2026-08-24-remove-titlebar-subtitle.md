+++
id = "2026-08-24-remove-titlebar-subtitle"
type = "feature"
release_bump = "none"
status = "verified"
+++

# 移除标题栏非必要副标题

## 目标

移除标题栏中不必要的说明性副标题，让控制台首屏信息更简洁。

## 现状

标题栏标题下方显示“使用本地供应商目录，切换后无需重启客户端”等说明，该信息与当前操作区域重复且占用视觉空间。

## 设计范围

- 删除标题栏副标题文本和对应计算逻辑。
- 保留标题、品牌标记、状态信息和操作按钮。

## 非目标

- 不修改其他页面说明、接口、布局或业务逻辑。
- 不做移动端专属调整。

## 兼容性

无接口、配置和数据兼容性影响。

## 风险

标题栏高度和品牌区域对齐可能需要由现有 flex 布局自然调整；通过构建和页面检查验证。

## 测试计划

- 运行 `npm run build`。
- 运行 `node --test tests/local_proxy_vue_ui.test.js`。
- 运行 `git diff --check`。

## 实际改动

已从 `Titlebar.vue` 移除标题栏副标题及其计算逻辑，并删除对应样式。

## 验证结果

已验证：`npm run build` 通过；`node --test tests/local_proxy_vue_ui.test.js` 的 5 项测试全部通过；`git diff --check` 通过。

## PR

pending
