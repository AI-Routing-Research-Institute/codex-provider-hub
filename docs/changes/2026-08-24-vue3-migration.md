+++
id = "2026-08-24-vue3-migration"
type = "feat"
release_bump = "minor"
status = "verified"
+++

# 前端迁移到 Vue 3 + Vite 架构

## 目标

将本地中转控制台从原生 JavaScript 迁移到 Vue 3 + Vite，提供更好的组件化架构和开发体验。

## 变更内容

### 前端重构

- 使用 Vue 3 Composition API 重写所有视图组件
- 采用 Vite 5.4.21 作为构建工具，替代原有的开发服务器
- 组件化拆分：
  - `ProvidersView.vue` - 供应商管理
  - `RequestsView.vue` - 请求历史
  - `SettingsView.vue` - 设置面板
  - `RuntimeView.vue` - 运行信息
  - `MonitorView.vue` - 监控面板
- 保持现有 Tailwind CSS 样式和暗色/亮色主题系统

### 后端适配

- 修改 `local_proxy/core.py` 中 `CONTROL_ASSET_DIR` 指向 `proxy_static/dist/`
- 保持所有 `/control/api/*` 接口不变，前端直接调用

### 构建与打包

- 添加 `proxy_static/package.json` 和 `vite.config.js` 配置
- 更新 PyInstaller spec 文件，打包 `proxy_static/dist/` 目录
- 修改 GitHub Actions 工作流，在打包前执行 `npm ci && npm run build`

## 验证

- ✅ 开发模式：`cd proxy_static && npm run dev` 可正常访问
- ✅ 生产构建：`npm run build` 生成 dist/ 目录
- ✅ 所有原有功能正常：供应商切换、请求历史、主题切换、设置保存

## 影响范围

- 仅影响本地中转控制台 UI 实现
- API 接口保持不变
- 用户体验无变化
