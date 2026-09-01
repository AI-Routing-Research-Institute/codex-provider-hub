+++
id = "2026-08-31-modern-provider-health-details"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复新版服务器检测详情

## 目标

让新版供应商页的服务器检测详情弹窗与经典界面一致，显示完整检测信息并正确贴近点击按钮。

## 现状

新版弹窗使用固定坐标，仅显示状态、可用率和延迟，缺少最后探测时间、检测模型及模型历史。

## 设计范围

- 根据详情按钮和弹窗实际尺寸动态定位，窗口变化时重新计算。
- 展示最后探测时间、24 小时可用率、最近延迟、检测模型数量、模型状态与最近检测历史。
- 保留移动端视口约束和关闭行为。

## 非目标

- 不修改服务器检测接口或经典界面。

## 兼容性

仅新版前端展示修正，无接口和数据迁移影响，使用 `patch` 版本提升。

## 风险

- 检测地址存在多个前缀候选时必须保持未匹配，避免把其他供应商的健康数据错误展示出来。
- 弹窗内容较多时可能超出视口，因此限制最大高度并保留内部纵向滚动。

## 测试计划

- 运行 Vue 结构测试、全部 JavaScript 测试、JavaScript 语法检查、Vite 构建与 Python 单测。
- 在桌面和移动视口对照经典界面，检查上下定位、顶部指标、模型历史数量、颜色和横向溢出。

## 实际改动

- `proxy_static/src/components/ProvidersView.vue` 增加模型详情、检测历史、按钮锚点定位和 resize 清理，按经典规则进行精确或唯一前缀地址匹配，并直接从已选检测对象读取 24 小时可用率与最近延迟。
- `proxy_static/src/styles.css` 保持详情内容自然高度、限制视口内最大高度，并保留移动端底部 12px 布局。
- `tests/local_proxy_vue_ui.test.js` 增加详情字段、地址匹配和动态定位回归断言。
- `proxy_static/dist/` 更新为本次验证通过的 Vite 构建产物，并按仓库属性保持入口 HTML 为 LF 行尾。

## 验证结果

- `.\.venv\Scripts\python.exe scripts/team_policy.py pre-push`：通过；Python 单测 514/514，JavaScript 测试 71/71，依赖锁定安装、Vite 构建和 JavaScript 语法检查均成功。
- JavaScript 语法检查：通过，经典脚本、新版共享脚本和状态页脚本均无语法错误。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }`：通过，71/71。
- `npm run build --prefix proxy_static`：通过，Vite 构建成功。
- `git diff --check`：通过。
- 浏览器对照：经典页与新版页同一供应商均显示 120 个详情槽位，其中 67 个真实状态槽位、53 个灰色缺失槽位；顶部实际显示 `24 小时可用率 95.26%` 与 `最近延迟 90 秒`；桌面端动态显示在按钮上方，移动端左右及底部保持 12px 且无横向溢出。

## PR

pending
