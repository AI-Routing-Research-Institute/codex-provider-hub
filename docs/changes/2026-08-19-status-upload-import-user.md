+++
id = "2026-08-19-status-upload-import-user"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复上传阶段 SSH 账号

## 目标

修复服务器初始化成功后，供应商实际上传仍返回 `Authentication failed.` 的问题。

## 现状

本地保存的 `ubuntu` 账号仅用于首次密码引导。初始化生成的私钥授权给受限导入账号 `codex-status-import`，但上传阶段错误复用了 `ubuntu`，导致私钥认证失败。

## 设计范围

- 上传阶段固定使用受限导入账号。
- 首次初始化继续使用用户填写的 SSH 账号。
- 增加上传连接账号回归断言。

## 非目标

- 不改变服务器地址、端口、主机指纹、私钥位置或远端权限策略。
- 不改变 Codex/Claude 请求转发。

## 兼容性

已完成初始化的本地配置无需重新输入密码；升级后可立即使用已有专用私钥上传。

## 风险

受限账号名称与远端引导脚本保持一致；认证失败仍会明确返回给界面，不会写入远端配置。

## 测试计划

运行状态上传单测、完整 Python/JavaScript 测试、语法检查、打包冒烟及真实供应商上传。

## 实际改动

- `local_proxy/status_upload.py`：首次引导使用用户账号，后续上传固定使用 `codex-status-import`，并在发送 JSON 后关闭 SSH 写端。
- `scripts/status_provider_import.py`：安装远端导入器时将 Windows 换行规范为 Linux LF，避免 shebang 执行失败。
- `tests/test_status_provider_upload.py`：覆盖受限账号、换行规范化和 EOF 发送。

## 验证结果

已通过：状态上传单测 6 项；修复后 PyInstaller EXE 启动成功；真实 SSH 初始化返回 HTTP 200；真实供应商上传到达远端导入器并按重复 `provider_id` 返回 HTTP 409，确认认证和导入执行链路正常。

测试包：`.tmp-dist-status-upload-final-eof-fix/CodexLocalProxy-win-x64.exe`。

SHA-256：`3460eb10922a849910a73cbee305e9aa983d102a756674326725d2cc3e4c49b8`。

## PR

pending
