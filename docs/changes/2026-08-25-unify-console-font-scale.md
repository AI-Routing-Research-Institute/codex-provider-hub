+++
id = "2026-08-25-unify-console-font-scale"
type = "style"
release_bump = "none"
status = "verified"
+++

# 统一控制台小字号文字

## 目标

将控制台实际内容文字统一到正文至少 13px、辅助文字至少 12px，修复时间弹层、认证列、请求表格及其他页面残留的 9px、10px、11px 小字。

## 现状

上一轮字号调整只覆盖了标题、下拉框、按钮和部分供应商字段；时间弹层、认证状态、检测元信息、请求记录及设置页仍保留旧的小字号声明。

## 设计范围

- 新增正文 13px、辅助文字 12px 的统一字号变量。
- 将供应商、请求、设置、运行、监控及各类弹层的正文和辅助信息映射到统一字号层级。
- 时间弹层标题使用 16px，说明和字段标签使用 12px，输入内容使用 13px。
- 保留计数徽章和信息图标内部字符的既有小字号。
- 增加样式契约测试，限制内容文字重新出现 9px、10px、11px。

## 非目标

- 不修改颜色、圆角、间距、布局、交互逻辑或后端接口。
- 不增加移动端专属字号或布局规则。
- 不缩小已有 14px 及以上文字。

## 兼容性

无接口、配置、数据和迁移影响；仅调整 CSS 字号。

## 风险

密集表格和弹层可能因字号增加出现溢出；保持现有布局尺寸，并通过桌面和平板宽度的浏览器检查验证文本适配。

## 测试计划

- 运行 Vue UI 定向测试和生产构建。
- 在浅色、深色主题检查时间弹层、供应商认证列和请求表格。
- 在桌面和平板宽度检查文字不溢出、不遮挡。
- 运行全量 Node/Python 测试、语法检查、编译检查和 `git diff --check`。

## 实际改动

- 在 `proxy_static/src/styles.css` 新增正文 `13px`、辅助文字 `12px` 的统一变量。
- 将供应商、请求、设置、运行、监控、用量历史、会话路由及弹层中的内容性小字号映射到统一层级。
- 将时间弹层标题调整为 `16px`，说明、标签和错误提示调整为 `12px`，输入内容调整为 `13px`。
- 保留页签计数徽章和 Token 信息图标的 `9px` 字号，未修改颜色、圆角、间距、布局、交互或接口。
- 在 `tests/local_proxy_vue_ui.test.js` 增加字号变量、关键区域字号及小字号允许列表的契约断言。

## 验证结果

- `npm run build`（`proxy_static`）：通过，Vite 生产构建完成，22 个模块转换成功。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：通过，共 12 项 Node 测试。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：通过，共 486 项 Python 测试。
- `.venv\Scripts\python.exe -m compileall -q local_proxy provider_status tests`：通过。
- `node --check proxy_static/src/api.js`、`node --check proxy_static/vite.config.js`、`node --check provider_status/static/app.js`：通过。
- `git diff --check`：通过。
- 浏览器浅色、深色桌面检查：时间弹层标题为 `16px`、说明和标签为 `12px`、输入为 `13px`；供应商认证和正文为 `13px`，辅助信息为 `12px`，未发现文字溢出。
- 浏览器 `1024x768` 检查：时间弹层无横向或纵向溢出，请求表格保留原有横向滚动；供应商固定列在平板宽度的既有裁切不属于本次纯字号改动范围。

## PR

pending
