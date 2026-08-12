+++
id = "2026-08-13-web-update-entry"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 在线更新入口迁移到 Web 控制台

## 目标

把「检查更新」从仅托盘菜单迁移到 Web 控制台运行设置页：显示当前版本，提供「检查更新」按钮，Windows 便携版额外提供「更新并重启」一键闭环；其他平台仅检测并引导手动下载。

## 现状

在线更新逻辑（`updater.py` + `application.py` 的退出重启闭环）已具备，但入口只在系统托盘菜单里，Web 控制台无任何版本显示或更新入口。

## 设计范围

- `application.py` 新增进程级 `UpdateController`，复用 `updater.check_for_update`/`download_asset` 与 `tray_holder["update_apply_path"]` → `launch_update_helper` 闭环，经 `create_proxy_app(update_controller=...)` 注入。
- 新增 `GET /api/update`（读缓存状态）、`POST /api/update/check`（线程池检测）、`POST /api/update/apply`（Windows 下载校验后触发退出重启），沿每个 `/control/{service}` 前缀注册。
- 运行设置页新增「版本与更新」区块：当前版本、检查更新、更新并重启，出错兜底展示 Releases 链接。

## 非目标

不改动检测/下载/校验核心逻辑本身，不做 macOS 就地自更新，不新增 403 限流专项容错（按需求本次仅迁移入口到界面）。

## 实际改动

- `local_proxy/application.py`：新增 `UpdateController`（`status`/`check`/`download`/`finalize`），复用 `updater.check_for_update`、`updater.download_asset`、`updates_directory`、`update_supported`；`run_application` 内构造并经 `create_proxy_app(update_controller=...)` 注入，`finalize` 沿用 `tray_holder["update_apply_path"]` → `launch_update_helper` 退出重启闭环。
- `local_proxy/core.py`：`create_proxy_app` 新增 `update_controller` 形参并透传给统一应用。
- `local_proxy/server.py`：`create_unified_proxy_app`/`_register_control_routes` 透传 `update_controller`；新增 `GET /api/update`（读缓存状态）、`POST /api/update/check`（线程池调用检测，`UpdateError` → 502）、`POST /api/update/apply`（下载校验后返回 restarting 并后台触发 finalize），阻塞 httpx 走 `run_in_threadpool`。
- `proxy_static/index.html`、`proxy_static/app.js`：运行设置页新增「版本与更新」区块，显示当前版本、检查更新、更新并重启，出错时展示信息与 Releases 链接兜底；页面加载时读取版本状态。
- `tests/test_server.py`：新增 `UpdateRouteTests`（注入 fake controller，覆盖无 controller、检测成功、缺控制头 403、UpdateError → 502、非 Windows 409、下载后触发 finalize）。

## 兼容性

无运行时协议影响。新增均为 `/control/{service}/api/update*` 只读/受控端点，需 `X-Local-Proxy-Control` 头。`update_controller` 缺省为 None 时端点降级为不可用/仅返回版本占位。非 Windows 或源码运行时 `supported=false`，前端隐藏「更新并重启」。

## 风险

- 检测仍走 GitHub 匿名 API（60 次/小时/IP），共享出口 IP 被限流时返回 403，界面展示错误信息 + Releases 手动下载链接兜底，不影响主服务。
- 一键更新复用既有 Windows 替换闭环，失败回滚 `.bak`。

## 测试计划

`python -m unittest discover -s tests -p "test_*.py"` 全量通过，覆盖新增 update 路由；`node --test` 前端用例回归。

## 验证结果

`python -m unittest discover -s tests -p "test_*.py"` 全量 416 项通过（含新增 6 项 update 路由用例）；`node --test tests/*.test.js` 37 项通过；`node --check proxy_static/app.js` 语法通过。

## PR

pending
