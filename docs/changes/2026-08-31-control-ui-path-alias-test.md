+++
id = "2026-08-31-control-ui-path-alias-test"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复控制台资源路径跨平台测试

## 目标

让控制台静态资源隔离测试在 Windows 和 macOS runner 的临时目录存在路径别名时仍验证真实文件位置，并恢复两个平台的自动发布。

## 现状

`resolve_control_asset()` 按设计返回 `Path.resolve()` 后的规范路径，但测试将结果与未经解析的 `TemporaryDirectory()` 路径直接比较。Windows runner 会展开短路径别名，macOS 会将 `/var` 展开为 `/private/var`，导致 `v1.3.0` 的两个发布工作流在 Python 测试阶段失败。

## 设计范围

- 将资源解析测试的预期路径也规范化后再比较。
- 保留路径穿越、资源白名单和命名空间覆盖不变。
- 重新执行完整本地门禁，并通过新的补丁版本重新触发两平台发布。

## 非目标

- 不修改控制台资源解析实现、路由、界面选择或打包内容。
- 不改写 `v1.3.0` 标签及已发布的双界面功能说明。

## 兼容性

只修复跨平台测试断言，不影响接口、配置、数据或运行时行为。由于上一版本发布被该测试阻塞，需要选择 `patch` 生成可完成发布的新版本。

## 风险

仅解析预期测试路径可能掩盖返回值未规范化；测试仍分别断言经典和新版资源的准确规范路径，并继续覆盖路径穿越和未授权资源拒绝行为。

## 测试计划

- 运行 `tests.test_control_ui` 聚焦测试。
- 运行仓库 pre-push 完整验证，包括 Vite 构建、Python 全量测试、JavaScript 语法和测试。
- 合并后跟踪 Automatic Release、Windows Release 和 macOS Release 至完成。

## 实际改动

- `tests/test_control_ui.py` 将经典和新版资源的预期文件路径规范化后再比较，兼容 Windows 短路径和 macOS `/var` 路径别名，同时保留对准确文件、资源白名单和路径穿越的断言。

## 验证结果

- `python -m unittest tests.test_control_ui`：2 项通过。
- `python scripts/team_policy.py pre-push`：通过；Vite 构建成功，Python 全量 506 项通过，15 个 JavaScript 测试文件共 64 项通过，JavaScript 语法检查通过。
- npm 仍报告现有依赖含 1 个 moderate、1 个 high 漏洞；本次未执行破坏性依赖升级。

## PR

pending
