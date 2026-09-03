+++
id = "2026-09-03-token-usage-trend"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# Token 消耗趋势曲线

## 目标

在新版控制台新增"用量趋势"视图：以时间曲线展示选定范围内 token 消耗（输入/输出/合计）随时间的变化，按小时或按天分桶，附请求量与成功/失败汇总，帮助用户直观看到消耗节奏、定位异常时段。

## 现状

- `UsageStore`（`local_proxy/core.py`）只有 `summary()`（窗口内整体聚合、按供应商分组）和 `history()`（单供应商请求明细分页），没有按时间分桶的序列数据。
- 新版控制台没有专门用量视图：token 汇总只在战报海报（`ShareCardDialog.vue`）和经典界面出现；请求记录页只有逐条列表，无法回答"消耗集中在什么时候"。
- 新版与经典控制台均没有任何时间序列图表。

## 设计范围

1. `UsageStore.timeline(window, start_at, end_at, provider_id=None, now=None)`：
   - 分桶粒度：按覆盖时长自动选择——跨度 ≤ 48 小时按小时（`today`/`24h` 及短跨度 `custom`/`all`），更长按天；小时对齐取整点，天对齐取本地 0 点（与 `_usage_window_cutoff` 一致）。
   - 覆盖区间：`today` 为本地当日 0 点至 now；`24h` 为 now-24h 向下取整点至 now；`7d`/`30d` 为窗口起点对齐本地 0 点至 now；`all` 从库内最早 `recorded_at`（对齐本地 0 点）至 now，无记录时返回空桶数组。
   - 每桶输出：`start_at`/`end_at`（毫秒）、`request_count`、`successful_requests`、`failed_requests`、`input_tokens`、`output_tokens`、`total_tokens`、`cached_tokens`、`reasoning_tokens`；空桶补零输出，保证曲线连续。
   - 返回 `{window, granularity, start_at, end_at, provider_id, buckets, total}`；`custom` 复用 `_custom_time_bounds`（上限 90 天）。
2. 新端点 `GET /control/{app}/api/usage-timeline`：复用 `_query_time_range`（`usage_window` 参数，默认 `24h`，允许 `USAGE_WINDOWS`，custom 上限 90 天）；可选 `provider_id`，校验方式与 usage-history 一致；`usage_store` 缺失返回 503；参数错误 422、供应商不存在 404、数据库错误 503。
3. 前端新组件 `UsageTrendView.vue`（新增"用量趋势"标签页）：
   - 时间范围选择（今天/近 24 小时/近 7 天/近 30 天/全部），默认近 24 小时；
   - 手写 SVG 图表（无新增依赖）：输入/输出堆叠面积 + 合计折线，y 轴自适应，x 轴按粒度标注小时或日期，悬停显示该桶明细；
   - 汇总行展示总 token、请求数、成功/失败；30 秒自动刷新 + 手动刷新。
4. `ViewTabs.vue` 增加"用量趋势"标签，`App.vue` 挂载视图，`styles.css` 增加图表样式。

## 非目标

- 不改经典界面（classic），趋势图只在新版控制台提供。
- 不做按供应商筛选曲线：后端参数预留并校验，本次 UI 不提供选择器。
- 不做图片导出/分享（战报海报已覆盖）、不做金额成本维度、不动 Claude 链路。

## 兼容性

- 纯新增：新查询方法、新端点、新视图；`request_usage` 无 schema 变更、无迁移；现有 `summary`/`history` 行为不变。

## 风险

1. `all` 窗口桶数随历史增长：按天分桶且以库内最早记录为起点，桶数上限由数据保留策略天然约束；`custom` 限 90 天。
2. 时区与对齐错误导致曲线偏移：与 `_usage_window_cutoff` 相同的本地时区规则，单测覆盖跨天/跨小时边界。
3. 手写 SVG 在窄屏的可用性：响应式宽度、最小高度、悬停按桶索引对齐，不做像素级悬停。

## 测试计划

- Python 单测（`tests/test_proxy_core.py` UsageTests）：today/24h 小时对齐与桶数、7d 天对齐、空桶补零、`all` 从最早记录起、`custom` 边界与上限、provider_id 过滤、total 与桶求和一致；端点测试（422/404/503/200、参数传递）。
- JS 测试（`tests/local_proxy_vue_ui.test.js` 模式）：组件源断言（标签页注册、时间范围选项、请求路径与参数构造）。
- 完整验证按 release 口径：`python -m unittest discover -s tests -p "test_*.py"`、`node --test tests/*.test.js`、`npm run build --prefix proxy_static`。

## 实际改动

- `local_proxy/core.py`：
  - 新增常量 `TIMELINE_MAX_CUSTOM_SECONDS`（90 天）、`TIMELINE_HOURLY_SPAN_SECONDS`（48 小时）、`TIMELINE_BUCKET_FIELDS`；
  - 新增辅助函数 `_timeline_granularity`（跨度 ≤ 48 小时按小时，否则按天）、`_timeline_bucket_seconds`、`_align_timeline_start`（小时取整点、天取本地 0 点）；
  - `UsageStore` 新增 `timeline()`：按窗口解析边界（`all` 取库内最早记录），SQL 按桶索引分组聚合，空桶补零，total 由桶求和得出；
  - `create_proxy_app` 新增 `GET /control/api/usage-timeline` 路由（`usage_window` 参数默认 `24h`、custom 上限 90 天、可选 `provider_id` 校验、422/404/503 错误口径与 usage-history 一致）。
- `proxy_static/src/components/UsageTrendView.vue`（新增）："用量趋势"视图：时间范围选择（今天/近 24 小时/近 7 天/近 30 天/全部/自定义）、汇总卡片（合计/输入/输出/请求/成功/失败）、手写 SVG 图表（输入/输出堆叠面积 + 合计折线、自适应 y 轴、悬停竖线与明细浮层、图例）、30 秒自动刷新、窗口选择持久化到 localStorage。
- `proxy_static/src/components/ViewTabs.vue`：新增"用量趋势"标签（`usage`），随 `usageEnabled` 属性与 usage_history 功能开关联动。
- `proxy_static/src/App.vue`：挂载 `UsageTrendView`（受 `features.usage_history` 门控），存储视图回退逻辑扩展到 `usage`。
- `proxy_static/src/styles.css`：新增 `.usage-*` 图表与面板样式（坐标轴字号使用 `var(--font-meta)`，暗色主题经既有 CSS 变量自动适配）。
- `proxy_static/dist/`：`npm run build` 重建产物（index-LOpJNy1t.js）。
- 测试：`tests/test_proxy_core.py` 新增 5 个 `timeline` 存储测试（today 小时分桶与零填充、24h 整点对齐与供应商过滤、7d 本地天对齐、all 起点与空库、custom 粒度与边界校验）和 1 个端点测试（200/过滤/422/404/503/默认窗口）；`tests/local_proxy_vue_ui.test.js` 更新共享视图清单并新增"usage trend view renders a time-bucketed token curve"源断言测试。

## 验证结果

- `python -m unittest discover -s tests -p "test_*.py"` → Ran 549 tests：新增 6 个测试全部通过；4 个失败/错误为本轮改动前即存在的本机环境问题（fake-IP DNS 将 `.example` 保留域解析为非公网地址 ×3、本机 cc-switch 无 Codex API 供应商 ×1），与在 origin/main 上复现的结果一致（2026-09-03）。
- `node --test tests/*.test.js` → 全部通过（含新增用例；Vue UI 测试 24/24）（2026-09-03）。
- `npm run build --prefix proxy_static` → 构建成功，产物 `index-LOpJNy1t.js`。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/74
