+++
id = "2026-09-06-deepseek-gpt-history-compat"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# DeepSeek 与 GPT 切换时的历史和工具协议兼容

## 目标

定位已有 Codex 任务从本地映射的 DeepSeek 切回 GPT 后出现 HTTP 400 的具体输入因素，修复已确认的协议兼容缺口，支持已有任务继续使用。

## 现状

失败请求入站和上游模型均为 gpt-5.6-sol，没有模型改写。352 条输入包含 DeepSeek 阶段的 7 条 UUID 加序号形式的 encrypted_content，以及 6 条被转换为普通 function_call 的 custom exec 工具调用。上游仅返回通用 upstream_error。当前 DSML 转换器只识别顶层 tools 并固定输出 function_call，现有历史兼容只处理 item id。

## 设计范围

- 使用原失败请求的受控副本对上游做差异验证，仅接收响应，不执行模型工具调用，不修改任务记录、供应商设置和运行中服务。
- 按验证证据对发出的请求副本做有边界的历史兼容处理，保留普通消息、工具调用配对和有效 GPT 推理状态。
- 正确识别 tools 和 input.additional_tools 中的命名空间、普通函数与自定义工具；为 DSML 和 Chat Completions 转换生成正确的 Responses 类型、参数及流事件。
- 对明确无法无损转换的工具参数返回可诊断协议错误，避免猜测执行。
- 为真实失败形态、GPT → DeepSeek → GPT 往返和正常 Responses 透传补充回归测试。

## 非目标

- 不提交、推送、创建 PR、合并或发版；用户手动重启。
- 不修改本地数据库和原始任务历史，不自动执行上游测试产生的工具调用。
- 不实现任意第三方协议和推理格式的通用转换，不修改显式模型选择及现有精确映射语义。

## 兼容性

保持现有配置和数据库结构。仅针对已识别的第三方历史及转换格式修复，普通 GPT 请求继续保持原样。属于现有功能修复，版本选择 patch。

## 风险

- 错误识别推理状态可能丢失必要上下文：限制到已确认的供应商标记，保留有效 GPT 加密状态；所有修改只作用于发送副本。
- 自定义工具参数必须保持原始输入，不能把 shell 命令猜测转换为 JavaScript；参数含义不明确时显式报错。
- SSE 类型、命名空间、索引和完成事件必须一致；使用拆包和完整往返测试验证。
- 工作区已有未提交的 DeepSeek、调试记录、取消清理和模型选择修复，本次保留这些工作，不纳入 Git 交付。

## 测试计划

- 上游对照：原请求、仅处理第三方推理、仅处理旧工具历史、组合处理；根据结果缩小到具体条目，记录状态和脱敏证据。
- 单元与集成：普通函数、自定义工具、命名空间、额外工具定义、错误参数、流式及非流式转换、跨供应商重试从原请求重新计算。
- 完整 Python 单测、JavaScript 语法和全量测试、npm ci、前端构建、编译检查、完整 diff 检查。

## 结构化自审

- 需求：用户明确授权自行组装参数重试、定位具体项并修复；不提交和手动重启约束继续适用。
- 数据：凭据仅在内存中用于原供应商请求，不输出或写入报告；不保存真实会话正文为仓库测试夹具。
- 边界：先获取对照证据再确定历史处理条件，不把通用 400 一律认定为历史问题。
- 验证：本地全量检查及受控上游对比均记录真实结果，不将 HTTP 200 等同于工具执行成功。

## 实际改动

- `local_proxy/protocols/responses_history.py`：只在本次实际发送模型为 `gpt-` 系列时，过滤带 UUID 加输出序号标记且含 reasoning_text 的第三方 reasoning 条目及明确指向这些条目的引用；保留有效 GPT 推理、普通消息、工具历史和原始数据库记录。仅修改发送副本，不修改客户端请求。
- `local_proxy/core.py`：每次尝试完成精确模型映射后调用协议适配器的可选请求准备步骤；重试始终从原始请求重新计算，避免过滤后的 GPT 历史影响切回 DeepSeek。调试记录同时保留原请求和实际发送副本。
- `local_proxy/protocols/responses_tools.py`：统一识别顶层 tools、additional_tools、命名空间、Chat 风格 function 定义；区分 custom/function，并无损解包 custom 的字符串 input。缺失工具名、未知/歧义工具名、无效 JSON 和不能无损解释的额外参数显式报错。
- `local_proxy/protocols/deepseek_dsml.py`：DSML 与 Chat 流式和非流式转换按真实工具契约生成调用及参数事件；custom 使用 custom_tool_call/input 和 ctc ID，普通函数保留 function_call/arguments。Chat 参数拆包后再转换，保留前言文本和唯一输出索引；生成的调用 ID 按每次调用唯一，避免重复相同命令产生历史 ID 冲突。字符串参数保留空白、Unicode 和字面 HTML 实体。
- `tests/test_responses_compatibility.py`：新增过滤边界、模型映射、重试、真实客户端工具定义、参数保真、流式/非流式、协议错误、标准 Responses 透传和 GPT → DeepSeek → GPT 完整往返回归。
- 保留旧的 6 条 function_call 及 aborted 输出：受控请求已经证明它们不会导致本次 HTTP 400，避免对旧历史做无证据的转换。

## 验证结果

已完成定位与定向测试，完整验证进行中。

- 原请求未加入任何测试参数时仍返回 400（上游请求 ID `22504da1-6e4a-4266-b8c9-4d0ce8d6fb72`）。
- 原请求只删除 input[324,328,332,336,340,343,347] 后，gpt-5.6-sol 返回 200 且 response.completed（`7487cb91-c46b-4ef5-934a-807eab3a8709`）；全部原消息和 6 对旧 DSML 工具调用保留。
- 独立成功对照的 gpt-6-astra 和 gpt-5.6-sol 均返回 200/response.completed。向成功对照逐条插入上述 7 条 reasoning，每条均返回 400。只删除其中的 encrypted_content 或 content 字段也仍是 400，因此采用整条过滤。
- 对照还确认 max_output_tokens 会在该上游单独触发 400；早期包含此参数的实验不用于归因，后续所有正式对照均未添加该字段。产品代码不增加或修改此参数。
- 调用产品 normalize_deepseek_history 处理原请求后返回 200（`0781d2e9-6011-4185-80f0-173b087774de`），收到正常推理、文本及 custom_tool_call 事件；为限制测试生成量，在 75 秒主动关闭测试流，未执行返回的工具。
- `.venv\Scripts\python.exe -m unittest tests.test_responses_compatibility tests.test_deepseek_dsml tests.test_codex_profile -v`：30 项通过（初次定向验证）。

- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：612 项全部通过，122.860 秒，退出码 0；日志 `%TEMP%/codex-deepseek-python-full.log`。
- `scripts.team_policy._syntax_check_commands()` 所列 9 个 JavaScript 文件的 `node --check` 全部通过；构建后的 `proxy_static/dist/static/assets/index-CYbF-afU.js` 另行 `node --check` 通过，共 10 个文件。
- 对 `tests/*.test.js` 逐文件执行 `node --test`：18 个文件，95 项全部通过。
- `npm ci --prefix proxy_static`、`npm run build --prefix proxy_static`：均退出码 0，构建 30 个模块。本地构建产物已更新供手动重启使用，未暂存或提交；npm 保留既有 1 moderate / 1 high 审计提示，没有升级依赖。
- `.venv\Scripts\python.exe -m compileall -q local_proxy tests` 与 `git diff --check` 通过。
- HEAD `29f2d2b`，分支 `fix/deepseek-gpt-history-compat`，与已同步的 origin/main 无提交差异；未暂存、提交、推送或重启服务。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/94
