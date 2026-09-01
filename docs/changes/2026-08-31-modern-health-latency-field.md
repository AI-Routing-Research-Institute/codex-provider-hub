+++
id = "2026-08-31-modern-health-latency-field"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复新版检测延迟字段

## 目标

让新版服务器检测详情正确显示检测接口返回的最近延迟。

## 现状

经典界面读取 `latest_latency`，新版仅读取 `latency_ms` 和 `latency`，导致详情中的最近延迟显示为破折号。

## 设计范围

- 在新版延迟格式化中优先读取 `latest_latency`，兼容其他已有字段。
- 增加结构回归断言。

## 非目标

- 不修改检测接口、采集逻辑或经典界面。

## 兼容性

仅前端字段兼容修正，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

- 延迟字段单位为毫秒，必须继续使用经典格式化规则，避免大于一秒的数据被错误显示为毫秒。

## 测试计划

- 增加 `latest_latency` 读取断言，运行 Vue 结构测试、完整 JavaScript 测试、Vite 构建和 Python 单测。
- 在实际弹窗中确认延迟值与经典界面一致。

## 实际改动

- `proxy_static/src/components/ProvidersView.vue` 在外部摘要延迟显示中优先读取 `latest_latency` 并保留其他字段回退，弹窗顶部则直接读取已选检测对象的 `latest_latency`。
- `tests/local_proxy_vue_ui.test.js` 增加 `latest_latency` 字段断言。

## 验证结果

- `.\.venv\Scripts\python.exe scripts/team_policy.py pre-push`：通过；Python 单测 514/514，JavaScript 测试 71/71，依赖锁定安装、Vite 构建和 JavaScript 语法检查均成功。
- JavaScript 语法检查：通过。
- 全部 JavaScript 测试：通过，71/71。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check`：通过。
- 浏览器验证：详情最近延迟按经典规则显示为 `90 秒`，不再显示空值或错误的毫秒文本。

## PR

pending
