+++
id = "2026-09-06-codex-model-selection-passthrough"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# Codex 显式模型选择在无映射时原样透传

## 目标

Codex 桌面端、CLI 或 IDE 已在 Responses 请求体中明确选择模型时，本地中转只在当前供应商存在精确模型映射且命中该模型时改写；没有映射或映射未命中时，向上游原样发送 Codex 选择的模型。

## 现状

供应商导入配置的顶层 `model` 当前被解释为无条件模型重写。只要供应商没有模型映射表，请求体中的显式模型就会被替换为供应商配置模型。例如 Codex 选择 `gpt-6-astra`、供应商配置 `model = "gpt-5.6-sol"` 时，上游实际收到 `gpt-5.6-sol`。运行中请求显示重写后的模型，完成历史又记录原始模型，进一步造成观测歧义。

模型映射功能已经提供显式的模型名转换入口；继续将供应商顶层 `model` 作为隐式兜底会让“没有映射”反而触发模型替换，并使 Codex 的模型选择失效。

## 设计范围

- Codex Responses 转发仅在供应商模型映射精确命中时改写顶层 `model`。
- 没有模型映射或映射未命中时原样透传客户端模型。
- 供应商顶层 `model` 继续作为导入配置和启动默认模型信息保存，但不参与中转请求改写。
- 请求列表的 `model` 始终表示客户端模型；`upstream_model` 只在显式映射实际改变模型时记录远端模型。
- 运行中记录、完成历史和请求调试记录保持相同字段语义。
- 更新供应商编辑界面文案，避免继续将顶层 `model` 描述为请求重写规则。

## 非目标

- 不实现供应商模型列表与 Codex 模型选择器的动态联动。
- 不增加通配符映射、自动回退或根据上游报错猜测模型。
- 不修改已有模型映射数据，也不自动生成 `gpt-6-astra -> gpt-5.6-sol` 映射。
- 不修改 Claude 请求链路。

## 兼容性

数据库和控制 API 不需要迁移，现有供应商顶层 `model` 数据继续保留。依赖旧单值强制重写行为的供应商会改为收到客户端模型；若上游模型名不同，用户需要在现有模型映射界面明确添加本地模型到远端模型的映射。该修复恢复“无映射即透传”的一致语义，选择 `patch`。

## 风险

- 单模型供应商可能在修复后拒绝 Codex 当前选择的模型。通过上游明确错误和显式映射解决，不做静默回退。
- 转发重试切换供应商时必须从原始客户端模型重新计算，避免继承前一个供应商的映射结果。
- 当前工作区包含 DeepSeek、请求调试和取消清理的未提交改动；实现时只修改本次所需逻辑并在完整 diff 中确认没有覆盖既有工作。

## 测试计划

- Python：供应商仅设置顶层 `model`、没有映射时，请求模型原样透传。
- Python：映射精确命中时正常改写，并同时记录客户端模型与实际发送模型。
- Python：映射未命中时原样透传。
- Python：运行中请求与完成历史在无映射时都记录客户端模型，`upstream_model` 为空。
- Python：重试切换供应商后按新供应商的精确映射重新计算，无映射供应商透传原值。
- JavaScript：供应商编辑文案不再把顶层 `model` 表述为请求重写。
- 完整执行 Python 单测、JavaScript 语法检查、JavaScript 测试和前端构建。

## 结构化自审

- 需求对应：用户明确要求 Codex 选择什么就发送什么，且没有配置模型映射。
- 最小实现：删除旧单值重写分支，保留精确映射能力，不扩展动态模型目录提案。
- 可观测性：客户端模型与映射模型分别记录，避免运行中和历史含义变化。
- 兼容策略：保留配置数据；需要转换的供应商改用现有显式映射。
- 安全与数据：不触及凭据、认证头、请求正文留存和数据库 schema。

## 实际改动

- `local_proxy/core.py`：移除供应商单值 `model` 对 Codex Responses 请求的隐式重写；每次尝试只在当前供应商的模型映射精确命中时改写，请求重试和供应商切换仍从原始客户端模型重新计算。
- `proxy_static/src/components/ProvidersView.vue`、`proxy_static/classic/index.html`：将供应商字段改名为“启动默认模型”，明确说明转发时不覆盖客户端模型。
- `README.md`、`README.en.md`：将模型行为更新为“仅精确映射改写；没有映射或未命中时原样透传”。
- `tests/test_proxy_core.py`：更新旧单值重写断言，并增加 Astra 请求在供应商保存 Sol 默认值时，上游、运行中记录和完成历史均保持 Astra 的回归测试。
- `tests/local_proxy_console_ui.test.js`、`tests/local_proxy_vue_ui.test.js`：验证现代与经典供应商编辑器使用启动默认模型语义。

## 验证结果

- `\.venv\Scripts\python.exe -m unittest -v tests.test_proxy_core.ProxyAppTests.test_provider_default_model_does_not_override_explicit_request tests.test_proxy_core.ProxyAppTests.test_provider_default_model_keeps_request_records_on_client_model tests.test_proxy_core.ProxyAppTests.test_provider_default_model_does_not_change_retry_model tests.test_proxy_core.ProxyAppTests.test_unmapped_model_passes_through_when_mappings_exist` → 4 项通过。
- `node --test tests/local_proxy_console_ui.test.js tests/local_proxy_vue_ui.test.js` → 29 项通过。
- `\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"` → 578 项通过（2026-09-06）。
- 对 `proxy_static/src`、`proxy_static/classic`、`provider_status/static` 的 JavaScript 文件逐个执行 `node --check` → 10 个文件通过。
- 对 `tests/*.test.js` 逐文件执行 `node --test` → 18 个文件、94 项通过。
- `npm ci --prefix proxy_static` → 成功；npm 审计提示 1 个 moderate、1 个 high 级既有依赖告警，本次未运行破坏性自动升级。
- `npm run build --prefix proxy_static` → 成功，Vite 转换 30 个模块并生成生产资源。
- `git diff --check` → 通过，仅输出工作区既有 LF/CRLF 提示。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/94
