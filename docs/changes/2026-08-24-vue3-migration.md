+++
id = "2026-08-24-vue3-migration"
type = "feature"
release_bump = "minor"
status = "planned"
+++

# 迁移到 Vue 3 + Vite 前端架构

## 目标

将 Codex Provider Hub 本地控制台从原生 JavaScript + DOM 操作迁移到 Vue 3 + Vite 现代化前端架构，提升代码可维护性、组件复用性和开发体验。

## 现状

**当前技术栈**：
- 纯原生 JavaScript（163KB app.js）
- 直接 DOM 操作（querySelector、innerHTML、事件监听器）
- 无组件化、无状态管理
- Tailwind CSS 编译环境（proxy_static/dev/）
- PyInstaller 打包静态文件到可执行文件

**存在的问题**：
- 代码耦合度高，难以维护和测试
- 重复的 DOM 操作逻辑
- 状态管理混乱（全局变量、事件传递）
- 无法使用现代前端工具链（TypeScript、组件库、热重载）
- 图标系统原始（Unicode 字符 + SVG data URI）

## 设计范围

### 1. 技术栈选型

**Vue 3 + Vite**：
- Vue 3 Composition API
- Vite 5.x（快速构建、热模块替换）
- TypeScript（可选，渐进式引入）
- Simple Icons（3000+ 品牌图标库）
- Tailwind CSS（保留现有配置）

### 2. 项目结构重组

```
proxy_static/
├── index.html          # Vite 入口（改造）
├── vite.config.js      # Vite 配置
├── package.json        # 合并 dev/package.json
├── src/                # 新增：Vue 源码
│   ├── main.js         # Vue 应用入口
│   ├── App.vue         # 根组件
│   ├── assets/         # 静态资源
│   │   └── icons/      # Simple Icons SVG
│   ├── components/     # Vue 组件
│   │   ├── Toolbar.vue
│   │   ├── TimeRangeControl.vue
│   │   ├── SearchBox.vue
│   │   ├── RequestTable.vue
│   │   ├── ProviderCard.vue
│   │   └── ThemeControl.vue
│   ├── composables/    # 组合式函数
│   │   ├── useTheme.js
│   │   ├── useProviders.js
│   │   └── useRequests.js
│   └── styles/         # 样式文件
│       ├── main.css    # 主样式（Tailwind 入口）
│       └── variables.css # CSS 变量（保留）
├── dist/               # 构建输出（Git 忽略）
└── dev/                # 保留或删除（待定）
    └── README.md       # 迁移说明
```

### 3. 构建流程

**开发模式**：
```bash
cd proxy_static
npm run dev
# Vite 开发服务器 http://localhost:5173
# 热模块替换（HMR）
```

**生产构建**：
```bash
npm run build
# 输出到 proxy_static/dist/
# index.html + assets/index-[hash].js + assets/index-[hash].css
```

### 4. CI/CD 集成

**GitHub Actions 修改**：

```yaml
# .github/workflows/windows-release.yml
jobs:
  build-and-release:
    steps:
      # ... 现有步骤 ...
      
      # 新增：安装 Node.js
      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: proxy_static/package-lock.json
      
      # 新增：构建 Vue 应用
      - name: Build Vue frontend
        shell: pwsh
        run: |
          cd proxy_static
          npm ci
          npm run build
          if ($LASTEXITCODE -ne 0) { throw "Vue build failed" }
      
      # 新增：验证构建产物
      - name: Verify build artifacts
        shell: pwsh
        run: |
          $indexPath = "proxy_static/dist/index.html"
          if (-not (Test-Path -LiteralPath $indexPath)) {
            throw "Build artifact missing: $indexPath"
          }
          $assetsCount = (Get-ChildItem proxy_static/dist/assets -File).Count
          if ($assetsCount -lt 2) {
            throw "Expected JS and CSS assets in dist/assets/"
          }
      
      # 现有：Check JavaScript syntax（改为检查构建后的文件）
      - name: Check JavaScript syntax
        shell: pwsh
        run: |
          # Vue 构建后的文件已经过验证，跳过或检查其他 JS
          node --check provider_status/static/app.js
      
      # ... 其余步骤不变 ...
```

### 5. PyInstaller 打包调整

**CodexLocalProxy.spec 修改**：

```python
data_files = [
    # 改为打包 dist/ 目录（Vue 构建产物）
    (str(ROOT / "proxy_static" / "dist"), "proxy_static/dist"),
    (str(ROOT / "scripts" / "status_provider_import.py"), "scripts"),
    (str(ROOT / "provider_status" / "config.py"), "status_bootstrap"),
    (str(ROOT / "provider_status" / "claude_probe.py"), "status_bootstrap"),
    *collect_data_files("tiktoken"),
]
```

**Python 路由调整**（local_proxy/application.py）：

```python
# 修改静态文件路由，指向 dist/ 目录
@app.route("/")
def index():
    return send_from_directory("proxy_static/dist", "index.html")

@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory("proxy_static/dist/assets", filename)
```

### 6. 组件拆分计划

**Phase 1：基础组件**（保留现有功能）
- `App.vue` - 根组件，全局布局
- `Toolbar.vue` - 工具栏容器
- `TimeRangeControl.vue` - 时间范围选择器
- `SearchBox.vue` - 搜索框
- `ThemeControl.vue` - 主题切换器

**Phase 2：数据展示**
- `RequestTable.vue` - 请求记录表格
- `ProviderCard.vue` - 供应商卡片
- `UsageChart.vue` - 用量图表（如果有）

**Phase 3：交互功能**
- `ProviderEditor.vue` - 供应商编辑表单
- `SettingsPanel.vue` - 设置面板
- `Modal.vue` - 通用模态框

### 7. 图标系统

**Simple Icons 集成**：

```bash
npm install simple-icons
```

```vue
<script setup>
import { siOpenai, siAnthropic, siGoogle } from 'simple-icons';
</script>

<template>
  <svg viewBox="0 0 24 24" class="provider-icon">
    <path :d="siOpenai.path" :fill="siOpenai.hex" />
  </svg>
</template>
```

## 非目标

- **不引入状态管理库**（Pinia/Vuex）：应用规模不需要，使用 Composition API + provide/inject
- **不使用 UI 组件库**：保持轻量，手写组件符合现有设计
- **不改动 Python 后端逻辑**：仅前端架构迁移
- **不破坏现有功能**：所有现有功能必须在迁移后保持可用
- **不引入 SSR/SSG**：保持纯客户端渲染
- **暂不引入 TypeScript**：先完成迁移，后续渐进式引入

## 兼容性

**破坏性改动**：

1. **目录结构变化**：
   - `proxy_static/index.html` → `proxy_static/dist/index.html`（构建产物）
   - `proxy_static/app.js` → `proxy_static/dist/assets/index-[hash].js`
   - `proxy_static/styles.css` → `proxy_static/dist/assets/index-[hash].css`

2. **Python 路由调整**：
   - 静态文件路由需要指向 `dist/` 目录
   - 开发模式可能需要代理到 Vite 开发服务器

3. **构建依赖**：
   - CI/CD 需要 Node.js 20+
   - 开发者需要运行 `npm install` 和 `npm run build`

**缓解措施**：

1. **Git 保留原始文件**：
   - 将现有 `app.js` 和 `index.html` 移动到 `proxy_static/legacy/` 作为参考
   - 创建迁移分支，主分支保持稳定

2. **分阶段迁移**：
   - Phase 1：搭建 Vite 环境，复刻现有页面（无功能变化）
   - Phase 2：逐个组件化，保持功能对等
   - Phase 3：优化和增强

3. **回滚方案**：
   - 保留原始文件的最后可用版本
   - PyInstaller spec 可快速切换回 legacy 目录

4. **开发者文档**：
   - 更新 `proxy_static/dev/README.md`
   - 提供本地开发指南和构建说明

## 风险

**风险 1**：构建产物大小增加

- **当前**：app.js 163KB + styles.css 56KB = 219KB
- **预估**：Vue 3 runtime ~50KB + app code ~180KB + styles ~60KB = 290KB（+32%）
- **缓解**：Vite 自动代码分割、tree-shaking、gzip 压缩

**风险 2**：CI/CD 构建时间增加

- **当前**：无前端构建步骤
- **新增**：npm install + npm run build（~30-60 秒）
- **缓解**：GitHub Actions 缓存 node_modules

**风险 3**：开发者学习成本

- **影响**：需要学习 Vue 3 Composition API 和 Vite
- **缓解**：提供详细文档和示例代码，代码审查时指导

**风险 4**：PyInstaller 打包路径变化

- **影响**：静态文件从 `proxy_static/` 变为 `proxy_static/dist/`
- **缓解**：充分测试打包流程，smoke test 验证

**风险 5**：浏览器兼容性

- **影响**：Vue 3 需要现代浏览器（ES2015+）
- **缓解**：项目本身已要求现代浏览器，无额外影响

## 测试计划

**自动化测试**：
- ✅ Vite 构建成功（`npm run build` 退出码 0）
- ✅ 构建产物存在（`dist/index.html` 和 `dist/assets/*.js`）
- ✅ JavaScript 语法检查（构建后的文件）
- ✅ PyInstaller 打包成功
- ✅ Smoke test 通过（可执行文件启动和基本功能）

**人工验证**：
- ✅ 所有现有功能可用（时间选择、搜索、主题切换、供应商管理）
- ✅ 浅色/深色模式切换正常
- ✅ 响应式布局（桌面/平板/手机）
- ✅ 性能无明显下降（首屏加载、交互响应）
- ✅ 打包后的可执行文件运行正常
- ✅ 图标显示正确（Simple Icons）

**回归测试**：
- ✅ 供应商 CRUD 操作
- ✅ 请求记录查询和筛选
- ✅ 时间范围编辑
- ✅ 设置保存和恢复
- ✅ 主题偏好持久化

## 实际改动

**待实施，完成后更新此章节。**

## 验证结果

**待验证，完成后更新此章节。**

## PR

pending
