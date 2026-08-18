+++
id = "2026-08-18-status-provider-upload"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 供应商上传到状态服务

## 目标

在本地代理控制台的供应商列表中提供上传入口，将可检测的供应商安全发送到远程 Codex 状态服务并开始监测。

## 现状

状态服务的供应商配置需要人工编辑服务器文件和凭据。桌面控制台没有将 CC Switch 供应商同步到状态服务的能力。

## 设计范围

- 首次通过 SSH 密码自动初始化受限导入账号和密钥。
- 供应商行提供上传按钮与模型选择弹窗。
- 通过 SSH 发送标准 Key/Auth Token 配置。
- 服务端以独立 providers.d 片段和 0600 凭据文件保存配置。
- 重复 provider_id 直接拒绝，不覆盖已有配置。
- Worker 重载后由公开状态页展示首次检测结果。

## 非目标

- 不支持自定义 HTTP 请求头鉴权供应商。
- 不通过公开 HTTP 接口接收 API Key。
- 不提供远程删除或修改已有供应商配置。
- 不改变现有 Codex/Claude 请求转发协议。

## 兼容性

现有本地控制台和状态服务配置继续可用；新增服务端 providers.d 目录为空时行为不变。首次初始化需要服务器用户具备 sudo 权限。

## 风险

SSH 初始化失败、主机指纹变化、远程服务重启失败和重复 ID 必须可识别并保持原配置不变。上传凭据只经过 SSH 加密通道，不进入浏览器日志和公开数据库。

## 测试计划

覆盖导入 payload 校验、重复拒绝、TOML 片段加载、凭据类型、初始化命令、安全路径校验、模型建议和前端上传状态；运行完整 Python/Node 测试与 PyInstaller 冒烟检查。

## 实际改动

- `local_proxy/status_upload.py`：保存 SSH 初始化状态、生成 Ed25519 密钥、校验主机指纹，并通过受限导入命令上传配置。
- `local_proxy/server.py`、`local_proxy/application.py`、两套 profile：接入初始化、模型预览和上传 API。
- `proxy_static/index.html`、`app.js`、`styles.css`：供应商行上传按钮、模型编辑弹窗和一次性 SSH 初始化表单。
- `scripts/status_provider_import.py`：远端原子写入 providers.d/凭据、维护 systemd LoadCredential drop-in、失败回滚和并发锁；初始化时同步 Worker 支持模块。
- `provider_status/config.py`、`provider_status/claude_probe.py`：读取配置片段并支持 Claude Auth Token。
- PyInstaller 资源和 `requirements-status.txt`：包含 SSH 依赖及远端初始化支持文件。

## 验证结果

已通过：`python -m unittest discover -s tests -p "test_*.py"`（449 项）；全部 `tests/*.test.js`（44 项）；`node --check proxy_static/app.js`；`git diff --check`；源码和 PyInstaller 打包后的 `--smoke-test` 均通过。

## PR

 pending
