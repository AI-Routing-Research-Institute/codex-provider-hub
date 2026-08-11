+++
id = "2026-08-11-view-tab-persistence"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 控制台主导航页签持久化

## 目标

让用户刷新或重新打开本地中转控制页后，继续停留在最后选择的“供应商、请求、重试设置或运行设置”页签；当前服务不支持保存页签时安全回退到“供应商”。

## 现状

`switchView()` 只更新按钮和面板的 DOM 状态，没有持久化当前页签。页面初始化也没有恢复逻辑，因此每次加载都会采用 HTML 中默认激活的“供应商”，用户在“请求”页面刷新后会被强制跳回。

## 设计范围

- 使用 `localStorage` 保存最后选择的主导航页签。
- 存储键包含 `uiConfig.service_id`，避免 Codex 与 Claude 控制台相互覆盖。
- 用户点击页签以及从供应商请求入口进入“请求”页时都通过现有 `switchView()` 持久化。
- UI 配置加载并确定功能可见性后恢复保存页签。
- 仅接受 `providers`、`requests`、`settings`、`runtime`；非法值回退 `providers`。
- 如果保存的是 `requests` 但当前服务关闭请求历史功能，则回退 `providers`。

## 非目标

- 不保存请求页内部的状态、供应商或搜索筛选；这些行为保持现状。
- 不通过 URL 或后端数据库同步页签。
- 不自动重启本地中转。

## 兼容性

无接口、配置或数据库迁移。未保存过页签的用户继续默认进入“供应商”；浏览器禁用本地存储时保持现有行为。属于向后兼容缺陷修复，版本选择 `patch`。

## 风险

- 保存值损坏可能隐藏全部面板；恢复前使用白名单规范化并回退供应商。
- Claude 控制台可能不提供请求页；在 UI 配置应用后检查页签是否隐藏。
- 恢复运行设置或请求页会触发现有惰性加载；继续复用 `switchView()`，避免复制加载逻辑。
- 本地存储可能抛出安全或配额异常；读写均捕获异常并采用默认页签。

## 测试计划

- 验证四个合法页签值保持不变，非法值回退供应商。
- 验证请求功能关闭时，保存的请求页回退供应商。
- 验证存储键按服务 ID 隔离。
- 验证 `switchView()` 保存规范化页签，初始化在 UI 配置后恢复。
- 修改提交后扫描 diff 中的密钥、令牌和私钥标记。
- 运行完整 Python、Node 测试、JavaScript 语法、Python compileall 和 diff 检查。

## 实际改动

- `proxy_static/app.js` 增加按 `service_id` 隔离的页签存储键，并只保存 `providers`、`requests`、`settings`、`runtime` 白名单值。
- `switchView()` 统一规范化并持久化实际生效页签；用户点击导航或通过现有请求入口切换时都会保存。
- 页面在 UI 配置加载后恢复最后页签；请求历史功能关闭、保存值非法或本地存储不可用时回退供应商。
- `tests/local_proxy_view_state.test.js` 增加合法页签、隐藏请求页回退、服务键隔离、切换持久化和初始化恢复回归测试。

## 验证结果

- `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"`：384 项通过，1 项跳过。
- `node --test tests/*.test.js`：35 项通过。
- `node --check proxy_static/app.js` 与 `node --check provider_status/static/app.js`：通过。
- `.venv/Scripts/python.exe -m compileall -q local_proxy tests`：通过。
- `git diff --cached --check` 与 `git diff --check`：通过。
- 提交后对 `origin/main...HEAD` 完整差异执行 7 类敏感信息扫描：私钥头、`sk-` 密钥、GitHub token、Bearer token、password 赋值、API key 赋值和 Authorization 头赋值均无命中。

## PR

pending
