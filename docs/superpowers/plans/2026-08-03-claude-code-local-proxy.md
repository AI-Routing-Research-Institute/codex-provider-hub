# Claude Code 双网页本地中转 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有 Codex 行为和安装包名称的前提下，让一个桌面程序同时启动 Codex 与 Claude Code 两个本地中转网页。

**Architecture:** 将现有转发循环中的协议差异收敛到适配器接口，Codex 继续使用 Responses 适配器，Claude 使用 Anthropic Messages 适配器。两个 FastAPI 服务、路由状态和数据目录彼此独立，由 `codex_local_proxy_app.py` 统一启动、打开两个网页并通过一个托盘管理生命周期。

**Tech Stack:** Python 3.11+、FastAPI、httpx、SQLite、unittest、原生 HTML/CSS/JavaScript、PyInstaller、PowerShell。

---

## 文件结构

- 新建 `provider_proxy_protocol.py`：声明协议适配器接口以及 Codex/Claude SSE 和 usage 解析器。
- 修改 `codex_local_proxy.py`：让共享转发循环接受协议适配器，同时保持默认 Codex 行为。
- 新建 `claude_local_proxy.py`：Claude CC Switch 加载器和 Claude FastAPI 服务。
- 新建 `claude_local_proxy_app.py`：Claude 设置、配置片段和服务装配，不单独创建托盘。
- 修改 `codex_local_proxy_app.py`：统一启动两个服务、打开两个网页并管理单托盘。
- 新建 `claude_proxy_static/`：Claude Code 独立控制台。
- 修改 `packaging/CodexLocalProxy.spec`：把 Claude 网页资源打进现有同名程序。
- 新建 `tests/test_claude_local_proxy.py`、`tests/test_claude_local_proxy_app.py`：Claude 功能测试。
- 修改 `tests/test_codex_local_proxy.py`、`tests/test_codex_local_proxy_app.py`、`tests/test_windows_release.py`：共享适配器和双服务回归测试。
- 修改 `README.md`、`docs/codex-local-proxy.md`：中文使用说明。

### Task 1: 建立协议适配边界并保持 Codex 行为不变

**Files:**
- Create: `provider_proxy_protocol.py`
- Modify: `codex_local_proxy.py`
- Modify: `tests/test_codex_local_proxy.py`

- [ ] **Step 1: 写 Codex 默认适配器的失败测试**

在 `tests/test_codex_local_proxy.py` 增加断言，确认未显式传适配器时仍转发 Responses 请求、替换 `Authorization`，并识别 `response.output_text.delta` 为已输出事件。

```python
async def test_default_protocol_remains_codex_responses(self) -> None:
    seen: list[httpx.Request] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "response-1"})

    app = create_proxy_app(
        ProviderRouter((provider("selected", current=True),)),
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    response = await self.client_for(app).post(
        "/v1/responses",
        headers={"Authorization": "Bearer local-placeholder"},
        json={"model": "gpt-5", "input": "hello"},
    )
    self.assertEqual(response.status_code, 200)
    self.assertEqual(seen[0].url.path, "/v1/responses")
    self.assertNotEqual(seen[0].headers["authorization"], "Bearer local-placeholder")
```

- [ ] **Step 2: 运行测试并确认失败原因是适配器接口不存在**

Run: `python -m unittest tests.test_codex_local_proxy.ProxyAppTests.test_default_protocol_remains_codex_responses -v`

Expected: FAIL，提示 `protocol_adapter` 或适配器类型尚不存在。

- [ ] **Step 3: 新增协议接口和 Codex 默认实现**

在 `provider_proxy_protocol.py` 定义小接口，不把路由、数据库或 FastAPI 代码搬入该文件：

```python
class ProxyProtocol(Protocol):
    name: str
    generation_paths: frozenset[str]

    def request_headers(
        self, incoming: Mapping[str, str], provider: Any
    ) -> dict[str, str]: ...

    def retry_kind(self, response: httpx.Response) -> str | None: ...
    def sse_preflight(self, buffered: bytes, *, end_of_stream: bool = False) -> PreflightDecision: ...
    def usage_capture(self, request_body: bytes, upstream_path: str) -> UsageCaptureProtocol: ...


CODEX_PROTOCOL = CodexResponsesProtocol()
```

修改 `create_proxy_app(..., protocol_adapter=CODEX_PROTOCOL)` 和 `_forward_request(..., protocol_adapter)`，让 Header、状态码、SSE 提交点和 usage capture 通过适配器调用。保留现有公开函数作为 Codex 适配器的兼容包装，避免已有测试和调用者失效。

- [ ] **Step 4: 运行 Codex 代理测试**

Run: `python -m unittest tests.test_codex_local_proxy -v`

Expected: PASS，现有 Codex 测试和新增特征测试全部通过。

- [ ] **Step 5: 提交协议边界**

```powershell
git add provider_proxy_protocol.py codex_local_proxy.py tests/test_codex_local_proxy.py
git commit -m "refactor: extract proxy protocol adapter"
```

### Task 2: 只读加载 Claude 供应商

**Files:**
- Create: `claude_local_proxy.py`
- Create: `tests/test_claude_local_proxy.py`

- [ ] **Step 1: 写真实结构的 Claude 数据库测试**

测试库包含 `settings_config.env`、`meta.apiFormat`、`meta.apiKeyField`、`common_config_claude` 和 `provider_endpoints`。只断言认证字段名，不断言密钥内容。

```python
providers = load_claude_proxy_providers(database)
self.assertEqual(len(providers), 2)
self.assertEqual(providers[0].base_url, "https://claude.example.test")
self.assertEqual(providers[0].credential_kind, "api_key")
self.assertEqual(providers[1].credential_kind, "auth_token")
self.assertFalse(providers[1].compatible)
```

- [ ] **Step 2: 运行测试并确认加载器缺失**

Run: `python -m unittest tests.test_claude_local_proxy.ClaudeCCSourceTests -v`

Expected: FAIL with `ModuleNotFoundError` or missing `load_claude_proxy_providers`.

- [ ] **Step 3: 实现 Claude provider 数据模型和加载器**

```python
@dataclass(frozen=True)
class ClaudeProxyProvider(ProxyProvider):
    credential_kind: str = "api_key"
    api_format: str = "anthropic"
    compatible: bool = True
    default_models: Mapping[str, str] = field(default_factory=dict)


def load_claude_proxy_providers(
    db_path: Path = DEFAULT_DATABASE,
) -> tuple[ClaudeProxyProvider, ...]:
    # 使用 mode=ro，查询 app_type='claude'，解析 env/meta/common config。
    ...
```

加载器必须过滤 `claude-desktop`，将 `openai_chat` 标记为不兼容，并对缺地址、无认证、重复 ID 和非法 URL 给出可测试错误。

- [ ] **Step 4: 运行 Claude 加载器测试**

Run: `python -m unittest tests.test_claude_local_proxy.ClaudeCCSourceTests -v`

Expected: PASS。

- [ ] **Step 5: 提交 Claude 数据加载器**

```powershell
git add claude_local_proxy.py tests/test_claude_local_proxy.py
git commit -m "feat: load Claude providers from CC Switch"
```

### Task 3: 实现 Anthropic Messages 协议、重试和 Token 解析

**Files:**
- Modify: `provider_proxy_protocol.py`
- Modify: `claude_local_proxy.py`
- Modify: `tests/test_claude_local_proxy.py`

- [ ] **Step 1: 写 Anthropic 协议失败测试**

覆盖 API Key/Auth Token 请求头、HTTP 529、SSE `overloaded_error`、输出前重试、输出后不重放和 usage：

```python
event_stream = (
    b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
    b'{"input_tokens":12,"cache_read_input_tokens":4}}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta",'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","usage":'
    b'{"output_tokens":3}}\n\n'
)
```

断言最终统计为输入 `12`、输出 `3`、缓存 `4`，并断言真实上游请求不包含客户端占位 Key。

- [ ] **Step 2: 运行 Anthropic 协议测试并确认失败**

Run: `python -m unittest tests.test_claude_local_proxy.ClaudeProtocolTests -v`

Expected: FAIL，缺少 `CLAUDE_PROTOCOL`、Anthropic usage 或 SSE 判定。

- [ ] **Step 3: 实现 Claude 协议适配器**

```python
class ClaudeMessagesProtocol:
    name = "anthropic_messages"
    generation_paths = frozenset({"messages"})
    retryable_status_codes = frozenset({408, 429, 500, 502, 503, 504, 529})

    def request_headers(self, incoming, provider):
        headers = strip_proxy_headers(incoming, extra={"x-api-key"})
        if provider.credential_kind == "auth_token":
            headers["Authorization"] = f"Bearer {provider.api_key}"
        else:
            headers["x-api-key"] = provider.api_key
        return headers
```

SSE 解析提交规则：`message_start` 继续缓冲；包含文本、思考或工具参数的 `content_block_delta` 提交；临时 `error` 事件在提交前触发重试，提交后只记录。

- [ ] **Step 4: 运行 Claude 协议和 Codex 回归测试**

Run: `python -m unittest tests.test_claude_local_proxy tests.test_codex_local_proxy -v`

Expected: PASS。

- [ ] **Step 5: 提交 Anthropic 适配器**

```powershell
git add provider_proxy_protocol.py claude_local_proxy.py tests/test_claude_local_proxy.py
git commit -m "feat: proxy Anthropic Messages requests"
```

### Task 4: 增加 Claude FastAPI 控制服务

**Files:**
- Modify: `claude_local_proxy.py`
- Modify: `tests/test_claude_local_proxy.py`

- [ ] **Step 1: 写 Claude 控制 API 测试**

```python
status = await client.get("/control/api/status")
self.assertEqual(status.json()["service"], "claude-local-proxy")
self.assertEqual(status.json()["current_provider_id"], "anthropic-a")
self.assertFalse(status.json()["providers"][1]["compatible"])

response = await client.post(
    "/control/api/providers/anthropic-b/select",
    headers={"X-Local-Proxy-Control": "1"},
)
self.assertEqual(response.status_code, 200)
```

同时验证不兼容供应商返回 `409`，`/v1/messages` 可转发，其他 `/v1/*` 路径返回 `404`。

- [ ] **Step 2: 运行测试并确认 Claude app 尚未实现**

Run: `python -m unittest tests.test_claude_local_proxy.ClaudeProxyAppTests -v`

Expected: FAIL。

- [ ] **Step 3: 实现 Claude FastAPI app**

复用共享 `ProviderRouter`、`RetryPolicyStore`、`UsageStore` 和 `RecoveryHistoryStore`，但提供独立 `create_claude_proxy_app`：

```python
def create_claude_proxy_app(router: ProviderRouter, **kwargs: Any) -> FastAPI:
    return create_proxy_app(
        router,
        protocol_adapter=CLAUDE_PROTOCOL,
        service_name="claude-local-proxy",
        control_asset_dir=CLAUDE_CONTROL_ASSET_DIR,
        allowed_proxy_paths=frozenset({"messages"}),
        **kwargs,
    )
```

控制状态增加 `compatible`、`api_format` 和默认模型，不向浏览器返回 credential value。

- [ ] **Step 4: 运行 Claude app 测试**

Run: `python -m unittest tests.test_claude_local_proxy.ClaudeProxyAppTests -v`

Expected: PASS。

- [ ] **Step 5: 提交 Claude Web 服务**

```powershell
git add claude_local_proxy.py codex_local_proxy.py tests/test_claude_local_proxy.py
git commit -m "feat: add Claude local proxy web service"
```

### Task 5: 增加 Claude 设置、配置片段和运行装配

**Files:**
- Create: `claude_local_proxy_app.py`
- Create: `tests/test_claude_local_proxy_app.py`

- [ ] **Step 1: 写 Claude 设置与配置测试**

```python
self.assertEqual(default_settings()["port"], 17891)
self.assertEqual(data_directory().name, ".claude-local-proxy")
snippets = claude_config_snippets(17891)
self.assertIn('ANTHROPIC_BASE_URL = "http://127.0.0.1:17891"', snippets["powershell"])
self.assertIn('export ANTHROPIC_API_KEY="local-claude-proxy"', snippets["bash"])
self.assertNotIn("real-key", str(snippets))
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python -m unittest tests.test_claude_local_proxy_app -v`

Expected: FAIL with missing module/functions。

- [ ] **Step 3: 实现 Claude 设置和服务工厂**

`claude_local_proxy_app.py` 复用 Codex 设置校验函数，但固定独立目录和默认端口：

```python
DEFAULT_CLAUDE_PORT = 17891
APP_DATA_DIRECTORY_NAME = ".claude-local-proxy"


def claude_config_snippets(port: int = DEFAULT_CLAUDE_PORT) -> dict[str, str]:
    base_url = f"http://127.0.0.1:{port}"
    return {
        "powershell": f'$env:ANTHROPIC_BASE_URL = "{base_url}"\n'
                      '$env:ANTHROPIC_API_KEY = "local-claude-proxy"\n',
        "bash": f'export ANTHROPIC_BASE_URL="{base_url}"\n'
                'export ANTHROPIC_API_KEY="local-claude-proxy"\n',
    }
```

提供 `build_claude_server()` 返回已装配但尚未启动的 `LocalProxyServer`，由统一桌面入口控制生命周期。

- [ ] **Step 4: 运行设置测试**

Run: `python -m unittest tests.test_claude_local_proxy_app -v`

Expected: PASS。

- [ ] **Step 5: 提交 Claude 运行装配**

```powershell
git add claude_local_proxy_app.py tests/test_claude_local_proxy_app.py
git commit -m "feat: add Claude proxy runtime settings"
```

### Task 6: 创建 Claude Code 独立控制台

**Files:**
- Create: `claude_proxy_static/index.html`
- Create: `claude_proxy_static/app.js`
- Create: `claude_proxy_static/styles.css`
- Modify: `tests/test_claude_local_proxy.py`
- Modify: `tests/theme_runtime.test.js`

- [ ] **Step 1: 写静态资源和运行时测试**

断言 Claude 页面包含独立标题、端口、跨页链接、Anthropic 协议标签和 Claude 配置复制按钮；脚本访问 `/control/api/claude-config`，不访问 `/control/api/codex-config`。

```python
self.assertIn("Claude Code 本地中转", page.text)
self.assertIn("127.0.0.1:17891", page.text)
self.assertIn("http://127.0.0.1:17890/control/", page.text)
self.assertIn("/control/api/claude-config", script.text)
self.assertNotIn("/control/api/codex-config", script.text)
```

- [ ] **Step 2: 运行静态资源测试并确认失败**

Run: `python -m unittest tests.test_claude_local_proxy.ClaudeControlAssetTests -v`

Expected: FAIL，Claude assets 不存在。

- [ ] **Step 3: 复制现有交互并替换 Claude 文案与接口**

保留现有布局、主题、列表管理、重试设置和响应式样式。修改：品牌标识为 `CC`、标题为 Claude Code、本地地址为 `17891`、协议为 `Messages · SSE`、配置按钮提供 PowerShell/Bash 选择，并在标题栏加入 Codex 控制台链接。

- [ ] **Step 4: 给 Codex 页面增加 Claude 跳转并验证前端**

Run:

```powershell
node --check local_proxy_static/app.js
node --check claude_proxy_static/app.js
node --test tests/theme_runtime.test.js
python -m unittest tests.test_claude_local_proxy.ClaudeControlAssetTests -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交 Claude 控制台**

```powershell
git add claude_proxy_static local_proxy_static tests/test_claude_local_proxy.py tests/theme_runtime.test.js
git commit -m "feat: add Claude proxy control console"
```

### Task 7: 统一启动双服务、双网页和单托盘

**Files:**
- Modify: `codex_local_proxy_app.py`
- Modify: `claude_local_proxy_app.py`
- Modify: `tests/test_codex_local_proxy_app.py`

- [ ] **Step 1: 写双服务生命周期失败测试**

```python
codex_server = mock.Mock(running=True)
claude_server = mock.Mock(running=True)
with mock.patch.object(webbrowser, "open") as browser:
    run_hub_servers(codex_server, claude_server, open_browser=True, tray=False)
browser.assert_any_call("http://127.0.0.1:17890/control/")
browser.assert_any_call("http://127.0.0.1:17891/control/")
codex_server.start.assert_called_once_with()
claude_server.start.assert_called_once_with()
```

托盘测试断言菜单顺序为“打开 Codex 控制台”“打开 Claude Code 控制台”“重启本地中转”“退出本地中转”。

- [ ] **Step 2: 运行桌面入口测试并确认失败**

Run: `python -m unittest tests.test_codex_local_proxy_app -v`

Expected: FAIL，缺少双服务入口或托盘菜单。

- [ ] **Step 3: 修改统一入口**

`codex_local_proxy_app.py` 保持 PyInstaller 入口和程序名称，内部构造两台 `LocalProxyServer`。启动成功后依次打开两个控制台。第二台启动失败时停止第一台并返回错误。重复启动检测两个健康地址，发现旧实例时打开两个已有页面。

```python
servers = (codex_server, claude_server)
try:
    for server in servers:
        server.start()
    if open_browser:
        webbrowser.open(codex_control_url)
        webbrowser.open(claude_control_url)
finally:
    for server in reversed(servers):
        server.stop()
```

- [ ] **Step 4: 运行桌面入口和代理回归测试**

Run: `python -m unittest tests.test_codex_local_proxy_app tests.test_claude_local_proxy_app tests.test_codex_local_proxy tests.test_claude_local_proxy -v`

Expected: PASS。

- [ ] **Step 5: 提交统一启动器**

```powershell
git add codex_local_proxy_app.py claude_local_proxy_app.py tests/test_codex_local_proxy_app.py
git commit -m "feat: launch Codex and Claude proxy consoles together"
```

### Task 8: 打包、文档和最终验证

**Files:**
- Modify: `packaging/CodexLocalProxy.spec`
- Modify: `scripts/build_local_proxy_exe.ps1`
- Modify: `README.md`
- Modify: `docs/codex-local-proxy.md`
- Modify: `tests/test_windows_release.py`

- [ ] **Step 1: 写打包资源测试**

```python
self.assertIn('ROOT / "local_proxy_static"', spec)
self.assertIn('ROOT / "claude_proxy_static"', spec)
self.assertIn('name="CodexLocalProxy-win-x64"', spec)
```

并断言 README 同时记录 `17890`、`17891`、两个网页地址和一个安装包。

- [ ] **Step 2: 运行发布测试并确认 Claude 资源断言失败**

Run: `python -m unittest tests.test_windows_release -v`

Expected: FAIL，spec 尚未包含 `claude_proxy_static`。

- [ ] **Step 3: 更新 PyInstaller 与中文文档**

在 spec 的 `data_files` 中加入：

```python
(str(ROOT / "claude_proxy_static"), "claude_proxy_static"),
```

保持 EXE 名称 `CodexLocalProxy-win-x64`。README 和详细文档说明安装后同时打开两个页面、Claude Code 环境变量配置、支持的重试错误和 `openai_chat` 限制。

- [ ] **Step 4: 运行完整验证**

Run:

```powershell
python -m unittest discover -s tests
node --check local_proxy_static/app.js
node --check claude_proxy_static/app.js
node --check provider_status/static/app.js
node --test tests/theme_runtime.test.js
git diff --check
```

Expected: 所有命令退出码为 `0`。

- [ ] **Step 5: 本地双端口冒烟测试**

使用临时测试数据库启动统一程序的无浏览器模式，验证：

```text
GET http://127.0.0.1:17890/healthz -> service=codex-local-proxy
GET http://127.0.0.1:17891/healthz -> service=claude-local-proxy
GET 两个 /control/ -> 200
```

停止程序后确认两个端口都不再监听。

- [ ] **Step 6: 提交发布和文档改动**

```powershell
git add packaging/CodexLocalProxy.spec scripts/build_local_proxy_exe.ps1 README.md docs/codex-local-proxy.md tests/test_windows_release.py
git commit -m "build: package Codex and Claude proxy consoles together"
```
