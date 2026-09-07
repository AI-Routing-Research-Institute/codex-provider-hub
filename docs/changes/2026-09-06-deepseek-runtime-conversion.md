+++
id = "2026-09-06-deepseek-runtime-conversion"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# DeepSeek 转换开关与工具、流式消息修复

## 目标

修复 DeepSeek 参数错误被表现为流断开重连、中间说明覆盖和消息生命周期不完整的问题。在运行设置提供可持久化的兼容转换开关，统一控制请求和响应处理，支持随时关闭。

## 现状

2026-09-06 14:52 的真实请求显示：两次 DSML exec 把 shell 参数传给 JavaScript 自定义工具，适配器发出裸 error 后关闭流，客户端提示缺少 response.completed。另一次把 git 命令当 JavaScript 执行产生语法错误，之后两次正确的 JavaScript 工具调用执行成功。上游把所有说明都标为 final_answer；转换器又在 done 事件丢掉 phase，空前言消息缺少结束事件。控制台将本地转换错误笼统显示为“上游以 error 结束响应”。

## 设计范围

- Codex 运行设置新增 DeepSeek 兼容转换布尔开关，默认开启，覆盖请求历史处理、工具指引和响应转换。关闭后停止这些转换，精确模型映射保持独立。
- 开关保存到 Codex 协议设置，后续转发即时读取；现代与经典界面均可操作，Claude 不展示该选项。
- 为可确认的 Codex JavaScript exec 增加明确工具说明；有界兼容已观察到的 shell 调用形态，使用 JSON 编码构造 tools.exec_command 调用，保留工作目录与输出/等待参数，不在代理内执行工具。
- DeepSeek 桥接消息暂存到阶段可确认后再发送，工具前言标记 commentary，普通最终回答标记 final_answer，完整关闭已开始的消息；正常 GPT 流原样通过。
- 本地协议错误输出完整 response.failed 终止事件并保留具体原因；控制台区分本地转换错误和上游错误。
- 使用真实缓存离线重放以及合成边界测试验证，不修改生产数据库和运行中服务。

## 非目标

- 不提交、推送或发布；用户手动重启。
- 不改变普通模型选择、供应商凭据、已有显式模型映射和 Claude 行为。
- 不支持任意 shell 与 JavaScript 互相猜测转换；不伪造工具执行成功或 response.completed。
- 不修改 Codex 桌面端内部渲染器。

## 兼容性

新增可选布尔字段，旧配置默认开启以延续当前兼容行为。无数据库迁移。关闭转换可恢复请求/响应原始转发（不含独立的模型映射和既有 item-id 修复）。补齐现有适配行为并增加退出开关，选择 patch。

## 风险

- 无法从 DeepSeek 首个文本块确定是否随后调用工具，因此其可见正文需要有界缓冲；推理/连接事件继续流式显示。保存前在界面说明用途。
- 命令参数必须 JSON 编码且只允许明确字段；不丢弃额外参数，不把已有 JavaScript 当 shell 执行。
- 失败终止事件必须与原 response id 匹配，序号单调且不同时声称成功。
- 开关校验必须发生在持久化前，避免非法值造成部分设置变更。

## 测试计划

- 离线重放失败记录 655、656 和成功/语法错误记录 657–660，验证消息 phase、关闭配对、工具参数及终止事件。
- 单元/集成测试覆盖命令包装保真、普通 JavaScript 透传、非法参数失败、错误明细记录、阶段缓冲、空前言及真实 GPT 流透传。
- 设置加载/保存、非法值、开关即时生效、关闭时请求响应保真、Claude 隔离及前端控件保存测试。
- 完整 Python 单测、JavaScript 语法及全量测试、npm ci、前端构建、编译和完整 diff 检查；若有偶发失败按仓库规则复跑确认并如实记录。

## 结构化自审

- 授权：用户明确要求修复并增加开关，不提交 Git，由用户手动重启。
- 数据：真实请求仅用于本地缓存重放；测试夹具使用合成内容，不提交会话正文或凭据。
- 边界：只转换明确识别的 DeepSeek 桥接行为，开关关闭可恢复透传；正常 GPT 和 Claude 不受消息阶段处理影响。
- 可观测性：明确标记本地适配失败，不能用成功终止事件隐藏故障。

## 实际改动

- `local_proxy/shared_settings.py`、`local_proxy/codex_profile.py`：持久化默认开启的 `deepseek_compatibility_enabled`，校验先于共享设置落盘，运行时动态选择适配器；Claude 不展示开关。
- `proxy_static/src/components/RuntimeView.vue`、`proxy_static/classic/index.html`、`proxy_static/classic/app.js`：两种界面均可保存开关，说明覆盖请求历史、工具调用和回复显示，模型映射保持独立。
- `local_proxy/protocols/responses_tools.py`：通过命名空间和真实工具描述识别 Codex JavaScript exec；向 DeepSeek 请求补充工具指引，仅将明确 shell 形态转换为 JSON 编码的 tools.exec_command 调用，保留参数并拒绝未知字段。
- `local_proxy/protocols/deepseek_dsml.py`：转换后暂存 DeepSeek 可见消息事件，直到工具或终止事件确定阶段，保持 added/done/completed 的 phase 一致，关闭空前言，修复 Chat 前言重复；推理事件继续流式发送。缓冲有大小上限。
- `local_proxy/core.py`：同一上游尝试的请求与响应使用同一适配器快照；完整 response.failed 终止事件保留本地错误原因并经过已有脱敏后写入调试历史。
- `tests/test_deepseek_runtime_conversion.py` 和前端运行设置行为测试覆盖切换、持久化、失败原子性、Claude 隔离、在途切换、关闭透传、历史清理、消息阶段、命令参数与错误明细；更新既有协议测试为完整失败终止契约。

## 验证结果

- 定向 Python 协议/设置测试：54 项通过；补充开关控制 GPT 历史与 Claude 隔离后新增测试文件 16 项全部通过。
- `node --test tests/local_proxy_vue_ui.test.js`：28 项通过，包含开关两种布尔值保存和 Claude 不发送该字段。
- 真实缓存记录 654–660 离线重放：654 正常 GPT 字节不变；655–659 所有工具脚本通过 `node --input-type=module --check`（仅解析，不执行），消息 added/done 配对且均为 commentary；660 无工具，消息阶段为 final_answer，所有记录均 response.completed。未请求上游或修改生产数据。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：612 项全部通过，122.860 秒，退出码 0；日志 `%TEMP%/codex-deepseek-python-full.log`。
- `scripts.team_policy._syntax_check_commands()` 所列 9 个 JavaScript 文件的 `node --check` 全部通过；构建后的 `proxy_static/dist/static/assets/index-CYbF-afU.js` 另行 `node --check` 通过，共 10 个文件。
- 对 `tests/*.test.js` 逐文件执行 `node --test`：18 个文件，95 项全部通过。
- `npm ci --prefix proxy_static`、`npm run build --prefix proxy_static`：均退出码 0，构建 30 个模块。本地构建产物已更新供手动重启使用，未暂存或提交；npm 保留既有 1 moderate / 1 high 审计提示，没有升级依赖。
- `.venv\Scripts\python.exe -m compileall -q local_proxy tests` 与 `git diff --check` 通过。
- HEAD `29f2d2b`，分支 `fix/deepseek-gpt-history-compat`，与已同步的 origin/main 无提交差异；未暂存、提交、推送或重启服务。

## 后续观察（本次修复边界）

15:54 用户新增报告：记录 757 由 DeepSeek 上游返回 HTTP 400，提示 `reasoning_text` 必须回传。记录 755 的 912 字符 reasoning 已正确保留至 756/757；记录 756 在本地读取到的流中没有新的 reasoning 事件，转换后直接产生下一次工具调用，757 的该次调用之前没有对应 reasoning。仅删除自动补出的空消息并真实重放 757 仍返回同一 400，排除空消息作为唯一根因。重新请求 756 的完整上游流得到 200/completed 且有 reasoning，但生成内容不同，不能用于证明原始 756 后续一定含 reasoning。当前 DSML 工具块闭合后立即终止读取，因此原始记录不能区分上游省略思考与转换器未读取后续思考。这一新增的连续工具调用思考回传问题尚未修复；不能以本次自动测试通过宣称它已解决。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/94
