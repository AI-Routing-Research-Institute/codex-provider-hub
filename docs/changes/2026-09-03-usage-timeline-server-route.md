+++
id = "2026-09-03-usage-timeline-server-route"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复用量趋势端点在正式服务下 404

## 目标

让"用量趋势"视图在正式启动的服务（`/control/{codex|claude}/`）下正常返回数据，消除 `GET /control/codex/api/usage-timeline → 404 Not Found`。

## 现状

- v1.8.0（PR #74）在 `local_proxy/core.py` 的 `create_proxy_app` 上新增了 `/control/api/usage-timeline` 端点，前端 `UsageTrendView.vue` 通过 `controlFetch('/api/usage-timeline')` 请求当前服务的 `/control/{app}/api/usage-timeline`。
- 但正式运行的多服务应用由 `local_proxy/server.py` 的 `_register_control_routes` 按 `/control/{service_id}` 前缀逐条注册控制台路由，该清单漏掉了 usage-timeline，导致线上 404、视图显示 Not Found。
- 既有端点测试只覆盖 `create_proxy_app`（无前缀单应用变体），没有覆盖 `server.py` 的注册清单，因此漏检。

## 设计范围

1. `local_proxy/server.py`：导入 `TIMELINE_MAX_CUSTOM_SECONDS`，在 `_register_control_routes` 中新增 `control_usage_timeline` 处理器（参数、校验与错误口径与 `core.py` 端点及 usage-history 一致：`usage_window` 默认 `24h`、custom 上限 90 天、可选 `provider_id` 校验 404、`usage_store` 缺失 503、参数错误 422、数据库错误 503、`Cache-Control: no-store`），并注册 `GET {prefix}/api/usage-timeline`。
2. `tests/test_server.py`：新增 server 级回归测试，直接请求 `/control/codex/api/usage-timeline` 与 `/control/claude/api/usage-timeline`，覆盖 200、双服务数据隔离、跨服务 provider 404。

## 非目标

- 不改 `core.py` 端点行为、`UsageStore.timeline()` 查询逻辑与前端组件。
- 不重构 `_register_control_routes` 的逐条注册方式。

## 兼容性

- 纯补注册，无行为迁移；`create_proxy_app` 单应用端点保持不变。

## 风险

- server.py 处理器与 core.py 端点行为不一致：通过镜像 usage-history 的实现方式并新增双服务隔离测试控制。

## 测试计划

- `python -m unittest tests.test_server`（新增用例）与 `tests/test_proxy_core`（既有 timeline 用例不回归）。
- 全量验证：`python -m unittest discover -s tests -p "test_*.py"`、`node --test tests/*.test.js`、`npm ci` + `npm run build --prefix proxy_static`（经基线对比的 pre-push 钩子执行）。

## 实际改动

- `local_proxy/server.py`：
  - 从 `local_proxy.core` 导入 `TIMELINE_MAX_CUSTOM_SECONDS`；
  - `_register_control_routes` 新增 `control_usage_timeline` 处理器（`usage_window` 默认 `24h`、custom 上限 90 天、可选 `provider_id` 不存在 404、`usage_store` 缺失 503、参数错误 422、数据库错误 503、`Cache-Control: no-store`，与 `core.py` 端点及 usage-history 口径一致）；
  - 注册 `GET {prefix}/api/usage-timeline`（紧跟 usage-history 之后）。
- `tests/test_server.py`：新增 `test_usage_timeline_is_served_per_service_console`——直接请求 `/control/codex/api/usage-timeline` 与 `/control/claude/api/usage-timeline`，断言 200、按小时分桶、桶求和与 total 一致、双服务数据隔离（7/14 tokens）、跨服务 provider 404、`Cache-Control: no-store`。

## 验证结果

- `python -m unittest tests.test_server` → 18 项全部通过（含新增 1 项，2026-09-03 16:30）。
- `python -m unittest tests.test_proxy_core` → 144 项全部通过（既有 timeline 用例无回归）。
- `python -m unittest discover -s tests -p "test_*.py"` → 555 项全部通过（2026-09-03 16:31）。
- `node --test tests/*.test.js`、`node --check`（classic/src/provider_status）、`npm ci` + `npm run build --prefix proxy_static` → 全部通过（2026-09-03 16:31）。

## PR

pending
