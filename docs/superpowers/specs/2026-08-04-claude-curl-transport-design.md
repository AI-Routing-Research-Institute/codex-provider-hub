# Claude curl_cffi Transport Design

## Goal

Make Claude Code provider switching as transparent as Codex switching, including providers such as AgentRouter that route Python httpx requests to an HTML page instead of the Anthropic API.

## Architecture

Codex keeps the existing httpx upstream client. Claude uses a dedicated async `curl_cffi` client that implements the small client/response surface consumed by `create_proxy_app`: build a request, open a streaming response, iterate raw body chunks, close a response, and close the client.

The shared router, retries, circuit breaker, provider switching, SSE preflight, usage capture, and recovery history remain unchanged. Tests may continue injecting `httpx.AsyncClient` with `MockTransport`; only the production Claude app selects the curl transport factory.

## Request And Response Rules

- Preserve method, URL, query parameters, body, Anthropic beta headers, API-key/Bearer replacement, and provider custom headers.
- Ask the upstream for identity encoding so decoded curl chunks are not forwarded with a stale compression header.
- Do not follow redirects.
- Translate curl connection and streaming failures into httpx transport errors so the existing retry path handles them.
- Closing a downstream request sets curl's quit event and waits for the upstream task to finish.
- Treat successful HTML responses from a Claude Messages provider as malformed and retry before sending output.

## Packaging

Add `curl_cffi` to Windows dependencies. The PyInstaller smoke test must import the package and instantiate/close the Claude transport, proving that its native `_wrapper.pyd` is included.

## Verification

- Unit tests for query forwarding, headers/body, streaming chunks, close/cancellation, and error translation.
- Existing Claude and Codex regression suites.
- Packaged EXE smoke test.
- A second instance on temporary ports, followed by a real Claude Code request through the temporary Claude port to the selected AgentRouter provider.
