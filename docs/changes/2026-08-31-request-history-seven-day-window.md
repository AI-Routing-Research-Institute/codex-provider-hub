+++
id = "2026-08-31-request-history-seven-day-window"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复请求记录近七天查询范围

## 目标

让请求记录页面的“近 7 天”筛选按完整 168 小时查询，正确返回保留范围内的历史记录。

## 现状

请求历史查询将 `7d` 映射为 7 小时，导致页面选择“近 7 天”时只显示最近 7 小时的记录。数据库保留与清理仍按 168 小时执行，历史数据没有丢失。

## 设计范围

- 将请求历史预设窗口中的 `7d` 修正为 `7 * 24` 小时。
- 增加覆盖 7 小时之外、7 天以内记录的回归测试。
- 保持 1 小时、6 小时、24 小时、自定义范围和七天保留策略不变。

## 非目标

- 不修改 Token 用量统计窗口。
- 不迁移或改写现有 SQLite 数据。
- 不调整请求历史保留时长和分页上限。

## 兼容性

接口参数和响应格式不变，只修正 `window=7d` 的查询边界。无需配置或数据库迁移，版本选择 `patch`。

## 风险

近七天结果数量会恢复为预期值，查询数据量可能高于错误实现；现有时间索引和分页限制继续控制查询成本。

## 测试计划

- 运行请求历史聚焦测试，验证 8 小时前的记录只出现在 `7d`，不出现在 `6h`。
- 运行相关核心测试并执行 `git diff --check`。

## 实际改动

- `local_proxy/core.py` 将请求记录预设 `7d` 的小时数从 7 修正为 `7 * 24`。
- `tests/test_proxy_core.py` 增加 8 小时前记录在 `6h` 与 `7d` 窗口中的边界回归测试。

## 验证结果

- `python -m unittest tests.test_proxy_core.UsageTests.test_request_history_uses_full_168_hour_window_for_seven_days tests.test_proxy_core.UsageTests.test_custom_request_range_filters_and_enforces_seven_day_retention tests.test_proxy_core.UsageTests.test_request_history_cleanup_is_throttled`：3 项通过。
- `.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：514 项通过。
- `python -m compileall -q local_proxy tests` 与 `git diff --check`：通过。

## PR

pending
