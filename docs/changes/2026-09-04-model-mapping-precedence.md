+++
id = "2026-09-04-model-mapping-precedence"
type = "fix"
release_bump = "patch"
headline = "在 Codex CLI 里切换模型不再被供应商的固定模型设置改回旧模型"
status = "verified"
+++

# 模型映射优先于供应商单值模型重写

## 目标

供应商配置了模型映射表时，未匹配映射的请求模型**保持原值透传**，不再被供应商顶层 `model` 单值（v1.5 遗留"模型重写"）强制覆盖——用户在 codex-cli 中切换模型（如 sol → luna）能够真正生效。

## 现状

转发循环（`local_proxy/core.py`）的模型处理顺序：

1. `provider.model_mappings` 命中 → 改写为上游名 ✓；
2. 未命中 → 落入 `if not mapping_applied and provider.model:` 单值兜底，请求模型被强制替换为 `provider.model`。

#65（2026-09-01-provider-model-mapping.md）设计范围明确承诺"无匹配映射时保持原值"，实现却在此处被 v1.5 单值兜底打破：映射表非空但请求模型（如新切换的 luna）无条目时，被 `provider.model`（如 sol）压回，用户切换模型永远无效。

## 设计范围

- 转发循环条件改为：**仅当供应商映射表为空时**才走 `provider.model` 单值兜底（保留 v1.5"固定模型"用法且不破坏既有配置）；映射表非空时未匹配模型原样透传上游。

## 非目标

- 不改映射表本身的读写、UI 与校验；不废弃单值字段；不改重试/重路由逻辑（每次尝试仍按当前供应商重新计算）。

## 兼容性

- 仅映射表非空的供应商行为变化（且是向设计文档承诺的行为收敛）；映射表为空的供应商行为完全不变。

## 风险

- 依赖"未匹配→单值覆盖"旧行为的映射用户（映射表非空、同时期望兜底）：修复后未匹配模型将透传，若上游不支持该模型会收到明确报错——比静默改回旧模型更可诊断；可在映射表中补条目恢复改写。

## 测试计划

- `tests/test_proxy_core.py`：新增转发级测试——映射表非空且未匹配时请求体模型保持原值（不被 `provider.model` 覆盖）；映射表命中时正常改写；映射表为空时单值改写仍生效。
- 全量验证：Python 单测、`node --test`、`npm ci` + `npm run build`。

## 实际改动

- `local_proxy/core.py`：转发循环单值兜底条件由 `if not mapping_applied and provider.model:` 收紧为 `... and not provider.model_mappings`——供应商配置了映射表时未匹配模型透传原值；映射表为空时单值改写行为不变。
- `tests/test_proxy_core.py`：新增 `test_unmapped_model_passes_through_when_mappings_exist`（映射非空未匹配透传 luna、映射空单值仍改写、映射命中改写 upstream 名 三情形）。

## 验证结果

- `python -m unittest tests.test_proxy_core -k model` → 15 项通过；全量 `python -m unittest discover -s tests -p "test_*.py"` → 561 项全部通过；`node --test tests/*.test.js` 全部通过（2026-09-04 11:23）。

## PR

pending
