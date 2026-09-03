+++
id = "2026-09-03-share-card-terminal-restyle"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 战报海报重设计为终端风卡片

## 目标

按用户提供的设计稿（`C:\Users\Kane\Documents\token_report_card.html`，"今日 Token 战报"终端风卡片）**一比一复刻**战报海报：macOS 窗口红绿灯标题栏 + `codex — today's-usage.log` 路径、`$` 日期行、ember 渐变大数字、昨日对比行、输入/输出/缓存三行统计、请求元信息、消耗构成条（第一名供应商）、"最晚一战"页脚与光标块，全部接真实数据。

## 现状

- `ShareCardDialog.vue` 用 canvas 绘制 600×1000 星空渐变海报（紫色星云 + 甜甜圈图），用户认为太丑。
- 数据管线已完备：`/api/status?usage_window=today` + 昨日 custom 窗口对比 + `share-card.js` 纯函数（汇总、供应商排名、昨日对比、最晚一战、徽章）。
- 下载/复制（剪贴板 PNG）功能经 canvas `toBlob` 实现，需要保留。

## 设计范围

1. `ShareCardDialog.vue` 重写 `drawCard`：380px 宽终端风卡片，逻辑尺寸约 436×509（含 28px 阴影边距，3× 缩放导出）；标题栏红黄绿圆点 + 路径文本；正文按设计稿间距逐段 y 游标布局；hero 数字用 `Space Grotesk` + `linear-gradient(100deg, #ff5f2e, #ffb020)` 文字渐变（canvas 渐变 fill）；静态扫描线纹理（每 3px 一条 1px 白 1.2% 透明线）；消耗构成条 8px 圆角 + 90° 渐变（#ff5f2e → #c893ff），宽度=第一名占比；页脚光标块静态渲染（导出为图片）。
2. 字体：`styles.css` 增加 Google Fonts `@import`（JetBrains Mono 400–700、Space Grotesk 500–700），绘制前 `document.fonts.ready`（1.2s 超时竞速）加载在线字体；离线时回退 ui-monospace/系统等宽栈，布局不变。
3. 对话框操作按钮按设计稿 `.actions` 样式重绘（次级=panel-2 描边、主按钮=ember 渐变深字）。
4. 真实数据映射：hero=今日总 tokens；标签徽章=`shareBadgeTitle`；对比行=`yesterdayCompare`（▼/▲ 百分比、首日/持平态）；统计=输入/输出/缓存；元信息=请求/成功率/失败（含估算）；构成=第一名供应商名与占比（多家时 `共 N 家 · X 领跑`，独家时 `独揽今日全部用量`）；页脚=`latestBattleLabel` HH:MM。
5. `share-card.js`：移除新设计不再使用的 `posterStars`/`posterLayout`，新增 `terminalCardTheme()`（色板、字体栈、间距常量）作为绘制与测试的单一来源。

## 非目标

- 不改数据管线、后端与入口交互（Providers 分享按钮）。
- 不做导出动图/光标闪烁动画（导出为静态 PNG）。
- 不改经典界面（classic）的分享卡。

## 兼容性

- 纯前端视觉重构；下载文件名、复制行为、空数据提示保持。dist 重建。

## 风险

1. webfont 离线不可用导致字形差异：回退等宽栈 + 布局不依赖具体字宽（右侧数值统一右对齐）。
2. 长供应商名/长数字溢出：fitText 截断加省略号。
3. 对比语义：设计稿箭头与百分比均为主色绿（--good），如实复刻（升/降同为绿）；持平/首日用中性灰。

## 测试计划

- `tests/share_card.test.js`：`posterStars`/`posterLayout` 用例替换为 `terminalCardTheme` 断言（色板 hex、字体栈、间距与设计稿一致）。
- `tests/local_proxy_vue_ui.test.js`：更新海报源断言（新画布尺寸、JetBrains Mono/Space Grotesk、扫描线、标题栏圆点、渐变色值、`document.fonts.ready` 竞速）。
- 全量验证：Python 单测、`node --test tests/*.test.js`、`npm ci` + `npm run build --prefix proxy_static`。

## 实际改动

- `proxy_static/src/components/ShareCardDialog.vue`：`drawCard` 整体重写为终端风卡片（逻辑 380px 宽 + 28px 阴影边距，3× 缩放导出 PNG）：标题栏（红黄绿圆点 + `codex — today's-usage.log`，服务名取自 `controlBase`）、`$` 日期行、`Space Grotesk` 46px 渐变大数字（`linear-gradient(100deg, #ff5f2e, #ffb020)`）、▼/▲ 昨日对比（绿）、输入/输出/缓存三行（右对齐数值）、请求元信息、消耗构成条（90° 渐变 #ff5f2e→#c893ff，宽度=第一名占比；多家显示"共 N 家 · X 领跑"）、"最晚一战/你正在敲代码"页脚 + 绿色光标块、静态扫描线；绘制前 `document.fonts.ready`（1.2s 超时竞速）；对话按钮换 `.share-terminal-button`（主按钮 ember 渐变）。
- `proxy_static/src/share-card.js`：删除 `posterStars`/`posterLayout`（旧星空海报专用），新增 `terminalCardTheme()`（色板/字体栈/间距单一来源）。
- `proxy_static/src/styles.css`：Google Fonts `@import`（JetBrains Mono + Space Grotesk）、`.share-card-canvas-terminal`、`.share-card-actions-terminal`、`.share-terminal-button(.primary)`。
- `tests/share_card.test.js`：`posterStars`/`posterLayout` 两个用例替换为 `terminalCardTheme` 色板与度量断言。
- `tests/local_proxy_vue_ui.test.js`：海报用例重写为终端卡断言（画布 436×509、字体、扫描线、渐变、文案、旧布局工具不残留）。
- `proxy_static/dist/`：重建（index-CmG0ijTb.js / index-c2vqW2_1.css）。

## 验证结果

- `node --test tests/share_card.test.js tests/local_proxy_vue_ui.test.js` → 34/34 通过（2026-09-03 17:49）。
- 真实渲染可视化验证（2026-09-03 17:50）：以设计稿同数值的假数据驱动 verbatim 提取的绘制代码，浏览器截图逐项比对——标题栏圆点与路径、`$ 2026-09-03 周四`、渐变大数字 11,654,341、`▼ 77.6% 较昨日全天`、三行统计（11.6M/54.62K/10.85M）、构成条 `100% baicai copy` + `独揽今日全部用量`、`最晚一战 17:00` + `你正在敲代码` 光标块全部与设计稿一致，无重叠/溢出。
- `python -m unittest discover -s tests -p "test_*.py"` → 555 项全部通过；`node --test tests/*.test.js` 全部通过；`node --check`（classic/src/provider_status）、`npm run build --prefix proxy_static` 全部通过（2026-09-03 17:52）。

## PR

pending
