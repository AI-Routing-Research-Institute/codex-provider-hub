# README 使用教程与双许可证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 README 重构为以 CC Switch 为推荐入口的完整中文使用教程，并增加 AGPL 开源、出处保留和闭源商业授权文件。

**Architecture:** 运行时代码保持不变，公开文档按普通用户任务顺序重新组织。许可证职责拆分为标准 `LICENSE`、出处 `NOTICE` 和替代授权入口 `COMMERCIAL-LICENSE.md`，由独立 Python 测试锁定关键边界，避免后续文案破坏 AGPL 属性。

**Tech Stack:** Markdown、GNU AGPL v3、Python `unittest`、仓库团队策略脚本。

---

### Task 1: 文档与许可证契约测试

**Files:**
- Create: `tests/test_project_documentation.py`

- [ ] **Step 1: 编写许可证文件失败测试**

创建 `ProjectDocumentationTests`，读取仓库根目录文件并断言：`LICENSE` 包含 GNU Affero General Public License v3 标题和网络交互源码条款；`NOTICE` 包含项目名、贡献者版权和原始仓库地址；`COMMERCIAL-LICENSE.md` 同时包含闭源授权入口及“遵守 AGPL 的商业使用无需商业授权”的边界。

- [ ] **Step 2: 编写 README 教程失败测试**

断言 README 包含“适合谁”“五分钟快速开始”“推荐搭配 CC Switch”“配置 Codex”“配置 Claude Code”“请求传输方式”“常见问题”“开源与商业授权”；包含两个控制台地址和 `ANTHROPIC_BASE_URL`；不再包含“当前仓库尚未附带开源许可证”。

- [ ] **Step 3: 运行测试并确认因文件或章节缺失失败**

Run: `python -m unittest tests.test_project_documentation`

Expected: FAIL，原因是根目录许可证文件不存在或 README 缺少新教程章节。

### Task 2: 增加双许可证文件

**Files:**
- Create: `LICENSE`
- Create: `NOTICE`
- Create: `COMMERCIAL-LICENSE.md`
- Test: `tests/test_project_documentation.py`

- [ ] **Step 1: 添加标准 AGPL 正文**

从 GNU 官方许可证文本加入未经修改的 GNU Affero General Public License version 3 全文，并通过 `AGPL-3.0-or-later` 标识引用。

- [ ] **Step 2: 添加出处声明**

`NOTICE` 写明 `Copyright (c) 2026 Codex Provider Hub contributors`、原始仓库 URL、再分发和衍生项目需保留 `LICENSE`、`NOTICE`、版权声明与原始出处。

- [ ] **Step 3: 添加商业授权入口**

`COMMERCIAL-LICENSE.md` 明确闭源分发、不公开网络修改源码、专有产品集成等情况需要替代授权；遵守 AGPL 的个人或商业使用无需付费；通过仓库 Issue 联系维护者，文件本身不授予商业许可证。

- [ ] **Step 4: 运行许可证契约测试**

Run: `python -m unittest tests.test_project_documentation`

Expected: README 相关断言仍失败，许可证相关断言通过。

### Task 3: 重构 README 使用教程

**Files:**
- Modify: `README.md`
- Test: `tests/test_project_documentation.py`

- [ ] **Step 1: 重写项目定位和目标用户**

第一屏保留项目名、简短定位、平台与 Release 徽章，随后直接说明适合多 Codex/Claude Code 供应商用户，不提供 Key 或公网多租户服务。

- [ ] **Step 2: 编写 CC Switch 推荐流程与五分钟快速开始**

说明首次启动从 CC Switch 初始化导入到本地独立目录、页面编辑不反向修改 CC Switch，以及“仅新增/覆盖已有”再次导入方式。给出 Windows/macOS 下载、托盘启动和两个控制台地址。

- [ ] **Step 3: 编写 Codex 与 Claude Code 配置教程**

说明控制台复制配置入口、Codex 首次重启要求、Claude `ANTHROPIC_BASE_URL=http://127.0.0.1:17890` 不追加 `/v1`、本地占位认证值和直接启动命令。

- [ ] **Step 4: 编写日常管理和兼容传输教程**

覆盖选择、排序、隐藏、新增、编辑、导入；说明默认 `httpx`，Cloudflare HTML 403 时只对目标供应商选择 `curl_cffi`，不把真实 401/503 描述为可由传输方式修复。

- [ ] **Step 5: 编写重试、监控和常见问题**

解释输出前重试和手动切换语义、Token/请求记录、远程监控上传；提供 `Not logged in`、403、401、503、页面打不开、端口与配置未生效的排查顺序。

- [ ] **Step 6: 后置开发者内容并更新授权章节**

保留源码运行、服务端部署、项目结构、测试和安全边界，修正编号和旧的 CC Switch 只读描述；链接 `LICENSE`、`NOTICE`、`COMMERCIAL-LICENSE.md`，准确说明 AGPL 与闭源商业授权边界。

- [ ] **Step 7: 运行文档契约测试**

Run: `python -m unittest tests.test_project_documentation`

Expected: PASS。

### Task 4: 验证、记录和交付

**Files:**
- Modify: `docs/changes/2026-08-21-readme-license-guide.md`

- [ ] **Step 1: 更新变更记录为 implemented**

记录 README、LICENSE、NOTICE、商业授权说明和测试的实际改动。

- [ ] **Step 2: 运行完整验证**

Run: `python -m unittest discover -s tests`

Expected: 全部 Python 测试通过。

Run: `node --check proxy_static/app.js; node --check provider_status/static/app.js`

Expected: 两个脚本语法检查通过。

Run: `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`

Expected: 全部 JavaScript 测试通过。

Run: `python scripts/team_policy.py pre-commit; git diff --check`

Expected: 仓库策略和差异格式检查通过。

- [ ] **Step 3: 更新变更记录为 verified**

写入实际测试命令、数量和结果，不记录未执行的验证。

- [ ] **Step 4: 提交、rebase、重新验证并推送**

显式暂存本次文件，使用符合仓库格式的中文提交；rebase 最新 `origin/main` 后重跑完整验证并推送功能分支。

- [ ] **Step 5: 创建 PR、精确 SHA squash 合并并等待自动发布**

通过 GitHub API 创建 PR，尝试自动合并后使用 PR 精确 head SHA squash 合入。验证 `main` 合并提交，等待自动版本、Windows 与 macOS release workflow 全部成功，并检查 Release 资产齐全。
