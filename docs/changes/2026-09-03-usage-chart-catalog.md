+++
id = "2026-09-03-usage-chart-catalog"
type = "feature"
release_bump = "patch"
status = "verified"
+++

# 用量趋势图型目录扩展（Lieflat Charts 风格六图型）

## 目标

按用户选择把用量趋势扩展为六种差异化图型（同一数据多种回答），视觉规范参考 Lieflat Charts（手写 SVG 复刻，不复制其 PolyForm NC 代码）：

1. **趋势**（保留，动画画入）
2. **日历**（重做热力）：今天/24h 保留"日×24 小时"方格；≥7 天改为 GitHub 风格"周×星期"圆点日历（L17 Calendar Heat：点面积=当日 tokens、最忙日虚线环标注、月份刻度）
3. **累计**（保留，冲击线+滚动大数字）
4. **节律**（新增，G14 Punch Card）：星期×24 小时点阵，点半径=消耗量，看工作日/周末节律
5. **构成**（新增，G4 Dot Waffle）：100 枚圆点（1 点≈1%）按供应商占比着色 + 排名榜
6. **发丝线**（新增，L3 Barcode）：每个分桶一根发丝线+圆点，安静桶也有短线

并修复图例布局：图例移出图表卡片，独立成下方单行（色块项 nowrap、说明文字右对齐），不再与 SVG 抢空间导致换行错乱。

## 现状

- 三图型（趋势/热力/累计）全部属时间序列家族，同质化。
- 图例在固定高度图表卡内与 SVG 竞争空间，长文字换行断词、色块与文字拥挤。
- "节律"需要跨天的小时×星期聚合，现有 timeline 端点只有单一粒度分桶，前端无法获得该形状。

## 设计范围

1. 后端：`UsageStore.weekday_hour(window, start_at, end_at, provider_id, now)` 返回 `{"matrix": 7×24 按天聚合格子（周一=0 行、24 列小时，字段同 timeline）, "total", "window", ...}`；新端点 `GET /control/api/usage-weekday-hour`（`usage_window` 默认 `30d`，custom 上限 90 天，可选 provider_id，422/404/503 口径同 timeline），**在 core.py 与 server.py 两层都注册**。
2. 前端 `UsageTrendView.vue`：
   - 选择器 6 项（趋势/日历/累计/节律/构成/发丝线），旧持久化值映射（heat→calendar）；
   - 日历：≥7 天渲染"周×星期"圆点阵（半径=√(tokens/max)×rMax，最忙日虚线环，月份刻度，stagger 入场），today/24h 保留小时方格；7 天窗口显示单周；
   - 节律：请求 weekday-hour 端点，7×24 点阵（半径=√值），stagger；
   - 构成：请求 `/api/status?usage_window=...`，`buildShareCardData(providerLimit=8)` 得到排序占比，10×10 waffle 点阵 + 右侧排名榜（名称+占比+tokens）；
   - 发丝线：既有 timeline 分桶逐桶发丝线+圆点（面积∝值），安静桶保留短基线；
   - 指标药丸与轮播仅在 趋势/累计 显示；日历/节律/构成/发丝线固定 tokens 维度；
   - 图例全部移到图表卡片下方单行（nowrap + 说明右对齐）。
3. 样式：`.usage-legend-row`、waffle/发丝线/圆点日历样式；`prefers-reduced-motion` 降级沿用。

## 非目标

- Bar Race / Dynamic Stream 等强动画图型；地图类；不改经典界面。

## 兼容性

- 后端纯新增端点与查询，`request_usage` 无 schema 变更；timeline 行为不变。dist 重建。
- 发布级别经用户确认由 minor 调整为 patch：本轮功能跨度有限，不足以提升第二位版本号。

## 风险

1. weekday_hour 聚合在超大时间范围（all）下为单查询分组聚合，量级可控；custom 限 90 天。
2. `all` 窗口发丝线桶数可达数千：发丝线按桶数压缩线宽（最小 1px），超过 400 桶时抽稀绘制并保持首尾。
3. 两层端点注册遗漏（#78 教训）：server 层注册与测试同时提交。

## 测试计划

- `tests/test_proxy_core.py`：weekday_hour 单测（固定 now 的星期/小时落位、provider 过滤、空库零矩阵、custom 边界）。
- `tests/test_server.py`：`/control/codex|claude/api/usage-weekday-hour` 200/数据隔离/跨服务 404。
- `tests/local_proxy_vue_ui.test.js`：六图型断言、图例行重构断言、端点请求断言。
- 组件 stub 几何抽查 + 浏览器实测六图型渲染。
- 全量验证：Python 单测、`node --test`、`npm ci` + `npm run build`。

## 实际改动

- `local_proxy/core.py`：`UsageStore.weekday_hour()`——按本地时区星期×24 小时聚合（SQL `strftime('%w'/'%H', recorded_at, 'unixepoch', 'localtime')`，周一=0 行），字段与 timeline 一致，空库/`all` 时 `start_at=null`；新端点 `GET /control/api/usage-weekday-hour`（默认 `30d`、custom 限 90 天、provider 404/参数 422/store 503/数据库 503/no-store）。
- `local_proxy/server.py`：`_register_control_routes` 同步注册 `{prefix}/api/usage-weekday-hour`（处理器与 core 层口径一致）。
- `proxy_static/src/components/UsageTrendView.vue`：图型选择器扩为 趋势/日历/累计/节律/构成/发丝线 六项（旧值映射：heat→calendar、area/line/bars→trend）；日历图在 ≥7 天窗口渲染"周×星期"圆点阵（半径=√(tokens/max)、最忙日虚线环、月份/星期刻度、stagger 入场），今天/24h 保留小时方格；节律图消费 weekday-hour 端点渲染 7×24 点阵；构成图请求 `/api/status` 并经 `buildShareCardData(providerLimit=8)` 渲染 10×10 waffle 点阵；发丝线逐桶线+圆点（安静桶保留短基线，线宽随桶数压缩）；图例移出图表卡片为下方单行（色块项 nowrap、说明文字右对齐，窄屏换行）；指标药丸与轮播仅在趋势/累计显示；30 秒自动刷新经 `document.hidden` 门控并在节律/构成激活时同步刷新辅助数据。
- `proxy_static/src/styles.css`：`.usage-legend-row`（含 900px 断点换行）、`.usage-cal-dot`/`.usage-punch-dot`/`.usage-waffle-dot` stagger 动画、`.usage-cal-peak-ring`、`.usage-waffle-1..4`、`.usage-barcode-line(.is-empty)`/`.usage-barcode-dot`；reduced-motion 降级扩展到全部新动画。
- 测试：`tests/test_proxy_core.py` 新增 weekday_hour 矩阵落位/provider 过滤/空库/非法窗口与 90 天上限 2 个用例；`tests/test_server.py` 新增双服务 usage-weekday-hour 200/隔离/跨服务 404 用例；`tests/local_proxy_vue_ui.test.js` 图型断言扩为六图型并新增图例行结构断言。
- `proxy_static/dist/`：重建（index-D6mAsM1S.js / index-CIs_K6wk.css）。

## 验证结果

- `python -m unittest tests.test_proxy_core.UsageTests` → 26 项通过；`tests.test_server` → 19 项通过；全量 `python -m unittest discover -s tests -p "test_*.py"` → 558 项全部通过（2026-09-03 23:28）。
- `node --test tests/*.test.js` 全部通过（Vue UI 27/27，含六图型与图例断言）。
- 组件 stub 数值抽查：30 天日历 26px 单元 178×212、punch 7×24=168 点半径合规、发丝线 30 桶 x 递增、waffle 恰 100 点按占比 70/30 分布。
- 浏览器 harness 截图：六图型全部正常渲染（日历月刻度+最忙日虚线环、节律工作日/周末纹理、waffle、发丝线、累计、趋势）；`npm run build --prefix proxy_static` 构建成功。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/84
