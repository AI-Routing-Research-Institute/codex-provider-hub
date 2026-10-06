+++
id = "2026-10-06-classic-usage-features"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 经典界面补齐用量趋势、今日战报与请求次数

## 目标

在保留经典界面布局与交互的前提下，增加独立用量趋势页、今日 Token 战报分享和按当前统计范围展示的供应商请求次数。

## 现状

经典界面已有 Token 汇总、用量明细与活动请求徽章，但缺少新版的六种用量图表和 PNG 战报。供应商列表的请求列只显示活动请求，未常驻显示统计范围内累计次数。

## 设计范围

- 导航在“请求”之后新增“用量趋势”，独立全宽显示六种图表，复用新版现有组件、统计接口与指标切换行为。
- 供应商页 Token 汇总区右侧新增“今日战报”，弹窗预览并下载/复制 PNG；战报固定统计今日 00:00 至现在，不跟随供应商页的时间筛选。
- 现有“请求”列改为“请求次数”，展示统计范围内累计次数，下方保留原活动请求徽章、悬浮会话明细与点击行为；不增加新列。
- 使用独立构建入口将共享 Vue 组件挂载到经典页的局部容器，不替换经典应用，不引入整套新版全局样式；保留经典浅色/深色与窄屏布局。
- 关闭用量记录功能时隐藏相关入口；离开趋势页时清理轮询和事件监听，加载异常提供可见错误反馈。
- 构建、打包冒烟验证与前端回归测试覆盖新增局部资源；生成的 dist 仍不提交。

## 非目标

不新增“按供应商跳转并自动筛选”的第四项交互，不修改代理转发、供应商配置、数据库结构、鉴权或发布工作流，不自动重启用户服务，不自动发版。

## 兼容性

新增 UI 能力，无接口、配置和数据迁移。复用现有 Codex/Claude 服务前缀及统计接口。构建仍通过 npm ci / npm run build 产出 dist，发布继续打包同一资源目录。选择 minor，因为经典界面新增用户可用能力，无不兼容改动。

## 风险

- 共享组件样式泄漏到经典页：独立资源入口，新增样式限定在经典功能容器内，不加载新版全局 CSS。
- 动态组件轮询残留或离开页面后仍请求：仅激活趋势页时挂载，离开时卸载并复用现有清理逻辑。
- 统计口径混淆：请求次数跟随供应商页时间范围，活动数单独显示；按钮明确写“今日战报”，弹窗注明今日及昨日全天对比。
- 新资源漏打包或入口失效：固定入口路径、构建检查、静态资源服务测试和打包冒烟必需资源校验。

## 测试计划

- JS 单测覆盖视图恢复与禁用回退、局部组件挂载/卸载、战报开关、累计/活动次数与原悬浮行为、静态入口及样式作用域。
- Python 控制台资源测试覆盖 Codex/Claude 两套经典入口及新增构建资源；构建后核对主入口、共享块和打包必需资源。
- 检查桌面/窄屏及浅色/深色布局，验证六种图表、时间范围、战报弹窗及图片导出。
- Python 全量、JS 全量、所有 JS 语法、npm ci/build、git diff --check；最新主线 rebase 后准确 HEAD 完整验证、Ruleset、PR squash。

## 结构化自审

- 用户范围：仅经典界面的第一、第二、第三项，保留原请求徽章行为。
- 技术范围：优先组件复用，新增局部构建入口与容器，不复制两份绘图/海报逻辑。
- 安全：不访问或提交私有配置、凭据、数据库；不提交构建产物；不重启服务。
- 交付：说明状态与真实验证同步；经 PR 合并后提出版本与摘要，再询问是否发版。

## 实际改动

- `proxy_static/classic/index.html`、`classic/app.js`、`classic/styles.css`：请求之后新增趋势导航；Token 汇总右侧增加今日战报；请求列展示所选统计范围内累计次数并保留活动徽章、悬浮详情及点击行为；用量功能关闭时隐藏入口并回退供应商页。
- `proxy_static/src/classic-features.js`、`classic-features.css`：独立 Vue 局部挂载入口，共享新版趋势与战报组件；切换页面挂载/卸载趋势，战报关闭恢复按钮焦点；样式只限定经典功能容器，兼容浅深色与窄屏，修正窄屏日期面板越界。
- `proxy_static/src/components/UsageTrendView.vue`：保留主统计接口错误提示；持久化并校验自定义日期范围，避免卸载/恢复后丢失；卸载清理轮询并阻止排队及辅助刷新。上述共享组件修正同时适用于新版。
- `proxy_static/vite.config.js`：增加经典多入口构建，固定经典 JS/CSS 入口名，其他资源继续哈希命名与共享代码块，不改变新版入口。
- `local_proxy/application.py`：打包冒烟验证要求经典新增 JS/CSS，资源数量按集合去重；现有静态资源路由直接服务这两个文件，无后端接口变更。
- `tests/classic_usage_features.test.js`、`local_proxy_view_state.test.js`、`local_proxy_ui_config.test.js`、`local_proxy_vue_ui.test.js`、`test_server.py`：覆盖入口顺序、禁用及恢复、局部挂载生命周期、累计/活动次数、原交互、统计错误、自定义范围与卸载时排队刷新、双协议静态资源；配置测试补全浏览器事件环境并验证入口隐藏及同步事件。

## 验证结果

- 定向 JS：`node --test tests/classic_usage_features.test.js tests/local_proxy_view_state.test.js tests/local_proxy_requests.test.js tests/local_proxy_vue_ui.test.js tests/share_card.test.js`，初始 62/62 通过；补充范围恢复及排队刷新测试后，新增测试文件 9/9 通过。
- 定向 Python：`python -m unittest tests.test_control_ui tests.test_server.UnifiedProxyAppTests.test_control_views_share_assets_and_keep_service_state_separate`，3/3 通过。
- `npm run build --prefix proxy_static`：通过，生成独立经典 JS/CSS、共享趋势组件块及新版入口；dist 为 ignored 生成文件，不提交。
- 真实浏览器：独立 `127.0.0.1:17991` 临时服务器，仅使用合成供应商和临时数据库，未重启或修改用户服务。1280×720 桌面、390×844 窄屏、浅色/深色均检查；六图型、近 7 天、自定义日期及跨页恢复正确；供应商页近 7 天合计 627.98K，战报仍为今日 20,384 Token；下载显示“已保存 token-card-20261006.png”，复制显示“战报已复制”；Codex/Claude 经典入口和新版入口均正常加载。
- 浏览器自动化 download 事件等待超时，但实际页面明确反馈保存成功；未通过该事件取得下载文件路径，不将文件落盘检查声称为完成。
- 首轮完整门禁发现 `tests/local_proxy_ui_config.test.js` 的 VM 缺少 `window` 事件环境；真实浏览器无该问题。补全测试环境并增加禁用入口/事件断言，`node --test tests/local_proxy_ui_config.test.js` 1/1 通过。
- `python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8'); from scripts.team_policy import run_full_verification; run_full_verification()"`：完整门禁通过，npm ci/build、策略 JS 语法、Python 全量与所有 JS 测试文件均完成；相对 merge-base `8e7c350ed3694e6408f3e9f298464399a03407f3` 无新增失败，基线豁免 0 项。
- `node --test tests/*.test.js`：107/107 通过，无失败、无跳过。
- `rg --files -g '*.js'` 列出仓库 32 个 JS 文件，对每个执行 `node --check`：全部通过；`git diff --check`：通过。
- `verify_ruleset('AI-Routing-Research-Institute/codex-provider-hub', token=...)`：只读核验 `agent-delivery-main`（21697881）通过；token 未输出或持久化。
- `npm ci` 报告依赖安全提示 5 项（1 moderate、4 high），本轮未改依赖或锁文件，不进行越范围升级。
- commit/rebase 后由 pre-push hook 针对准确 HEAD 再次完整验证，验证证据与最终 head SHA 见 PR。

## PR

pending
