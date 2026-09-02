+++
id = "2026-09-01-classic-toast-icon-alignment"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 对齐经典界面消息提示图标

## 目标

修正经典界面消息提示框前置状态图标的对齐，使图标内容在圆形区域内水平、垂直居中。

## 现状

经典提示框的状态图标使用 `span` 元素，会同时命中提示正文的通用 `span` 样式，继承额外上边距和文本行高，造成图标视觉偏移。

## 设计范围

- 仅覆盖经典界面 `.toast-icon` 的对齐相关样式。
- 使用固定尺寸的 SVG 绘制状态符号，避免字体基线造成视觉偏移。
- 清除通用正文样式施加到图标上的上边距和行高，明确图标在提示框网格及自身圆形区域内居中。
- 更新经典 CSS 和 JavaScript 的资源版本参数，确保浏览器加载修正后的图标。

## 非目标

不修改新版界面提示框，不修改经典提示框的位置、尺寸、颜色、文字、关闭按钮、动画或触发逻辑。

## 兼容性

仅经典界面 CSS 对齐修正，无接口、配置、数据和迁移影响；使用 `patch` 版本提升。

## 风险

选择器优先级不足或资源缓存可能导致修正未生效；使用限定于经典提示框图标的高精度选择器、固定尺寸 SVG、资源版本参数和自动化断言规避。

## 测试计划

运行经典界面相关结构测试、JavaScript 语法检查和 `git diff --check`，检查成功与失败图标的 SVG 结构，并确认新版提示框文件无变化。

## 实际改动

- `proxy_static/classic/app.js` 将字体状态符号替换为固定视口的 SVG 成功勾和失败感叹号，消除字体基线偏移。
- `proxy_static/classic/styles.css` 使用更高优先级恢复绿色圆形容器的 `display: grid` 和 `place-items: center`，避免被通用 `.toast span { display: block; }` 覆盖；SVG 不再使用位移补偿。
- `proxy_static/classic/index.html` 更新经典 CSS、JavaScript 资源版本参数，避免浏览器继续使用旧缓存。
- `tests/local_proxy_vue_ui.test.js` 增加经典 SVG 图标、视觉偏移和资源版本回归断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：通过，21/21。
- `node --test tests/local_proxy_console_ui.test.js`：通过，1/1。
- `node --check proxy_static/classic/app.js`：通过。
- `git diff --check`：通过。
- `'' | & .venv\\Scripts\\python.exe scripts/team_policy.py pre-push`：通过，Python 单测 521/521、全部 JavaScript 测试通过、构建和语法检查通过。

## PR

pending
