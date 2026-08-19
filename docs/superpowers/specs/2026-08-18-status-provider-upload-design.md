# 供应商上传到状态服务设计

## 目标

从本地控制台一键把可检测供应商导入远程 `provider_status` 服务。首次使用通过密码完成 SSH 初始化，之后使用专用受限密钥执行唯一的导入命令。

## 组件

- 本地 `proxy_static/app.js`：渲染上传按钮、模型选择和结果状态。
- 本地 `local_proxy/server.py`：提供初始化、预览和上传控制接口；凭据只由后端读取。
- 本地 `local_proxy/status_upload.py`：SSH 客户端、密钥存储和请求编排。
- 服务器 `scripts/status_provider_import.py`：校验 JSON、拒绝重复、原子写入配置片段和凭据、重载 Worker。
- 服务器 `provider_status/config.py`：读取主配置旁的 `providers.d/*.toml` 片段。

## 初始化

用户在本地设置中填写服务器地址、SSH 端口、用户名和一次性密码。客户端校验并保存主机指纹，生成 Ed25519 密钥，通过密码连接并安装受限账号、公钥 forced-command 和精确 sudo 规则。密码只存在于本次请求内；私钥存放在本地应用数据目录，权限限制为当前用户。

## 上传流程

前端只发送 provider ID、协议和模型选择到本地后端。后端从只读 CC Switch 数据库加载完整配置和凭据，拒绝无凭据、非标准 Key/Auth Token 或自定义请求头供应商，然后通过 SSH 将结构化 JSON 发送到导入器。导入器使用 provider ID 作为唯一键；已有 ID 返回重复错误，不触碰任何文件。

新配置写入独立 `providers.d/<safe-id>.toml`，凭据写入 `/etc/codex-provider-probe/secrets/<safe-name>`，并维护 Worker 的 `LoadCredential` drop-in。所有写入使用临时文件、文件锁和原子替换；Worker 重载失败时删除本次文件并恢复 drop-in。

## 安全

SSH 是唯一上传通道；不新增明文 HTTP 管理接口。公钥使用 `restrict`、forced-command 和 `sudo -n` 限制，不能取得 Shell、端口转发或读取任意文件。服务端公开数据库只包含脱敏状态。导入器严格校验 provider ID、URL、模型、协议和 JSON 字段，拒绝路径穿越与额外字段。

## 验证

测试覆盖本地模型建议和按钮状态、SSH 初始化/上传的 fake transport、服务端配置合并和重复拒绝、凭据类型、原子回滚以及现有状态服务回归。部署验证只检查状态页和服务健康，不在测试中使用真实 API Key。
