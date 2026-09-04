+++
id = "2026-09-04-readme-refresh"
type = "docs"
release_bump = "none"
status = "verified"
+++

# README 全面刷新并加入 Linux.do 社区入口

## 目标

README 与项目当前功能对齐（用量趋势六图型、今日战报分享卡片、模型映射、在线更新等近期功能此前均未收录），整体观感更专业；新增 Linux.do 社区支持入口（https://linux.do/）。中英双语同步更新。

## 现状

- README.md / README.en.md 停留在用量趋势与战报卡片、模型映射等功能之前；无社区入口；功能特性无总览区（读者需通读全文才能拼出能力图景）。

## 设计范围

1. 徽章区新增 Linux.do 社区徽章；简介句补充用量分析与战报分享。
2. 新增"功能特性"总览节（双协议中转/供应商管理与无缝切换/智能重试/模型映射/用量分析与战报/远程监控/双控制台/便携与在线更新/安全边界 分组）。
3. "管理和切换供应商"补模型映射入口说明；"重试、统计与监控"新增"用量趋势与战报卡片"与"模型映射"小节（六图型、指标轮播、reduced-motion、微信兼容深底板、映射优先/未匹配透传语义）。
4. FAQ"模型无可用渠道"更新为映射表优先的现行语义。
5. 新增"社区与支持"节（Linux.do 讨论区、GitHub Issues/Releases）。
6. README.en.md 同结构英文同步。
7. 保留 `test_project_documentation` 要求的全部既有章节标题与关键内容。

## 非目标

- 不改代码与功能；不新增截图资产（后续可单独补充）。

## 兼容性

- 纯文档；无运行时影响；release_bump=none。

## 风险

- 双语版本漂移：同轮提交同步修改，段落一一对应。

## 测试计划

- `python -m unittest tests.test_project_documentation`（README 结构断言不回归）；全量 Python + JS。

## 实际改动

- `README.md`：徽章区新增 Linux.do 社区徽章；简介句补充用量趋势/战报卡片；目录新增"功能特性/社区与支持"；新增"功能特性"总览节（双协议中转/供应商管理与无缝切换/智能重试/模型映射/用量分析与战报/远程监控/控制台与交付/安全边界 八组）；"管理 Codex 供应商"补模型映射维护项；"重试、统计与监控"新增"用量趋势与战报卡片""模型映射"两小节（六图型、指标轮播、reduced-motion、深底板、映射优先/未匹配透传语义）；FAQ 模型段更新为映射优先语义；新增"社区与支持"节（Linux.do/Issues/Releases）。
- `README.en.md`：同结构英文同步（Feature overview、Usage trends and report card、Model mapping、Community and support、FAQ 与徽章）。

## 验证结果

- `python -m unittest tests.test_project_documentation` → 6 项通过（README 结构断言不回归）（2026-09-04 17:23）。
- 全量 `python -m unittest discover -s tests -p "test_*.py"` → 561 项全部通过；`node --test tests/*.test.js` 全部通过。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/93
