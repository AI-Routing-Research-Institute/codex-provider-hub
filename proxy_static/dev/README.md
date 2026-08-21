# UI 开发指南

本目录包含 Codex Provider Hub 的前端开发工具。

## 📁 文件结构

```
proxy_static/
├── index.html          # HTML 模板（提交到 Git）
├── app.js              # JavaScript 逻辑（提交到 Git）
├── styles.css          # 编译后的 CSS（提交到 Git）
└── dev/                # 开发依赖目录
    ├── package.json
    ├── tailwind.config.js
    ├── styles.src.css  # Tailwind 源文件
    └── node_modules/   # 忽略（不提交）
```

## 🎨 设计理念

参考 [sub2api](https://github.com/Wei-Shaw/sub2api) 的 **Swiss Minimal（瑞士极简主义）** 设计风格：
- 简洁清晰的视觉呈现
- 充足的留白空间
- 扁平化设计
- 响应式布局

## 🚀 开发流程

### 首次设置

```bash
cd proxy_static/dev
npm install
```

### 开发模式（自动监听）

```bash
cd proxy_static/dev
npm run dev
```

然后编辑 `index.html` 或 `styles.src.css`，CSS 会自动重新编译。

### 构建生产版本

```bash
cd proxy_static/dev
npm run build
```

生成的 `proxy_static/styles.css` 会被提交到 Git。

## 📦 CI/CD 兼容性

✅ **无需修改 CI/CD 配置**

- 编译后的 `styles.css` 提交到 Git
- PyInstaller 直接打包 `proxy_static/` 目录
- 其他开发者 clone 后可直接运行（有编译好的 CSS）
- 只有需要修改样式的人才需要 `npm install`

## 🎯 Tailwind 使用

### CSS 变量映射

现有的 CSS 变量已映射到 Tailwind：

```css
/* 使用 CSS 变量 */
background: var(--teal);
color: var(--text);

/* 或使用 Tailwind 类 */
<div class="bg-teal text-[var(--text)]">
```

### 常用类名

```html
<!-- 按钮 -->
<button class="primary-button">保存</button>
<button class="secondary-button">管理</button>
<button class="icon-button">↻</button>

<!-- 布局 -->
<div class="flex items-center gap-4">
<div class="grid grid-cols-2 gap-2">

<!-- 间距 -->
<div class="px-4 py-2 mt-3 mb-6">

<!-- 响应式 -->
<div class="hidden md:block">  <!-- 在平板以上显示 -->
<div class="grid-cols-1 md:grid-cols-2">  <!-- 响应式列数 -->
```

### 提取自定义组件

在 `styles.src.css` 的 `@layer components` 中添加：

```css
@layer components {
  .my-card {
    @apply p-4 rounded-lg border border-line bg-surface;
  }
}
```

## 🔧 配置文件

### tailwind.config.js

定义了颜色、间距、字体等主题配置。修改后需要重新编译。

### styles.src.css

包含三个部分：
- `@layer base` - 基础样式和 CSS 变量
- `@layer components` - 复用组件样式
- `@layer utilities` - 工具类

## 📊 文件大小

编译后的 CSS：**~16KB**（minified）

## ⚠️ 注意事项

1. **始终提交编译后的 `styles.css`**
2. **不要提交 `node_modules/`**（已在 .gitignore）
3. **编辑样式后记得运行 `npm run build`**
4. **深色模式通过 `[data-theme="dark"]` 选择器**

## 🧪 验证

编译后验证语法：

```bash
node --check proxy_static/app.js
```

本地运行：

```bash
python local_proxy_app.py --open-browser
```

## 📚 参考资源

- [Tailwind CSS 文档](https://tailwindcss.com/docs)
- [sub2api 前端设计](https://github.com/ZYHUO/sub2api-frontend)
- [Swiss Design Principles](https://www.smashingmagazine.com/2009/07/lessons-from-swiss-style-graphic-design/)
