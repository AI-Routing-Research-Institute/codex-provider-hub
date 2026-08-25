+++
id = "2026-08-25-fix-request-custom-time"
type = "fix"
release_bump = "none"
status = "verified"
+++

# 修复请求记录自定义时间筛选

## 目标

让请求记录页面的自定义时间按钮能够打开时间选择器，并使用所选开始和结束时间查询请求记录。

## 现状

请求记录页面显示了日历按钮，但按钮没有点击处理，页面也未挂载时间范围弹层；请求查询只发送固定时间窗口参数。

## 设计范围

- 复用现有 `TimeRangePopover` 组件为请求记录页提供自定义时间输入。
- 在请求时间选项中加入“自定义时间”。
- 应用自定义范围后向请求接口发送 `window=custom`、`start_at` 和 `end_at`。
- 分页加载和取消行为继续沿用现有请求筛选逻辑。
- 增加前端结构契约断言。

## 非目标

- 不修改后端时间边界、数据库保留周期、固定时间选项或其他页面。
- 不改变请求记录表格、排序和状态筛选功能。

## 兼容性

无接口、配置和数据迁移影响；使用现有请求查询参数。

## 风险

自定义范围未应用时可能触发一次无效查询；通过在查询前打开弹层并等待应用来避免该情况。

## 测试计划

- 运行 Vue UI 定向测试和前端构建。
- 运行全量 Node/Python 测试、语法检查、Python 编译检查和 `git diff --check`。

## 实际改动

- `proxy_static/src/components/RequestsView.vue`：为日历按钮绑定自定义时间弹层，增加自定义时间选项，并集中生成包含 `start_at`、`end_at` 和分页游标的请求参数。
- `tests/local_proxy_vue_ui.test.js`：增加请求页自定义时间控件和参数传递的结构契约断言。

## 验证结果

- `npm run build`（`proxy_static`）：通过，生产资源已同步到运行目录。
- `node --test tests/local_proxy_vue_ui.test.js`：7/7 通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：全部 Node 测试通过。
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"`：486/486 通过。
- `.venv\\Scripts\\python.exe -m compileall -q local_proxy provider_status tests`：通过。
- `node --check proxy_static/src/api.js; node --check proxy_static/vite.config.js; node --check provider_status/static/app.js`：通过。
- `git diff --check`：通过。

## PR

pending
