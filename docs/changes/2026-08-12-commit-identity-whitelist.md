+++
id = "2026-08-12-commit-identity-whitelist"
type = "feature"
release_bump = "patch"
status = "verified"
+++

# 提交身份白名单门禁

## 目标

新增提交身份白名单门禁：只有登记在白名单中的 git 用户名才能提交；一旦作者、提交者或任一 Co-authored-by 身份不在白名单中，则拒绝提交与推送。

## 现状

策略只校验分支名、暂存路径、变更说明与提交信息格式，不校验提交身份，导致非项目成员身份（例如公司内网身份）可能混入提交。

## 设计范围

- 在 `scripts/team_policy.py` 新增 `ALLOWED_COMMIT_NAMES` 白名单与 `validate_commit_identities`。
- `pre-commit` 校验 author 与 committer 用户名。
- `commit-msg` 校验 author、committer 及全部 Co-authored-by 用户名。
- `validate-pr` 对推送区间内每个提交校验 author、committer 及 Co-authored-by 用户名。
- 白名单基于用户名而非邮箱，避免把成员邮箱写入仓库对外暴露。

## 非目标

不改写既有历史提交，不引入远端身份服务，不新增审批流程。

## 兼容性

无运行时影响；仅作用于本地 git 钩子与 PR 策略校验。白名单为项目当前成员用户名，新增成员需更新常量。

## 风险

用户名可被伪造，门禁强度弱于签名校验；缓解方式是配合远端 ruleset 与 PR 必需检查共同约束。白名单遗漏合法成员会阻断其提交，通过更新常量解决。

## 测试计划

运行 `python -m unittest discover -s tests -p test_*.py`，覆盖白名单接受、任一身份越界拒绝、用户名与 Co-authored-by 解析。

## 实际改动

- `scripts/team_policy.py`：新增 `ALLOWED_COMMIT_NAMES`、`COAUTHOR_RE`、`_extract_name`、`_coauthor_names`、`validate_commit_identities`；在 `command_pre_commit`、新增的 `command_commit_msg`、`validate_pr` 中接入身份校验。
- `tests/test_team_policy.py`：新增白名单接受、越界拒绝、用户名/Co-authored-by 解析用例。

## 验证结果

`python -m unittest discover -s tests -p "test_team_policy.py"` 全部 25 项通过。

## PR

pending
