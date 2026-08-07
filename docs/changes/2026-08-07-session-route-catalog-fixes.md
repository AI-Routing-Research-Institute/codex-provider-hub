+++
id = "2026-08-07-session-route-catalog-fixes"
type = "fix"
release_bump = "patch"
status = "verified"
+++

# 修正会话目录与旧请求状态

## 目标

让会话路由列表完整、稳定地展示最近会话，并在多会话分别固定供应商时准确判断真正尚未完成的旧请求。

## 现状

会话设置只读取 Codex 会话索引，请求历史中已存在但索引尚未更新的会话可能缺失；重复索引记录可能影响活动时间排序。会话设置浮层打开后还会随每秒状态轮询反复读取目录。旧请求提示只比较实际供应商与全局当前供应商，导致固定到其他供应商的正常会话被误判。

## 设计范围

- 合并最近 7 天的 Codex 会话索引与可用请求历史，以线程标识去重并隐藏原始线程 ID。
- 使用最大有效活动时间排序，同名会话增加稳定的序号标记。
- 会话目录使用 60 秒缓存，后台刷新保留已有列表，手动刷新可强制更新。
- 后端根据每个会话当前有效路由计算 `draining_requests`，前端据此展示旧请求。

## 非目标

- 不改变上游请求转发、重试和流式响应协议。
- 不扩大请求历史的 24 小时本地保留期限。
- 不为 Claude 控制台启用会话路由入口。

## 兼容性

控制状态中的供应商对象新增 `draining_requests` 数值字段，现有字段和配置格式保持不变；无需数据迁移。前端静态资源缓存版本递增。

## 风险

请求历史仅能补充其保留期内的会话，超过保留期仍依赖 Codex 会话索引。缓存可能让新会话最多延迟 60 秒出现在已打开的浮层中，用户可通过刷新按钮立即更新。

## 测试计划

- Python 全量 unittest。
- Node 前端测试和 JavaScript 语法检查。
- Python compileall、`git diff --check` 和提交前敏感信息扫描。
- 覆盖请求历史独有会话、重复索引时间、同名会话、固定路由并发请求和真正旧请求场景。

## 实际改动

- `local_proxy/codex_sessions.py` 保留重复索引记录中的最大有效更新时间。
- `local_proxy/core.py` 从请求历史生成会话目录，并按会话有效供应商统计旧请求。
- `local_proxy/codex_profile.py` 合并索引与请求历史会话。
- `proxy_static/app.js` 增加目录缓存、静默刷新、同名会话标记和路由感知的旧请求展示。
- `proxy_static/index.html` 更新前端静态资源缓存版本。
- 补充 Python 与 Node 回归测试。

## 验证结果

- `.venv\Scripts\python.exe -m unittest discover -s tests -q`：重基到最新 `origin/main` 后通过，366 项测试。
- `node --test tests/*.test.js`：重基到最新 `origin/main` 后通过，26 项测试。
- `node --check proxy_static/app.js`：通过。
- `.venv\Scripts\python.exe -m compileall -q local_proxy`：通过。
- `git diff --check`：通过。
- 提交前扫描私钥、API Key、GitHub Token、Bearer 凭据和公网 IP：未发现敏感值。

## PR

pending
