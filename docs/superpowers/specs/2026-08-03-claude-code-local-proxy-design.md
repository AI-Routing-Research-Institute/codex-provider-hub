# Claude Code Local Proxy Design

## Goal

Add a standalone Claude Code local proxy beside the existing Codex proxy. The
Claude service will listen on `127.0.0.1:17891`, read Claude providers from the
local CC Switch database, support provider selection, retries, circuit
breaking, recovery history, token aggregation, and a dedicated control page.
The existing Codex service remains available on `127.0.0.1:17890` with no
behavioral change.

## Scope and Non-Goals

In scope:

- CC Switch rows with `app_type = "claude"`.
- Anthropic Messages API requests at `/v1/messages`.
- JSON and `text/event-stream` responses.
- Provider switching before a response has produced visible output.
- Retry, circuit breaking, sanitized recovery history, usage aggregation, and
  local control APIs.
- PowerShell and Bash environment snippets for Claude Code.

Out of scope for the first version:

- Claude Desktop providers.
- Providers marked `apiFormat = "openai_chat"`; they are shown as incompatible
  and are not selected for forwarding.
- Translating OpenAI Chat Completions to Anthropic Messages.
- Writing provider changes back to CC Switch.
- Public/non-loopback listening.

## Architecture

The proxy logic is split into a protocol-neutral core and protocol adapters.
The core owns provider routing, request lifecycle, retries, circuit state,
recovery records, and usage persistence. A protocol adapter owns upstream URL
construction, credential headers, retry classification, SSE preflight rules,
and usage extraction.

Planned modules:

- `provider_proxy_core.py`: shared provider model, router, retry policy,
  circuit breaker, recovery store, usage store, and generic forwarding loop.
- `provider_proxy_codex.py`: Responses API adapter used by the current Codex
  service after a behavior-preserving migration.
- `provider_proxy_claude.py`: Anthropic Messages adapter.
- `claude_local_proxy.py`: Claude-specific CC Switch loader, FastAPI app,
  control endpoints, and Claude static asset wiring.
- `claude_local_proxy_app.py`: Windows/background entry point, settings under
  `~/.claude-local-proxy/`, and default port `17891`.
- `claude_proxy_static/`: dedicated control page assets; visual structure may
  reuse the existing page but all labels and generated configuration target
  Claude Code.

The migration of Codex to the shared core must preserve its current public
routes, settings format, retry behavior, and test expectations. The two entry
points use separate settings, usage databases, recovery history, and selected
provider state.

## Provider Loading

The Claude loader opens `~/.cc-switch/cc-switch.db` through SQLite read-only
mode and queries `providers` with `app_type = 'claude'`, joining
`provider_endpoints` on the same app type. It parses `settings_config` as JSON
with this shape:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://...",
    "ANTHROPIC_AUTH_TOKEN": "...",
    "ANTHROPIC_MODEL": "..."
  },
  "model": "opus"
}
```

The effective upstream URL is `env.ANTHROPIC_BASE_URL`, falling back to the
joined endpoint URL. The URL is normalized like the Codex loader and cannot
contain credentials, query parameters, or fragments.

Credential selection is deterministic:

1. `meta.apiKeyField` when it is `ANTHROPIC_API_KEY` or
   `ANTHROPIC_AUTH_TOKEN`.
2. `ANTHROPIC_API_KEY` if present.
3. `ANTHROPIC_AUTH_TOKEN` if present.

The credential value is kept only in memory and is never returned by control
APIs or written to logs. The selected credential field controls injection:
`ANTHROPIC_API_KEY` becomes `x-api-key`; `ANTHROPIC_AUTH_TOKEN` becomes
`Authorization: Bearer ...`.

`meta.apiFormat` defaults to `anthropic` when absent. Rows with
`apiFormat = "openai_chat"` remain visible with an incompatible marker but are
excluded from the selectable forwarding set. Rows without an endpoint or
credential are visible as unavailable and produce a clear setup error.

If `meta.commonConfigEnabled` is true, `settings.common_config_claude` is
parsed and merged into the provider environment, with provider values taking
precedence. Only environment fields are used by the proxy; Claude Code
permissions and local settings are not copied into the proxy.

## Request and Response Flow

Claude Code is configured with the local base URL and a placeholder local key.
It sends `POST /v1/messages` to the Claude proxy. For each request:

1. The router snapshots the selected compatible provider and increments its
   active request count.
2. The proxy reads the body up to the existing 64 MiB limit and extracts the
   requested model for usage aggregation.
3. Client authentication headers (`x-api-key`, `authorization`, and related
   hop-by-hop headers) are removed. Provider-specific authentication and
   configured static headers are applied.
4. The request is sent to `<provider base url>/v1/messages`, preserving query
   parameters and the Anthropic protocol headers.
5. JSON responses are passed through unchanged. SSE responses are streamed
   without converting event names or payloads.
6. On completion, usage is persisted without request/response content and the
   router finalizes the request.

The proxy does not rewrite the requested model. Provider default model
environment values are displayed in the control page and used for health
checks, while Claude Code's request model remains authoritative.

## Retry and Switching Semantics

Retryable pre-output failures are:

- connection and stream-start transport errors;
- HTTP `408`, `429`, `500`, `502`, `503`, `504`, and Anthropic `529`;
- HTML gateway `404` responses;
- Anthropic SSE error events whose type/code is `overloaded_error`,
  `api_error`, `rate_limit_error`, or an equivalent transient server error.

The adapter buffers SSE events only until it can decide whether the response has
committed visible output. A `content_block_delta` containing text, thinking,
tool input, or other client-visible content commits the response. Before that
point, a transient failure closes the upstream response and retries according
to the shared policy. `message_start` alone does not commit output. Once output
is committed, later stream failures are recorded as `after_output` and passed
through; the request is never replayed to avoid duplicate text or tool calls.

On each retry, the current provider is re-read. A manual provider selection
therefore takes effect for the next attempt of a waiting request. Existing
retry limits, exponential/fixed backoff, `Retry-After`, circuit thresholds, and
client-disconnect handling remain shared behavior. A provider's consecutive
transient failure count opens its circuit and returns a bounded `Retry-After`.

## Usage Accounting

The Claude adapter extracts upstream usage from:

- `message_start.message.usage.input_tokens`;
- `message_start.message.usage.cache_creation_input_tokens`;
- `message_start.message.usage.cache_read_input_tokens`;
- `message_delta.usage.output_tokens`.

If a successful response has no usage, the core estimates input/output tokens
from text and structured content using the existing tokenizer fallback. The
stored row contains provider ID, model, token counts, source, and status code;
it never contains prompts, completions, headers, or keys.

## Control Surface and Configuration

The Claude app exposes the same control operations as the Codex app under its
own origin and port: status, provider selection, visibility, ordering, refresh,
retry policy, recovery history, runtime settings, and shutdown. Status payloads
identify the service as `claude-local-proxy` and mark each provider's protocol
compatibility.

The configuration endpoint returns two snippets:

PowerShell:

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:17891"
$env:ANTHROPIC_API_KEY = "local-claude-proxy"
```

Bash:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:17891"
export ANTHROPIC_API_KEY="local-claude-proxy"
```

The placeholder key is intentionally not used upstream; the proxy replaces it
with the selected provider credential.

## Error Handling and Security

- Only loopback hosts are accepted by the HTTP server.
- The CC Switch database remains read-only.
- Provider keys are excluded from status, diagnostics, retry summaries, and
  exception text through the existing sanitization rules extended for
  Anthropic headers.
- Invalid provider rows are skipped with an aggregate startup error rather
  than crashing midway through a refresh.
- Unsupported `openai_chat` Claude rows cannot become the active route.
- Missing credential, missing endpoint, self-referential local endpoint, and
  circuit-open states return structured Anthropic-compatible error JSON.

## Testing and Acceptance

Unit tests will cover:

- loading real-shape Claude rows with `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` without exposing values;
- endpoint fallback, common-config merge, duplicate IDs, invalid URLs, and
  incompatible `apiFormat` handling;
- non-streaming and streaming `/v1/messages` forwarding with exact headers;
- retry classification for HTTP and embedded SSE errors, including 529;
- pre-output retry, post-output no-replay, provider switching between attempts,
  circuit opening, and client disconnect;
- Claude usage extraction and fallback estimation;
- control status, selection, refresh, and PowerShell/Bash snippets;
- independent ports and data directories for Codex and Claude apps.

The existing full Python and JavaScript test suites must pass unchanged. A
manual smoke check will start the Claude app against the local database, verify
`/healthz` and the control page, and issue a mocked `/v1/messages` request
without contacting a real provider.

## Rollout Order

1. Extract protocol-neutral core with characterization tests for current Codex
   behavior.
2. Migrate Codex entry point and run the complete existing suite.
3. Add Claude provider loader and Anthropic adapter tests.
4. Add Claude app, settings, static control page, and configuration snippets.
5. Run full tests and local smoke checks, then document startup and Claude Code
   environment setup.
