+++
id = "2026-08-21-tailwind-css-integration"
type = "feature"
release_bump = "minor"
status = "completed"
+++

# 集成 Tailwind CSS 并现代化 UI 组件

## 目标

为 Codex Provider Hub 本地控制台引入 Tailwind CSS 开发环境，并现代化 toolbar 区域的表单控件，提升界面的现代感和一致性。

## 现状

- 界面样式完全手写 CSS（61KB）
- 字体大小不统一（11px、12px、13px 混用）
- 选择框使用浏览器原生样式，不同浏览器显示不一致
- 按钮和输入框尺寸较小（36px），移动端可点击性不佳
- 边框颜色较深（line-strong），视觉较重
- 缺少现代化的交互反馈（悬停效果、阴影等）

## 设计范围

1. **Tailwind CSS 集成**
   - 在 `proxy_static/dev/` 创建开发环境
   - 通过 `@import` 保留所有原始样式
   - 渐进式迁移策略，不破坏现有界面

2. **Toolbar Actions 现代化**
   - 统一字体大小为 13px
   - 现代化选择框（自定义箭头、深色模式支持）
   - 优化尺寸（38px 高度、6px 圆角）
   - 更柔和的边框色（line 替代 line-strong）
   - 添加现代化交互效果（悬停上浮、阴影、focus 光晕）

3. **全局表单控件统一**
   - 所有 select 元素自动应用现代化样式
   - 所有 input/textarea 统一字号和交互效果

## 非目标

- 不重构 HTML 结构
- 不修改 JavaScript 逻辑
- 不改动其他页面组件（本次仅 toolbar）
- 不引入 Vue/React 等编译型框架

## 兼容性

**无破坏性改动**：

- ✅ 保留所有原始 CSS 变量
- ✅ 保留所有原始类名
- ✅ JavaScript 功能不受影响
- ✅ 深色模式完全兼容
- ✅ CI/CD 无需修改（编译后的 CSS 提交到 Git）
- ✅ PyInstaller 打包流程不变
- ✅ 其他开发者 clone 后可直接运行（有编译好的 CSS）

**开发依赖**：

只有需要修改样式的开发者才需要 `npm install`，普通用户不受影响。

## 风险

**风险 1**：CSS 文件大小增加

- **缓解**：Tailwind 按需编译，最终只增加 ~5KB（61KB → 56KB，实际反而减小）

**风险 2**：深色模式兼容性

- **缓解**：所有颜色使用 CSS 变量，自定义 SVG 箭头支持深色模式切换

**风险 3**：浏览器兼容性

- **缓解**：Tailwind 自动添加浏览器前缀，支持现代浏览器

## 测试计划

**自动化测试**：
- ✅ JavaScript 语法检查（`node --check`）
- ✅ CSS 编译成功

**人工验证**：
- ✅ 浅色模式显示正常
- ✅ 深色模式显示正常
- ✅ 响应式布局（桌面/平板/手机）
- ✅ 所有交互效果（悬停、focus、active）
- ✅ 所有表单控件可用
- ✅ 选择框自定义箭头显示正确

## 实际改动

**新增文件**：
- `proxy_static/dev/package.json` - npm 配置
- `proxy_static/dev/tailwind.config.js` - Tailwind 配置
- `proxy_static/dev/styles.src.css` - CSS 源文件
- `proxy_static/dev/README.md` - 开发指南
- `proxy_static/dev/*.md` - 实施文档
- `proxy_static/styles.original.css` - 原始样式备份

**修改文件**：
- `proxy_static/styles.css` - 编译后的 CSS（61KB → 56KB）
- `.gitignore` - 忽略 node_modules

**改动内容**：

1. **字体统一**：
   - 所有按钮、输入框、选择框字号统一为 13px
   - 字重从 700 改为 600（更现代）

2. **尺寸优化**：
   - 高度：36px → 38px
   - 圆角：5px → 6px
   - 间距：8px → 12px

3. **选择框现代化**：
   - `appearance: none` 去除原生样式
   - 自定义 SVG 下拉箭头
   - 深色模式自动切换箭头颜色

4. **边框优化**：
   - `var(--line-strong)` → `var(--line)`（更柔和）

5. **交互效果**：
   - 悬停：轻微上浮（1px）+ 微妙阴影
   - Focus：3px teal 色光晕
   - 图标悬停时变色

## 验证结果

**编译测试**：
```bash
cd proxy_static/dev
npm install
npm run build
# ✅ Done in 1050ms
```

**文件大小**：
```bash
# Before: 61.39 KB (原始)
# After:  55.83 KB (编译后)
# 减少：  9% 🎉
```

**语法检查**：
```bash
node --check proxy_static/app.js
# ✅ 无错误
```

**浏览器测试**：
- ✅ Chrome/Edge - 显示正常
- ✅ 深色模式切换 - 正常
- ✅ 选择框箭头 - 显示正确
- ✅ 悬停效果 - 流畅
- ✅ 响应式布局 - 正常

**打包测试**：
```bash
# PyInstaller 仍然只打包 proxy_static/ 根目录
# dev/ 目录不会被打包
# ✅ 打包流程不受影响
```

## PR

本次提交包含在当前分支 `feat/local-proxy-full-bleed-responsive`
