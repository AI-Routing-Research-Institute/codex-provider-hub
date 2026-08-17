+++
id = "2026-08-17-provider-delete"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 本地供应商删除

## 目标

允许用户从 Codex 独立供应商目录删除不再使用的供应商，并在删除后立即刷新本地路由。

## 现状

控制台已支持新增和编辑本地供应商，但后端目录和管理界面都没有删除能力，用户只能隐藏无用供应商。

## 设计范围

- 在独立 SQLite 目录增加按供应商 ID 删除记录的原子操作。
- 增加受本地控制请求保护的 DELETE 接口，删除成功后热刷新路由并返回最新状态。
- 禁止删除当前正在使用的供应商，要求用户先切换，避免路由失去当前配置。
- 在编辑供应商弹窗提供危险操作入口，并在执行前显示包含供应商名称的二次确认。
- 新增模式不显示删除入口；删除过程中禁用保存和删除按钮，失败时保留弹窗并显示错误。

## 非目标

- 不删除或回写 CC Switch 数据库中的供应商。
- 不提供批量删除、回收站或跨设备同步。
- 不自动切换当前供应商后再删除。
- 不改变隐藏供应商和排序功能。

## 兼容性

删除仅作用于 `~/.codex-local-proxy/codex-providers.sqlite3`。已有目录会继续自动维护索引，无需迁移；被删除供应商的历史请求记录保持不变。新增用户可见能力，版本提升选择 `minor`。

## 风险

- 删除不可撤销，必须二次确认并清晰展示供应商名称。
- 删除当前供应商会使路由状态不明确，因此后端必须独立校验，不能只依赖前端禁用。
- SQLite 删除和路由刷新必须保持串行，避免请求看到已删除但仍可选择的供应商。
- API 错误不得泄露供应商凭据。

## 测试计划

- 覆盖目录删除成功、未知供应商和当前供应商拒绝删除。
- 覆盖 DELETE 接口控制请求校验、删除后热刷新和响应脱敏。
- 覆盖编辑弹窗删除入口、二次确认和错误展示所需的页面结构与脚本。
- 运行完整 Python 单元测试、JavaScript 语法检查、JavaScript 测试和 `git diff --check`。

## 实际改动

- 修改 `local_proxy/provider_catalog.py`，增加事务内按 ID 删除并返回已删除记录的目录操作。
- 修改 `local_proxy/server.py`，增加受控制请求保护的 DELETE 接口，拒绝删除当前供应商，并在成功后热刷新路由及清理隐藏/排序偏好。
- 修改 `proxy_static/index.html`、`proxy_static/app.js` 和 `proxy_static/styles.css`，在编辑弹窗增加危险操作按钮、当前供应商禁用状态、二次确认和错误反馈。
- 扩展 `tests/test_provider_catalog.py` 和 `tests/test_proxy_core.py`，覆盖目录、API 和页面入口。

## 验证结果

- `python -m unittest discover -s tests -p 'test_*.py'`：通过，运行 441 个测试。
- `node --check proxy_static/app.js`：通过。
- 逐个运行 `tests/*.test.js`：通过，共 40 个 JavaScript 测试。
- `git diff --check`：通过。
- 前端和功能说明 UTF-8 解码检查：通过。

## PR

pending
