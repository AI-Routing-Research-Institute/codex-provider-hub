+++
id = "2026-09-03-version-vertical-center"
type = "style"
release_bump = "none"
status = "verified"
+++

# 版本与更新版本号上下居中

## 目标

“版本与更新”行的版本号与右侧按钮文字上下居中对齐，不再偏上。

## 现状

外层 `.setting-row` 有 `align-items: center`，但内层 `.setting-control-with-action` 是 `display:grid` 且未写 `align-items`（默认 `stretch`）：`<code>` 版本号被纵向拉满、文字贴顶；按钮文字天生居中，于是版本号看起来偏上。新版与经典界面同一处写法相同，都有此问题。

## 设计范围

- `proxy_static/src/styles.css` 与 `proxy_static/classic/styles.css` 的 `.setting-readonly-row code` 各加 `align-self: center`。
- `tests/local_proxy_vue_ui.test.js` 与 `tests/local_proxy_console_ui.test.js` 各加一条规则存在性断言。
- 检查更新按钮后加 GitHub 按钮（文案 `GitHub`，新开标签页跳仓库主页）：新版 `RuntimeView.vue` 与经典 `index.html` 各一处 `<a>`（经典 `app.js` 无需改动）；按钮包一层 `.update-actions`（flex 右对齐可换行）；两套样式加 `a.secondary-button` 与 `.update-actions` 作用域规则。
- 影响仅限只读行的 `code`（版本号与客户端配置文件路径），输入框、按钮、其他行与窄屏堆叠布局不受影响。

## 非目标

- 不改任何布局结构与列宽，不改检查/更新逻辑。

## 兼容性

无。纯视觉对齐，`none` 不触发发版。

## 风险

- 选择器写错导致断言/样式失效：回归测试覆盖。
- 缓解：断言与样式同文件评审。

## 测试计划

- `node --test tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js`。
- JS 全量测试；浏览器肉眼确认版本号与按钮文字水平对齐。

## 实际改动

- `proxy_static/src/styles.css` 与 `proxy_static/classic/styles.css`：`.setting-readonly-row code` 各加 `align-self: center`（+1/-1 行）；新增 `a.secondary-button.setting-action-button` 与 `.update-actions` 作用域规则。
- `proxy_static/src/components/RuntimeView.vue`：按钮包 `update-actions` 层，检查更新后加 GitHub 外链（仓库主页，新开标签页）。
- `proxy_static/classic/index.html`：`#update-check-button` 后加 `#update-github-link` 外链（`app.js` 无需改动）。
- `tests/local_proxy_vue_ui.test.js` 与 `tests/local_proxy_console_ui.test.js`：加规则与 href 断言。
- `proxy_static/dist`：重建，新 bundle 含居中规则与仓库地址；移除被替换的旧 hash 产物。
- 新增本说明 `docs/changes/2026-09-03-version-vertical-center.md`。

## 验证结果

- 浏览器实测（真实样式，1280px 视口，文本行矩形中心对比）：修复前版本号文字比按钮文字偏上 10.5px；修复后差值 0px，上下居中。
- GitHub 按钮实测：版本号/检查更新/GitHub 三者文字中心两两差值 0；href 为仓库主页；`target=_blank` + `rel=noreferrer noopener`；链接渲染为按钮样式。
- `node --test tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js`：26 tests 通过；JS 全量 0 fail。
- 改动文件换行符检查通过（全 LF）。
- rebase 到 `origin/main 9302d55`（#78/#79）后复验见推送门禁；`dist/index.html` 入库归一为 LF（`--ignore-cr-at-eol` 为空）。

## PR

pending
