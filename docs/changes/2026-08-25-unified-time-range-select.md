+++
id = "2026-08-25-unified-time-range-select"
type = "feature"
release_bump = "none"
status = "verified"
+++

# 统一时间范围选择框

## 目标

将供应商页和请求页的预设时间范围与自定义日期范围合并为一个相对定位的选择框。

## 现状

两个页面使用独立的下拉框和日历按钮，旧自定义时间弹层使用固定定位且精确到秒。

## 设计范围

- 使用共享 `TimeRangeSelect` 提供两列预设选项和日期范围应用区。
- 自定义范围按本地日期处理：开始日 00:00 到结束日次日 00:00。
- 弹层锚定在选择器容器下方，不改变现有接口参数和值。

## 非目标

- 不新增后端时间范围接口或时区选择。
- 不调整其他筛选项、表格布局或移动端专用布局。

## 兼容性

保留现有 `usage_window`、`window`、`start_at` 和 `end_at` 参数；预设范围行为不变。版本 bump 为 `none`，因为仅统一现有前端交互且无接口变化。

## 风险

日期解析依赖本地时区；通过使用本地 `Date(year, month, day)` 并采用半开区间保证结束日完整包含。

## 测试计划

- Vue UI 契约和日期范围转换测试。
- Node 测试、Vite 构建、语法检查和 `git diff --check`。
- 浅色/深色主题下两个页面的预设、自定义和弹层定位检查。

## 实际改动

- 新增 `TimeRangeSelect.vue` 和 `timeRange.js`，替换供应商页、请求页的独立时间控件。
- 新增选择框两列菜单、日期输入和相对定位样式。
- 增加日期边界及 UI 结构测试。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：11 项通过。
- 全量 Node 测试：16 项通过。
- 全量 Python 测试：481 项通过。
- `npm run build`：Vite 生产构建通过。
- JavaScript 语法检查：`timeRange.js`、`api.js`、`ccswitch.js` 通过。
- 浏览器实测：预设点击不会立即关闭或提交；点击“应用”后才应用预设。自定义日期按日期半开区间提交，弹层与触发按钮保持 6px 相对定位。
- “近 24 小时”实测仍使用原有 `24h` 查询参数，日期字段仅显示日期，不替代当前时刻向前 24 小时的实际范围。
- `git diff --check`：通过。

## PR

pending
