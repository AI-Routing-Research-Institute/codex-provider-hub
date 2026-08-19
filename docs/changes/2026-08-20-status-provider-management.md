+++
id = "2026-08-20-status-provider-management"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 状态服务供应商管理

## 目标

在本地控制台管理远程状态服务上的全部供应商，支持排序、删除、刷新和立即检测。

## 现状

当前只能上传供应商，无法管理服务器已有配置；删除和排序需要手动登录服务器操作。

## 设计范围

- 新增“监控管理”页面。
- 通过受限 SSH 查看和修改全部远程供应商。
- 支持排序、删除、立即检测和状态刷新。
- 删除时清理配置、凭据和公开状态记录，并在失败时回滚。

## 非目标

- 不开放公网管理 API。
- 不修改本地 Codex/Claude 供应商目录。
- 不改变状态探测算法。

## 兼容性

无顺序文件时保持远程原有配置顺序；现有上传 SSH 密钥和导入协议继续可用。

## 风险

删除属于破坏性操作，必须二次确认；Worker 重启失败时恢复原文件并返回错误。

## 测试计划

覆盖远程管理动作、顺序加载、删除回滚、前端管理页和完整打包验证。

## 实际改动

- `provider_status/config.py`：读取 providers.d/.order.json 并按服务器顺序加载。
- `scripts/status_provider_import.py`：增加 list/order/delete 管理动作，删除同步清理凭据、顺序、systemd 凭据清单和公开状态记录；Worker 重启失败时恢复修改前状态。
- `local_proxy/status_upload.py`、`local_proxy/server.py`：增加受限 SSH 管理 API，并由本地后端代理立即检测请求，避免浏览器跨域预检失败。
- `proxy_static/index.html`、`proxy_static/app.js`、`proxy_static/styles.css`：新增监控管理页面、排序、删除、立即检测、检测进度和刷新操作。
- 服务器已部署新版受限管理命令，真实列表和排序操作验证通过。

## 验证结果

已通过：`python -m unittest discover -s tests -p "test_*.py"`（461 项）；全部 `tests/*.test.js`；`node --check proxy_static/app.js`；`git diff --check`；PyInstaller EXE `--smoke-test`。新包在独立 `17892` 端口启动，浏览器验证监控管理页显示服务器全部 8 个供应商且无布局溢出；真实服务器管理列表和立即检测成功，管理列表不返回凭据名称，Worker 状态数据保持 fresh。

测试包：`.tmp-dist-status-management-final/CodexLocalProxy-win-x64.exe`。

SHA-256：`6b485cfbd7c10c286a6d144efc2148886fb7b44583107fa036933636925e58d0`。

## PR

pending
