+++
id = "2026-09-03-usage-view-padding"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 用量趋势视图补齐页面边距

## 目标

用量趋势视图与供应商/请求等视图一致，具备左右页面边距和 `surface-soft` 背景内容区，不再左右顶格。

## 现状

- `.requests-view`（请求）：`padding: 18px 20px 20px; background: var(--surface-soft)`，内容 `width: min(1520px, 100%); margin-inline: auto` 居中，窄屏 `padding: 16px 12px`。
- `.settings-view`（设置/运行/监控）：`padding: 24px; background: var(--surface-soft)`，窄屏 `18px 12px`。
- `.usage-view`（用量趋势）：自 v1.8.0 引入起只有 `display:flex; gap:14px`，无 padding、无背景、无限宽——标题、汇总卡、图表全部左右顶格，与其他视图观感割裂。

## 设计范围

1. `.usage-view` 对齐 `.requests-view` 口径：`padding: 18px 20px 20px; background: var(--surface-soft);`，子元素 `width: min(1520px, 100%); margin-inline: auto` 居中。
2. 窄屏媒体查询补 `.usage-view { padding: 16px 12px; }`。
3. 图表区限宽后保持既有固定高度与热力居中逻辑不变。

## 非目标

- 不改图表绘制、动画与数据管线；不动其他视图样式。

## 兼容性

- 纯 CSS 视觉修复；dist 重建。

## 风险

- 限宽 1520px 后超宽屏下图表变窄：与请求视图同一内容宽度，观感一致，属预期。

## 测试计划

- `tests/local_proxy_vue_ui.test.js` 增加样式断言（padding/背景/限宽/窄屏）。
- `npm run build` 后浏览器截图对比请求视图边距。
- 全量验证：Python 单测、`node --test`、`npm ci` + `npm run build`。

## 实际改动

- `proxy_static/src/styles.css`：`.usage-view` 补齐 `padding: 18px 20px 20px; background: var(--surface-soft)`，子元素 `width: min(1520px, 100%); margin-inline: auto` 限宽居中（与 `.requests-view` 同一口径）；窄屏媒体查询补 `.usage-view { padding: 16px 12px; }`。
- `tests/local_proxy_vue_ui.test.js`：新增 "usage trend view matches the shared page padding and content width" 样式断言。
- `proxy_static/dist/`：重建（index-BF0iKsD1.js / index-BTBZCZE2.css）。

## 验证结果

- `node --test tests/local_proxy_vue_ui.test.js` → 26/26 通过（含新增断言）（2026-09-03 18:54）。
- 浏览器实测（2026-09-03 18:56）：从源码启动的服务加载新构建，用量趋势视图标题/汇总卡/图表均与请求视图一致出现左右边距与浅色背景区，不再顶格。
- `node --test tests/*.test.js` 全部通过；`python -m unittest discover -s tests -p "test_*.py"` → 555 项全部通过；`npm run build --prefix proxy_static` 构建成功（2026-09-03 18:57）。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/82
