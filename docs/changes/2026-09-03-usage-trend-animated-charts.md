+++
id = "2026-09-03-usage-trend-animated-charts"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 用量趋势动态图表重构（差异化图型 + 动画 + 紧凑比例）

## 目标

按用户确认的需求重构用量趋势视图：

1. **尺寸与比例修复**：图表区紧凑固定高度（约 240–280px），热力图等比缩放居中、列少时不再被拉伸成竖构图巨图；整体留白收紧。
2. **图型差异化**：去掉同质的面积/折线/柱状三选一，换成三个不同家族的图型——时序趋势图（面积+合计线合一，动画版）、日历热力/时段矩阵、累计冲击线+大数字（G18 Draw-in + Counter 风格）。
3. **动画**：入场画入（quarticOut 快进快停）、热力格 stagger 依次出现、大数字滚动；悬停图表重播；指标轮播（tokens ↔ 请求数 ↔ 成功率，5 秒自动切换、悬停暂停、可手动切换）；`prefers-reduced-motion` 自动降级为静态且不轮播。零新增依赖（rAF + CSS）。

## 现状

- 上一版四图型中面积/折线/柱状是同一笛卡尔时序的三种填充，用户认为无意义。
- 热力图 SVG `width:100%` 拉伸：7 天窗口为 1 列×7 行竖构图，放大到面板全宽后高度爆炸（约一千多像素），是"太大、比例不对"的直接原因。
- 图表区无高度约束，视觉臃肿；无任何动画。

## 设计范围

1. `UsageTrendView.vue` 重写图表区：
   - 图型选择改为 趋势/热力/累计 三项（旧持久化值 `line`/`bars` 回退映射到 `trend`）；
   - 趋势图：指标维度驱动（METRICS：tokens=输入/输出堆叠+合计线；requests=请求面积+线；success=成功率线），路径用 `<path>` 支持 `getTotalLength` 画入；
   - 热力图：按可用宽高计算等比单元格（clamp 10–26px），SVG 显式定宽高并居中，横向布局（天粒度=周×星期，小时粒度=日×24 小时）；stagger 入场（CSS 动画 + 每格延迟，上限约 600ms）；
   - 累计冲击线：桶累计曲线一笔画入 + 右上角大数字滚动计数（quarticOut 900ms）；
   - 指标轮播：5 秒切换（悬停/页面隐藏时暂停），趋势与累计图生效，热力恒为 tokens；指标药丸可点击；
   - 动画引擎：`quarticOut` 缓动、rAF 画入、防抖的悬停重播（最小间隔 1.5 秒）、reduced-motion 全降级。
2. `styles.css`：`.usage-chart` 固定高度 260px + 居中；热力容器独立类；`.usage-metric-pills`；`@media (prefers-reduced-motion: reduce)` 下关闭 stagger/过渡。
3. 汇总卡与标题间距收紧（compact）。

## 非目标

- Bar Race（供应商排名动画）需新增按供应商时间序列端点，本轮不做。
- 不引入 ECharts/Chart.js；不改后端。

## 兼容性

- 纯前端；localStorage 图表键沿用，旧值映射兼容；30 秒自动刷新改为仅更新数据不重播入场。

## 风险

1. 动画在大量桶（如 all 窗口数百天）下性能：stagger 总时长封顶、画入用单 path 一次 rAF。
2. 轮播打扰读数：默认 5 秒、悬停即停、药丸常显当前指标；reduced-motion 不轮播。
3. 热力列数极多（all 多年）时 cell 到下限 10px：容器内横向滚动兜底。

## 测试计划

- 组件脚本 stub 数值抽查：热力 cell 等比 clamp（7 天竖构图不再放大）、累计曲线单调、指标映射覆盖三维度。
- `tests/local_proxy_vue_ui.test.js`：重写图型断言（三图型、METRICS、quarticOut、reduced-motion、轮播定时器、固定高度 CSS）。
- 全量验证：Python 单测、`node --test`、`npm ci` + `npm run build`。

## 实际改动

- `proxy_static/src/components/UsageTrendView.vue`：图表区整体重写——
  - 图型改为 趋势/热力/累计 三项（`normalizeChart` 将旧持久化值 `line`/`bars`/`area` 映射到 `trend`）；
  - 趋势图按指标维度渲染（tokens=输入/输出堆叠面积+合计线；requests=请求面积+线；success=成功率线），路径改 `<path>`；
  - 热力图 `finishHeatGeom`：按可用宽高等比计算 cell（clamp 10–26px）、SVG 显式宽高居中不再拉伸（修复 7 天窗口竖构图巨图 bug）、格子 stagger 入场（每格 12ms、总延迟上限 600ms）；
  - 累计冲击线：`cumulativeLinePath`/`cumulativeAreaPath` 桶累计曲线 + 右上角大数字滚动（`counterDisplay`）；
  - 动画引擎：`quarticOut` 缓动 + rAF 画入（DRAW_MS=900，`strokeDashoffset` 绑定）、`scheduleHoverReplay`（最小间隔 1.5s）、`prefers-reduced-motion` 全降级；
  - 指标轮播：`CAROUSEL_MS=5000` 自动切换（悬停/隐藏暂停、热力图不轮播）、指标药丸可手动切换；
  - 30 秒自动刷新只更新数据并重播入场。
- `proxy_static/src/styles.css`：`.usage-chart` 固定 260px 高并居中；`.usage-heat-svg`/`.usage-chart-heat`；`usage-heat-pop` 关键帧与 stagger；`.usage-metric-pill(.is-active)`；`.usage-cumulative-number/label`（大数字 24px Space Grotesk）；`@media (prefers-reduced-motion: reduce)` 关闭动画；清理旧 mean/bar/拉伸规则；小字号用 `calc(var(--font-meta) - 1px)` 维持全库小字号治理约束。
- `tests/local_proxy_vue_ui.test.js`：图型断言重写（三图型、METRICS、quarticOut、reduced-motion、轮播、热力 clamp、固定高度 CSS、旧图型不残留）。
- `proxy_static/dist/`：重建（index-D7SZ50xP.js / index-Cxx8pZPt.css）。

## 验证结果

- 组件脚本 stub 数值抽查（2026-09-03 18:29）：7 天热力 96×210（紧凑不再放大）、24 小时 668×54 单行、30 天 174×210；累计曲线单调递增、路径 7 点 x 递增且终点贴右边界；指标元数据 tokens/requests/success 就绪。
- `node --test tests/local_proxy_vue_ui.test.js` → 25/25 通过（含重写图型断言）；`node --test tests/*.test.js` 全部通过。
- `python -m unittest discover -s tests -p "test_*.py"` → 555 项全部通过；`npm run build --prefix proxy_static` 构建成功（2026-09-03 18:30）。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/81
