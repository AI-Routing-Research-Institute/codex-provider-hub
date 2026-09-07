+++
id = "2026-09-07-eol-normalization"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 换行符长效治理：加宽 attributes 与编辑器配置

## 目标

Windows 换行符（CRLF）以后只停留在工作区噪音层面：`git diff` 只显示真实改动，入库永远 LF，不再出现整文件飘红、清不掉的 M、pull/rebase 被挡。

## 现状

- `.gitattributes` 仅覆盖 6 个路径，`.py/.js/.vue/.md` 等全靠运气；编辑器/AI 工具在 Windows 下写入 CRLF 会导致整文件 diff。
- `vite build` 会往 `dist/` 吐 CRLF，曾三次污染入库（#64/#67/#72/#79 均出现过）。
- 当前全仓入库 0 CR（已验证），本次无需清理历史，只需立规矩防复发。
- 无 `.editorconfig`，工具链各写各的。

## 设计范围

- `.gitattributes`：`* text=auto` 打底；源码文本类型显式 `text eol=lf`；已知二进制显式 `binary`。
- 新增 `.editorconfig`：`end_of_line = lf` + `charset = utf-8`（仅此两项，不碰缩进与行尾空格，避免噪音）。
- 执行 `git add --renormalize .` 验证预期 no-op（有漏网则一并洗掉，标准：`--ignore-cr-at-eol` 为空）。

## 非目标

- 不改 vite 构建行为（工作区仍可能出现 CRLF，一 checkout 即干净，不再污染提交）。
- 不给门禁加 CRLF 拦截（另行决策）。
- 不改任何源码逻辑。

## 兼容性

无。纯换行符策略；`none` 不触发发版。二进制文件显式标记，不断其内容。

## 风险

- 规则写错导致二进制被当文本归一：已全仓扫描，二进制仅 1 个 png 且显式标记；归一前后 diff 为空即证明。
- 缓解：合并前逐项复核 `git status/diff`。

## 测试计划

- 归一前后 `git status` 对比；入库 CR 全仓复扫为 0。
- 模拟验证：临时文件写入 CRLF，确认 `git diff` 仅显示真实改动后还原（不入库）。
- 运行 `tests/test_team_policy.py`（门禁脚本无改动，冒烟即可）；必要时全量单测。

## 实际改动

- `.gitattributes`：`* text=auto` 打底；源码文本类型显式 `text eol=lf`；二进制显式 `binary`（原有 6 条保留并补回 `.githooks/*`）。
- 新增 `.editorconfig`（`end_of_line = lf` + `charset = utf-8`）。
- 新增本说明 `docs/changes/2026-09-07-eol-normalization.md`。

## 验证结果

- `git add --renormalize .` 为 no-op：仅本分支 3 个预期文件，无历史 blob 需改动。
- 全仓入库 CR 复扫为 0；`check-attr` 抽查（py/vue/html/png/hooks）均符合预期。
- 耐受演示：整文件强制 CRLF 后 `git diff --stat` 为空（仅 warning），还原后树干净。
- `tests/test_team_policy.py`：33 tests OK。

## PR

pending
