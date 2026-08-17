+++
id = "2026-08-17-provider-catalog-pr-record"
type = "docs"
release_bump = "none"
status = "verified"
+++

# 独立供应商目录 PR 记录补充

## 目标

在不改写已发布功能说明的前提下，补充独立供应商目录功能的最终 PR、合并提交和 Release 对应关系。

## 现状

`docs/changes/2026-08-17-independent-provider-catalog.md` 已随 v0.11.0 发布并保持不可变，但其 `PR` 字段仍为 `pending`。实际功能已通过 PR #33 合并到 `main`。

## 设计范围

- 记录原功能说明对应的 PR URL、squash 合并提交和 Release URL。
- 保留原功能说明不变，使用新的永久说明完成元数据补充。

## 非目标

- 不修改产品代码、配置、测试或构建工作流。
- 不改写或删除已发布的原功能说明。
- 不创建新的版本标签或发布产物。

## 兼容性

无接口、配置、数据或迁移影响。版本提升选择 `none`，因为本次仅补充发布元数据。

## 风险

主要风险是中文链接说明出现编码损坏；通过 UTF-8 解码检查和乱码候选扫描验证。

## 测试计划

- 运行 `git diff --check`。
- 使用 Python 按 UTF-8 读取补充说明并扫描常见乱码字符。
- 运行仓库 pre-commit 门禁。

## 实际改动

- 新增本说明，记录原功能 PR 为 <https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/33>。
- 记录 squash 合并提交为 `86b10f0721b8acceb80609c69ef873f6ea1d62ad`。
- 记录自动发布结果为 <https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/tag/v0.11.0>。

## 验证结果

- `git diff --check`：通过。
- Python UTF-8 解码和常见乱码候选扫描：通过。
- 仓库 pre-commit 门禁：通过。

## PR

pending
