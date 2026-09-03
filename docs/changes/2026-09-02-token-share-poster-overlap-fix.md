+++
id = "2026-09-02-token-share-poster-overlap-fix"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修复今日 Token 战报图表重合

## 目标

修复今日 Token 战报海报中环形图与供应商图例、单供应商说明发生重合的问题，同时保持海报导出尺寸为 600×1000。

## 现状

环形图描边的实际外缘已经覆盖到图例文字所在的纵坐标，单供应商说明也紧贴圆环底部。模板声明的 Canvas 高度仍为 940，而绘制逻辑使用 1000，导致预览尺寸声明与导出尺寸不一致。

## 设计范围

- 在前端 Canvas 绘制中统一 600×1000 尺寸。
- 缩小并上移环形图，为图例和单供应商说明预留安全间距。
- 将最多四个供应商图例按最大宽度自动排成一行或两行，并截断过长名称。
- 通过共享布局计算安排图例、最晚一战、分割线和页脚坐标。
- 增加布局边界单测并重建新版静态资源。

## 非目标

- 不修改后端接口、Token 统计数据、配色、文案或下载/复制行为。
- 不增加海报高度，不修改经典控制台。

## 兼容性

仅影响新版控制台战报 Canvas 的预览和 PNG 导出布局；导出尺寸保持 600×1000，数据接口和交互兼容不变。

## 风险

长供应商名称或多行图例可能挤压底部区域。布局函数限制图例宽度和行数，并对名称执行宽度截断；测试覆盖无供应商、单供应商和多供应商边界。

## 测试计划

- `node --test tests/share_card.test.js tests/local_proxy_vue_ui.test.js`
- `npm run build --prefix proxy_static`
- 浏览器检查无供应商、单供应商、多供应商和长名称场景，并核对导出 PNG 无重合、越界或裁切。

## 实际改动

- `proxy_static/src/share-card.js` 新增 `posterLayout`，统一计算圆环、图例、底部战报和页脚的安全坐标。
- `proxy_static/src/components/ShareCardDialog.vue` 将 Canvas 声明统一为 600×1000，缩小并上移圆环，支持图例自动换行并使用共享布局坐标。
- `tests/share_card.test.js` 和 `tests/local_proxy_vue_ui.test.js` 增加布局边界和尺寸回归测试。
- `proxy_static/dist` 重建为 `index-Dje8QKyK.js` 与 `index-aE9onvpw.css`。

## 验证结果

- `node --test tests/share_card.test.js tests/local_proxy_vue_ui.test.js`：32/32 通过（2026-09-03，分支 `fix/token-share-poster-overlap-fix`，基线 `origin/main@432e519`）。
- `node --test tests/*.test.js`：88/88 通过。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py`：532/532 通过。
- `node --check proxy_static/classic/app.js`、`proxy_static/src/*.js`、`provider_status/static/app.js`：全部通过。
- `npm run build --prefix proxy_static`：Vite 构建成功，产物仍为 `index-Dje8QKyK.js` 与 `index-aE9onvpw.css`。
- `git diff --check`：通过。
- 浏览器实测 `http://127.0.0.1:4173/`：真实两供应商数据下圆环、图例、最晚一战和页脚无重合；Canvas 预览正常。多行布局由纯函数边界测试覆盖。

## PR

pending
