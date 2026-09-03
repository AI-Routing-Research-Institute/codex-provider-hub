+++
id = "2026-09-03-usage-trend-chart-gallery"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 用量趋势多图型画廊（Lieflat Charts 风格）

## 目标

把用量趋势视图的单一样式图表升级为**多种可切换的图型**，视觉与编码规范参考开源 Skill [Lieflat Charts](https://github.com/larashero3-dotcom/lieflat-charts)（Mono 灰阶保底、诚实编码、结论式摘要、图型按数据形状选择），继续零新增依赖（手写 SVG）。

## 现状

- `UsageTrendView.vue` 只有单一图型：输入/输出堆叠面积 + 合计折线，无图型选择。
- 图表用色为彩色系，未遵循单一色系约定；汇总行为字段名式（"合计 Token"）而非结论式。
- 时间范围选择、30 秒自动刷新、悬停明细、localStorage 持久化已具备。

## 设计范围

1. 图型选择器（四选一，选择持久化到 `localStorage` `local-proxy-usage-trend-chart`）：
   - **堆叠面积**（默认，现有图形升级）：输入/输出两档灰度堆叠 + 合计深色描边；
   - **合计折线**：单线 + 窗口均值虚线参考；
   - **按桶柱状**：每桶一根零基线堆叠柱（输入浅灰 + 输出深灰），不断轴；
   - **热力矩阵**：小时粒度（today/24h）为"日期 × 24 小时"单轴矩阵，天粒度（7d/30d/all）为"星期 × 周"日历热力；值=合计 token，灰阶五档。
2. 诚实编码规范（Lieflat 约定）：所有图型 y 轴零基线、禁止断轴；编码含义写进图例/副标题；同一视图内锁定 Mono 灰阶单色系（暗色主题经 CSS 变量映射）。
3. 汇总卡第一项改为结论式（"近 24 小时合计 → N tokens"风格），标题写结论不写字段名。
4. 悬停明细四图型通用（面积/折线/柱状按桶索引竖线；热力按格 pointerenter）。
5. `styles.css` 新增 `--usage-mono-*` 灰阶变量与热力格样式（明暗两套）。

## 非目标

- 不引入 ECharts/Chart.js 等第三方库；不复制 Lieflat Charts 仓库代码（其许可证为 PolyForm Noncommercial，禁止并入本仓库再分发），仅借鉴其公开设计规范。
- 不改后端 `timeline()` 数据结构与端点；不加供应商筛选。
- 不做动画与 reduced-motion（静态 SVG，无动画即无需降级）。

## 兼容性

- 纯前端展示层变更；旧 localStorage 时间范围键沿用，新增图表类型键缺失时默认 `area`。dist 重建。

## 风险

1. 热力矩阵在 `all` 长跨度（周数多）下格子过小：限制最大列数标签抽稀、格子最小尺寸，超宽时仅标注首末周。
2. 图型切换与自动刷新竞态：切换只改渲染，不触发重新请求，数据流不变。
3. 灰阶在暗色主题对比度不足：灰阶变量在暗色主题取反方向（浅→深），图例同步。

## 测试计划

- `tests/local_proxy_vue_ui.test.js`：扩展用量趋势源断言——图型选择器四选项、持久化键、各图型渲染分支（`renderBarRects`/`heatCells`/均值参考线）、灰阶 CSS 变量与热力格样式存在。
- 全量验证：`python -m unittest discover -s tests -p "test_*.py"`、`node --test tests/*.test.js`、`npm ci` + `npm run build --prefix proxy_static`（pre-push 基线对比）。

## 实际改动

- `proxy_static/src/components/UsageTrendView.vue`：重写图表区为四图型画廊——新增图型切换按钮组（面积/折线/柱状/热力，持久化 `local-proxy-usage-trend-chart`，默认 `area`）；新增合计折线 + `meanValue` 窗口均值虚线；新增 `barRects` 零基线堆叠柱（输入/输出两档灰度）；新增 `heatLayout`/`heatCells`/`heatLevel` 热力矩阵（小时粒度=日期×24 小时、天粒度=星期×周，值=合计 token 五档灰阶，格级 `hoverCell` 悬停明细）；汇总卡第一项改结论式（`conclusionLabel` = "窗口合计 → N tokens"）；图例按图型切换并标注编码含义（"虚线=窗口均值；y 轴从 0 开始"、"零基线 · 不断轴"）。
- `proxy_static/src/styles.css`：新增 `--usage-mono-1..4`/`--usage-mono-ink`/`--usage-heat-0` 灰阶变量（明暗两套主题取反方向）；图表配色由 teal/amber 彩色系切换为 Mono 单色系；新增图型切换按钮、均值线、堆叠柱、热力格与热力图例样式。
- `tests/local_proxy_vue_ui.test.js`：新增 "usage trend view offers selectable mono chart types" 源断言测试（四图型选项与持久化键、`meanValue`/`barRects`/`heatCells`/`heatLevel`/`conclusionLabel` 渲染分支、灰阶 CSS 变量与新样式存在）。
- `proxy_static/dist/`：`npm run build` 重建（index-DqQoFmZx.js / index-ytPSDIQN.css）。
- 另安装了用户级 Skill `C:\Users\Kane\.agents\skills\lieflat-charts`（克隆自上游仓库，仅作为设计规范参考，未复制其代码，仓库内无其任何文件）。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js` → 25/25 通过（含新增 1 项）（2026-09-03 17:35）。
- 组件脚本 stub 执行几何抽查：天粒度热力 7 格分档单调（levels 4122334）、柱状堆叠底边落在零基线、24 小时热力 24 格单行且列宽越界检查通过（2026-09-03 17:36）。
- `python -m unittest discover -s tests -p "test_*.py"` → 555 项全部通过；`node --test tests/*.test.js` 全部通过；`node --check`（classic/src/provider_status）、`npm ci` + `npm run build --prefix proxy_static` 全部通过（2026-09-03 17:36）。

## PR

pending
