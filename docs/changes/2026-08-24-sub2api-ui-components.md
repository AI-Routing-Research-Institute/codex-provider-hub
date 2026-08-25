+++
id = "2026-08-24-sub2api-ui-components"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 采用 Sub2API 风格的 Vue UI 基础组件

## 目标

将本地控制台从浏览器原生表单控件和分散手写样式，升级为接近 sub2api-frontend 的 Tailwind 4 设计层与本地 Ui 组件，同时保留现有原版控制台的信息架构、接口和桌面/平板布局。

## 现状

当前前端使用 Vue 3 + Vite，但生产样式主要由单一手写 CSS 文件提供，多个页面直接使用原生 `<select>`。控件外观受浏览器影响，和参考的 sub2api 风格不一致。

## 设计范围

- 引入 Tailwind CSS 4 Vite 插件和本地主题变量，不引入远程运行时资源。
- 新增可访问的 `UiSelect` 组件，支持键盘操作、点击外部关闭、Esc 关闭、禁用态和动态选项；下拉菜单采用传送到 `body` 的固定浮层、圆角卡片、阴影和柔和选中态，复刻 sub2api 的 Select 方案。
- 新增统一的 `UiIcon` 线性图标组件，采用 24px viewBox、currentColor 描边和固定尺寸，覆盖刷新、设置、主题、关闭、复制和下拉箭头等操作图标。
- 替换控制台供应商、请求、设置和弹窗中的原生下拉框。
- 替换现有按钮中的 Unicode/文字符号图标，保留按钮的 aria-label 和 title。
- 保留现有 Vue 状态、API 请求、页面结构、接口参数和全视口桌面/平板布局。
- 为新组件补充前端契约测试，并验证构建产物。

## 非目标

- 不迁移后端 API、Pinia 状态管理、Axios 请求层或 VueUse 工具。
- 不引入 Element Plus、Naive UI 或其他重量级组件库。
- 不引入远程图标字体或运行时 CDN；图标路径随前端构建产物发布。
- 不新增移动端专属设计，不重做原版页面的信息架构。

## 兼容性

现有 API、配置、数据值和服务入口保持不变。新增 Tailwind 构建依赖只影响前端构建产物；下拉组件继续发出原有 `v-model` 值。

## 风险

- 自定义下拉菜单可能出现焦点、定位或滚动容器问题；通过原生键盘事件、ARIA 属性和定向 DOM 测试缓解。
- Tailwind 预设样式可能影响旧 CSS；仅使用主题与 utilities，保留现有布局规则并通过构建和静态资源测试验证。
- 动态选项可能包含数字或空值；组件按原值比较并保留 `v-model` 类型。

## 测试计划

- 在 `proxy_static` 执行 `npm run build`。
- 运行 Vue UI Node 契约测试。
- 运行控制台静态资源相关 Python 单测。
- 执行 `git diff --check`，并人工检查桌面和平板下拉菜单定位与键盘操作。

## 实际改动

已在 `proxy_static/vite.config.js` 接入 Tailwind CSS 4 Vite 插件，在 `proxy_static/src/styles.css` 增加 sub2api 风格主题变量；新增 `UiSelect.vue` 和 `UiIcon.vue`，将下拉菜单改为传送到 `body` 的固定浮层并复刻 sub2api 的圆角、阴影、间距、选中态和键盘交互，替换供应商、请求、设置、监控和弹窗中的原生下拉框及 Unicode 操作图标。更新 Vue 和静态资源契约测试，并重新生成 `proxy_static/dist`。

## 验证结果

- `npm run build`（`proxy_static`）：通过，生成 63.70 KB CSS 和 127.52 KB JS。
- `node --test tests/local_proxy_vue_ui.test.js`：5 项全部通过。
- `.venv\\Scripts\\python.exe -m unittest tests.test_claude.ClaudeProxyAppTests.test_control_assets_and_claude_config_endpoint tests.test_proxy_core.ProxyAppTests.test_control_page_refresh_config_and_shutdown`：2 项全部通过。
- `git diff --check`：通过，仅有 Windows 换行转换提示。

## PR

pending
