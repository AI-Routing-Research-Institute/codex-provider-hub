# 监控排序一致性与停用黑与白 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保持公开监控健康度排序，让监控管理展示与公开最终顺序一致，并将黑与白切换为仅手动检测。

**Architecture:** 状态服务继续计算健康度排序；服务器配置顺序作为仅针对全不可用供应商的稳定 tie-breaker。管理页同时读取配置列表和公开状态顺序，优先采用公开顺序；受限 SSH 管理动作原子更新指定片段并重启 Worker。

**Tech Stack:** Python 3、FastAPI、SQLite、Vue 3、Vite、unittest。

## Global Constraints

- 不删除黑与白的配置、凭据和历史数据。
- 不改变健康度排序、状态聚合和公开 API 字段。
- 不新增公网管理接口；所有修改继续经过受限 SSH 命令。
- 代码在 `fix/monitor-order-and-disable-hybzg` 功能分支完成。

---

### Task 1: 健康排序加入人工顺序低优先级 tie-breaker

**Files:**
- Modify: `provider_status/web.py`
- Test: `tests/test_status_web.py`

**Interfaces:**
- Extend `_sort_providers_by_model(providers, model_name, manual_jobs=None, manual_histories=None, manual_order=None)`; return a new list。

- [x] **Step 1: Write failing tests**
  - 增加测试：健康/波动供应商仍按健康排序；两个 `down` 供应商按 `manual_order` 排序；没有人工顺序时保持原顺序。
- [x] **Step 2: Run focused tests and observe failure**
  - Run `python -m unittest tests.test_status_web.ProviderSortTests.test_provider_sort_manual_order_only_breaks_down_provider_ties -v`；现有函数不接受 `manual_order` 或排序结果错误。
- [x] **Step 3: Implement minimal sorting change**
  - 为排序 key 增加 provider state；仅当状态为 `down` 时将人工位置作为最后一级 key，健康/波动维持既有 key。
- [x] **Step 4: Run focused tests**
  - Run `python -m unittest tests.test_status_web -v`；全部通过。

### Task 2: 管理列表与公开排序对齐

**Files:**
- Modify: `local_proxy/status_upload.py`, `proxy_static/src/components/MonitorView.vue`
- Test: `tests/test_status_provider_upload.py`, `tests/test_local_proxy_app.py`

**Interfaces:**
- `StatusUploadManager.manage` 继续代理 `list/order/disable` 动作。
- 管理页 `loadProviders` 获取配置列表后读取 `status_url + /api/status`，按公开 provider id 顺序重排；请求失败回退配置顺序并显示错误。

- [x] **Step 1: Write failing tests**
  - 测试管理列表排序函数优先采用公开顺序；公开请求失败返回配置顺序。
- [x] **Step 2: Run focused tests and observe failure**
  - Run `node --test tests/local_proxy_vue_ui.test.js`；现有 MonitorView 未读取公开状态顺序。
- [x] **Step 3: Implement minimal UI ordering**
  - 在 MonitorView 增加 `statusUrl` 派生地址、公开状态 fetch、`alignProviders`；移动操作成功后刷新列表以显示最终排序。
- [x] **Step 4: Run focused tests/build**
  - Run `npm run build --prefix proxy_static` and `node --test tests/local_proxy_vue_ui.test.js`。

### Task 3: 增加停用指定供应商动作并停用黑与白

**Files:**
- Modify: `scripts/status_provider_import.py`, `proxy_static/src/components/MonitorView.vue`
- Test: `tests/test_status_provider_upload.py`, `tests/test_status_worker.py`

**Interfaces:**
- New `disable_provider(provider_id, probe_mode="manual_only")` updates the provider fragment atomically, restarts Worker, and restores bytes on failure.
- `serve()` accepts `{action: "disable", provider_id, probe_mode: "manual_only"}`.

- [x] **Step 1: Write failing tests**
  - 测试片段模式更新、非法模式拒绝、重启失败回滚；manual_only 不进入自动队列。
- [x] **Step 2: Run focused tests and observe failure**
  - Run `python -m unittest tests.test_status_provider_upload tests.test_status_worker -v`；disable action 未实现。
- [x] **Step 3: Implement minimal remote action and UI control**
  - 更新包含 `probe_mode` 的片段文本，保持其他字段/凭据不变；管理页为黑与白（或所有 automatic 供应商）提供“停止自动监控”按钮，确认后调用 disable。
- [x] **Step 4: Run focused tests**
  - 重跑上述 unittest。

### Task 4: 完整验证与文档状态

**Files:**
- Modify: `docs/changes/2026-08-28-monitor-order-and-disable-hybzg.md`

- [x] **Step 1:** Run `python -m unittest discover -s tests -p "test_*.py"`。
- [x] **Step 2:** Run `node --test tests/*.test.js`、`node --check provider_status/static/app.js`、`node --check proxy_static/src/api.js`、`node --check proxy_static/src/monitorOrder.js`、`npm run build`（`proxy_static`）、`git diff --check`。
- [x] **Step 3:** Record exact results, set change status to `verified` only after all commands pass。
