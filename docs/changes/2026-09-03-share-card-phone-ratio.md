+++
id = "2026-09-03-share-card-phone-ratio"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 战报卡片去白边并改为手机比例

## 目标

1. 导出的战报 PNG 不再带外围白边：去掉画布四周 28px 透明边距与烘焙阴影，卡片满幅导出（透明区在聊天工具白底上显示为白边，即用户所见）。
2. 卡片改为手机屏比例 9:19.5（逻辑 360×780，3× 导出恰为 1080×2340，主流手机屏幕分辨率）。

## 现状

- 当前导出画布为卡片 380px 宽 + 左右各 28px 透明 margin（436px），阴影烘焙进 PNG；粘贴到白底聊天窗口时透明区呈现白边。
- 卡片高约 453px（1:1.19），与手机屏比例差距大；改为 780 高后原内容仅占约 60%，需要内容重排。

## 设计范围

1. `share-card.js` `terminalCardTheme()`：`margin` 移除（满幅）、`cardWidth: 360`、新增 `cardHeight: 780`、`bodyPaddingX: 24`；色板与字体不变。
2. `ShareCardDialog.vue`：
   - 画布满幅绘制（无外框描边、无烘焙阴影；对话框预览阴影由 CSS 提供）；模板 canvas 属性 360×780。
   - 竖版重排：hero 数字加大到 56px、各节间距放大；新增"今日时段分布"小节——数据来自 `GET /api/usage-timeline?usage_window=today`（fetch 失败或全零显示占位文案），24 个小时位细条（高度∝该小时 tokens），峰值条与构成条同为 ember→紫渐变，右侧标注"峰值 X · HH:00"，下轴 00/06/12/18/23 刻度。
   - 页脚分区分隔线与文字锚定底部。
3. 预览样式 `.share-card-canvas-terminal`：`max-width: min(340px, 100%); border-radius: 0`（与导出图一致，方角满幅）。

## 非目标

- 不改数据口径、徽章、昨日对比逻辑与下载/复制交互；不加动画。

## 兼容性

- 纯前端视觉；导出文件名不变；dist 重建。

## 风险

- today 时间线接口偶发失败：降级为占位文案，不影响其余卡面。

## 测试计划

- `tests/share_card.test.js`：主题断言改为 360×640、圆角 16、无 margin、bodyPaddingX 24。
- `tests/local_proxy_vue_ui.test.js`：画布属性断言、时段分布小节与 today 时间线请求断言。
- `npm run build` + 浏览器假数据 harness 截图核对比例、满幅与圆角。
- 全量验证：Python 单测、`node --test`、`npm ci` + `npm run build`。

## 实际改动

- `proxy_static/src/share-card.js`：`terminalCardTheme()` 改为 `cardWidth: 360`、`cardHeight: 640`（9:16）、新增 `cornerRadius: 16`、移除 `margin`（满幅导出）。
- `proxy_static/src/components/ShareCardDialog.vue`：画布属性 360×640；整卡圆角裁剪绘制（标题栏顶部圆角、扫描线随裁剪、restore 后圆角描边）；hero 数字 56px 带超宽自适应缩号；新增"今日时段分布"小节——数据来自 `GET /api/usage-timeline?usage_window=today`（Promise.all 并入，失败降级占位），24 个小时位渐变细条（高度∝该小时 tokens）、右上"峰值 X · HH:00"、00/06/12/18/23 刻度；页脚锚定底部。
- `proxy_static/src/styles.css`：`.share-card-canvas-terminal` 圆角 16px、max-width 340px，与导出图一致。
- 测试：`tests/share_card.test.js` 主题断言更新；`tests/local_proxy_vue_ui.test.js` 画布 360×640、时段分布、today 时间线请求、无烘焙阴影断言。
- `proxy_static/dist/`：重建（index-BAE2UGRC.js / index-DtB-Aow- 为本轮构建产物）。

## 验证结果

- `node --test tests/share_card.test.js tests/local_proxy_vue_ui.test.js` → 36/36 通过（2026-09-03 19:22）。
- 假数据 harness 浏览器截图逐项核对：满幅圆角无白边、9:16 紧凑比例、hero 大数字、输入/输出/缓存、构成条、时段分布（渐变细条+峰值 5.2M · 10:00+刻度）、页脚光标，全部正常无重叠溢出（2026-09-03 19:25）。
- `node --test tests/*.test.js` 全部通过；`python -m unittest discover -s tests -p "test_*.py"` → 555 项全部通过；`npm run build --prefix proxy_static` 构建成功（2026-09-03 19:27）。

## PR

pending
