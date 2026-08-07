+++
id = "2026-08-07-agent-delivery-pipeline"
type = "build"
release_bump = "none"
status = "planned"
+++

# Agent 自驱交付流水线

## 目标

让 Agent 负责功能审查、分支开发、规范提交、PR、自动合并、版本决策和发版，减少三人协作中的人工操作。

## 现状

仓库只有 `AGENTS.md` 文字约束，三名开发者可能直接基于 `main` 工作；本地 hooks、PR required checks、自动版本和自动发布尚未形成统一门禁。

## 设计范围

引入仓库内提交 skill、永久变更记录、共享策略脚本、Git hooks、Windows/macOS PR CI、main Ruleset 和自动发版协调工作流。

## 非目标

不部署独立 GitHub App，不要求人工 PR 审批，不改变本地代理运行时业务功能。

## 兼容性

运行时接口和配置格式不变；开发者需要安装仓库 hooks，并改用功能分支和 PR。

## 风险

远端 Ruleset 配置需要仓库管理权限；自动发布错误可能影响版本标签，因此必须使用并发锁和标签冲突检查。

## 测试计划

对策略解析、提交校验、Ruleset 载荷、版本计算和 release notes 进行单元测试，并运行完整 Python 与 Node 测试。

## 实际改动

等待实施完成后记录。

## 验证结果

等待完整验证。

## PR

pending
