+++
id = "2026-09-03-update-version-check"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复版本与更新显示及检查误报

## 目标

开发环境“版本与更新”同时展示当前运行版本与检测到的最新版本；检查更新不再误报“已是最新”，可正确提示发现新版本（含不支持就地更新时的手动下载指引）。

## 现状

- 前端 `proxy_static/src/components/RuntimeView.vue` 使用 `update.update_available`，后端 `UpdateController.status()/check()` 仅返回 `has_update`，字段断裂导致恒显示“当前已经是最新版本”，更新按钮永不出现。
- `APP_VERSION` 在进程启动时快照一次，长进程不刷新；旧检出上 `git describe HEAD` 得 `1.3.1`，而远端最新已到 `1.6.2`。
- `updater.parse_release()` 将“远端有新版但本机无可用产物/不支持”与“已是最新”混为 `has_update=False`；`GET /api/update` 未检查前 `latest_version=None`，前端展示空白易误解。

## 设计范围

- 后端 `status()` 同时返回 `has_update` 与别名 `update_available`；无 controller 降级 payload 补齐同名字段。
- `status()/check()` 每次重算 `resolve_app_version()` 并同步 `current_version`，失败回退内置值。
- `parse_release()` 区分 `newer_available`（远端有新版）与 `has_update`（可就地更新），`status()` 透传。
- 前端同时兼容 `update_available ?? has_update`，展示 `当前 X / 最新 Y（未检查显示 —）`，三分支文案：可更新/手动下载/已是最新；仅 `supported` 时显示更新按钮并附 `release_url`。

## 非目标

- 不显示“落后主线/落后基线”提示，不对比 `origin/main`。
- 不在版本号中加入提交哈希、分支名或脏标记。
- 不改变发布标签生成、自动发版或便携包更新流程。

## 兼容性

- 接口加字段（`update_available`、`newer_available`），保留 `has_update`，旧客户端不受影响。
- `current_version` 仍为纯三段 SemVer 字符串，无配置、数据库或迁移变化。

## 风险

- 开发机无 Git/无标签/浅克隆时 `resolve_app_version()` 必须回退而不阻塞启动；已捕获 `OSError/SubprocessError` 并校验严格 tag 格式。
- Linux 开发机无可用产物时需走手动下载分支，避免再次误报最新。
- GitHub API 失败时 `check` 返回 502，前端显示检查失败而非最新。

## 测试计划

- `python -m pytest tests/test_updater.py tests/test_server.py tests/test_version.py -q`
- 全量 `python -m unittest discover -s tests -p "test_*.py"`
- JS 语法检查与 JS 测试（按仓库现有方式）。
- 手工：同步主线重启后端，`GET /api/update` 核对当前版，`POST /api/update/check`（mock 新版 asset）核对最新版与文案分支。

## 实际改动

- `local_proxy/updater.py`：`UpdateInfo` 新增 `newer_available`（远端有新版），`has_update` 保持“新版且本机有可用产物”的语义；无产物时 `has_update=False` 但 `newer_available=True`。
- `local_proxy/application.py`：`UpdateController` 新增 `_refresh_current_version()`，`status()/check()` 每次重算 `resolve_app_version()` 并同步 `current_version`；`status()` 同时返回 `has_update` 与别名 `update_available`，并透传 `newer_available`。
- `local_proxy/server.py`：无 controller 时的降级 payload 补齐 `update_available/newer_available/latest_version/release_url/notes`。
- `proxy_static/src/components/RuntimeView.vue`：版本行改为 `当前 X / 最新 Y`（未检查 `最新`显示 `—`）；兼容读取 `update_available ?? has_update`；检查文案三分支（可更新/手动下载/已是最新）；更新按钮仅 `有更新且 supported` 时显示。
- `proxy_static/classic/index.html` + `proxy_static/classic/app.js`：同上口径，版本行改为 `当前 vX / 最新 vY`，`check` 兼容两字段并区分手动下载分支，更新按钮沿用 `has_update && supported`（兼容别名）。
- `tests/test_updater.py`：新增无产物时 `newer_available=True` 断言、`newer_available` 跟踪用例、`UpdateController` 契约用例（别名/刷新/透传）。
- `tests/test_server.py`：`FakeUpdateController` 对齐新契约，`check` 与无 controller 用例追加新字段断言。

## 验证结果

- `.venv\Scripts\python.exe -m unittest tests.test_updater tests.test_version tests.test_server`：36 tests OK。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：534 tests OK（33s）。
- `node --check proxy_static/classic/app.js`：通过；`RuntimeView.vue` script 段抽取为 `.mjs` 后 `node --check`：通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：全部通过，0 fail。
- 行为核验：`resolve_app_version('0.1.7')` → `1.6.2`；模拟 `v1.6.2` 发布包：`1.6.1→has_update True/newer True`（提示发现新版本），`1.6.2→False/False`（已是最新），无产物→`has False/newer True`（手动下载分支）。

## PR

pending
