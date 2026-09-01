+++
id = "2026-09-01-token-share-poster"
type = "feature"
release_bump = "patch"
status = "verified"
+++

# 今日 Token 战报海报（分享卡片视觉升级）

## 目标

把已有的"今日 Token 分享卡片"从仪表盘式布局升级为网易云年度报告风格的战报海报：高饱和渐变背景、星点与光斑装饰、超大主数字、称号徽章、昨日真实对比、供应商环形图与"本命供应商"等仪式感文案，下载/复制交互保持不变。

## 现状

`ShareCardDialog.vue` 当前生成白底内容卡 + 四格指标 + 排行条，信息清晰但视觉朴素，用户反馈"不够花哨"、希望接近网易云活动海报的观感。

## 设计范围

- `share-card.js` 新增纯逻辑：称号分档（按今日总 Token）、昨日对比文案（今日 vs 昨日总量，涨跌百分比与方向）、"本命供应商"（榜首名）、最晚奋战时刻（`last_request_at` 格式化为"HH:mm"）；装饰星点用固定种子伪随机，保证同日生成结果一致。
- `ShareCardDialog.vue` 重写绘制：竖版海报（约 600×940），深夜蓝紫→品红→琥珀渐变底、多层径向光晕、星点/圆点网格装饰；顶部日期与"今日 Token 战报"标题；超大主数字 + TOKENS 副标；称号徽章（如"Token 渐入佳境"）；"较昨日 +xx%"真实对比条（昨日数据通过 `usage_window=custom` 请求昨日 00:00 至今日 00:00，失败或为零时隐藏该条）；输入/输出/缓存三列胶囊；供应商环形份额图 + 本命供应商文案；"HH:mm 你还在敲代码"最晚时刻；页脚保留本地统计声明。
- 昨日对比请求失败时静默降级（不影响卡片其余部分），请求并行发起。
- 重建 `proxy_static/dist`；更新单测。

## 非目标

- 不做多页滑动 H5、动效或音乐（产物是单张 PNG）。
- 不修改后端 API 与数据结构；不新增模型维度统计。
- 不改经典控制台；不提供多主题模板切换（配色固定为战报风）。

## 兼容性

- 前端与构建产物变更；接口与数据结构零改动；`buildShareCardData` 现有返回字段保持不变，仅新增字段。
- 昨日对比新增一次 `/api/status` 请求（custom 窗口），失败自动降级，不阻塞卡片渲染。

## 风险

- 装饰与文字重叠导致可读性下降：装饰元素固定在边缘与低对比度层，主内容区保持高对比。
- 昨日窗口请求参数单位错误导致对比失真：单测覆盖窗口毫秒换算，实测核对。
- 深色背景上小字号发虚：关键文案使用高字号与纯白/高亮色。

## 测试计划

- 扩展 `tests/share_card.test.js`：称号分档边界、昨日对比涨跌与除零、最晚时刻格式化、星点伪随机一致性。
- 全量：Python 单测、`node --check`、`node --test`、`npm run build`、`--smoke-test`。
- 浏览器实测出图，人工核对布局与文案。

## 实际改动

- `proxy_static/src/share-card.js`：新增 `shareBadgeTitle`（五档称号：今日蓄力中/初出茅庐/操练生/渐入佳境/大户/吞天巨兽）、`yesterdayCompare`（涨跌百分比；昨日无基线且今日有消耗时返回"首日作战 · 明日开启对比"）、`latestBattleLabel`（`last_request_at` 毫秒转 HH:mm）、`posterStars`（LCG 固定种子伪随机星点，含十字闪光星）。
- `proxy_static/src/components/ShareCardDialog.vue`：绘制重写为 600×940 竖版战报海报——深夜蓝紫→品红→琥珀对角渐变、四处径向光晕、64 颗种子星点；顶部字距拉开的品牌标题与日期；"今日狂飙"超大主数字（金色辉光、按位数自适应字号）+ 渐变 TOKENS 字标；琥珀→品红渐变称号徽章；昨日对比胶囊（涨绿/跌红/持平白）；输入/输出/缓存三枚半透明胶囊；供应商环形份额图（中心"N 家 出战"）+ "本命供应商"高亮与"独挑 xx%"（#fde68a 提亮）；"最晚一战 HH:mm · 你还在敲代码"；渐隐分割线页脚。挂载时并行请求今日、ui-config 与昨日 custom 窗口（`start_at`/`end_at` 毫秒），昨日请求失败静默降级。
- `proxy_static/src/styles.css`：`.share-card-canvas` 改为 `max-height: min(66vh, 720px)` + `width:auto`，海报在弹窗内完整可见不再裁切出滚动条。
- 重建 `proxy_static/dist`（index-D95ld-GT.js / index-Ch6z0FYd.css）。
- `tests/share_card.test.js`：新增 4 个用例（称号分档边界、昨日对比涨跌/持平/零基线、最晚时刻格式化与非法输入、星点种子确定性），共 9 用例。

## 验证结果

- `node --test tests/share_card.test.js`：9/9 通过。
- `python -m unittest discover -s tests -p "test_*.py"`：514 个测试全部通过。
- `node --check`（classic/app.js、provider_status/static/app.js、全部 tests/*.test.js）通过；`node --test` 全部 JS 测试无失败。
- `npm run build --prefix proxy_static` 成功；`python local_proxy_app.py --smoke-test` ok:true（classic/modern 双 UI 资源齐备）。
- 浏览器实测（17891 临时实例 + 真实今日数据）：海报完整渲染于弹窗内（无滚动裁切）；大数字 3,006,844、"Token 大户"徽章、"↑ 首日作战 · 明日开启对比"胶囊（本机昨日统计为 0 属真实数据）、三胶囊数值、环形图"1 家"、"本命供应商 cai · 独挑 100%"、"最晚一战 16:41"均正确；导出 PNG（1200×1880）核对无文字重叠或越界。

## PR

pending
