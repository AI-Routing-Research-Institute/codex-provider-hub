+++
id = "2026-08-12-test-identity-push"
type = "chore"
release_bump = "none"
status = "verified"
+++

# 测试协作者身份提交流程

## 目标

验证非 moye12325 身份的协作者（以 loongkkk 为例）在规则集同步后可以正常完成提交与推送，不再被 pre-push 钩子拦截。

## 现状

规则集与 `team_policy.py` 已同步（deletion + pull_request，无 CI 检查），需要端到端验证协作者身份的全流程。

## 设计范围

- 以 `loongkkk` 白名单身份创建功能分支并提交一个验证用变更
- 变更内容：本说明文档 + 一个无害的测试标记文件
- 验证 hooks（pre-commit / commit-msg / pre-push）全部通过

## 非目标

- 不修改产品代码、配置或构建逻辑
- 不合并该测试分支到 main（验证后关闭 PR）

## 兼容性

- 无接口、配置或数据影响

## 风险

- 测试分支会短暂出现在远端，验证后立即删除

## 测试计划

- 以 loongkkk 身份完成 commit（pre-commit + commit-msg 钩子）
- push 分支（pre-push 钩子含完整测试套件与 validate_pr）
- 创建 PR 并确认可正常创建

## 实际改动

- `docs/changes/2026-08-12-test-identity-push.md`：本说明
- `docs/identity-test-marker.txt`：验证用标记文件

## 验证结果

- 待执行后回填

## PR

pending
