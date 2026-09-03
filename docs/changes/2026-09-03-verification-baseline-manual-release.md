+++
id = "2026-09-03-verification-baseline-manual-release"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 全量验证基线对比与人工确认发版

## 目标

降低交付门禁的时间与 Agent token 成本，并让发版回归人工确认：

1. 删除依赖真实桌面环境的 GUI 冒烟测试（`GuiSmokeTests`）。
2. pre-push 全量验证改为**基线对比**：失败集合是 merge-base（origin/main）基线子集时放行、不排查；只拦截本次改动新增的失败。
3. 发版从合并后自动触发改为**人工确认**：Agent 汇总待发布内容并向用户确认后，手动触发发版协调工作流创建标签并派发双平台发布。

## 现状

- `tests/test_probe_codex_gui.py` 的 `GuiSmokeTests` 依赖本机 cc-switch 数据库（≥1 个 Codex API 供应商）与 codex 二进制，在无供应商的机器上必失败，曾阻塞推送。
- `run_full_verification()` 在 pre-push 无条件要求 549 项全部通过：任何与本改动无关的既有失败（如本机 fake-IP DNS 把保留域解析为非公网地址）都会阻塞，且 Agent 需消耗大量上下文排查以证明"非本次引入"。
- `auto-release.yml` 在 push 到 main 时自动选版本、打标签、触发双平台发布，用户没有确认机会。

## 设计范围

1. 删除 `GuiSmokeTests` 及其专用 import；保留同文件两个纯单元测试类与 `--smoke-test` 脚本入口。
2. `scripts/team_policy.py`：
   - 全量验证拆为"硬门禁"（`npm ci`、`npm run build`、`node --check` 全部仍须通过）与"可比对套件"（Python unittest、逐文件 `node --test`）。
   - 新增基线机制：按 merge-base SHA 把基线失败集合缓存到 `.git/policy-baselines/<sha>.json`；缓存缺失时在临时 worktree 检出 merge-base 运行可比对套件生成基线（不跑 npm）。
   - HEAD 失败集合为基线子集 → 放行并打印忽略清单；出现新增失败 → 对新增项复跑一次确认（缓解偶发失败），仍失败才阻止。
   - unittest 失败 ID 解析自 `FAIL|ERROR: name (id)` 行；node 失败解析自 TAP `not ok` 行。
3. `auto-release.yml` 重命名为 `release.yml`，触发方式改为 `workflow_dispatch`；内部逻辑（读取 verified 说明、选最高 bump、创建唯一标签、显式派发 `windows-release.yml` / `macos-release.yml`）保持不变。
4. `AGENTS.md` 与 `git-commit-helper` skill 同步：发版流程改为"Agent 提议版本与 notes → 用户同意 → 触发 release 工作流"；硬停止条件改为"全量验证未运行，或出现基线之外的新增失败"。

## 非目标

- 不改 release tag 时 `windows-release.yml` / `macos-release.yml` 的全量测试与阻塞行为（发布前最后一道闸门保持全量且必须全过）。
- 不改 PR 阶段不跑 CI、squash 合并、Ruleset、提交白名单等既有门禁。
- 不删除 `probe_codex_gui.py --smoke-test` 脚本本身（仍可手动运行）。

## 兼容性

- 无产品运行时影响，仅改交付工具链；`release_bump = "none"`，不产生产品版本发布。
- 基线缓存位于 `.git/`（不提交、不进版本库），旧缓存条目在写入新基线时清理。
- 已有 `release-plan` / `release-notes` CLI 与函数不变，发版工作流复用。

## 风险

1. 基线对比对偶发失败（flaky）不免疫：新增失败复跑一次确认后仍失败才拦截；基线侧偶发失败会进缓存导致放行偏松（可接受方向：宁松勿卡）。
2. node 逐文件失败按测试名集合比对，个别 reporter 格式变化可能解析不到 → 解析为空集合等同"无既有失败"，退化为严格模式（安全方向）。
3. 基线在临时 worktree 跑 Python 套件依赖本机环境（如 fake-IP DNS）：基线与 HEAD 在同一台机器运行，环境性失败同时出现在两侧，自动对消——这正是本设计要解决的问题。
4. 自动发版取消后，若 Agent 忘记询问用户，改动会滞留在 main 未发布：由 skill 步骤强制要求合并后必须给出"发版或跳过"的提议收尾。

## 测试计划

- `tests/test_team_policy.py`：新增 unittest/TAP 失败解析、失败集合差集、基线缓存读写、release 工作流为手动触发且不自动 push 触发的断言；更新与 AGENTS.md/skill 新文案相关的治理断言。
- 全量验证：`python -m unittest discover -s tests -p "test_*.py"`、`node --test tests/*.test.js`、`npm ci` + `npm run build --prefix proxy_static`。
- 实际推送本分支时用新 pre-push 钩子走一遍真实基线对比（merge-base 为 origin/main）。

## 实际改动

- `scripts/team_policy.py`：
  - 新增失败解析（`UNITTEST_FAILURE_RE`、`NODE_FAILURE_RE`、`parse_unittest_failures`、`parse_node_failures`）与哨兵常量（套件非零退出但解析不到失败 ID 时保守拦截）；
  - `run_full_verification()` 重构：`npm ci`、`npm run build`、`node --check` 全部为硬门禁；Python unittest 与逐文件 `node --test` 改为"可比对套件"，失败集合与 merge-base 基线比对，子集放行（打印忽略数量），新增失败复跑一次确认后仍失败才阻止；
  - 新增基线设施：`run_comparable_suites`、`diff_new_failures`、`_baseline_cache_path`（`.git/policy-baselines/<merge-base>.json`）、`read_baseline_cache`/`write_baseline_cache`（写入时清理旧缓存）、`collect_baseline`（临时 worktree 检出 merge-base 跑可比对套件，不跑 npm）、`load_or_build_baseline`、`_confirm_new_failures`。
- `tests/test_probe_codex_gui.py`：删除 `GuiSmokeTests` 及其专用 import（json/os/subprocess/sys/Path/ROOT/GUI_SCRIPT）；保留两个纯单元测试类。
- `.github/workflows/auto-release.yml` 重命名为 `release.yml`：名称改为 Manual Release，触发改为 `workflow_dispatch`；计划计算、标签创建与双平台派发逻辑不变。
- `AGENTS.md`：Git 与 PR 增加基线对比条款；"自动版本与自动发版"改为"版本与人工确认发版"（合并后不自动发版，Agent 汇总并向用户确认后触发 `release.yml`）；硬停止条件改为"本地全量验证未运行，或出现基线之外的新增失败"。
- `.agents/skills/git-commit-helper/SKILL.md`：第 8/9/10 步与停止条件同步新口径（基线对比、无自动发版、用户同意后 `gh workflow run release.yml --ref main`）。
- `tests/test_team_policy.py`：治理断言更新（AGENTS/skill 新文案、release.yml 手动触发且 auto-release.yml 不存在）；新增 `VerificationBaselineTests` 6 项（unittest/TAP 失败解析、去重、子集放行与新增项判定、哨兵、基线缓存 roundtrip 与旧缓存清理）。

## 验证结果

- `python -m unittest tests.test_team_policy tests.test_probe_codex_gui tests.test_project_documentation` → 39 项全部通过（2026-09-03）。
- 端到端演练基线机制（2026-09-03 15:10）：HEAD 可比对套件 18.6s 失败 0；merge-base（5f06e6e）基线 17.9s 失败 1（main 上仍存在的冒烟测试在本机 cc-switch 无供应商时失败）；`diff_new_failures` 为空 → 按新规则应放行且无需排查，行为符合设计；基线缓存已写入 `.git/policy-baselines/`。
- `python -m unittest discover -s tests -p "test_*.py"` → 554 项全部通过（2026-09-03）。
- `node --test tests/*.test.js`、`node --check`（classic/src/provider_status）、`npm ci` + `npm run build --prefix proxy_static` → 全部通过（2026-09-03）。
- 实际推送本分支时由新版 pre-push 钩子执行真实基线对比（见 PR 与推送记录）。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/76
