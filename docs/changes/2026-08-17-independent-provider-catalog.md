+++
id = "2026-08-17-independent-provider-catalog"
type = "feature"
release_bump = "minor"
status = "verified"
+++

# Codex 独立供应商管理

## 目标

让 Codex 本地中转使用自己的供应商目录运行，支持在控制台新增、编辑供应商，并将 CC Switch 降级为可选的只读导入来源。

## 现状

Codex 本地中转启动、刷新和运行设置都直接读取 CC Switch SQLite 数据库。“管理列表”只支持排序和隐藏，不能创建或修改供应商；CC Switch 数据库不可用时也无法独立启动。

## 设计范围

- 在 `~/.codex-local-proxy/codex-providers.sqlite3` 建立独立供应商目录。
- 首次初始化时从现有 CC Switch 数据库自动复制一次快照；导入失败或源不存在时仍允许空目录启动。
- 提供本地供应商新增、编辑和 CC Switch 手动导入接口。
- 导入保留 CC Switch 供应商 ID，默认跳过本地已存在记录，可明确选择覆盖。
- 管理界面增加新增、导入和逐行编辑入口，保存后立即刷新路由且不需要重启。
- API 不返回已保存的 API Key；空 Key 表示保留，显式选项才允许清除。

## 非目标

- 本轮不改 Claude Code 供应商数据源。
- 不向 CC Switch 数据库写入任何内容，也不提供持续双向同步。
- 不改变服务器健康检测供应商配置。
- 不实现跨设备同步或云端密钥托管。

## 兼容性

首次启动保留导入供应商的原 ID，因此当前选择、顺序、隐藏状态、会话路由和历史统计继续关联。共享设置中的 CC Switch 数据库路径继续保留，但仅作为导入来源。新增本地供应商不会自动纳入服务器健康检测。

这是新增独立供应商管理能力，版本提升选择 `minor`。

## 风险

- 供应商 Key 属于敏感信息，必须存放在仅当前用户可访问的本地数据库中，且禁止通过状态接口、日志或错误返回。
- 编辑当前供应商时必须以原子方式刷新路由，避免请求看到半写入状态。
- 再次导入不能静默覆盖本地修改，默认采用仅新增策略。
- 首次迁移失败不能阻止控制台启动，否则用户无法通过新增功能修复配置。

## 测试计划

- 覆盖独立目录初始化、首次导入、重复导入、新增、编辑和 Key 保留/清除行为。
- 覆盖新增及编辑接口不泄露凭据和路由热刷新。
- 覆盖无 CC Switch 数据库时的空目录启动。
- 覆盖管理界面的新增、编辑和导入交互。
- 运行完整 Python 单元测试、JavaScript 语法检查和 JavaScript 测试。

## 实际改动

- 新增 `local_proxy/provider_catalog.py`，在 `codex-providers.sqlite3` 中维护本地供应商、目录元数据和自动索引，并在首次初始化时将 CC Switch 配置展开为独立快照。
- 修改 `local_proxy/codex.py`、`local_proxy/codex_profile.py` 和 `local_proxy/shared_settings.py`，让 Codex 启动、刷新和请求路由只读取本地目录，CC Switch 路径仅参与首次或手动导入。
- 修改 `local_proxy/server.py`，增加供应商详情、新增、编辑和 CC Switch 导入接口；目录读写在线程池执行，保存后热刷新路由。
- 修改 `proxy_static/index.html`、`proxy_static/app.js` 和 `proxy_static/styles.css`，增加新增、逐行编辑、仅新增/覆盖导入和响应式管理界面；将低频管理操作移到仅在管理模式显示的独立操作条，并将行内认证状态与管理按钮整理为紧凑横向操作组。
- API Key、敏感 Header 和敏感查询参数不返回原值；编辑时支持保留、替换或显式清除 Key，并保留脱敏配置项的原值。
- 新增 `tests/test_provider_catalog.py` 并扩展现有页面、路径和 UI 配置断言。

## 验证结果

- `python -m unittest discover -s tests -p 'test_*.py'`：通过，运行 440 个测试。
- `node --check proxy_static/app.js`：通过。
- 逐个运行 `tests/*.test.js`：通过，共 40 个 JavaScript 测试。
- `git diff --check`：通过。

## PR

pending
