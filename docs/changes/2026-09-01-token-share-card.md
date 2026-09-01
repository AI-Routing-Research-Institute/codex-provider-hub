+++
id = "2026-09-01-token-share-card"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 今日 Token 消耗分享卡片

## 目标

在新版（Vue）控制台的供应商页提供"生成今日 Token 卡片"入口，把当日 Token 总消耗、输入/输出/缓存/推理拆分、请求数与成功率、供应商用量排行渲染成一张分享卡片图片，支持下载 PNG 与复制到剪贴板。

## 现状

控制台已在供应商页展示"今日"Token 总量与拆分（`/api/status?usage_window=today` 返回 `usage.total` 与 `usage.by_provider` 汇总），但只能截图浏览器窗口分享，缺少一键生成美观卡片的能力。

## 设计范围

- 新增纯逻辑模块 `proxy_static/src/share-card.js`：从 `/api/status?usage_window=today` 载荷构建卡片数据模型（日期、总量、拆分、成功率、供应商排行与份额）、数字千分位格式化、下载文件名。
- 新增 `ShareCardDialog.vue` 模态组件：Canvas 2倍采样绘制卡片（品牌、日期、Token 大数字、指标行、请求统计、供应商排行条、页脚），提供"下载图片"与"复制图片"操作；打开时独立拉取今日数据，不受当前页时间范围选择影响。
- `ProvidersView.vue` 的用量摘要条增加分享入口按钮；`UiIcon.vue` 增加 share 图标；`styles.css` 增加模态与按钮样式。
- 重新构建 `proxy_static/dist` 产物。
- 数据为空（今天没有请求）时仍可生成全零卡片，并在弹窗内提示"今天还没有请求记录"。

## 非目标

- 不修改经典控制台（`proxy_static/classic`）。
- 不新增或修改任何后端 API、数据库结构、供应商路由行为。
- 不支持自定义卡片模板、水印、多日期对比或直接分享到社交平台。
- 不在卡片中展示供应商地址、API Key 等敏感信息，仅展示名称与用量。

## 兼容性

- 前端新增文件与组件，后端零改动；`/api/status` 现有载荷结构即可满足。
- 复制图片依赖 `navigator.clipboard.write` 与 `ClipboardItem`，在不支持的浏览器中降级为提示仅可下载；不影响其他功能。

## 风险

- Canvas `roundRect` 在旧浏览器缺失：实现手动圆角路径回退。
- 剪贴板写入需要安全上下文与用户手势：失败时toast提示改用下载。
- 高分屏绘制模糊：按设备像素比放大画布并等比缩放。

## 测试计划

- 新增 `tests/share_card.test.js`：覆盖卡片数据构建（总量、拆分、供应商排序与份额、成功率、空数据）、千分位格式化、文件名生成。
- 本地执行 Python 单测（`python -m unittest discover -s tests -p "test_*.py"`）与 JS 测试（`node --test`），`node --check` 语法检查。
- `npm run build --prefix proxy_static` 构建通过，产物更新。

## 实际改动

- 新增 `proxy_static/src/share-card.js`：卡片数据模型构建（日期与星期、Token 总量与拆分、请求数/成功率/失败数、供应商用量排行与份额、空数据兜底）、千分位数字格式化、下载文件名生成；全部为纯函数，无 DOM 依赖。
- 新增 `proxy_static/src/components/ShareCardDialog.vue`：分享卡片模态组件。挂载时并行请求 `/api/status?usage_window=today` 与 `/api/ui-config`（后者失败时回退默认品牌文案），按设备像素比 2-3 倍采样在 Canvas 绘制卡片（渐变背景、品牌区、Token 大数字、输入/输出/缓存/推理四格指标、请求统计行、供应商排行进度条、页脚），提供"下载图片"（`canvas.toBlob` + `<a download>`）与"复制图片"（`ClipboardItem`，不支持时提示改用下载）；空数据显示"今天还没有请求记录"提示；Esc 关闭。
- `proxy_static/src/components/ProvidersView.vue`：用量摘要条新增"分享卡片"入口按钮与 `shareCardOpen` 状态，弹窗挂载于供应商视图根节点。
- `proxy_static/src/components/ui/UiIcon.vue`：新增 `share` 图标。
- `proxy_static/src/styles.css`：新增 `.usage-share-button` 与 `.share-card-*` 模态/画布/提示样式，扩展 `.usage-estimate-wrap` 布局。
- 重新构建 `proxy_static/dist`（产物哈希更新为 index-CdNcPg76.js / index-dlpmpobK.css）。
- 新增 `tests/share_card.test.js`：5 个用例覆盖千分位格式化、汇总与排行排序/份额/限长、空数据兜底、文件名生成。

## 验证结果

- `node --test tests/share_card.test.js`：5/5 通过。
- `python -m unittest discover -s tests -p "test_*.py"`：514 个测试全部通过。
- `node --check proxy_static/classic/app.js`、`node --check provider_status/static/app.js`、全部 `tests/*.test.js` 语法检查通过；`node --test` 逐个执行全部 `tests/*.test.js` 无失败。
- `npm run build --prefix proxy_static`：构建成功（29 modules transformed）。
- `python local_proxy_app.py --smoke-test`：`ok:true`，classic/modern 双 UI 资源校验通过。
- 浏览器实测（本机 17891 端口临时实例 + 真实今日数据）：分享按钮出现在用量摘要条；弹窗正常渲染 Canvas 卡片（1200x1560，含 3,006,844 Token 大数字、四格指标、供应商"cai"排行条）；"复制图片"返回"已复制"成功提示；无控制台错误。

## PR

pending
