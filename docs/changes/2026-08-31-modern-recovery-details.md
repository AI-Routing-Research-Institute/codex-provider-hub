+++
id = "2026-08-31-modern-recovery-details"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复新版自动恢复详情

## 目标

让新版右侧自动恢复状态支持通过详情按钮查看最近恢复历史，行为与经典界面一致。

## 现状

新版仅显示自动恢复状态和无行为的详情按钮，没有读取恢复历史、展示条目或动态定位弹窗；经典界面已通过 `/api/recovery-history` 提供这些能力。

## 设计范围

- 当存在最近恢复记录时显示可点击的 `i` 按钮。
- 从 `/api/recovery-history` 读取最近 24 小时记录，显示时间、供应商、恢复类型、结果和错误摘要。
- 支持加载态、空态、错误态和 `next_cursor` 分页。
- 根据详情按钮与弹窗实际尺寸动态定位，窗口变化时重新计算；移动端使用底部全宽布局。

## 非目标

- 不修改后端恢复策略、接口或数据结构。
- 不改变经典界面和自动重试行为。

## 兼容性

复用现有恢复历史接口，无配置、数据库或迁移影响，选择 `patch` 版本提升。

## 风险

详情读取失败不应影响恢复状态显示；弹窗使用独立加载状态并保留错误提示。定位使用实际尺寸，避免固定高度造成过大间距或视口溢出。

## 测试计划

- Vue 结构测试覆盖按钮条件、接口、条目字段、分页、定位和 resize 清理。
- 运行 JavaScript 测试、Vite 构建和 git diff 检查。
- 浏览器验证桌面和移动端打开、关闭、加载及分页表现。

## 实际改动

- `proxy_static/src/components/ProvidersView.vue` 为恢复详情按钮增加 `/api/recovery-history` 加载、空态/错误态/分页渲染和动态定位；按钮仅在存在恢复记录时显示。
- `proxy_static/src/styles.css` 增加恢复记录空状态样式，沿用现有恢复弹窗滚动和移动端布局。
- `tests/local_proxy_vue_ui.test.js` 增加恢复详情接口、条目字段、分页、定位和 resize 清理回归断言。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js tests/local_proxy_console_ui.test.js`：通过，18/18。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check`：通过，仅有行尾转换提示。

## PR

pending
