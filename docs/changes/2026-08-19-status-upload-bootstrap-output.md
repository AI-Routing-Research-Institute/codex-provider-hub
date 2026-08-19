+++
id = "2026-08-19-status-upload-bootstrap-output"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复状态服务初始化误报失败

## 目标

修复首次上传供应商时，远端初始化已完成但本地界面显示 `502 Bad Gateway` 的问题。

## 现状

远端 `visudo -cf` 会在 JSON 成功结果之前输出诊断文本。客户端将整个标准输出作为单个 JSON 解析，导致初始化成功被错误判为失败，本地密钥和初始化状态没有保存。

## 设计范围

- 客户端兼容最后一行有效 JSON 结果。
- 远端引导命令静默 `visudo` 的成功诊断输出。
- 覆盖带命令诊断文本的首次初始化回归用例。

## 非目标

- 不改变 SSH 凭据、远端账号权限或供应商导入协议。
- 不修改现有 Codex/Claude 转发行为。

## 兼容性

已经部署旧导入器的服务器可由新版客户端完成初始化；新版导入器保持单一 JSON 协议输出。

## 风险

客户端只接受最后一个可解析的 JSON 对象，无法解析时仍明确报错，不会将初始化状态写入本地。

## 测试计划

运行状态上传单测、完整 Python/JavaScript 测试、语法检查和新 PyInstaller 包的启动及首次初始化验证。

## 实际改动

- `local_proxy/status_upload.py`：读取远端命令输出的最后一条 JSON 结果。
- `scripts/status_provider_import.py`：静默 sudoers 校验成功输出。
- `tests/test_status_provider_upload.py`：新增带诊断文本的初始化回归测试。

## 验证结果

已通过：`python -m unittest discover -s tests -p "test_*.py"`（450 项）；全部 `tests/*.test.js`（通过）；`node --check proxy_static/app.js`；`git diff --check`；修复后 PyInstaller EXE `--smoke-test`；新 EXE 在 `17893` 端口真实 SSH 初始化返回 HTTP 200，并成功保存本地 `status-upload.json` 与 Ed25519 私钥。

测试包：`.tmp-dist-status-upload-bootstrap-fix/CodexLocalProxy-win-x64.exe`。

SHA-256：`129559ebdbd8be7fbfbf97b652044112ad903071db7eb39702a39e1f57b2fad6`。

## PR

pending
