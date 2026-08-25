+++
id = "2026-08-25-distinguish-button-surfaces"
type = "style"
release_bump = "none"
status = "verified"
+++

# 区分按钮与页面背景层级

## 目标

参考 Sub2API，为中性按钮和自定义下拉触发器提供独立于页面背景的控件表面色，使按钮边界和层级清晰可见。

## 现状

部分中性按钮直接使用页面表面色，深色主题下尤其容易与背景融为一体；自定义下拉触发器与其他按钮的底色层级也不一致。

## 设计范围

- 为浅色和深色主题增加统一的中性控件表面色与轻量阴影。
- 将中性按钮、自定义下拉触发器和日历按钮统一使用该表面层级。
- 保持主按钮、危险按钮、输入框、卡片及布局不变。
- 增加样式契约测试。

## 非目标

- 不修改主题主色、字体、圆角、间距和交互逻辑。
- 不调整绿色主操作按钮或透明文字按钮。

## 兼容性

无接口、配置、数据和迁移影响；仅调整 CSS 外观。

## 风险

控件底色提高后可能过于突出；使用接近 Sub2API 的克制明度差和轻量阴影，并在浅色、深色主题中分别验证。

## 测试计划

- 运行 Vue UI 定向测试和生产构建。
- 在浅色、深色主题中检查控件与页面背景的计算颜色和页面截图。
- 运行全量 Node/Python 测试、语法检查、编译检查和 `git diff --check`。

## 实际改动

- `proxy_static/src/styles.css`：新增浅色/深色控件表面色与阴影变量，将中性按钮、自定义下拉触发器、日历按钮和辅助操作按钮统一到独立控件层级。
- `tests/local_proxy_vue_ui.test.js`：增加两种主题的控件表面变量及关键按钮背景契约断言。

## 验证结果

- `npm run build`（`proxy_static`）：通过，生产资源已同步到运行目录。
- `node --test tests/local_proxy_vue_ui.test.js`：7/7 通过。
- 浏览器浅色主题：按钮 `rgb(255, 255, 255)`，页面 `rgb(245, 250, 252)`，并应用轻量阴影。
- 浏览器深色主题：按钮 `rgb(27, 48, 71)`，页面 `rgb(19, 40, 61)`。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：全部 Node 测试通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：486/486 通过。
- `.venv\\Scripts\\python.exe -m compileall -q local_proxy provider_status tests`：通过。
- `node --check proxy_static/src/api.js; node --check proxy_static/vite.config.js; node --check provider_status/static/app.js`：通过。
- `git diff --check`：通过。

## PR

pending
