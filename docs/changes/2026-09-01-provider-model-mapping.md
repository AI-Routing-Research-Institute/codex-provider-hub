+++
id = "2026-09-01-provider-model-mapping"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 支持供应商模型映射

## 目标

允许用户为每个本地供应商配置“本地模型名 -> 上游模型名”映射。本地 Codex 继续使用原模型名，代理在每次向上游发起请求前按当前供应商转换模型，供应商切换和重试对本地客户端无感。

## 现状

本地代理只读取请求体顶层 `model` 用于活动状态、请求历史和 Token 统计，实际转发时保持原请求体不变。不同供应商若使用不同的模型标识，用户必须手动修改本地 Codex 模型，或者承担上游返回模型不存在错误。

## 设计范围

- 在本地供应商目录 SQLite 中增加供应商模型映射表，启动时自动创建或迁移。
- 为每个供应商提供模型映射读取和整体保存接口，校验本地模型与上游模型均为非空字符串且本地模型不能重复。
- 在供应商管理列表增加“模型映射”入口，使用独立弹窗新增、编辑和删除映射行。
- 在每次上游尝试前按当前供应商重写 JSON 请求体顶层 `model`；无匹配映射时保持原值。
- 重试切换供应商后重新按新供应商映射，避免沿用前一个供应商的上游模型。
- 请求历史和活动请求继续记录本地模型名，实际转换关系通过供应商模型映射配置查看。
- 请求记录额外保存并在“模型”右侧独立展示请求当时实际发送的映射模型；未发生映射时显示为空值占位，历史记录不按当前配置反推。

## 非目标

- 不改写 SSE 或普通响应中的模型字段。
- 不做通配符、正则表达式、大小写模糊匹配或模型能力自动推断。
- 不修改 CCS 数据库，也不让 CCS 导入覆盖已经保存的本地映射。
- 不支持跨协议模型转换；本次仅处理 Codex Responses 请求体顶层 `model`。

## 兼容性

现有供应商默认没有映射，请求行为保持不变。SQLite 表通过 `CREATE TABLE IF NOT EXISTS` 自动初始化，无需用户手动迁移。控制 API 只新增端点和字段，不删除或改变现有字段。该功能增加新的用户能力，版本选择 `minor`。

## 风险

错误映射会导致上游模型不可用，因此保存时进行严格校验，并在界面明确展示映射方向。请求体重写必须使用 JSON 解析和序列化，只修改顶层 `model`，不得影响输入项、工具、推理强度或其他字段。重试时必须始终从原始本地模型计算，避免多次转换产生链式映射。

## 测试计划

- 覆盖映射表自动初始化、保存、覆盖、删除供应商清理和 CCS 导入保留映射。
- 覆盖请求体有映射、无映射、无效 JSON、缺失模型和非字符串模型的行为。
- 覆盖同一供应商重试及切换供应商后按各自映射重新转换。
- 覆盖控制 API 的读取、保存与参数校验。
- 覆盖现代控制台模型映射入口、弹窗、保存请求和映射行操作。
- 覆盖运行中请求及历史记录的映射模型持久化、搜索和现代/经典控制台独立列展示。
- 运行完整 Python 单测、JavaScript 测试与语法检查、Python 编译、Vite 构建和差异检查。

## 实际改动

- `local_proxy/provider_catalog.py` 将本地供应商目录 schema 升级到版本 2，新增 `provider_model_mappings` 表和排序索引，并实现映射读取、整体替换、清空和随供应商删除；CCS 覆盖导入不改动本地映射。
- `local_proxy/codex.py`、`local_proxy/core.py` 和 `local_proxy/codex_profile.py` 将映射加载到 `ProxyProvider`，在每次 Codex Responses 上游尝试前从原始请求体按当前供应商重写顶层 `model`，并保持活动请求、用量和请求历史记录本地模型名。
- `local_proxy/server.py` 新增供应商模型映射 GET/PUT 控制接口，校验空值、重复本地模型和无效数据，保存后立即热加载路由。
- `proxy_static/src/components/ProvidersView.vue`、`proxy_static/src/styles.css` 和 `proxy_static/src/components/ui/UiIcon.vue` 为现代控制台增加模型映射入口、编辑弹窗、增删保存、加载失败保护和响应式布局。
- `proxy_static/classic/index.html`、`proxy_static/classic/app.js` 和 `proxy_static/classic/styles.css` 为经典控制台增加等价管理能力，并将经典资源缓存版本更新到 CSS `v=29`、JS `v=37`。
- `tests/test_provider_catalog.py`、`tests/test_proxy_core.py` 和 `tests/local_proxy_model_mapping.test.js` 覆盖数据库迁移、CCS 保留、API 校验与热加载、请求改写、同供应商重试、供应商切换、切换到未映射供应商时清空实际模型、历史模型名和两套控制台契约。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：通过，532 项测试；仅出现既有 Starlette 依赖弃用警告。
- `Get-ChildItem -Path tests -File -Filter *.test.js | Sort-Object Name | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }`：通过，16 个 JavaScript 测试文件、34 个测试全部成功。
- `node --check proxy_static/classic/app.js`，并对 `proxy_static/src`、`provider_status/static` 下 JavaScript 文件逐个执行 `node --check`：通过。
- `.\.venv\Scripts\python.exe -m compileall -q provider_status local_proxy scripts tests`：通过。
- `npm run build --prefix proxy_static`：通过，Vite 转换 29 个模块；本地 `dist` 构建产物保留用于手动重启测试，后续交付时不提交。
- `git diff --check`：通过；新增行乱码和敏感信息扫描无命中。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/65
