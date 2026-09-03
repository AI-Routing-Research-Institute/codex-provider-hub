+++
id = "2026-09-03-dist-index-eol"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 归一 proxy_static/dist/index.html 换行符

## 目标

消除 `proxy_static/dist/index.html` 入库 blob 中的孤立 CRLF，使工作区在该文件上保持干净，不再出现永久 `M` 噪音。

## 现状

该文件入库 blob 含 1 个 CRLF 行，而 `.gitattributes` 已声明此路径 `eol=lf`，检出时被转成 LF，导致工作区永远与入库不一致（`git status` 常驻 1 个 M）。此 CRLF 由 #72 合并时 Windows 下 `vite build` 产物原样入库带入（#68 当时是干净的，此前 #64/#67 也有过）。

## 设计范围

- 仅对 `proxy_static/dist/index.html` 执行 `git add --renormalize`，将入库归一为 LF。
- 验证：暂存 diff 仅此 1 文件，且忽略换行符后 diff 为空（零语义改动）。

## 非目标

- 不改 `.gitattributes` / 不加 `.editorconfig`（治本方案另行排期）。
- 不重建 dist、不改任何源码与构建逻辑。

## 兼容性

无。纯换行符归一，渲染与构建产物语义不变。

## 风险

- 若归一后 diff 含除换行符外的内容，立即停止（说明文件被意外改动）。
- 缓解：提交前用 `git diff --cached --ignore-cr-at-eol` 强制校验为空。

## 测试计划

- `git status` 确认干净；抽查文件内容行数一致。
- 无需跑全量测试（零语义改动），推送门禁会自动执行既有校验。

## 实际改动

- `proxy_static/dist/index.html`：`git add --renormalize` 将入库唯一 CRLF 行归一为 LF（1+/1-）。
- 新增本说明 `docs/changes/2026-09-03-dist-index-eol.md`。

## 验证结果

- 入库后 blob：CRLF=0（13 行全 LF）；忽略换行符后与父提交内容完全一致（零语义改动）。
- `git status` 干净，常驻 M 消除。
- 推送门禁会自动执行既有全量校验。

## PR

pending
