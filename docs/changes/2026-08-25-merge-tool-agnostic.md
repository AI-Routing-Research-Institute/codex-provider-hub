+++
id = "2026-08-25-merge-tool-agnostic"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 合并不再强制 gh CLI，允许网页合并

## 目标

团队部分成员未安装 gh CLI，无法执行「合并一律 gh pr merge --squash」的既有约定。放宽合并工具要求：有 gh 的成员继续用 gh，没有 gh 的成员可直接用 GitHub 网页的 Squash and merge 完成 PR 合并，降低交付门槛。

## 现状

- AGENTS.md 与 git-commit-helper skill 均写明「合并一律使用 gh pr merge --squash 以 PR 的最新准确 head SHA 执行」。
- 团队成员机器未统一安装 gh CLI，按字面执行该约定会阻塞无 gh 成员的合并操作。
- 远端规则集 `agent-delivery-main` 仅含 `deletion` + `pull_request`（0 审批、无 required checks），服务端对合并工具无任何限制。

## 设计范围

- AGENTS.md 与 `.agents/skills/git-commit-helper/SKILL.md` 的合并方式描述放宽为：`gh pr merge --squash` 或网页 Squash and merge 均可，不强制 gh CLI。
- 保留合并质量底线：squash 合并、基于 PR 最新准确 head SHA、合并后验证目标提交、禁止分支名/旧 SHA/模糊 ref/绕过门禁直接改动远端。
- 治理测试 `test_merge_policy_matches_ruleset_gates` 增加断言锁定放宽后的措辞，防止未来同步时回退。

## 非目标

- 不修改 `scripts/team_policy.py` 的规则集模板与客户端校验逻辑。
- 不调整远端规则集（deletion + pull_request 不变）。
- 不改变 commit message、身份白名单、change record 等既有门禁。

## 兼容性

无接口、配置或数据影响；仅仓库约束文档文本更新。

## 风险

- 网页合并不经 gh 的 head SHA 参数约束 —— 缓解：网页 Squash and merge 天然基于 PR 最新 head，且「合并后验证目标提交」要求保留。
- 文档放宽后可能误以为可绕过 squash —— 缓解：明确「合并使用 squash 方式」为底线。

## 测试计划

- `git diff --check` 通过。
- `python -m unittest discover -s tests -p "test_*.py"` 全量通过。
- `python scripts/team_policy.py pre-commit` / `commit-msg` / `pre-push` 校验通过。

## 实际改动

- `AGENTS.md`：合并方式由「一律 gh pr merge --squash」放宽为「gh pr merge --squash 或网页 Squash and merge 均可，不强制 gh CLI」。
- `.agents/skills/git-commit-helper/SKILL.md`：step 9 合并方式同步放宽（gh CLI not required）。
- `tests/test_team_policy.py`：`test_merge_policy_matches_ruleset_gates` 增加「不强制 gh CLI」「gh CLI not required」断言。
- 新增本变更说明。

## 验证结果

- `git diff --check`：通过。
- test_team_policy.py 及全量测试：435 项通过。
- team_policy.py pre-commit / commit-msg / pre-push：通过。

## PR

pending
