+++
id = "2026-09-03-hook-encoding"
type = "fix"
release_bump = "none"
status = "verified"
+++

# 修复门禁中文报错乱码

## 目标

git-bash 下触发门禁失败时，中文 policy 报错在终端清晰可读，不再显示 `����`。

## 现状

`scripts/team_policy.py` 用普通 `print(..., file=sys.stderr)` 输出中文，编码跟随进程 locale；钩子跑在 git-bash（MSYS sh）下默认代码页非 UTF-8，中文在 sh→终端链路上被错误解码成乱码，难排查。

## 设计范围

- `.githooks/commit-msg、pre-commit、pre-push`：`export PYTHONIOENCODING=utf-8`（加 `LC_ALL=C.UTF-8` 兜底）。
- `scripts/team_policy.py` 启动时重配 stdio 为 UTF-8（`errors="backslashreplace"`，try 包裹防管道异常），无环境变量时也不乱码。
- 顺带把 #74 带入的 `proxy_static/dist/index.html` 换行符噪音（11 行 CRLF + 1 行孤立 `\r\r\n`）归一为 LF，保持主线干净；字节级验证零语义。
- 报错文本一个字不改。

## 非目标

- 不改任何门禁规则与校验逻辑。
- 不改其他脚本输出。

## 兼容性

无。仅输出编码，行为与接口不变；`none` 不触发发版。

## 风险

- 老环境 Python 若不支持 `reconfigure`：已 try 包裹降级，无影响。
- 缓解：回归跑 `tests/test_team_policy.py`。

## 测试计划

- 故意触发一次门禁失败（如坏 commit-msg），同终端确认中文清晰。
- 运行 `tests/test_team_policy.py`。

## 实际改动

- `.githooks/commit-msg、pre-commit、pre-push`：各加 `export PYTHONIOENCODING=utf-8` + `export LC_ALL=C.UTF-8`。
- `scripts/team_policy.py`：新增 `_ensure_utf8_stdio()` 并在 `main()` 入口调用，stdio 重配 UTF-8（`backslashreplace`，异常吞掉降级）。
- 新增本说明 `docs/changes/2026-09-03-hook-encoding.md`。
- `proxy_static/dist/index.html`：归一 #74 带入的 CRLF（含 1 行 `\r\r\n`）为 LF。

## 验证结果

- A/B 对照（同终端同坏消息）：旧代码输出 `policy error: commit �������ʹ��ǰ�� emoji��Conventional Commit �ͼ�����������`；新代码经钩子输出 `policy error: commit 标题必须使用前置 emoji、Conventional Commit 和简体中文描述`，清晰可读。
- `python -m unittest tests.test_team_policy`：25 tests OK。
- dist 归一验证：去 CR 后与基线字节完全一致（零语义）；入库 blob CR=0。
- 编辑后全文件换行符检查通过（`team_policy.py` 还原为 LF，diff 仅 +20 行）。

## PR

pending
