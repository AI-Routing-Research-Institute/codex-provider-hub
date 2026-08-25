+++
id = "2026-08-25-search-filter-order"
type = "style"
release_bump = "none"
status = "verified"
+++

# 调整搜索框与筛选条件顺序

## 目标

将供应商页和请求页的搜索框放在同一行其他筛选条件之前，统一筛选操作顺序。

## 现状

供应商页搜索框位于 Token 时间条件之后，请求页搜索框位于时间、状态和供应商条件之后，与期望的“先搜索、再细化条件”顺序不一致。

## 设计范围

- 供应商页搜索框移动到 Token 时间条件之前。
- 请求页搜索框移动到时间、状态和供应商条件之前。
- 同步请求筛选栏的网格列宽顺序，保持各控件原有宽度语义。
- 增加模板顺序契约测试。

## 非目标

- 不修改筛选逻辑、查询参数、接口、颜色、字号、圆角或控件文案。
- 不增加移动端专属布局。

## 兼容性

无接口、配置、数据和迁移影响；仅调整前端控件显示顺序。

## 风险

请求筛选栏移动后可能出现列宽错配；通过同步网格模板并执行生产构建和浏览器检查验证。

## 测试计划

- 运行 Vue UI 定向测试和生产构建。
- 在供应商页和请求页确认搜索框位于其他条件之前，控件没有溢出。
- 运行 `git diff --check`。

## 实际改动

- `proxy_static/src/components/ProvidersView.vue`：将供应商搜索框移动到 Token 时间条件之前。
- `proxy_static/src/components/RequestsView.vue`：将请求搜索框移动到时间、状态和供应商条件之前。
- `proxy_static/src/styles.css`：按新控件顺序重排请求筛选栏列宽定义。
- `tests/local_proxy_vue_ui.test.js`：增加两个页面的搜索框顺序和请求筛选栏列宽契约断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js`：通过，共 7 项测试。
- `npm run build`（`proxy_static`）：通过，Vite 生产构建完成，22 个模块转换成功。
- `git diff --check`：通过。
- 浏览器检查：供应商页显示为“搜索、Token 时间、管理/刷新”，请求页显示为“搜索、时间、状态、供应商”；两个筛选栏均无横向溢出。

## PR

pending
