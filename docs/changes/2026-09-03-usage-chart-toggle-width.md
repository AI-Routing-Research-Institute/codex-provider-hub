+++
id = "2026-09-03-usage-chart-toggle-width"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 用量趋势图表类型按钮加宽防换行

## 目标

用量趋势视图右上角"趋势/热力/累计"图表类型切换按钮在空间不足时文字被压成两行（"趋/势"），视觉拥挤；加宽按钮并禁止换行。

## 现状

`.usage-actions` 为弹性布局，时间范围选择器较宽时图表切换组被压缩，按钮文字逐字换行。

## 设计范围

- `.usage-chart-toggle` 增加 `flex-shrink: 0`（整组不参与压缩）。
- `.usage-chart-toggle-button` 增加 `min-width: 48px; white-space: nowrap`，内边距 12→14px。

## 非目标

- 不改图型逻辑与数据；窄屏下若整体放不下由既有换行行为兜底。

## 兼容性

- 纯 CSS；dist 重建。

## 风险

- 极窄屏（<380px）下工具行可能溢出：按钮 nowrap 后整组约 160px，加上时间范围选择器仍在常见手机宽度内。

## 测试计划

- `tests/local_proxy_vue_ui.test.js` 增加 nowrap/min-width 断言；`npm run build` 后浏览器截图确认单行显示。

## 实际改动

- `proxy_static/src/styles.css`：`.usage-chart-toggle` 增加 `flex-shrink: 0`；`.usage-chart-toggle-button` 增加 `min-width: 48px; white-space: nowrap`，内边距 12→14px。
- `tests/local_proxy_vue_ui.test.js`：新增 "usage trend chart toggle stays on one line" 断言。
- `proxy_static/dist/`：随本轮重建。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js` → 26/26 通过（含新增断言）（2026-09-03 19:22）。
- `node --test tests/*.test.js` 全部通过；`python -m unittest discover -s tests -p "test_*.py"` → 555 项全部通过（2026-09-03 19:27）。

## PR

pending
