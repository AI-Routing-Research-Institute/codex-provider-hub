+++
id = "2026-08-17-provider-quota-classification"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 供应商额度不足诊断修复

## 目标

当供应商以 HTTP 403 返回额度、余额或配额不足信息时，将探测失败归类为“频率或额度受限”，避免错误展示为“当前客户端被拒绝”。

## 现状

Codex SQLite 诊断会优先把任意 HTTP 403 归类为 `client_blocked`，且额度语义仅覆盖少量英文短语。服务器实际返回“用户额度不足”时，状态页因此展示了错误原因。

## 设计范围

- 集中定义额度或限流相关的中英文语义短语。
- 在通用 HTTP 403 判断之前识别明确的额度不足语义。
- 让 Codex TUI 结果和 SQLite 诊断采用一致的语义分类，并纠正已带错误码的 TUI 结果。
- 为 HTTP 403 中文额度不足及既有客户端拒绝场景补充回归测试。

## 非目标

- 不改变连续成功或失败次数对应的健康状态转换规则。
- 不改变供应商排序、探测频率或模型配置。
- 不把供应商专用 Key 写入代码、配置样例、日志或版本库。

## 兼容性

无接口和数据迁移影响。既有错误代码保持不变，仅修正满足明确额度语义的 HTTP 403 分类。属于向后兼容缺陷修复，版本提升选择 `patch`。

## 风险

过宽的关键词可能误判普通错误。通过采用明确的额度、余额、配额及英文余额不足短语，并保留客户端或通道拒绝分类测试来控制风险。

## 测试计划

- 运行 Codex SQLite 诊断单元测试。
- 运行状态探测单元测试，覆盖 TUI 分类和既有直接诊断行为。
- 运行完整 Python 单元测试、JavaScript 语法检查和 JavaScript 测试。

## 实际改动

- 新增 `provider_status/error_semantics.py`，集中维护额度、余额、配额和限流的中英文语义。
- 修改 `provider_status/codex_diagnostics.py`，解析 JSON 日志中的 Unicode 文本，并让明确额度语义优先于通用 HTTP 403 客户端拒绝分类。
- 修改 `provider_status/probe.py`，统一普通 TUI 失败与已带诊断错误码结果的额度分类。
- 修改 `tests/test_codex_diagnostics.py` 和 `tests/test_status_probe.py`，覆盖服务器实际出现的 HTTP 403 中文额度不足响应，同时保留客户端拒绝场景。

## 验证结果

- `.\.venv\Scripts\python.exe -m unittest tests.test_codex_diagnostics tests.test_status_probe`：通过，21 项测试成功。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`：通过，435 项测试成功。
- `node --check proxy_static/app.js`：通过。
- `node --check provider_status/static/app.js`：通过。
- `Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }`：通过，40 项测试成功。

## PR

https://github.com/AI-Routing-Research-Institute/codex-provider-hub/pull/32
