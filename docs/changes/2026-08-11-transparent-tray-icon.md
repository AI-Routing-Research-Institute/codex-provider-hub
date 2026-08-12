+++
id = "2026-08-11-transparent-tray-icon"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 托盘与应用图标透明背景

## 目标

将本地中转托盘图标、可执行文件图标和 macOS 应用图标从实心青绿色底改为透明背景，让图标在不同深浅系统主题下都自然融入，不再出现突兀的纯色方块。

## 现状

图标由 `local_proxy/application.py` 的 `create_app_icon()` 在运行时用 Pillow 生成：整张 64×64 RGBA 画布用 `#146c73` 填充，再绘制同色的圆角矩形和白色 "CX" 文字。因此托盘图标、Windows 可执行文件图标（`--write-icon` 生成的 `.ico`）和 macOS `.icns`（构建脚本在打包时调用）都是带实心背景的方块，视觉效果不佳。

## 设计范围

- 将 `create_app_icon()` 的画布改为透明背景（RGBA alpha=0）。
- 保留单独的圆角矩形作为图形主体，但矩形背景改为透明，只保留白色 "CX" 文字与描边，使整体呈透明底、可随系统主题明暗变化。
- `--write-icon` 输出的 `.ico`（Windows）与 `.icns`（macOS）继续使用同一透明素材，保存时保留 alpha 通道。
- 托盘运行时图标（`pystray`）继续复用 `create_app_icon()`，与构建产物视觉一致。
- 同步更新图标相关测试，若现有测试断言了非透明背景则一并调整。

## 非目标

- 不重新设计图标图形、配色或文字内容（仍为白色 "CX" 透明底）。
- 不修改 `provider_status` 浏览器 favicon/apple-touch-icon。
- 不改变图标尺寸、圆角半径或生成格式选择逻辑。

## 兼容性

无接口、配置或数据库变更。图标生成入口与构建脚本（`scripts/build_local_proxy_exe.ps1`、`scripts/build_local_proxy_macos.sh`）不需要改动。仅产物视觉样式变化，属于向后兼容界面增强，版本选择 `minor`。

## 风险

- 透明底在 Windows 托盘浅色主题下可能看不清白色文字；通过保留深色描边或调整文字颜色缓解，并在冒烟测试中验证 alpha 通道确实为透明。
- `.ico`/`.icns` 对透明支持不同；生成时显式保留 RGBA alpha，并验证多尺寸输出均含透明像素。
- 测试若依赖具体颜色值会失败；优先断言 alpha 通道而非具体背景色。

## 测试计划

- 生成图标后断言画布 alpha 层在非图形区域为 0（透明），图形区域 alpha 为 255。
- 断言 `--write-icon` 输出的 `.ico` 与 `.icns` 可正常读取且含透明像素。
- 运行 Python 单元测试、Node 测试、`compileall` 与 `git diff --check`。

## 实际改动

- `local_proxy/application.py` `create_app_icon()`：64×64 画布由 `#146c73` 实心填充改为 `(0, 0, 0, 0)` 全透明；删除同色圆角矩形背景块；白色 "CX" 文字描边加粗为 `stroke_width=2` 并使用 `#146c73` 描边色，保证透明底上图标在深浅系统主题下都清晰可辨。
- `tests/test_local_proxy_app.py` 新增 `test_app_icon_has_transparent_background`：断言图标为 64×64 RGBA，且 alpha 通道中透明像素多于不透明像素（外框区域透明、"CX" 字形不透明）。
- 构建脚本与 `--write-icon` 入口未改动；`.ico`/`.icns` 继续复用同一 RGBA 素材，透明 alpha 随生成图标自然保留。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：393 项全部通过。
- `node --check proxy_static/app.js`、`node --check provider_status/static/app.js`：语法检查通过（本次未改动 JS，例行执行）。
- `node --test tests/*.test.js`：Node 测试全部通过。
- 生成图标逐像素检查：4096 像素中 3586 个 alpha=0（透明背景生效），白色文字带深青描边清晰。
- `.\.venv\Scripts\python.exe -m compileall -q local_proxy tests`：通过。
- `git diff --check`：无空白错误。

## PR

pending
