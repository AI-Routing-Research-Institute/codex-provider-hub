+++
id = "2026-08-21-local-proxy-full-bleed-responsive"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 本机控制台全屏响应式改造

## 目标

让 Codex 与 Claude 共用的本机控制台外壳铺满整个浏览器视口，消除四周留白，并在常见桌面与平板尺寸下保持清晰、无重叠、无非预期横向滚动的布局。

## 现状

控制台通过 `.stage` 在视口中居中显示一个最大宽度 1440px、最大高度 860px 的 `.app-window`。桌面宽屏下外壳四周固定留出 16px，超宽屏还会出现更大的左右空白；外壳同时带有边框、圆角和阴影。现有 860px 响应式断点只在窄屏时移除这些外层装饰。

## 设计范围

- 将 `.stage` 和 `.app-window` 调整为全视口宽高，移除桌面端外层留白、最大尺寸、边框、圆角与阴影。
- 以 `100vh` 作为兼容回退，并使用 `100dvh` 适配支持动态视口单位的浏览器。
- 宽度大于 860px 时保留现有双栏和内部滚动；宽度不大于 860px 时保留单栏堆叠和自然页面滚动。
- 保留标题栏、工具栏、列表、侧栏及设置表单已有的内部间距。
- 更新样式资源缓存版本和对应静态资源测试断言。

## 非目标

- 不修改远程服务商状态页或 Tkinter 探测工具。
- 不重新设计手机端布局，不改变现有 600px 与 440px 断点行为。
- 不改变页面文案、配色、主题、交互逻辑、后端接口、配置或数据结构。

## 兼容性

无 API、配置、数据库或迁移影响。变化仅涉及本机控制台的响应式视觉布局，现有浏览器不支持 `dvh` 时继续使用 `vh`。这是向后兼容的功能增强，版本选择 `minor`。

## 风险

- 桌面端改为全高后，矮视口可能压缩主内容区；继续使用现有网格最小尺寸与内部滚动，并覆盖 1366x768 验证。
- 平板竖屏从内部滚动切换为页面滚动时可能出现横向溢出；保留 860px 单栏规则并检查页面 `scrollWidth` 不超过视口宽度。
- 浏览器缓存可能继续使用旧样式；递增 CSS 查询版本确保新样式被加载。

## 测试计划

- 更新静态资源测试，验证 CSS 缓存版本及全屏布局关键声明。
- 运行 Python 全量单元测试、两套前端 JavaScript 语法检查、全量 Node 测试、Python `compileall` 和 `git diff --check`。
- 在 1920x1080、1440x900、1366x768、1024x768 和 768x1024 视口检查供应商、请求与设置页签。
- 验证浅色和深色主题下外壳均从 `(0, 0)` 铺满视口，桌面双栏和平板单栏无重叠、无非预期横向滚动。

## 实际改动

- `proxy_static/styles.css`：控制台舞台和应用外壳改为全视口宽高，使用 `100vh` 与 `100dvh` 渐进增强，移除桌面外层留白、最大尺寸、边框、圆角和阴影；860px 断点继续使用单栏自然滚动。
- `proxy_static/index.html`：CSS 缓存版本从 26 递增到 27。
- `tests/test_proxy_core.py`：同步缓存版本断言，并增加全视口尺寸和旧最大尺寸移除的回归断言。

## 验证结果

- `.venv\Scripts\python.exe -m unittest tests.test_proxy_core.ProxyAppTests.test_control_page_refresh_config_and_shutdown`：1 项针对性回归测试通过。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：486 项全部通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：45 项全部通过。
- `node --check proxy_static/app.js` 与 `node --check provider_status/static/app.js`：通过。
- `.venv\Scripts\python.exe -m compileall -q local_proxy tests`：通过。
- 浏览器视口测量：1920x1080、1440x900、1366x768、1024x768 下舞台与外壳均从 `(0, 0)` 铺满视口并保持双栏；768x1024 下切换为单栏自然滚动。所有尺寸均无页面横向溢出，供应商区与路由区无重叠。
- 浏览器交互检查：供应商、请求、重试设置页签在 1024x768 和 768x1024 下均正常显示；浅色与深色主题均无横向溢出。
- GitHub API ruleset 核验：`agent-delivery-main`（ID 20543407）为 active branch ruleset，限定 `refs/heads/main`，并包含 deletion 与 pull_request 必需规则，与 `scripts/team_policy.py` 期望一致。
- `git diff --check`：通过，仅显示仓库既有的 Windows LF/CRLF 转换提示。

## PR

pending
