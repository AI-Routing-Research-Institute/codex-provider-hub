+++
id = "2026-08-12-request-reasoning-effort"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 请求记录展示推理强度

## 目标

在请求记录表中新增“推理强度”列，并重新整理各列宽度、间距与对齐方式，使供应商、模型、耗时、Token 和结果等信息在宽屏与窄屏下都能清晰扫描，不再出现末尾列粘连或表头与内容对不齐。

## 现状

请求记录只保存并展示模型，不保存请求指定的推理强度。页面使用 8 列固定网格，只有 Token 表头单独右对齐，表头和单元格缺少按数据类型统一的对齐规则；耗时、Token 与结果列间距偏紧，长内容下尤其难以区分。

## 设计范围

- 从兼容 OpenAI Responses 的请求体中读取 `reasoning.effort`，并兼容顶层 `reasoning_effort` 字段。
- 将规范化后的推理强度保存在活动请求状态和本地请求历史中，通过请求记录接口返回。
- 为已有 SQLite 数据库自动增加可空的 `reasoning_effort` 列；旧记录与旧式用量记录保持为空。
- 请求表新增“推理强度”列，将常见值映射为简短中文标签，未知值安全截断后原样展示。
- 按字段类型统一表头和内容对齐：文本左对齐，短状态居中，数字右对齐。
- 调整表格网格宽度、列间距和最小宽度，窄屏继续使用横向滚动，不压缩到文字重叠。

## 非目标

- 不改变模型、供应商选择和重试路由逻辑。
- 不保存请求正文、响应正文、密钥或其他敏感字段。
- 不为请求记录增加排序、列显隐或用户自定义列宽功能。
- 不自动重启用户当前运行的本地中转服务。

## 兼容性

请求记录接口仅新增可空字段，现有调用方可忽略；SQLite 初始化会对已有数据库执行幂等列迁移，新建数据库直接包含该列。历史记录没有推理强度时显示破折号。该变更增加用户可见能力和接口字段，版本选择 `minor`。

## 风险

- 上游请求可能使用未知或非字符串推理强度；解析时只接受短字符串并限制长度，避免异常内容进入状态与数据库。
- 数据库迁移可能遗漏旧库；通过构造旧表结构的测试验证初始化会自动补列且不损坏原记录。
- 新增列可能让窄窗口更拥挤；保留表格横向滚动，并用稳定的最小列宽与实际浏览器截图验证。
- 表头与单元格规则可能因列序变化失配；使用语义类名控制对齐，减少依赖 `nth-child` 的脆弱选择器。

## 测试计划

- 验证嵌套、顶层、缺失和非法推理强度请求体的解析结果。
- 验证活动请求和已完成请求接口都返回推理强度。
- 验证新数据库写入、查询推理强度，以及旧数据库自动迁移。
- 验证前端中文标签、列结构和语义对齐样式。
- 运行完整 Python、Node 测试、JavaScript 语法检查、Python compileall 和 diff 检查。
- 在宽屏和窄屏浏览器视口检查表头、末尾列间距、文字截断和横向滚动行为。

## 实际改动

- `local_proxy/core.py` 从 Responses 请求的 `reasoning.effort` 或兼容顶层 `reasoning_effort` 读取短字符串，随活动请求状态公开，并在请求成功、失败或重试耗尽后写入历史记录。
- `local_proxy/core.py` 为新建 `request_history` 表增加可空的 `reasoning_effort` 列，并在初始化已有数据库时幂等执行 `ALTER TABLE`；历史接口为旧记录返回空值。
- `proxy_static/index.html` 和 `proxy_static/app.js` 在模型与耗时之间增加“推理强度”列，将 `none`、`minimal`、`low`、`medium`、`high`、`xhigh` 映射为“关闭、极低、低、中、高、极高”，缺失值显示破折号。
- `proxy_static/styles.css` 将请求表扩展为 9 列，使用语义类名统一表头和单元格对齐，增加列间距与结果列宽，并让窄屏下表头和数据行在同一容器中同步横向滚动。
- `tests/test_proxy_core.py` 覆盖请求解析、活动请求接口、旧 SQLite 迁移、历史记录读写和完成请求端到端落库。
- `tests/local_proxy_requests.test.js` 覆盖中文标签、9 列渲染顺序和语义对齐规则。

## 验证结果

- `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"`：423 项通过。
- `node --test tests/*.test.js`：40 项通过。
- `node --check proxy_static/app.js` 与 `node --check provider_status/static/app.js`：通过。
- `.venv/Scripts/python.exe -m compileall -q local_proxy provider_status probe_codex_cc_switch.py local_proxy_app.py`：通过。
- `git diff --check`：通过。
- 独立本地预览服务浏览器验证：`1600x900` 下 9 列完整、页面级横向溢出为 0，居中列的表头和内容中心点完全一致，耗时与 Token 的表头和内容右边界完全一致，Token 与结果内容间距为 66px。
- 独立本地预览服务浏览器验证：`900x800` 和 `560x800` 下表格在自身容器横向滚动且页面级横向溢出为 0；滚动 671px 后表头与数据行横向位移一致，筛选控件无重叠。

## PR

pending
