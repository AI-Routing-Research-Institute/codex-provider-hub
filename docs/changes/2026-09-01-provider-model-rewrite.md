+++
id = "2026-09-01-provider-model-rewrite"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 供应商级模型重写：会话内无缝切换供应商

## 目标

同一个 Codex 会话不重启、不改动 `~/.codex/config.toml` 的前提下，在网页控制台切换到任意供应商时，转发请求自动使用该供应商声明的模型名，消除切换后上游返回的 `model_not_found` / `The supported API model names are ... but you passed ...` 类 400 报错。

## 现状

- hub 路由只依据"会话固定 > 全局选中供应商"（`local_proxy/core.py` `ProviderRouter.begin_request`），请求体中的 `model` 仅用于统计，转发时原样透传。
- codex 侧模型名固定：hub 注册到 CC Switch 时硬编码 `gpt-5.6-sol`（`proxy_static/src/ccswitch.js` `DEFAULT_CC_SWITCH_CODEX_MODEL`），且 codex 启动后才读配置，运行中无法跟随切换。
- 切换供应商后模型名错配 → 上游 400 `invalid_request_error` → hub 归类为永久性业务错误原样透传，codex-cli 直接报错（用户实际遇到的 DeepSeek 上游报错即此模式）。
- CC Switch 每条 codex 配置本身携带顶层 `model` 字段，首次导入时已保留在本地目录 `raw_config` 中，但 `_proxy_provider`（`local_proxy/codex.py`）从未读取该键。

## 设计范围

1. `ProxyProvider`（`local_proxy/core.py`）新增 `model: str | None` 字段；为空表示保持现状（字节级透传）。
2. `_proxy_provider`（`local_proxy/codex.py`）读取 effective config 顶层 `model` 并传入；ccs 导入的配置自动携带各自模型，无需额外配置即可生效。
3. 本地目录（`local_proxy/provider_catalog.py`）：
   - `_build_raw_config` 在 TOML 顶层写入 `model = "..."`（顶层键须位于任何 table 头之前）；
   - `editable_fields` 暴露 `model`（从 effective config 根文档读取）；
   - `_validated_payload` 接受可选 `model`（字符串，≤240 字符；空字符串表示显式关闭重写；载荷中缺省表示保留原值，与 api_key 语义一致）。
4. 转发循环（`local_proxy/core.py`）：
   - 每次尝试按当前 `provider.model` 重写请求体 `model`（重试换供应商后按新供应商重新计算）；
   - 既有 `input[N].id` 剥离与 400 修复以重写后的请求体为源，二者可叠加；
   - 重写生效时刷新请求统计中的模型名为实际发往上游的模型，保证请求列表与用量记录可解释。
5. 控制台 UI（Vue `ProvidersView.vue` 与 classic `app.js`）供应商编辑表单增加"模型重写"输入框，创建/编辑/保存全链路往返。
6. README FAQ"返回 HTTP 503 或'模型无可用渠道'"补充模型重写指引。

## 非目标

- 不做模型名→供应商的反向路由（codex `-m`/`/model` 切换模型自动选择供应商）；当前设计下配置了重写的供应商始终重写为单一模型，多模型上游暂用重写值固定。
- 不做跨供应商会话历史清洗：旧供应商产生的 reasoning items 等历史内容若被新上游拒绝，沿用现有反应式修复机制（`InputItemIdCompatibilityStore` 及 400 修复模式），出现新的真实报错样本后再按同一模式扩展。
- 不改动 Claude 供应商链路与 Codex 客户端配置文件生成逻辑。

## 兼容性

- `model` 为空时转发行为与现状完全一致；已有本地目录记录无 schema 变更（`model` 存于 `raw_config` TOML 顶层，不新增列），无迁移步骤。
- 通过 UI 编辑既有供应商不会丢失导入时携带的模型（`editable_fields` 回显 + 载荷缺省保留语义）。
- API（`POST/PUT /control/codex/api/providers`）新增可选字段 `model`，向后兼容。

## 风险

1. 重写值与上游实际支持不符时仍会收到上游业务错误，但此时报错中的模型名是配置值，用户可据此修正；控制台请求列表展示实际发送模型便于诊断。
2. 顶层 `model` 键此前在目录中"存而不读"，本次开始参与转发；仅显式存在该键的记录行为改变，且该行为正是本功能目标。
3. 请求体从字节级透传改为条件性重写，仅当 `provider.model` 非空且请求体 `model` 与之不同时才重新序列化；与 id 剥离同样走 `asyncio.to_thread`，不阻塞事件循环。

## 测试计划

- Python 单测：
  - `_rewrite_request_model`：相同模型不重写、不同模型重写、非 JSON/无 model 键返回 None；
  - `_proxy_provider`：effective config 顶层 `model` 提取；
  - 目录：创建/编辑/回显 `model` 往返、载荷缺省保留、空字符串清除、导入记录保留模型；
  - 转发循环：上游收到重写后模型、重试换供应商后按新供应商重写、统计记录实际模型、无 `model` 配置时保持透传。
- JS：Vue 控制台表单字段往返（沿用现有 `node --test` 用例模式）。
- 完整验证：`python -m unittest discover -s tests -p "test_*.py"`、`node --test tests/*.test.js`（按 release 工作流口径）、`npm run build --prefix proxy_static`。

## 实际改动

- `local_proxy/core.py`：`ProxyProvider` 新增 `model` 字段；新增 `_rewrite_request_model` 助手（仅当请求体 `model` 为非空字符串且与目标不同时重新序列化）；转发循环每次尝试按当前供应商重写请求体模型名（重试重路由后按新供应商重算），既有 `input[N].id` 剥离与 400 修复改以重写后的请求体为源，重写生效时同步刷新请求统计中的模型名。
- `local_proxy/codex.py`：`_proxy_provider` 提取 effective config 顶层 `model`（≤240 字符）传入 `ProxyProvider`，CC Switch 导入的供应商自动生效。
- `local_proxy/provider_catalog.py`：`_build_raw_config` 支持在 TOML 顶层写入 `model`；`editable_fields` 暴露 `model`；`_validated_payload` 校验可选 `model` 字段（缺省保留原值、空字符串清除、非字符串拒绝）；`_effective_config` 重构为 `_effective_root` + `_select_provider_table` 以读取根文档。
- `proxy_static/src/components/ProvidersView.vue` 与重建后的 `proxy_static/dist/`：供应商编辑表单新增"模型重写"输入框，创建/编辑/保存全链路往返。
- `proxy_static/classic/index.html`、`proxy_static/classic/app.js`：classic 控制台同样新增该字段。
- `README.md`、`README.en.md`：FAQ"模型无可用渠道"补充模型名不被上游接受时的模型重写指引。
- 测试：`tests/test_proxy_core.py`（重写助手单测 2 个、转发循环 3 个）、`tests/test_provider_catalog.py`（目录往返/清除/非法类型 2 个）、`tests/local_proxy_console_ui.test.js`、`tests/local_proxy_vue_ui.test.js`（UI 字段往返断言各 1 个）。

## 验证结果

- `python -m unittest discover -s tests -p "test_*.py"` → Ran 521 tests, OK（2026-09-01，最终代码上完整执行）。
- `node --test tests/*.test.js` → 82 pass / 0 fail（2026-09-01）。
- `npm run build --prefix proxy_static` → 构建成功，产物 `index-CJtwgphn.js` 已提交。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/64
