+++
id = "2026-08-21-v1-bilingual-readme-about"
type = "docs"
release_bump = "major"
status = "verified"
+++

# v1 双语 README 与仓库定位

## 目标

为 Codex Provider Hub 提供内容完整、可互相切换的简体中文和英文 README，并更新 GitHub About 与 Topics，使仓库公开定位准确覆盖 Codex、Claude Code、CC Switch 和本地多供应商路由能力；合入后发布首个稳定主版本 `v1.0.0`。

## 现状

仓库只有简体中文 README，国际用户缺少完整使用教程。GitHub About 仍将项目描述为单一 Codex 代理，没有体现 Claude Code、CC Switch、双控制台、供应商切换和健康监控等当前能力，Topics 为空。

## 设计范围

- `README.md` 保持简体中文主入口，并在顶部增加语言切换。
- 新增完整 `README.en.md`，对应中文版的用户范围、快速开始、客户端配置、供应商管理、传输方式、重试统计、远程监控、故障排查、开发部署、安全和授权说明。
- 增加文档契约测试，校验双语入口、关键章节、控制台地址、下载地址、CC Switch、传输兼容方式和授权边界。
- GitHub About 使用中英双语的一句话重新描述产品定位。
- GitHub Topics 增加 Codex、Claude Code、CC Switch、本地代理、供应商路由、API 网关、FastAPI 和健康监控相关主题。
- 使用 `release_bump = "major"` 触发从 `v0.13.3` 到 `v1.0.0` 的自动发布。

## 非目标

- 不修改应用运行时、控制台界面、接口、配置格式、安装包名称或网络行为。
- 不修改仓库名称、主页地址、许可证或商业授权条款。
- 不手工创建或推送 Git Tag。

## 兼容性

仅修改公开文档、文档测试和 GitHub 仓库元数据，无运行时、客户端、数据库、配置或迁移影响。现有中文 README 链接继续有效，英文用户可使用新增的独立入口。

## 风险

主要风险是双语内容不同步或英文教程遗漏关键安全与授权边界。通过文档契约测试锁定两份 README 的共同关键内容，并保持章节结构对应。GitHub About 与 Topics 在 PR 合入后通过 API 回读验证。

## 测试计划

- 先新增双语 README 契约测试，确认在英文版不存在时按预期失败。
- 运行文档契约测试，确认双语入口和关键内容全部通过。
- 检查两份 README 的 Markdown 本地链接和公开下载链接。
- 运行完整 Python 测试、JavaScript 语法检查、JavaScript 测试、仓库策略检查和 `git diff --check`。
- PR 合入后回读 GitHub About、Topics，并确认自动发布版本和 Windows/macOS 资产。

## 实际改动

- `README.md` 顶部增加简体中文与英文切换入口，原有中文教程和链接保持不变。
- 新增 `README.en.md`，完整对应中文教程的 13 个主章节，保留相同的命令、配置、下载资产、端口、安全和授权边界。
- `tests/test_project_documentation.py` 增加双语入口与英文教程契约测试，锁定两套控制台地址、CC Switch、`curl_cffi`、下载资产和授权说明。
- GitHub About 与 Topics 将在 PR 合入后更新并通过 API 回读验证。

## 验证结果

- TDD RED：首次运行 `python -m unittest tests.test_project_documentation`，6 项中 2 项按预期因 `README.en.md` 不存在而失败。
- TDD GREEN：实现双语 README 后运行 `python -m unittest tests.test_project_documentation`，6 项全部通过。
- `python -m unittest discover -s tests -p "test_*.py"`：482 项全部通过。
- `node --check proxy_static/app.js` 与 `node --check provider_status/static/app.js`：全部通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：全部 JavaScript 测试通过。
- 两份 README 各有 13 个主内容章节、27 个 Markdown 链接和 5 个本地链接，本地链接无缺失。
- `git diff --check`：通过。

## PR

pending
