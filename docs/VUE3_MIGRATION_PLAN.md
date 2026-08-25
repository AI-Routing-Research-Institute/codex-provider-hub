# Vue 3 迁移实施计划

本文档为 [2026-08-24-vue3-migration.md](./changes/2026-08-24-vue3-migration.md) 的详细实施计划，包含每个阶段的具体任务和验证标准。

## 总体时间线

- **Phase 0**: 环境准备和基础配置（1-2 天）
- **Phase 1**: Vite 环境搭建和页面复刻（2-3 天）
- **Phase 2**: 组件化改造（3-5 天）
- **Phase 3**: CI/CD 集成和测试（2-3 天）
- **Phase 4**: 打包验证和文档（1-2 天）

**总计**: 9-15 天

---

## Phase 0: 环境准备 ✅

### 任务清单

- [x] 创建变更说明文档 `docs/changes/2026-08-24-vue3-migration.md`
- [ ] 备份现有文件
  - [ ] 移动 `proxy_static/app.js` → `proxy_static/legacy/app.js`
  - [ ] 移动 `proxy_static/index.html` → `proxy_static/legacy/index.html`
  - [ ] 移动 `proxy_static/styles.css` → `proxy_static/legacy/styles.css`
- [ ] 更新 `.gitignore`
  ```
  # Vue 构建产物
  proxy_static/dist/
  proxy_static/node_modules/
  proxy_static/.vite/
  ```

### 验证标准

- ✅ 变更说明文档已创建
- [ ] 原始文件已备份到 `legacy/`
- [ ] `.gitignore` 已更新

---

## Phase 1: Vite 环境搭建

### 1.1 初始化 Vite 项目

```bash
cd proxy_static
npm init -y
npm install -D vite@^5.4 @vitejs/plugin-vue@^5.0
npm install vue@^3.4
npm install -D tailwindcss@^3.4 autoprefixer postcss
npm install simple-icons
```

### 1.2 创建项目结构

```
proxy_static/
├── index.html          # Vite 入口（从 legacy/ 复制并改造）
├── vite.config.js      # 新建
├── postcss.config.js   # 新建
├── tailwind.config.js  # 从 dev/tailwind.config.js 迁移
├── package.json        # 合并 dev/package.json
├── src/
│   ├── main.js         # 新建
│   ├── App.vue         # 新建
│   └── styles/
│       ├── main.css    # 新建（Tailwind 入口）
│       └── variables.css # 从现有 CSS 提取变量
```

### 1.3 配置文件

**vite.config.js**:
```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  root: '.',
  base: '/static/',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html')
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:17890',
        changeOrigin: true
      }
    }
  }
})
```

**tailwind.config.js** (迁移自 dev/):
```js
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

**package.json** (关键脚本):
```json
{
  "name": "codex-provider-hub-ui",
  "version": "2.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.4.0",
    "simple-icons": "^13.0.0"
  },
  "devDependencies": {
    "vite": "^5.4.0",
    "@vitejs/plugin-vue": "^5.0.0",
    "tailwindcss": "^3.4.17",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.47"
  }
}
```

### 1.4 创建基础 Vue 应用

**src/main.js**:
```js
import { createApp } from 'vue'
import App from './App.vue'
import './styles/main.css'

createApp(App).mount('#app')
```

**src/App.vue**:
```vue
<template>
  <div id="app">
    <h1>Codex Provider Hub - Vue 3</h1>
    <p>迁移中...</p>
  </div>
</template>

<script setup>
// 待实现
</script>

<style scoped>
/* 组件样式 */
</style>
```

**src/styles/main.css**:
```css
/* Tailwind 指令 */
@tailwind base;
@tailwind components;
@tailwind utilities;

/* 导入 CSS 变量 */
@import './variables.css';
```

### 1.5 改造 index.html

从 `legacy/index.html` 复制，调整为 Vite 入口：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>本地中转</title>
  <script>
    // 主题初始化脚本（保留）
    (() => {
      const key = "local-proxy-theme";
      let preference = "system";
      try {
        const saved = localStorage.getItem(key);
        if (["system", "light", "dark"].includes(saved)) preference = saved;
      } catch (error) {}
      const dark = preference === "dark" || (preference === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
      document.documentElement.dataset.themePreference = preference;
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    })();
  </script>
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
```

### 验证标准

- [ ] `npm run dev` 启动成功
- [ ] 浏览器访问 `http://localhost:5173` 显示 "Codex Provider Hub - Vue 3"
- [ ] `npm run build` 构建成功
- [ ] `dist/index.html` 和 `dist/assets/` 产物存在

---

## Phase 2: 组件化改造

### 2.1 提取 CSS 变量

从 `legacy/styles.css` 提取所有 CSS 变量到 `src/styles/variables.css`:

```css
:root {
  --surface: #ffffff;
  --surface-soft: #f8f9fa;
  --text: #1a1f2e;
  --muted: #66737c;
  --line: #dfe4e8;
  --line-strong: #c5ccd3;
  --teal: #146c73;
  --teal-soft: rgba(20, 108, 115, 0.08);
  /* ... 其他变量 */
}

:root[data-theme="dark"] {
  --surface: #0f131c;
  --surface-soft: #161d2b;
  --text: #e5e9ed;
  --muted: #a2afb5;
  --line: #2a3340;
  --line-strong: #3d4855;
  --teal: #38bdf8;
  --teal-soft: rgba(56, 189, 248, 0.12);
  /* ... 其他变量 */
}
```

### 2.2 创建基础组件

按优先级逐个创建：

1. **ThemeControl.vue** - 主题切换器
2. **Toolbar.vue** - 工具栏容器
3. **TimeRangeControl.vue** - 时间范围选择器
4. **SearchBox.vue** - 搜索框
5. **RequestTable.vue** - 请求记录表格
6. **ProviderCard.vue** - 供应商卡片

### 2.3 创建 Composables

**src/composables/useTheme.js**:
```js
import { ref, watch, onMounted } from 'vue'

export function useTheme() {
  const theme = ref('system')
  const KEY = 'local-proxy-theme'

  const applyTheme = (value) => {
    const dark = value === 'dark' || (value === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.dataset.themePreference = value
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }

  const setTheme = (value) => {
    theme.value = value
    try {
      localStorage.setItem(KEY, value)
    } catch (e) {}
    applyTheme(value)
  }

  onMounted(() => {
    try {
      const saved = localStorage.getItem(KEY)
      if (['system', 'light', 'dark'].includes(saved)) {
        theme.value = saved
      }
    } catch (e) {}
  })

  watch(theme, applyTheme)

  return { theme, setTheme }
}
```

**src/composables/useProviders.js**:
```js
import { ref } from 'vue'

export function useProviders() {
  const providers = ref([])
  const currentProvider = ref(null)
  const loading = ref(false)

  const fetchProviders = async () => {
    loading.value = true
    try {
      const res = await fetch('/api/providers')
      providers.value = await res.json()
    } finally {
      loading.value = false
    }
  }

  const selectProvider = async (id) => {
    const res = await fetch(`/api/providers/${id}/select`, { method: 'POST' })
    if (res.ok) {
      currentProvider.value = id
    }
  }

  return {
    providers,
    currentProvider,
    loading,
    fetchProviders,
    selectProvider
  }
}
```

### 2.4 集成 Simple Icons

**src/components/ProviderIcon.vue**:
```vue
<template>
  <svg viewBox="0 0 24 24" :class="className" role="img">
    <title>{{ iconData.title }}</title>
    <path :d="iconData.path" :fill="fill || `#${iconData.hex}`" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'
import * as icons from 'simple-icons'

const props = defineProps({
  name: String, // 'openai', 'anthropic', 'google'
  fill: String,
  className: String
})

const iconData = computed(() => {
  const key = `si${props.name.charAt(0).toUpperCase()}${props.name.slice(1).toLowerCase()}`
  return icons[key] || icons.siCircle
})
</script>
```

### 验证标准

- [ ] 所有组件创建完成
- [ ] 主题切换功能正常
- [ ] 所有 API 调用正常（与 Python 后端联调）
- [ ] Simple Icons 显示正确

---

## Phase 3: CI/CD 集成

### 3.1 更新 GitHub Actions

修改 `.github/workflows/windows-release.yml`:

```yaml
jobs:
  build-and-release:
    runs-on: windows-latest
    steps:
      # ... 现有步骤 ...

      # 新增：Set up Node.js
      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: proxy_static/package-lock.json

      # 新增：Build Vue frontend
      - name: Build Vue frontend
        shell: pwsh
        run: |
          cd proxy_static
          npm ci
          npm run build
          if ($LASTEXITCODE -ne 0) { throw "Vue build failed" }

      # 新增：Verify build artifacts
      - name: Verify build artifacts
        shell: pwsh
        run: |
          $indexPath = "proxy_static/dist/index.html"
          if (-not (Test-Path -LiteralPath $indexPath)) {
            throw "Build artifact missing: $indexPath"
          }
          $jsFiles = Get-ChildItem "proxy_static/dist/assets" -Filter "*.js" -File
          $cssFiles = Get-ChildItem "proxy_static/dist/assets" -Filter "*.css" -File
          if ($jsFiles.Count -eq 0 -or $cssFiles.Count -eq 0) {
            throw "Expected JS and CSS assets in dist/assets/"
          }

      # 修改：Check JavaScript syntax（改为检查其他 JS）
      - name: Check JavaScript syntax
        shell: pwsh
        run: |
          # Vue 构建后的文件已经过 Vite 验证
          node --check provider_status/static/app.js

      # ... 其余步骤保持不变 ...
```

### 3.2 更新 PyInstaller 配置

修改 `packaging/CodexLocalProxy.spec`:

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

### 3.3 更新 Python 路由

修改 `local_proxy/application.py`:

```python
@app.route("/")
@app.route("/control/codex/")
@app.route("/control/claude/")
def index():
    return send_from_directory("proxy_static/dist", "index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    # 兼容旧路径，重定向到 assets
    if filename.startswith("assets/"):
        return send_from_directory("proxy_static/dist", filename)
    return send_from_directory("proxy_static/dist/assets", filename)
```

### 验证标准

- [ ] GitHub Actions 构建成功
- [ ] Vue 构建步骤通过
- [ ] 构建产物验证通过
- [ ] PyInstaller 打包成功
- [ ] Smoke test 通过

---

## Phase 4: 测试和文档

### 4.1 功能回归测试

手工验证所有现有功能：

- [ ] 启动可执行文件，托盘图标显示
- [ ] 打开 Codex 控制台
- [ ] 打开 Claude Code 控制台
- [ ] 主题切换（系统/浅色/深色）
- [ ] 时间范围选择和编辑
- [ ] 搜索功能
- [ ] 供应商列表显示
- [ ] 供应商选择和切换
- [ ] 请求记录查询
- [ ] 设置保存

### 4.2 性能测试

- [ ] 首屏加载时间（< 2 秒）
- [ ] 交互响应时间（< 100ms）
- [ ] 打包体积（< 5MB）

### 4.3 浏览器兼容性

测试以下浏览器：

- [ ] Chrome/Edge 最新版
- [ ] Firefox 最新版
- [ ] Safari 最新版（macOS）

### 4.4 更新文档

- [ ] 更新 `proxy_static/dev/README.md` → `proxy_static/README.md`
- [ ] 添加开发指南（如何启动开发服务器、构建、调试）
- [ ] 更新主 `README.md`（如果有用户可见的变化）
- [ ] 完善 `docs/changes/2026-08-24-vue3-migration.md`

### 验证标准

- [ ] 所有功能正常
- [ ] 性能符合预期
- [ ] 文档完整清晰

---

## 回滚方案

如果迁移失败，可以快速回滚：

1. 恢复 `legacy/` 目录中的文件到 `proxy_static/` 根目录
2. 恢复 `packaging/CodexLocalProxy.spec` 中的旧路径配置
3. 恢复 `local_proxy/application.py` 中的旧路由
4. 恢复 `.github/workflows/windows-release.yml` 中的旧构建步骤

---

## 下一步

完成此计划后，后续可以考虑：

1. 引入 TypeScript（渐进式，逐个组件迁移）
2. 添加单元测试（Vitest）
3. 添加端到端测试（Playwright）
4. 优化打包体积（代码分割、懒加载）
5. 引入状态管理（如果应用复杂度增加）

---

**准备开始实施？** 请确认计划无误后，我们可以从 Phase 0 开始逐步执行。
