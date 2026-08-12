+++
id = "2026-08-12-icon-glyph-size"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# 图标字形放大铺满画布

## 目标

将托盘与应用图标中的 "CX" 字形从 26px 小字放大到接近铺满 64×64 透明画布，使图标在托盘、任务栏和 Finder 中清晰醒目，视觉尺寸与同类应用图标相当。

## 现状

`create_app_icon()` 使用固定 26px 字号在 64×64 透明画布上绘制 "CX"。透明背景生效后（见 `2026-08-11-transparent-tray-icon`），字形四周留出大量空白，图标看起来比实际更小，在系统托盘中难以辨认。

## 设计范围

- 字号不再固定，按画布尺寸动态计算：从大到小尝试候选字号，选择字形包围盒（含描边）能放入目标区域（64px 画布留出安全边距）的最大字号。
- 保留白色填充、`#146c73` 描边的配色与居中逻辑；描边宽度随字形尺寸适度加粗，保证大字下描边比例协调。
- 无 TrueType 字体可用时回退到 Pillow 默认位图字体，居中绘制逻辑不变。
- 同步调整图标测试：透明背景断言保留，新增字形尺寸下限断言防止字号回退。

## 非目标

- 不改变图标文字内容、配色或透明背景。
- 不修改构建脚本与 `--write-icon` 输出格式。
- 不引入新的图标尺寸（仍为 64×64 源图，由 .ico/.icns 容器自行缩放）。

## 兼容性

无接口、配置或数据库变更。仅图标视觉变化，向后兼容，版本选择 `minor`。

## 风险

- 不同平台字体度量差异可能导致字形超出画布被裁切；通过以包围盒实测为准并保留安全边距缓解。
- 字形变大后透明/不透明像素比例变化，原有"透明像素多于不透明像素"的断言可能失败；调整为透明像素占比下限断言。
- macOS 字体回退（Helvetica.ttc）度量与 Windows 不同；测试只断言尺寸下限与透明占比，不断言具体字号。

## 测试计划

- 断言生成图标字形包围盒宽度和高度不低于阈值（如 40px），防止回退为小字。
- 断言四角区域仍为透明、透明像素占比不低于阈值。
- 运行 Python 单元测试、Node 测试、compileall 与 git diff --check。

## 实际改动

- `local_proxy/application.py` `create_app_icon()`：字号不再固定 26px，改为从大到小扫描候选字号，选择 "CX" 含描边包围盒能放入 64px 画布（边距 3、描边 3）的最大字号；居中计算改用新包围盒的左上角偏移，避免描边导致偏移。
- `tests/test_local_proxy_app.py`：`test_app_icon_has_transparent_background` 改为断言四角 alpha=0 且全透明像素占比 ≥ 1/4（适应字形放大后像素比例变化）；新增 `test_app_icon_glyphs_fill_canvas`，断言字形包围盒宽 ≥ 44、高 ≥ 28。"CX" 并排时宽度是约束维度（当前 57×35），高度阈值保守以保证跨平台字体度量下仍通过。

## 验证结果

- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：394 项全部通过。
- `node --check proxy_static/app.js`、`node --check provider_status/static/app.js`：语法检查通过（未改动 JS）。
- `node --test tests/*.test.js`：Node 测试全部通过。
- 生成图标逐像素检查：字形包围盒 57×35（旧版 26px 字号为 39×24），透明像素 2526/4096，四角 alpha=0。
- `.venv\Scripts\python.exe -m compileall -q local_proxy tests`：通过。
- `git diff --check`：通过（仅仓库既有 LF/CRLF 转换提示）。

## PR

pending
