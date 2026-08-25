+++
id = "2026-08-25-unify-rounded-ui-controls"
type = "style"
release_bump = "none"
status = "verified"
+++

# 统一控制台圆角控件

## 目标

将控制台中交互控件、面板和弹窗的倒角统一为接近 Sub2API 的圆润样式，消除同一界面中圆角不一致的问题。

## 现状

按钮、输入框、表单容器和列表面板分别使用 4px、5px、6px、7px、8px 等圆角；自定义下拉框已使用 12px，导致视觉不统一。

## 设计范围

- 统一主要按钮、输入框、选择器和图标按钮为 12px 圆角。
- 统一主要列表面板、设置表单、弹窗和提示容器为 12px 圆角。
- 保留状态徽章、圆点、滚动条和下拉菜单选项等具有明确语义的特殊形状。
- 增加样式契约测试，防止核心控件圆角回退。

## 非目标

- 不改变功能、数据、布局结构、颜色主题或响应式断点。
- 不将所有元素改为胶囊或完全圆形。

## 兼容性

无接口、配置、数据和迁移影响；仅调整 CSS 外观。

## 风险

圆角增大后，窄容器中的视觉密度可能略有变化；通过限定为现有控件和外框并运行构建及测试进行缓解。

## 测试计划

- 运行 Vue 前端构建。
- 运行本地代理 Vue UI 测试及全量 Node/Python 测试。
- 运行 JavaScript 语法检查、Python 编译检查和 `git diff --check`。

## 实际改动

- `proxy_static/src/styles.css`：新增控件/面板圆角变量，将主要按钮、输入框、选择器、列表面板、弹窗和提示容器统一为 12px 圆角。
- `tests/local_proxy_vue_ui.test.js`：增加核心控件与面板圆角契约断言。

## 验证结果

- `npm run build`（`proxy_static`）：通过，Vite 生产构建成功。
- `node --test tests/local_proxy_vue_ui.test.js`：7/7 通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：全部 Node 测试通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：486/486 通过。
- `.venv\\Scripts\\python.exe -m compileall -q local_proxy provider_status tests`：通过。
- `node --check proxy_static/src/api.js; node --check proxy_static/vite.config.js; node --check provider_status/static/app.js`：通过。
- `git diff --check`：通过。

## PR

pending
