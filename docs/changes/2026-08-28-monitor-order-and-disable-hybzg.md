+++
id = "2026-08-28-monitor-order-and-disable-hybzg"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 监控排序一致性与停用黑与白

## 目标

公开监控面板继续按健康度排序，监控管理与其最终顺序一致；管理页人工排序仅调整全不可用供应商。停用“黑与白”的自动监控并保留手动检测。

## 现状

公开面板按健康表现排序，而管理页只按 `.order.json` 配置顺序展示，导致两处顺序不一致。“黑与白”持续产生自动 403 失败记录。

## 设计范围

- 统一管理页与公开面板的健康度排序。
- 人工顺序作为全不可用供应商之间的低优先级 tie-breaker。
- 增加受限管理动作，将“黑与白”设为 `manual_only`。
- 保留供应商配置、凭据和历史数据，支持手动检测。

## 非目标

- 不改变健康度排序规则、状态聚合或公开接口字段。
- 不删除“黑与白”供应商、凭据或历史数据。
- 不新增公网管理接口。

## 兼容性

公开 API 字段保持兼容；管理排序读取失败时回退原配置顺序。`manual_only` 使用现有配置和数据库支持。版本选择 `patch`。

## 风险

- 状态接口暂时不可达时回退配置顺序，不错误推断健康状态。
- 停用动作重启失败时原子恢复配置。

## 测试计划

- 新增公开排序和管理排序一致性回归测试。
- 新增人工顺序仅影响 `down` 供应商测试。
- 新增停用动作渲染、回滚和 Worker 无自动目标测试。
- 运行完整 Python/JavaScript 测试、构建、语法及差异检查。

## 实际改动

- `provider_status/web.py`：保留连续成功次数和最近成功时间的健康度主排序，仅将 `.order.json` 的人工顺序作为 `down` 供应商的最低优先级 tie-breaker。
- `scripts/status_provider_import.py`：新增受限 `disable` 管理动作，原子将指定供应商切换为 `probe_mode = "manual_only"`，重启失败时恢复原配置。
- `proxy_static/src/monitorOrder.js`、`proxy_static/src/components/MonitorView.vue`：管理列表读取公开状态接口的最终顺序，接口不可用时回退配置顺序；上移/下移仅作用于不可用供应商，并新增停止自动监控操作。
- `proxy_static/src/styles.css`、`proxy_static/dist/`：补充排序提示、恢复中状态样式并更新生产构建资源。
- `tests/test_status_web.py`、`tests/test_status_provider_upload.py`、`tests/monitor_order.test.js`：覆盖健康排序 tie-breaker、停用回滚和管理/公开顺序对齐。
- 远程供应商 `7ee2e112-0645-46d1-9601-5212442b093f`（黑与白）已设为 `manual_only`，保留配置、凭据和历史数据。

## 验证结果

- `python -m unittest discover -s tests -p 'test_*.py'`：489 项通过。
- `node --test tests/*.test.js`：18 项通过。
- `npm run build`（`proxy_static`）：构建成功。
- `python -m compileall -q provider_status local_proxy scripts tests`、`node --check provider_status/static/app.js`、`node --check proxy_static/src/api.js`、`node --check proxy_static/src/monitorOrder.js`：全部成功。
- `git diff --check`：通过（仅显示 Git 的 LF/CRLF 转换警告）。
- 远程 `http://118.195.178.173/codex-status/api/status`：HTTP 200，`data_status = fresh`；黑与白为 `probe_mode = manual_only`、`state = unknown`、`model_count = 0`，展示模型仍为 `gpt-5.6-sol`、`gpt-5.6-luna`。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/51
