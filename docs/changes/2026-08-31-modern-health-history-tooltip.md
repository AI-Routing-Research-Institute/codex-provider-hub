+++
id = "2026-08-31-modern-health-history-tooltip"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 增加新版检测历史悬停详情

## 目标

让新版服务器检测历史柱与经典界面一致，悬停时显示单次检测详情。

## 现状

新版柱状图没有交互事件，无法查看单次检测时间、状态、延迟或失败原因。

## 设计范围

- 顶部和模型历史柱支持鼠标悬停详情。
- 按柱子实际位置动态定位提示层；空占位不显示。
- 显示检测时间、状态、延迟和错误摘要。

## 非目标

- 不修改检测接口、采集逻辑或经典界面。

## 兼容性

仅前端交互增强，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

- 根据整组柱子的横坐标计算索引时必须限制边界，避免悬浮到相邻记录。
- 固定提示层必须限制在视口内，并在关闭详情或组件卸载时清理状态。

## 测试计划

- 验证可用、失败和空槽位的悬浮行为，以及时间、状态、延迟和错误原因内容。
- 运行 Vue 结构测试、完整 JavaScript 测试、Vite 构建和 Python单测，并进行桌面与移动浏览器检查。

## 实际改动

- `proxy_static/src/components/ProvidersView.vue` 为摘要和详情历史柱增加与经典界面一致的悬浮/点击详情浮层、空槽位处理和动态定位；模型详情标题使用“模型名 最近检测历史”，错误原因独立显示。
- `proxy_static/src/styles.css` 复用历史详情浮层的层级和内容样式。
- `tests/local_proxy_vue_ui.test.js` 增加历史详情内容、交互和清理断言。

## 验证结果

- `.\.venv\Scripts\python.exe scripts/team_policy.py pre-push`：通过；Python 单测 514/514，JavaScript 测试 71/71，依赖锁定安装、Vite 构建和 JavaScript 语法检查均成功。
- JavaScript 语法检查：通过。
- 全部 JavaScript 测试：通过，71/71。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check`：通过。
- 浏览器验证：悬浮可用柱显示模型、时间和状态；悬浮失败柱额外显示错误原因，提示层按柱子位置动态显示。

## PR

pending
