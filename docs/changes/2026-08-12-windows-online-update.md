+++
id = "2026-08-12-windows-online-update"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# Windows 在线更新

## 目标

为便携版提供在线更新能力：Windows 便携版可检测到 GitHub 最新发布、下载并校验新版 exe，退出后由助手替换旧文件并自动重启到新版本；其他平台检测到新版本后引导手动下载。

## 现状

程序仅有内置 `APP_VERSION`，没有任何检测最新发布或自更新的逻辑，用户只能自行去 Releases 页面手动下载替换。

## 设计范围

- 新增 `local_proxy/updater.py`：查询 `releases/latest`、semver 比对、按平台选择产物、流式下载并做 SHA-256 校验。
- 托盘新增「检查更新／更新到 vX.Y.Z」菜单项与通知；启动后后台静默检测一次。
- Windows 冻结版全自动闭环：下载校验 → 退出 → 由新版二进制以 `--finalize-update` 等待旧进程退出后替换目标文件并重启，失败回滚 `.bak`。
- 非 Windows 或源码运行：仅检测提示，跳转 Releases 页面手动更新。
- 修正 README 的仓库地址为实际发布仓库 `AI-Routing-Research-Institute/codex-provider-hub`。

## 非目标

不做 macOS 就地自更新（依赖后续代码签名与公证），不做增量更新，不做静默强制升级。

## 兼容性

无运行时协议影响。新增托盘菜单项与后台检测线程；更新产物与校验文件写入 `~/.codex-local-proxy/updates/`。检测使用 GitHub 匿名 API（60次/小时/IP），失败静默降级。

## 风险

- 便携版信任模型为 HTTPS + GitHub 发布完整性 + SHA-256 校验，无代码签名；校验不通过即中止。
- 替换失败时回滚旧版本 `.bak` 并提示。
- API 限流或离线时检测失败，仅静默跳过，不影响主服务。

## 测试计划

`python -m unittest discover -s tests -p test_*.py`，覆盖 semver 比对、发布解析、SHA-256 文档解析与文件校验、注入客户端的检测、助手命令构造与进程存活判定；并修正既有托盘菜单测试。

## 实际改动

- `local_proxy/updater.py`：新增 `UpdateInfo`、`parse_version`/`is_newer`、`parse_release`、`fetch_latest_release`/`check_for_update`、`parse_sha256_document`/`verify_file_sha256`、`download_asset`。
- `local_proxy/application.py`：新增 `update_supported`、`updates_directory`、`_spawn_detached`、`_process_alive`、`launch_update_helper`、`finalize_update`、`_relaunch_target`；托盘接入检查/更新菜单、通知与后台检测；`run_local_proxy_server` 退出后触发助手；`main` 新增隐藏 `--finalize-update/--target/--wait-pid`。
- `tests/test_updater.py`：新增更新逻辑单测；`tests/test_local_proxy_app.py`：适配新增菜单项与 `enabled` 参数。
- `README.md`：仓库地址改为实际发布仓库。

## 验证结果

`python -m unittest discover -s tests -p "test_*.py"` 全量通过（含新增 13 项 updater 用例）。

## PR

pending
