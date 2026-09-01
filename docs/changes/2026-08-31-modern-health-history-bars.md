+++
id = "2026-08-31-modern-health-history-bars"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复新版检测历史柱状图

## 目标

让新版服务器检测历史柱状图与经典界面保持固定槽位和一致柱宽。

## 现状

新版直接渲染返回的历史条目数量，数据不足时柱子变粗且缺少经典界面的占位槽位。

## 设计范围

- 顶部摘要固定渲染 16 个检测槽位。
- 详情中的每个模型固定渲染 60 个检测槽位，缺失项显示未知状态。
- 保持历史顺序为较早到最近。

## 非目标

- 不修改检测接口、采集频率或经典界面。

## 兼容性

仅前端展示修正，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

- 历史数组顺序或补位方向错误会让较早和最近记录颠倒。
- 将状态值直接作为样式类会产生透明空柱，因此未知值必须统一回退为灰色。

## 测试计划

- 验证摘要固定 16 个槽位、每个模型固定 60 个槽位，并覆盖真实状态和灰色缺失槽位。
- 运行完整 JavaScript 测试、语法检查、Vite 构建和 Python 单测，并与经典界面同一供应商数据对照。

## 实际改动

- `proxy_static/src/components/ProvidersView.vue` 按经典界面直接读取供应商和模型历史数组，再分别补齐 16 个和 60 个槽位；真实记录保持绿、黄、红状态，缺失记录显示灰色。
- `proxy_static/src/styles.css` 保留经典界面按可用宽度均分柱子的布局，并防止详情区域产生横向溢出。
- `tests/local_proxy_vue_ui.test.js` 增加历史数组输入、固定槽位和唯一地址匹配断言。

## 验证结果

- `.\.venv\Scripts\python.exe scripts/team_policy.py pre-push`：通过；Python 单测 514/514，JavaScript 测试 71/71，依赖锁定安装、Vite 构建和 JavaScript 语法检查均成功。
- JavaScript 语法检查：通过。
- 全部 JavaScript 测试：通过，71/71。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check`：通过。
- 浏览器对照：新版外部 22 个供应商共 352 个槽位，与经典页状态分布一致；同一供应商详情为 120 个槽位，彩色与灰色槽位数量和经典页一致。

## PR

pending
