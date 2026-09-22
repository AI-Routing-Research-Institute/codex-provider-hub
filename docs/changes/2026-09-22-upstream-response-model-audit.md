+++
id = "2026-09-22-upstream-response-model-audit"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 请求记录展示上游响应模型

## 目标

在请求记录中记录上游响应声明的模型，并在界面新增“上游响应模型”列，帮助发现实际发送模型与上游返回模型不一致的供应商或路由问题。

## 现状

当前项目只记录客户端请求模型 `model` 和模型映射后的发送模型 `upstream_model`，没有解析上游 JSON/SSE 响应中的模型字段，也无法在请求列表中识别模型不一致。

## 设计范围

- 为请求历史和运行中请求增加上游响应模型及不一致标记。
- 从 OpenAI Responses/Chat Completions、Anthropic Messages 的 JSON/SSE 响应中提取模型。
- 使用实际发送模型（无映射时回退到请求模型）与响应模型比较，并处理 `-latest`、日期后缀等稳定版本变体。
- 在现代和经典请求列表中增加“上游响应模型”列；不一致时显示醒目标记。
- 保持响应转发内容不变，解析失败或未声明模型时不影响请求完成。
- 将 `proxy_static/dist/` 标记为 Git 忽略目录，并从索引移除已跟踪的前端构建产物；发布流程继续从源码重新构建。

## 非目标

- 不根据响应模型自动切换供应商或阻断请求。
- 不保存完整请求体、响应体或凭据。
- 本次不新增模型路由配置和 Dashboard 专用统计。

## 兼容性

SQLite 启动时通过增量列迁移兼容既有数据库；新增 API 字段均为可选字段。发布或从源码运行现代控制台前需要执行前端构建，以生成被 Git 忽略的 `proxy_static/dist/`。版本选择 `minor`，因为新增了请求审计能力和 UI/API 字段，但保持原有转发协议兼容。

## 风险

- 上游可能省略或伪造模型字段，系统只能将其标记为未知或按实际声明比较。
- SSE 分块和不同协议字段结构复杂；解析器必须限长并容错，任何异常都不得影响转发。
- 历史数据库缺少新列时需要幂等迁移，避免升级失败。
- 新鲜工作区没有构建产物，直接启动现代控制台前若未执行构建会缺少 `dist/index.html`；发布工作流会显式执行构建缓解该风险。

## 测试计划

- Python 单元测试覆盖 JSON、SSE、模型归一化、三态 mismatch 和 SQLite 迁移/查询。
- Node 测试覆盖现代/经典请求列表列数、字段渲染和不一致标记。
- `node --check`、前端 `npm ci`/构建及仓库全量 Python/JS 测试。

## 实际改动

- `local_proxy/response_models.py` 新增 OpenAI/Anthropic/Gemini 模型字段提取、版本归一化和 mismatch 三态判断。
- `local_proxy/core.py` 与 `local_proxy/protocols/claude_messages.py` 解析 JSON/SSE、更新运行中请求、迁移 SQLite 字段并通过请求 API 返回审计结果。
- `proxy_static/src/components/RequestsView.vue`、`proxy_static/classic/*` 新增“上游响应模型”列；不一致显示橙色“模型不一致”标签。
- `.gitignore` 忽略 `proxy_static/dist/`，并将原先已跟踪的构建产物移出 Git 索引但保留本地构建文件。
- `tests/test_response_models.py`、`tests/local_proxy_requests.test.js`、`tests/local_proxy_vue_ui.test.js` 增加解析、持久化和 UI 覆盖。

## 验证结果

- `python -m unittest discover -s tests -p 'test_*.py'`：620 个测试通过。
- `node --check`：仓库全部 JavaScript 文件通过。
- `node --test tests/*.test.js`：96 个测试通过。
- `npm ci`：依赖安装成功（npm 报告 1 个 moderate、1 个 high 漏洞，未自动升级依赖）；`npm run build`：Vite 生产构建成功。
- `git diff --check`：通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/97
