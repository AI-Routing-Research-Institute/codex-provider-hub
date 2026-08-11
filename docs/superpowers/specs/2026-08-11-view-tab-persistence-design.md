# 控制台页签持久化设计

## 根因

主导航当前完全依赖 HTML 初始状态和 `switchView()` 的内存内 DOM 更新。HTML 固定将 `providers` 标记为 active，刷新后没有任何代码读取用户上一页签，因此“请求”必然跳回“供应商”。

## 状态与存储

每个控制台服务使用独立键：

```text
local-proxy-view-<service_id>
```

值为一个经过白名单验证的字符串：`providers`、`requests`、`settings` 或 `runtime`。服务 ID 缺失时使用 `local`。存储不可访问、值非法或目标按钮隐藏时统一回退 `providers`。

## 初始化与切换

`readUiConfig()` 先应用功能开关，使隐藏页签状态可靠。随后初始化调用 `restoreView()`，由它读取本地存储并通过 `switchView(..., {persist: false})` 恢复。复用 `switchView()` 可保留请求记录和运行设置的现有惰性加载。

用户主动切换时，`switchView()` 先规范化目标页签，完成按钮及面板切换，再保存实际生效的页签。恢复过程关闭持久化，避免仅因加载页面而重写用户存储。

## 安全与兼容

存储内容不包含供应商、地址、Key、会话 ID 或查询文本，只保存固定白名单页签名。Codex 与 Claude 使用不同服务键。旧版本没有该键时继续进入供应商页。

## 测试

- 纯函数测试合法页签、非法页签及请求功能关闭回退。
- 存储键测试服务隔离。
- 源码接线测试 `switchView()` 写入和 `initialize()` 恢复调用。
- 交付前对分支 diff 执行敏感信息模式扫描，并运行完整仓库门禁。
