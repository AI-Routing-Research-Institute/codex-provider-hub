+++
id = "2026-08-31-modern-token-display"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 统一新版界面 Token 展示

## 目标

让新版控制台的全部 Token 数值使用经典界面的 K/M/B 紧凑格式，并由对应标题或字段标签明确 Token 单位。

## 现状

经典界面会将 Token 数值按阈值缩写为 K、M、B，最多保留两位小数；新版界面仍使用千分位完整数字。新版供应商统计中的输入、输出和缓存指标也没有在标签中明确 Token 单位，两套界面的相同数据展示不一致。

## 设计范围

- 为 Vue 控制台提供共享 Token 格式化函数，精确复用经典界面的阈值、舍入和无效值回退行为。
- 在供应商统计、供应商列表、用量弹窗和请求列表统一使用该格式化函数。
- 通过 Token 总量、输入 Token、输出 Token、缓存 Token、Token 用量和 Token 列标题表达单位，不在每个数值后重复追加 Token。
- 保留请求无用量时的破折号、供应商零用量状态及现有交互行为。

## 非目标

- 不修改经典界面。
- 不修改后端 Token 采集、估算、聚合、存储或 API 返回值。
- 不调整供应商和请求页面的筛选、点击或刷新逻辑。

## 兼容性

无接口、配置、数据或迁移影响。仅修正新版界面的展示一致性，选择 `patch` 版本提升。

## 风险

紧凑格式的阈值或舍入行为若与经典界面不一致，会导致两套界面显示不同；通过边界单元测试固定算法。新增单位文字可能挤压窄屏布局；通过桌面和移动视口浏览器检查确认无溢出或错位。

## 测试计划

- 单元测试覆盖无效值、0、999、1000、小数缩写以及 K/M/B 阈值。
- Vue 结构测试覆盖所有 Token 展示入口和单位标签。
- 运行完整 Python 单测、JavaScript 测试、JavaScript 语法检查、Vite 构建、Python compileall 和 git diff 检查。
- 在桌面与移动视口检查供应商页、请求页和用量弹窗。

## 实际改动

- `proxy_static/src/token-format.js` 新增与经典界面一致的 K/M/B Token 紧凑格式化函数。
- `proxy_static/src/components/ProvidersView.vue` 将 Token 总量、输入、输出、缓存、供应商用量和用量弹窗接入共享格式，并在指标标签中补充 Token 单位；供应商用量弹窗接入 `/api/usage-history` 明细、加载/错误/分页状态，并按触发按钮和视口动态定位。
- `proxy_static/src/components/RequestsView.vue` 将请求 Token 列接入共享格式，同时保留无用量破折号。
- `tests/token_format.test.js` 覆盖无效值、整数、两位小数以及 K/M/B 阈值；`tests/local_proxy_vue_ui.test.js` 验证全部 Vue 展示入口和单位标签。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js tests/token_format.test.js`：通过。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check`：通过，仅有行尾转换提示。

## PR

pending
