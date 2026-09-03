+++
id = "2026-09-04-release-notes-headline-keep"
type = "fix"
release_bump = "none"
headline = "修正更新摘要里人工一句话被冒号截断的问题，现在完整保留"
status = "verified"
+++

# Release 摘要 headline 不再被冒号截断

## 目标

`headline` 是人工撰写的通俗一句话，应整段进入 release 摘要；此前被按全角冒号断句，冒号后的内容被丢弃。

## 现状

`_record_headline()` 对 headline 复用了"目标"章节的首句提取逻辑（含冒号断句），例如 headline "版本更新说明改为一眼看懂的简短摘要：按新功能、问题修复分组，每条一句话" 只保留了冒号前半句。

## 设计范围

- `_plain_sentence()` 新增 `cut_at_separators` 开关；headline 路径设为 False（仅清洗与限长，不断句），自动提取"目标"的路径行为不变。

## 非目标

- 不改其他渲染逻辑与门禁。

## 兼容性

- 纯渲染层；无新增字段与流程变化。

## 风险

- 无。

## 测试计划

- `tests/test_team_policy.py`：headline 含冒号时完整保留的断言。

## 实际改动

- `scripts/team_policy.py`：`_plain_sentence(text, limit, *, cut_at_separators=True)`；`_record_headline` 的 headline 分支传 `cut_at_separators=False`。
- `tests/test_team_policy.py`：`test_release_notes_group_by_type_and_prefer_headline` 的 fix 记录 headline 改为含冒号完整句并断言整句出现在 notes。

## 验证结果

- `python -m unittest tests.test_team_policy` → 33 项通过（2026-09-04 01:51）。
- 本地渲染 `release-notes --base-tag v1.10.1 --head <分支>`：headline 完整保留"版本更新说明改为一眼看懂的简短摘要：按新功能、问题修复分组，每条一句话"。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/89
