+++
id = "2026-08-31-selectable-console-ui"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 支持切换经典与新版控制台

## 目标

在最新主线后端上同时提供经典原生控制台和 #48 引入的 Vue 控制台，用户可在运行设置中选择界面，保存后刷新页面立即生效，不通过切换 Git 版本改变界面。

## 现状

主线 `f8cb689` 只打包和运行 Vue/Vite 控制台；本地未提交分支已恢复经典 HTML、CSS、JavaScript 控制台并移植 #51、#52 功能，但删除了 Vue 源码和构建产物。两种实现使用同一组控制 API，却无法在同一个发布包中共存或由用户切换。

## 设计范围

- 保留主线 Vue/Vite 源码和构建流程，将经典控制台放入独立资源目录，并在桌面包中同时携带两套资源。
- 在共享运行设置中增加经过枚举校验的 `console_ui`，支持 `modern` 和 `classic`，默认 `modern` 以保持已发布版本行为。
- Codex 与 Claude 共用界面选择；控制页按持久化设置返回对应入口，静态资源使用独立命名空间并继续阻止路径穿越。
- 两套运行设置页面均提供“经典界面 / 新版界面”选择，修改后自动刷新当前控制台，无需重启中转。
- 支持 `?ui=classic` 和 `?ui=modern` 临时覆盖入口，作为某套前端无法加载时的恢复通道，不修改持久化设置。
- 保留经典界面已移植的 CCS 导入、供应商独立管理、请求与恢复历史、监控排序、停止自动监控和上传检测显示设置。

## 非目标

- 不改变供应商选择、会话路由、重试、SSE、诊断、认证或状态服务行为。
- 不让 Codex 与 Claude 分别选择不同界面。
- 不在运行时构建 Vue，不提交 `node_modules`、凭据、数据库或本地配置。
- 不删除或改写已经发布的 #48 至 #52 变更说明。

## 兼容性

控制页面 URL 和现有 API 保持兼容；共享设置新增可选字段，缺失或无效时回退为 `modern`，无需数据库迁移。经典与新版界面共享后端数据和协议设置。新增完整用户功能，版本选择 `minor`。

## 风险

两套入口可能混用静态资源或发布包漏带其中一套；通过资源命名空间、白名单路由、双入口 smoke test 和打包测试缓解。后续前端功能需要同步维护两套实现；通过共享 API 契约测试和两套前端回归测试降低功能漂移。界面切换后若目标资源损坏，可使用查询参数临时进入另一套界面恢复设置。

## 测试计划

- 覆盖共享设置默认值、持久化、无效值回退，以及运行设置 API 的读取和更新。
- 覆盖 Codex、Claude 在 `modern`、`classic` 设置与查询参数覆盖下返回正确入口。
- 覆盖经典和新版静态资源、路径穿越防护、打包文件与应用 smoke test。
- 运行全部 Python 单测、经典 JavaScript 测试、Vue 测试、Vite 构建、JavaScript 语法检查、Python `compileall` 和 `git diff --check`。
- 本地启动后验证两种协议、两套界面、桌面和窄屏布局，并双向切换确认无需重启。

## 实际改动

- 新增 `local_proxy/control_ui.py`，集中校验 `modern` / `classic` 模式、选择入口文件，并以白名单和规范化路径隔离两套静态资源。
- 扩展 `local_proxy/shared_settings.py`、`local_proxy/core.py`、`local_proxy/server.py` 和 `local_proxy/application.py`，持久化共享界面设置，支持查询参数临时覆盖，并让 Codex、Claude 共用同一选择。
- 将经典控制台整理到 `proxy_static/classic/`，保留 Vue/Vite 源码与 `proxy_static/dist/` 构建产物；两套运行设置均增加界面分段选择，保存后移除查询覆盖并刷新页面。
- 更新 Windows/macOS 打包配置、发布工作流和 smoke test，确保发布包同时包含经典资源和新版资源；同步更新 `.gitignore`、`.gitattributes`、项目策略及中英文文档。
- 新增 `tests/test_control_ui.py` 和经典控制台回归测试，并扩展共享设置、服务器路由、核心 API、Vue 界面、打包与仓库策略测试。

## 验证结果

- `npm ci --prefix proxy_static`：通过；npm 报告现有依赖含 1 个 moderate、1 个 high 漏洞，未执行强制升级。
- `npm run build --prefix proxy_static`：通过，Vite 构建 26 个模块，生成 `index-bvgJgLj9.css` 和 `index-BBo9KfVt.js`。
- `python -m unittest discover -s tests -p "test_*.py"`：506 项通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：15 个测试文件、64 项通过。
- JavaScript 语法检查、Python `compileall`、应用 smoke test 和 `git diff --check`：通过；smoke test 确认 6 个控制台资源以及 `classic`、`modern` 两种模式。
- 本地服务 `127.0.0.1:17991` 浏览器验证：Codex、Claude 的 `?ui=classic` 与 `?ui=modern` 均加载正确且互不混用的资源；运行设置均显示界面选择；1280x720 与 390x844 下无横向溢出，浏览器控制台无错误；查询覆盖未写入持久设置。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/53
