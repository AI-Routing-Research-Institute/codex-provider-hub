+++
id = "2026-08-25-fix-provider-launch-command-visibility"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复供应商临时启动命令显示开关

## 目标

关闭“供应商临时启动命令”设置后，供应商页立即隐藏“复制临时启动命令”按钮；重新开启后立即恢复。

## 现状

运行设置已正确持久化 `show_provider_launch_command`，但供应商页只判断 `features.provider_launch_command` 功能能力字段，导致用户开关不影响按钮显示。

## 设计范围

- 由应用根组件维护临时启动命令的用户可见性状态。
- 设置页读取和保存运行设置时，将最新开关值同步给根组件。
- 供应商页同时判断功能能力与用户设置，决定是否显示复制按钮。
- 增加 Vue UI 契约测试覆盖状态传递和双条件判断。

## 非目标

- 不修改临时启动命令生成逻辑或接口权限。
- 不修改其他供应商操作按钮、设置项和页面样式。
- 不处理移动端布局。

## 兼容性

不改变接口和持久化格式。默认值仍为开启；只修复已保存为关闭时的前端显示行为。

## 风险

运行设置读取失败时可能无法得知用户偏好；保持默认开启，与既有默认行为一致，并由现有错误提示告知读取失败。

## 测试计划

- 更新并运行 Vue UI 契约测试。
- 运行全部 Node 测试和 Python 单元测试。
- 运行前端生产构建、JavaScript/Python 语法检查和 `git diff --check`。

## 实际改动

- `App.vue` 维护临时启动命令的可见性状态，并在设置页与供应商页之间传递。
- `RuntimeView.vue` 在运行设置读取或保存成功后上报最新开关值。
- `ProvidersView.vue` 同时判断后端功能能力和用户设置，仅在二者都允许时显示复制按钮。
- `tests/local_proxy_vue_ui.test.js` 增加状态传递与双条件显示的契约断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：10 项通过。
- `node --test $testFiles`：15 项通过。
- `\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：481 项通过。
- `npm run build`：Vite 生产构建通过，17890 页面已加载新资源 `index-Diui-ypT.js`。
- `node --check proxy_static/src/api.js`、`node --check proxy_static/src/ccswitch.js`：通过。
- `\.venv\Scripts\python.exe -m py_compile local_proxy/codex_profile.py local_proxy/claude_profile.py local_proxy/core.py local_proxy/server.py local_proxy/application.py`：通过。
- 浏览器实测：运行设置开关为关闭，供应商页“复制临时启动命令”按钮数量为 0，当前资源无控制台警告或错误。
- `git diff --check`：通过。

## PR

pending
