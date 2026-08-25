+++
id = "2026-08-25-align-select-menu-surface"
type = "style"
release_bump = "none"
status = "verified"
+++

# 统一下拉菜单背景色

## 目标

让自定义下拉框展开菜单与其他中性控件使用相同背景色，避免深色主题下菜单呈现黑色。

## 现状

下拉触发器已使用中性控件表面色，但展开菜单仍使用页面画布色；深色主题下画布色接近黑色，与其他组件不一致。

## 设计范围

- 下拉菜单改用统一的 `--control-surface` 背景色。
- 下拉菜单边框同步使用控件的强边框色。
- 增加样式契约测试并在深色主题下实际验证展开状态。

## 非目标

- 不修改下拉选项高度、圆角、交互状态和选择逻辑。
- 不调整其他按钮、输入框、卡片或页面背景。

## 兼容性

无接口、配置、数据和迁移影响；仅调整 CSS 外观。

## 风险

菜单与触发器颜色相同后边界可能不明显；保留现有阴影并使用强边框维持层级。

## 测试计划

- 运行 Vue UI 定向测试和生产构建。
- 在深色主题中展开下拉菜单并检查计算背景色。
- 运行全量 Node/Python 测试、语法检查、编译检查和 `git diff --check`。

## 实际改动

- `proxy_static/src/styles.css`：下拉菜单背景改用 `--control-surface`，边框改用 `--line-strong`。
- `tests/local_proxy_vue_ui.test.js`：增加下拉菜单表面色与边框契约断言。

## 验证结果

- `npm run build`（`proxy_static`）：通过，生产资源已同步到运行目录。
- `node --test tests/local_proxy_vue_ui.test.js`：7/7 通过。
- 深色主题展开验证：触发器和菜单背景均为 `rgb(27, 48, 71)`，菜单边框为 `rgb(61, 82, 105)`。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：全部 Node 测试通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：486/486 通过。
- `.venv\\Scripts\\python.exe -m compileall -q local_proxy provider_status tests`：通过。
- `node --check proxy_static/src/api.js; node --check proxy_static/vite.config.js; node --check provider_status/static/app.js`：通过。
- `git diff --check`：通过。

## PR

pending
