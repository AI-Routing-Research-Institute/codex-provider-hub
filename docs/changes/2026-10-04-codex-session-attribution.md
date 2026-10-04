+++
id = "2026-10-04-codex-session-attribution"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 请求记录识别会话用途并关联主会话

## 目标

让请求记录中的名称生成、会话摘要、子 agent、自动审查、侧边聊天和关联分支显示所属会话及具体用途，减少无说明的“未知会话”。运行中请求与历史请求采用相同规则，现代和经典控制台展示一致。

方案已实现并通过全量验证；用户已手动重启，已核验在线接口与前端资源。按用户授权进入功能分支 / PR 交付，不自动发版。

## 现状

- 当前主线基线：`bd0280c7c7abe47a944faf17c584b044572bdd05`。
- `local_proxy/core.py` 只从 `x-codex-turn-metadata` 读取自身 `thread_id`；`local_proxy/codex_sessions.py` 只从 `session_index.jsonl` 查找会话名称。
- 当前请求历史未持久化父会话、来源和 agent 信息；临时分叉没有名称索引记录时显示“未知会话”。
- 已核实的两条摘要请求带有 `forked_from_thread_id`，且 `thread_source`、`turn_trigger` 都为 `thread_description`。
- 本地 `state_5.sqlite` 中存在 21 条普通子 agent 父子关系，这些子 agent 均不在名称索引中；其任务路径和昵称可从 `threads` 取得。
- 自动审查 agent 的来源包含 `guardian`，其 rollout 首条 `session_meta` 记录包含 `parent_thread_id`。
- 侧边聊天的完整请求字段尚未取得现场样本；已见到一个无名称索引的分叉及 `thread/fork` 日志，不能仅凭该日志认定它就是侧边聊天。
- 调试记录保留时间短于请求历史，部分临时会话及原始请求已经消失，历史补全存在明确边界。

## 设计范围

### 1. 展示方案

保留现有“会话”列，在同一单元格中采用两行展示：第一行是可识别的会话名称，第二行是用途或关联关系。普通主会话只有第一行。使用现有表格宽度，长名称截断，悬停显示完整文字。

| 场景 | 第一行 | 第二行 |
| --- | --- | --- |
| 普通主会话 | 经营合同数据源切换 | 无 |
| 后台摘要 | 经营合同数据源切换 | 会话摘要 |
| 明确识别的名称生成 | 经营合同数据源切换 | 会话命名 |
| 普通子 agent | 项目一体化 - 后端 | 子 agent · review_upload_recovery |
| 多层子 agent | 最上层主会话名称 | 子 agent · 当前任务名；悬停展示中间父会话 |
| 自动审查 | 所属主会话名称 | 自动审查 |
| 明确识别的侧边聊天 | 所属主会话名称 | 侧边聊天 · 分支名称（存在时） |
| 有自身名称的普通分叉 | 分叉自身名称 | 来自：直接父会话名称 |
| 有父会话、未识别具体用途的分支 | 所属主会话名称 | 关联分支 |
| 上下文压缩 | 所在会话名称 | 上下文压缩；属于子 agent 时附加任务名 |
| 已知用途、无法找到所属会话 | 会话摘要 / 子 agent / 自动审查等用途名称 | 所属会话未识别 |
| 既无名称又无用途或关联信息 | 未识别会话 | 来源信息不足 |

标签使用低强调样式；来源不明使用中性色。关联缺失不按请求失败或模型不一致着色。

### 2. 用途与关联独立判断

“这个请求做什么”和“它来自哪个会话”分别解析，允许只确认其中一项。

- `thread_description`：识别为会话摘要，使用已验证的 `thread_source` / `turn_trigger`。
- 会话命名、侧边聊天：只在客户端提供明确来源标记或本地元数据明确说明类型时标注；实施时根据现场样本补齐映射。没有明确类型时降级为“关联分支”。
- 普通子 agent：依据 `thread_spawn_edges` 或 `source.subagent.thread_spawn` 识别；任务名优先取 `agent_path` / 请求中的 `agent_name` 路径末段，再使用 `agent_nickname`。
- 自动审查：依据明确的 `guardian` 来源识别，不能把所有 `subagent` 都称为自动审查。
- 上下文压缩：依据 `request_kind=compaction` 识别，属于请求用途；保留其原有主会话或子 agent 关系。
- 不通过模型名、时间邻近、Token 数、相同目录或 `context_window_id` 猜测来源。
- `root_turn_id` 是轮次 ID，不能作为主会话 ID；`session_id` 也不能默认视为父会话 ID。

### 3. 关联与名称的数据来源

按用途解释每一种关系，不能把所有分叉都当成执行任务的子 agent：

1. 请求头中的明确关系：`parent_thread_id`、`forked_from_thread_id` 和客户端明确提供的根会话 ID。执行父关系与历史分叉关系分别保留。
2. Codex 本地数据库：只读查询 `thread_spawn_edges` 以及 `threads.source`、`name`、`title`、`agent_path`、`agent_nickname`。
3. 名称索引：`session_index.jsonl` 中的最新 `thread_name`；作为现有显示名称的优先来源，数据库 `name` / `title` 用于补充。
4. 已知 rollout 文件：仅在数据库给出文件路径而关系字段不足时，限量读取首条 `session_meta`，用于补充 guardian 等父关系；不扫描对话正文。
5. 本地历史名称快照：主会话被删除或本地索引暂时不可读时，使用请求记录中保存的名称。

父链最多追溯 16 层，并检测循环。信息冲突时，优先使用与当前请求类型相符的明确执行关系；无法消解的冲突保留已确认部分，不任选一个主会话。新请求携带的明确父关系不依赖临时会话是否落入数据库。

尊重 `CODEX_HOME`；动态发现可读的 `state_*.sqlite`，按实际表结构检查字段。数据库只读、短超时、按所需 ID 查询，使用有界缓存；SQLite、文件读取和历史补全在工作线程中执行，避免阻塞异步转发。

### 4. 请求身份与显示归属

请求的自身 `thread_id`、`session_key`、并发控制、重试、模型、供应商和 Token 仍用于该真实请求。父会话及根会话是额外关联字段，不能替换自身 ID。

后台摘要与主会话可以同时请求；多层子 agent 可以并发；取消一个请求不能取消同一主会话下的其他请求。归属变化不自动改变会话路由、供应商继承规则或 Token 统计口径。

### 5. 持久化和 API

在 `request_history`、`inflight_requests` 通过幂等增量迁移新增可空字段：

- `parent_thread_id`、`root_thread_id`：已确认的直接父关系和最上层归属。
- `root_session_name`：根会话名称快照，用于历史回退和搜索。
- `session_kind`：主会话、摘要、命名、子 agent、审查、侧聊、分支或未识别。
- `request_kind`：保留请求用途，例如 `turn` / `compaction`。
- `agent_name`、`agent_nickname`：用于子 agent 标签。
- `session_context_json`：仅保存经过白名单筛选、限长的来源、触发方式、分叉关系与解析依据，不存完整请求头、提示词或正文。

增加按根会话和时间查询的索引。请求开始、完成、取消及进程重启转历史时都保留归属信息。已查明的关联需要在本地持久化，避免代理重启或临时会话销毁后失效。

保留现有 API 字段，新增可选字段：

| API 字段 | 含义 |
| --- | --- |
| `session_display_name` | 本方案第一行文字 |
| `session_label` | 本方案第二行文字 |
| `session_tooltip` | 名称、用途及已确认的直接父会话名称，便于多层关系悬停查看 |
| `session_kind` | 规范化类型，前端不按模型自行猜测 |
| `parent_session_key` | 直接父会话的散列 key，无法确认时为空 |
| `root_session_key` | 根会话的散列 key，无法确认时为空 |
| `root_session_name` | 已确认的根会话名称 |
| `agent_name` / `agent_nickname` | agent 的任务名 / 昵称 |

`session_name`、`session_key` 保持原有含义。原始父会话 ID、根会话 ID 和读取到的本地文件路径不向控制台 API 暴露。前端缺少新字段时继续使用现有字段。

### 6. 搜索与历史补全

- 搜索主会话名称时，同时匹配其已确认关联的摘要、审查、子 agent 和分支请求；支持搜索 agent 任务名及用途标签。
- 过滤在后端查询阶段执行，保证总条数、翻页和游标一致，不能加载一页后才在前端筛掉。
- 当前名称优先用于显示。主会话更名后的搜索可通过已解析的会话 ID 集合关联查询，同时保留历史名称快照。
- 对保留期内缺少归属的旧记录，在历史查询的工作线程中分批、幂等补全。每批最多 200 条，元数据处理循环预算 250ms（单次只读操作可能略超预算），每批间隔至少 2 秒，完整扫描后间隔 60 秒。先使用稳定的本地父子关系；保留的调试记录只读白名单元数据。首次查询尚未轮到的旧记录会在后续刷新逐批补全。
- 调试记录按实际会话及请求的唯一匹配补全；不能用同一会话某次请求的 `compaction` 等用途覆盖全部历史请求。不能唯一匹配的记录跳过。
- 临时分支已删除且原始元数据过期时，保持“未识别会话 / 来源信息不足”。不用现有大模型追加请求推断归属。

### 7. 预计修改文件

| 文件 | 计划修改 |
| --- | --- |
| `local_proxy/session_attribution.py`（新增） | 请求元数据解析、白名单校验、类型归一化及归属数据结构 |
| `local_proxy/codex_sessions.py` | 增加结构化归属解析，读取名称、数据库父子关系及必要的 session_meta；保留现有名称接口 |
| `local_proxy/core.py` | 贯通请求生命周期、迁移、持久化、搜索及请求 API |
| `local_proxy/codex_profile.py`、`local_proxy/server.py` | 接入 Codex 归属解析回调，其他协议使用兼容的空结果 |
| `local_proxy/request_debug.py` | 提供有界历史元数据读取用于补全，不扩展正文采集 |
| `proxy_static/src/components/RequestsView.vue`、`proxy_static/src/styles.css` | 现代控制台两行会话单元格 |
| `proxy_static/classic/app.js`、`proxy_static/classic/styles.css` | 经典控制台同等展示 |
| `tests/test_session_attribution.py`（新增）及相关现有测试 | 元数据、关系、迁移、并发、搜索和两套 UI 的行为覆盖 |

## 非目标

- 本轮不新增折叠会话树、请求类型筛选器、会话详情弹窗或 Token 汇总面板。
- 不调整主会话与子 agent 的模型选择、自动派生策略、供应商路由继承或计费。
- 不修改 Codex 自身数据库、会话名称或对话文件。
- 不读取完整聊天内容，也不额外调用模型生成名称或猜测关系。
- 不承诺客户端未提供元数据、文件已删除的所有历史请求都能恢复归属。

## 兼容性

API 为可选字段扩展；旧数据库通过幂等新增列兼容。旧客户端继续读取原有字段，Claude 控制台和缺少 Codex 来源信息的请求沿用兼容回退。两套控制台使用同一后端结果；不提交 `proxy_static/dist/` 生成产物。

版本建议为 `minor`：增加请求来源识别、主会话关联和搜索能力，不移除原有 API 字段。

## 风险

- Codex 来源字段和数据库 schema 可能变化：采用字段探测、明确映射和可识别部分的回退，未知字段不导致代理失败。
- 名称索引写入较晚或主会话更名：使用短缓存、动态名称优先及持久化快照。
- WAL 模式下数据库主文件时间戳不能代表最新内容：缓存按短 TTL 刷新，不只观察主文件 mtime。
- 误把普通分叉认作侧聊或子 agent：用途与关系独立判断，只有明确来源才标具体类型。
- 错误关系、循环或超深父链：校验字段长度、控制字符，检测循环并限制深度，保留已确认部分。
- 本地数据库锁定或不可读：只读短超时，辅助查询失败回退到请求字段和快照，不能阻塞转发。
- 同一主会话下请求被错误合并：实际请求身份独立保留，专门验证并发、取消和路由。
- 历史补全错配或拖慢刷新：唯一匹配、批次上限、后台补全，不在每次分页时扫描全部文件。

## 测试计划

1. 以脱敏的已观察字段构造摘要、subagent、guardian、普通分叉、缺少来源、损坏字段和 compaction 样例；名称生成与侧聊映射仅在取得明确样本后加入。
2. 验证两层及多层父链、同名不同 ID、循环、父会话删除、更名、名称延迟、数据库缺少表/列及读取异常。
3. 旧 SQLite 迁移两次仍成功；运行中、完成、取消、重启恢复及代理重启后字段保持一致。
4. 主会话与摘要同时请求、两个子 agent 并发，一个请求取消不影响其他请求；原有会话路由不被归属替换。
5. 搜索根会话、agent 名、用途标签与分页计数一致；历史补全幂等、唯一匹配及不可恢复记录回退。
6. 现代/经典控制台渲染相同名称和标签，覆盖长名称、缺少新字段、来源不明及普通主会话单行显示。
7. 实现完成后按仓库门禁运行 Python 全量单测、JavaScript 全量测试与语法检查、前端 `npm ci` / `npm run build`、`git diff --check`；推送前针对 rebase 后准确 HEAD 完整验证。

## 结构化自审

- 目标与范围：围绕请求记录的名称和来源展示，能关联时显示主会话，只知用途时显示用途。
- 证据边界：摘要、普通子 agent、guardian 和 compaction 已有本地依据；名称生成及侧聊的精确标签需明确字段样本，父关系可先支持。
- 身份边界：自身会话 ID 与显示归属分离，取消、并发、路由及 Token 不被合并。
- 数据边界：只读 Codex 元数据，代理新增字段经过白名单处理，不保存提示词、正文或凭据。
- 兼容性：新增可空字段，保留现有名称接口与 API 字段，数据源缺失时能回退。
- 验收：新请求实时归属，两套界面一致，历史有证据则补全，无证据则明确展示未识别。
- 自审结论：方案可进入实现；精确类型映射以现场字段为依据，未知类型采用关联分支回退。

## 实际改动

- `local_proxy/session_attribution.py`：新增限长白名单解析、明确来源类型映射、两行名称/标签/悬停文字、关系散列 key；拒绝客户端注入本地名称快照与分类结果。
- `local_proxy/codex_sessions.py`：保留名称接口，增加原生 SQLite 只读归属查询、16 层父链/循环检测、有界短 TTL 缓存、guardian 首条 session_meta 恢复、名称更改及删除后的快照回退；执行关系优先于历史分叉，冲突关系不猜测根会话。
- `local_proxy/core.py`：历史与 inflight 新增 8 个可空列及索引，贯通开始、完成、取消、重启恢复；API 增加可选展示字段并删除内部原始 ID/上下文；保持实际请求身份与路由；根名称/agent/用途/未识别标签后端搜索和分页，名称匹配使用连接级临时表避免数量截断与 SQLite 参数上限；工作线程限量补全历史。
- `local_proxy/codex_profile.py`：接入 Codex 名称、归属和搜索解析；`local_proxy/request_debug.py`：按会话、模型、开始时间唯一匹配读取请求头白名单，单次头数据读取上限 256KiB，不能唯一匹配则跳过。`server.py` 无需修改，统一入口与独立入口共用 profile/core 路径。
- `proxy_static/src/components/RequestsView.vue`、`proxy_static/src/styles.css`、`proxy_static/classic/app.js`、`proxy_static/classic/styles.css`：现有会话列改为可选两行，无额外列；纯文本渲染、截断及完整悬停，缺少新字段回退旧接口。
- `tests/test_session_attribution.py`：新增 26 项覆盖元数据边界、原生关系、guardian、分叉、冲突/循环/深度、快照、更名、迁移、历史恢复、分页、超过 500 个匹配名称的关联搜索、路由和并发取消隔离；`tests/local_proxy_requests.test.js`：新增经典界面实际 DOM 文本构造及两套界面字段/样式一致性测试。
- 名称生成和侧聊仅接受明确 `thread_title` / `side_chat` / `side_conversation` 标记，未取得现场样本的其他字段不扩展推测；没有明确用途的普通分叉显示“关联分支”。
- 不代用户重启正在运行的代理，不修改 Codex 私有数据库，不提交前端 dist；本轮交付按功能分支 / PR / squash 合并执行，不自动发版。

## 验证结果

- `python -m unittest tests.test_session_attribution tests.test_codex_sessions tests.test_response_models tests.test_request_debug`：43 项通过。
- `node --test tests/local_proxy_requests.test.js`：15 项通过。
- `python -m unittest discover -s tests -p 'test_*.py'`：最终代码全量 646 项通过（46.648 秒），无失败/错误，无基线豁免。
- `node --test tests/*.test.js`：全量 98 项通过，无失败/跳过。
- `rg --files -g '*.js' -g '!proxy_static/dist/**' -g '!**/node_modules/**'` 枚举后逐个执行 `node --check`：30 个文件全部通过。
- 在 `proxy_static` 执行 `npm ci`、`npm run build`：依赖安装、Vite 生产构建通过；dist 仍被 Git 排除，未提交生成产物。现有锁文件的依赖审计提示 2 个漏洞（1 moderate、1 high），本轮未改动依赖或执行自动升级；另有 esbuild install-script 授权提示，未影响安装与构建。
- `git diff --check`：通过；已审阅全部 staged/unstaged diff 及三个新增文件，staged 为空，无私有配置、凭据、数据库或无关文件。
- `git fetch --no-tags origin main`：主线仍为 `bd0280c7c7abe47a944faf17c584b044572bdd05`，当前 `feat/codex-session-attribution` 从该准确主线开发。
- `verify_ruleset` 经当前 GitHub 登录凭据只读核验：`agent-delivery-main`（21697881）返回 `verified: true`。
- 2026-10-04 交付前重新执行 `python -c "from scripts.team_policy import run_full_verification; run_full_verification()"`：依赖安装、构建、语法、Python / JS 全量与最新主线 merge-base 基线对比均通过，既有失败 0 项，新增失败 0 项；另逐个复核全部 30 个 JS 文件语法通过。推送仍由 pre-push 钩子针对 rebase 后准确 HEAD 再执行完整门禁。
- 用户手动重启后，只读核验 `/control/codex/api/requests` 返回新增展示字段，实际提供的经典 JS / CSS 已包含会话第二行；搜索“上下文压缩”返回 6 条带该标签的记录。普通主会话无第二行，符合方案；尚未做额外浏览器绘制验收。
- 保留期内 10 条旧未知记录仍缺少可恢复的来源信息，返回“未识别会话 / 来源信息不足”，不凭模型或时间猜测关系。

## PR

pending
