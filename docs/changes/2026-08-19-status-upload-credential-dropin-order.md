+++
id = "2026-08-19-status-upload-credential-dropin-order"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复上传凭据未注入检测服务

## 目标

确保新上传供应商的凭据会注入状态服务 Worker，自动和即时检测均可产生状态。

## 现状

Worker 现有的凭据 drop-in 会清空此前 `LoadCredential` 列表。上传功能创建的 `90-` 文件加载顺序过早，导致已写入的新增凭据未注入运行目录，Worker 读取凭据失败并反复重启。

## 设计范围

- 新增凭据 drop-in 排在现有凭据重置规则之后。
- 回归测试锁定 drop-in 文件加载顺序。
- 服务端迁移现有 drop-in 并恢复 Worker。

## 非目标

- 不更改已有供应商凭据、探测逻辑或公开状态 API。
- 不改变 SSH 上传认证与权限模型。

## 兼容性

已经导入的供应商会在一次 systemd 重载和 Worker 重启后读取原有凭据，无需重新上传。

## 风险

依赖现有 drop-in 的命名顺序；使用 `zzzzz-` 前缀确保在当前 `zzzz-local-provider-credentials.conf` 后追加凭据。

## 测试计划

运行状态上传单测、完整 Python/JavaScript 测试、语法检查和打包 smoke-test；在服务器验证凭据目录、Worker 状态与即时检测结果。

## 实际改动

- `scripts/status_provider_import.py`：新增凭据 drop-in 改为 `zzzzz-imported-providers.conf`，确保在既有凭据重置规则后加载。
- `tests/test_status_provider_upload.py`：新增 drop-in 加载顺序回归测试。
- 服务器已将现有 `90-imported-providers.conf` 迁移至新文件名并重新加载 Worker。

## 验证结果

已通过：`python -m unittest discover -s tests -p "test_*.py"`（452 项）；全部 `tests/*.test.js`；`node --check proxy_static/app.js`；`git diff --check`；修复后 PyInstaller EXE `--smoke-test`。服务器 Worker 为 `active`，导入凭据已注入 `/run/credentials`，`https://cpa.largecabbage.cn/v1` 的即时检测任务完成并写入公开状态。

测试包：`.tmp-dist-status-upload-worker-fix/CodexLocalProxy-win-x64.exe`。

SHA-256：`6680456e6ec6bbe54b5c62463dde7dd0ac1f7422116319dffcb1852c16d57eeb`。

## PR

pending
