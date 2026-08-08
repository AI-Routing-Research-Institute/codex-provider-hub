+++
id = "2026-08-08-runtime-launch-display-request-table"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 临时启动命令显示设置与请求记录布局优化

## 目标

允许用户在运行设置中控制供应商卡片是否显示“复制临时启动命令”，并提升大量请求记录下的浏览效率和视觉层级。

## 现状

Codex 供应商卡片始终显示临时启动命令按钮，名称较长时会挤压卡片内容。请求记录表格在宽屏中列距过大、行高偏高，状态、供应商和结果的视觉权重接近，难以快速扫读大量记录。

## 设计范围

- 在 Codex 运行设置中增加临时启动命令显示开关，默认开启、保存后立即生效并持久化。
- Claude 页面不显示不适用的开关，现有临时启动命令接口和权限边界保持不变。
- 压缩请求记录行高，重新分配列宽并限制超宽布局的内容跨度。
- 强化会话主信息、弱化重复元数据，以紧凑状态标记和结果标记区分成功、失败与运行中。
- 保持表头吸顶、数字对齐、超长内容省略和完整内容悬停提示。

## 非目标

- 不改变请求历史的保存期限、分页、筛选或排序逻辑。
- 不关闭或删除临时启动命令后端接口。
- 不修改供应商路由、重试和服务器检测行为。

## 兼容性

新增可选布尔配置，缺失时按 `true` 处理，现有配置无需迁移。接口仅增加字段，不删除或改变已有字段。属于向后兼容的用户功能增强，版本选择 `minor`。

## 风险

- 配置字段若未正确归入 Codex 协议设置，可能误影响 Claude；通过协议级持久化和双控制页测试规避。
- 表格压缩可能使长文本难以辨认；通过弹性主列、单行省略和原生标题提示保留完整信息。
- 宽屏和窄屏布局可能不一致；通过响应式样式和前端渲染测试覆盖关键结构。

## 测试计划

- 运行协议设置读写、运行设置 API 与统一服务器测试。
- 运行请求记录和临时启动命令前端 Node 测试。
- 运行完整 Python、Node 测试，JavaScript 语法检查、Python compileall 和 diff 检查。
- 人工重启后确认开关即时隐藏/显示按钮，请求表头吸顶、滚动和失败样式正常。

## 实际改动

- `local_proxy/shared_settings.py`、`local_proxy/server.py` 与 `local_proxy/codex_profile.py` 增加协议级布尔配置的兼容读写、运行设置快照和即时保存回调；配置缺失时默认显示临时启动命令。
- `proxy_static/index.html` 与 `proxy_static/app.js` 在 Codex 运行设置中增加显示开关，保存后立即重绘供应商列表；Claude 根据功能配置隐藏该项。
- 供应商按钮仅在运行设置加载完成后渲染，避免已关闭按钮在页面刷新时短暂闪现；设置页复选框统一为保留原生语义的滑动开关。
- `proxy_static/app.js` 与 `proxy_static/styles.css` 压缩请求行、收拢宽屏内容、重新分配列宽，并使用紧凑状态和结果标识提升扫读效率。
- `tests/test_shared_settings.py`、`tests/local_proxy_copy_command.test.js` 与 `tests/local_proxy_requests.test.js` 覆盖配置默认值与持久化、前端开关链路和精简后的记录标签。

## 验证结果

- `\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：367 项通过。
- `node --test <tests/*.test.js>`：27 项通过。
- `node --check proxy_static/app.js`：通过。
- `\.venv\Scripts\python.exe -m compileall -q local_proxy tests`：通过。
- `git diff --check`：通过，仅出现仓库既有的 LF/CRLF 转换提示。
- 刷新闪烁门禁、滑动开关和静态资源缓存版本更新后，重新执行上述完整验证，结果保持 367 项 Python、27 项 Node 全部通过。

## PR

pending
