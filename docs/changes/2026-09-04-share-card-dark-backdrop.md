+++
id = "2026-09-04-share-card-dark-backdrop"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 战报卡片导出自带深色底板消除微信白角

## 目标

复制/下载的战报卡片粘贴到微信（含暗色模式）时不再出现白色填充角。导出图自带 `#0b0d10` 深色底板（原设计稿的页面底色），卡片以圆角浮于底板之上，整图无任何透明像素——微信压平 alpha 也不再可能出白。

## 现状

- 导出 PNG 四角为全透明（`rgba(0,0,0,0)`，已像素级验证），文件本身正确。
- 但微信 Windows 端读取剪贴板图片时丢弃 alpha 通道、按白底合成：透明角在微信（含暗色模式）显示为白色填充，视觉上把圆角补成方角。这是微信侧对透明 PNG 的处理方式，透明方案无法规避。

## 设计范围

1. `terminalCardTheme()`：新增 `backdrop: '#0b0d10'`、`backdropMargin: 12`；导出画布 = 卡片 360×640 + 四周 12px 底板（384×664，3× 导出 1152×1992）。
2. `ShareCardDialog.vue` `drawCard()`：先整幅填充深色底板，再平移至卡片原点绘制圆角卡片（既有圆角裁剪、描边、扫描线逻辑不变，坐标经 `translate` 偏移）；模板 canvas 属性 384×664。
3. 预览样式 `.share-card-canvas-terminal` 与导出一致（方形、无 CSS 圆角）。
4. 效果：任意粘贴环境不可能出现白色；圆角在深底板衬托下仍可辨识。

## 非目标

- 不改卡片内容布局、数据口径与下载/复制交互；不做可配置底色。

## 兼容性

- 纯前端视觉；导出文件名不变；dist 重建。

## 风险

- 底板边仅 12px，若用户期望"完全满幅无边"可能有出入：底板为原设计稿页面底色，视觉上是"深色卡片置于深色页面"的完整构图。

## 测试计划

- `tests/share_card.test.js`：主题断言补 backdrop/backdropMargin，卡宽高不变。
- `tests/local_proxy_vue_ui.test.js`：画布 384×664、底板填充断言。
- 浏览器实测：对话框打开后读取画布四角像素应为不透明深色（alpha 255）。
- 全量验证：Python 单测、`node --test`、`npm ci` + `npm run build`。

## 实际改动

- `proxy_static/src/share-card.js`：`terminalCardTheme()` 新增 `backdrop: '#0b0d10'`、`backdropMargin: 12`。
- `proxy_static/src/components/ShareCardDialog.vue`：`drawCard()` 先整幅填充深色底板，再 `translate(margin, margin)` 于卡片原点做既有圆角裁剪/内容/描边/扫描线绘制；模板画布属性 384×664。
- `tests/share_card.test.js`、`tests/local_proxy_vue_ui.test.js`：主题与画布断言更新（384×664、底板填充、平移、无透明）。
- `tests/test_proxy_core.py`：修复 `test_timeline_today...` 既有时间敏感缺陷——原测试用 `time.time()` 且在 now-3h 插数据，0-3 点运行时该记录落入昨天导致断言漂移；改为固定当天 12:30。
- `proxy_static/dist/`：重建（index-5Qz3Zn17.js）。

## 验证结果

- `node --test tests/share_card.test.js tests/local_proxy_vue_ui.test.js` → 36/36 通过（2026-09-04 00:39）。
- 浏览器实测（2026-09-04 00:41）：预览环境打开分享对话框，导出画布四角/底板边/卡片内像素均为不透明 `rgba(11,13,16,255)`，无任何透明像素——微信压平 alpha 后不可能出现白色。
- 全量 `python -m unittest discover -s tests -p "test_*.py"` → 558 项全部通过（含修复后的时间敏感用例，凌晨运行验证）；`node --test tests/*.test.js` 全部通过；`npm run build` 构建成功（2026-09-04 00:46）。

## PR

pending
