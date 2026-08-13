+++
id = "2026-08-13-transient-provider-dns"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 容忍供应商域名临时解析失败

## 目标

单个供应商域名临时无法解析或已经失效时，状态探测 worker 仍可启动并继续检测其他供应商；失效供应商在实际检测时记录独立失败。

## 现状

`load_config()` 会在 worker 启动时解析所有供应商域名。任一域名返回 NXDOMAIN 或发生临时 DNS 错误时，整个配置加载失败，worker 进入重启循环。2026-08-13 部署重启时，`welfare.0xpsyche.me` 已被公共 DNS 确认为 NXDOMAIN，因此其余供应商也无法继续自动检测。

## 设计范围

- 配置加载继续验证 HTTPS URL 结构、禁止凭据/查询参数/片段、localhost 和非公网 IP 字面量。
- 配置加载时若域名能够解析，继续拒绝任何非公网解析结果。
- 配置加载时仅容忍域名当前无记录或解析器临时失败，不因单个供应商阻止 worker 启动。
- 每次自动检测和点击检测前重新严格解析本次客户端实际使用的端点。
- 运行时无法解析或解析到非公网地址时，不启动 Codex/Claude 客户端，并返回该供应商的检测失败结果。
- 增加配置加载、自动检测与点击检测共用探测路由的回归测试。

## 非目标

- 不修改供应商地址、凭据、检测周期或状态页排序。
- 不绕过 HTTP、localhost、私网、链路本地地址或 DNS 重绑定防护。
- 不在服务器 hosts 文件中固定供应商地址，也不创建占位凭据。

## 兼容性

无接口、配置格式或数据库迁移。仅把启动阶段的 DNS 可用性要求延后到每次实际检测，选择 `patch`，因为这是 worker 可用性的缺陷修复。

## 风险

域名失效的供应商会持续产生独立网络失败记录；通过现有失败退避降低请求频率。运行时严格公网解析在每次检测前执行，确保启动容忍不会放宽实际请求的网络边界。

## 测试计划

- 测试配置加载允许解析器抛出临时错误和返回空地址。
- 测试配置加载仍拒绝私网解析结果及所有既有非法 URL。
- 测试运行时 DNS 失败和私网解析时不调用底层探测客户端。
- 测试运行时公网解析后正常调用对应 Codex/Claude 客户端。
- 运行状态模块聚焦测试、Python 与 JavaScript 全量测试、语法、编译和 diff 检查。

## 实际改动

- `provider_status/config.py`：复用公开 HTTPS 端点校验；配置加载仅容忍 DNS 异常或空解析，仍拒绝非法 URL 和已解析出的非公网地址。
- `provider_status/probe.py`：自动与点击检测共用的路由在每次调用底层 Codex/Claude 客户端前严格校验对应端点；失败时返回 `network_error`，不启动客户端。
- `tests/test_status_config.py`：覆盖启动阶段 DNS 异常和空解析容忍，并保留私网解析拒绝回归。
- `tests/test_claude_probe.py`：覆盖运行时公网路由、DNS 失败和私网解析拦截。

## 验证结果

- `.venv\Scripts\python.exe -m unittest tests.test_status_config tests.test_status_probe tests.test_claude_probe -v`：49 项通过。
- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：432 项通过。
- 对 `tests/*.test.js` 执行 `node --test`：40 项通过。
- `node --check proxy_static/app.js` 与 `node --check provider_status/static/app.js`：通过。
- `.venv\Scripts\python.exe -m compileall -q provider_status local_proxy scripts`：通过。
- `git diff --check`：通过。

## PR

pending
