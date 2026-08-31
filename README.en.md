# Codex Provider Hub

[简体中文](README.md) | [English](README.en.md)

<div align="center">

Manage multiple API providers for Codex and Claude Code in one local application, with browser-based switching, pre-output retries, request history, token usage, and remote health monitoring.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-4b5563?style=flat-square)
[![License](https://img.shields.io/badge/License-AGPL--3.0--or--later-663399?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/github/v/release/AI-Routing-Research-Institute/codex-provider-hub?style=flat-square)](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest)

</div>

## Contents

- [Who this is for](#who-this-is-for)
- [Five-minute quick start](#five-minute-quick-start)
- [Recommended with CC Switch](#recommended-with-cc-switch)
- [Configure Codex](#configure-codex)
- [Configure Claude Code](#configure-claude-code)
- [Manage and switch providers](#manage-and-switch-providers)
- [Request transport](#request-transport)
- [Retry, usage, and monitoring](#retry-usage-and-monitoring)
- [Troubleshooting](#troubleshooting)
- [Other installation methods](#other-installation-methods)
- [Development and deployment](#development-and-deployment)
- [Security boundaries](#security-boundaries)
- [Open-source and commercial licensing](#open-source-and-commercial-licensing)

## Who this is for

Codex Provider Hub is designed for users who:

- Use multiple Codex API or Claude Code API relays and want to switch between them immediately in a browser.
- Already manage providers with CC Switch and want Codex and Claude Code to share one local application.
- Need pre-output retries, request history, token usage, provider monitoring, and diagnostics.
- Use providers that reject ordinary Python HTTP clients and need per-provider `curl_cffi` compatibility transport.

It is not an API provider, does not supply API keys, and is not a general-purpose gateway for public multi-tenant deployment. You must provide your own legitimate provider endpoints and credentials.

The application listens only on `127.0.0.1:17890` by default. One process serves two independent control panels:

```text
Codex       http://127.0.0.1:17890/control/codex/
Claude Code http://127.0.0.1:17890/control/claude/
```

## Five-minute quick start

### 1. Prepare CC Switch

Install and configure CC Switch first. Add at least one Codex or Claude Code provider. Its default database location is:

```text
~/.cc-switch/cc-switch.db
```

The unified application builds both the Codex and Claude Code control panels at startup, so make sure this database exists and is readable before launching it.

### 2. Download the application

Windows x64:

- [Download CodexLocalProxy-win-x64.exe](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-win-x64.exe)
- [Download the SHA-256 checksum](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-win-x64.exe.sha256)

macOS Apple Silicon:

- [Download CodexLocalProxy-macos-arm64.zip](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-macos-arm64.zip)
- [Download the SHA-256 checksum](https://github.com/AI-Routing-Research-Institute/codex-provider-hub/releases/latest/download/CodexLocalProxy-macos-arm64.zip.sha256)

### 3. Start the application

On Windows, double-click the EXE. It starts silently and remains in the notification area without opening a browser. Right-click the tray icon to open either control panel, configure startup at sign-in, or exit.

On macOS, extract the `.app`. The current package is not Apple-signed or notarized, so right-click the application and choose **Open** the first time. You can also remove the quarantine attribute:

```bash
xattr -dr com.apple.quarantine /path/to/CodexLocalProxy-macos-arm64.app
```

### 4. Check providers

Open the Codex control panel and confirm that providers are listed. On first launch, Codex providers are imported from CC Switch into a separate local catalog.

Then open the Claude Code control panel. Claude Code providers continue to come directly from the current CC Switch data source. Records with an incompatible protocol or missing credentials are shown but cannot be selected.

### 5. Configure clients

Use **Import to CCS** in both control panels, then follow the next two sections to register the local proxy as Codex and Claude Code providers. Future provider switches do not require client configuration changes.

## Recommended with CC Switch

CC Switch centrally maintains providers. Codex Provider Hub handles local forwarding, switching, retries, and statistics. Their responsibilities are:

- **Codex:** when the local catalog is empty, providers are imported from CC Switch. Codex Provider Hub manages the imported copy independently, and browser edits do not write back to CC Switch.
- **Claude Code:** providers continue to be read from CC Switch. Edit Claude providers in CC Switch, then refresh the Claude Code control panel.
- **Import Codex again:** open **Providers**, select **Manage**, choose **Add new only** or **Overwrite existing**, then select **Import CCS**.
- **Add new only:** retain existing local providers and import only new CC Switch records.
- **Overwrite existing:** replace local records that have the same ID. This also replaces any independent local edits to those records.

You can add a small number of Codex providers directly in management mode. CC Switch remains the recommended way to maintain larger Codex and Claude Code provider catalogs.

## Configure Codex

### Import through CC Switch

1. Open `http://127.0.0.1:17890/control/codex/`.
2. Select **Import to CCS** at the bottom of the page.
3. In CC Switch, confirm the imported Codex provider named **Codex 本地中转** with `gpt-5.6-sol` as its default model.
4. Switch to that provider in CC Switch. CC Switch updates the Codex configuration.

The imported provider is equivalent to this core configuration:

```toml
model_provider = "local_cc_switch"

[model_providers.local_cc_switch]
name = "CC Switch Local Proxy"
base_url = "http://127.0.0.1:17890/v1"
wire_api = "responses"
requires_openai_auth = true
```

Codex now always connects to the local address. A provider selected in the browser is used immediately for new requests, without another `config.toml` change.

**Import to CCS** requires the CC Switch custom-protocol handler. If the browser reports that CC Switch is unavailable, install or re-register CC Switch first. Providers that point back to the current local proxy port are automatically excluded from this tool's upstream catalog.

### Temporarily bypass the local proxy

**Copy temporary launch command** on a Codex provider row produces a one-time command that starts the Codex CLI with that provider directly, without changing `config.toml`. The command contains provider credentials. Treat it as a secret and do not place it in public scripts, issues, or shared terminal history.

## Configure Claude Code

### Import through CC Switch

1. Open `http://127.0.0.1:17890/control/claude/`.
2. Select **Import to CCS** at the bottom of the page.
3. In CC Switch, confirm the imported Claude Code provider with `http://127.0.0.1:17890` as its endpoint.
4. Switch to that provider in CC Switch. CC Switch updates the Claude Code configuration.

Equivalent Windows PowerShell configuration:

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:17890"
$env:ANTHROPIC_API_KEY = "local-claude-proxy"
claude
```

Equivalent macOS/Linux configuration:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:17890"
export ANTHROPIC_API_KEY="local-claude-proxy"
claude
```

You can also merge the values into `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:17890",
    "ANTHROPIC_API_KEY": "local-claude-proxy"
  }
}
```

Notes:

- Do not append `/v1` to `ANTHROPIC_BASE_URL`. The application forwards Claude Code's `/v1/messages` path.
- `local-claude-proxy` is a non-empty local placeholder, not the real upstream key.
- Do not configure `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` as an empty string, or Claude Code may immediately ask you to log in.
- The local proxy removes client authentication headers and applies credentials from the Claude provider currently selected in the browser.

## Manage and switch providers

### Everyday switching

Select a provider in the provider list. A switch affects only subsequent requests. A stream that has already produced output is not interrupted or replayed.

### Manage Codex providers

Select **Manage** in the Codex control panel to:

- Drag providers to reorder them.
- Hide providers that are temporarily unused.
- Add a local provider.
- Edit the name, Base URL, API key, request headers, query parameters, and request transport.
- Delete any provider that is not currently selected.
- Import only new providers from CC Switch or overwrite existing ones.

Saving provider edits refreshes routing immediately. Normal status APIs and pages never return complete stored keys.

### Manage Claude Code providers

Claude Code providers come from CC Switch. Maintain their endpoint, authentication, and protocol in CC Switch, then refresh the Claude Code control panel. Only providers compatible with the Anthropic Messages protocol and containing credentials can be selected.

## Request transport

This setting is under **Providers > Manage > Edit > Request transport** in the Codex control panel.

- **Standard (`httpx`):** the default for normal providers. It preserves the original network behavior.
- **Compatibility (`curl_cffi`):** for a provider where the same endpoint and key work directly in Codex but consistently return a Cloudflare HTML 403 through the local proxy.

Enable compatibility transport only for providers that actually block the default client fingerprint. `curl_cffi` does not fix genuine authentication failures, exhausted quotas, or unavailable upstream model channels.

Claude Code upstream requests always use `curl_cffi`, so the Claude control panel does not expose this option.

## Retry, usage, and monitoring

### Automatic retries

- Connection failures, stream failures before the first output, and HTTP `429/500/502/503/504` responses can be retried automatically.
- Claude Code also retries `408` and `529`.
- Fixed delay, increasing delay, maximum delay, unlimited retries, and circuit breaking are supported.
- The default behavior does not rotate providers automatically. If you manually switch the current provider while a retry is waiting, the next attempt that has not emitted output follows the newly selected provider.
- Once output has reached the client, the request is not replayed through another provider. This avoids duplicate answers, tool calls, and charges.

### Request and token usage

- The Requests page shows active requests and the most recent 24 hours.
- Token counts use upstream `usage` when available and fall back to a `tiktoken` estimate.
- Today, last 24 hours, last 7 days, last 30 days, and custom time ranges are supported.
- The local database does not store request or response bodies.

### Remote monitoring

Codex providers can be uploaded through a restricted SSH importer to an independent status service. **Monitor management** lists all monitored server configurations and supports reordering, immediate probes, and deletion. Server deployment templates are under `deploy/`, and the public example is `config/providers.example.toml`.

Monitoring indicates endpoint availability only. It does not guarantee that the local catalog uses the same key or that a requested model is available for a local request.

## Troubleshooting

### Claude Code says `Not logged in · Please run /login`

1. Start Claude Code from the same terminal where the local environment variables were configured.
2. Confirm that `ANTHROPIC_BASE_URL` is `http://127.0.0.1:17890` without `/v1`.
3. Confirm that `ANTHROPIC_API_KEY` is a non-empty placeholder and remove any empty `ANTHROPIC_AUTH_TOKEN`.
4. Open the Claude Code control panel and confirm that the current provider can be selected and has credentials.

### Cloudflare HTML 403

If the error body contains `@font-face`, `cf-fonts`, or a Cloudflare page while the same key works directly in Codex, enable `curl_cffi` compatibility transport for that Codex provider only.

### HTTP 401

A 401 means the upstream rejected the authentication attached to the current request. Check the provider selected in the browser, the key stored in the local catalog, custom `Authorization` or `X-API-Key` headers, and the Base URL. A successful server-side monitor does not prove that the current local record uses the same credentials.

### HTTP 503 or "no available channel for model"

This is an upstream provider state. Switching between `httpx` and `curl_cffi` cannot fix a missing model, an empty provider group, exhausted quota, or provider maintenance. Manually switch to a provider that supports the model; the existing retry behavior will handle attempts that have not produced output.

### The control panel does not open

1. Check whether the application is still running in the system tray or menu bar.
2. Open `http://127.0.0.1:17890/healthz`.
3. Check whether another process is using port `17890`.
4. Do not troubleshoot by terminating every process with the same name, because that can interrupt sessions currently using the local proxy.

### Changes do not take effect

- Confirm that you are running the latest Release, not an old temporary test build.
- Select **Save** after editing a provider, and refresh the page if necessary.
- Codex and Claude providers have independent current selections. Make sure you switch in the correct control panel.
- After changing the port, exit and restart the application, then copy both client configurations again.
- Restart Codex once after its initial configuration. Later provider switches do not require a restart.

## Other installation methods

### Intel macOS

The current Release provides an Apple Silicon package only. On an Intel Mac, follow **Run from source** below.

### Windows desktop shortcut

After checking out the repository, install a shortcut for the current user:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_local_proxy_shortcut.ps1
```

### Run from source

Python 3.11 or newer is required. With `uv`:

```powershell
uv venv --clear .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-status.txt
uv run --python .venv\Scripts\python.exe local_proxy_app.py
```

macOS/Linux:

```bash
uv venv --clear .venv
uv pip install --python .venv/bin/python -r requirements-status.txt
uv run --python .venv/bin/python local_proxy_app.py
```

With a standard Python virtual environment on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-status.txt
.\.venv\Scripts\python.exe local_proxy_app.py
```

Append `--open-browser` to open both control panels at startup.

## Development and deployment

### Provider probe tool

```powershell
.\.venv\Scripts\python.exe probe_codex_cc_switch.py --list-providers
.\.venv\Scripts\python.exe probe_codex_cc_switch.py --current-only --json
```

### Status service

Copy the example configuration:

```bash
cp config/providers.example.toml config/providers.toml
```

Run the worker once:

```bash
./.venv/bin/python -m provider_status.worker \
  --config config/providers.toml \
  --control-database var/control/manual-probes.sqlite3 \
  --once
```

Start the status page:

```bash
./.venv/bin/python -m provider_status.web \
  --database var/public/status.sqlite3 \
  --control-database var/control/manual-probes.sqlite3 \
  --host 127.0.0.1 \
  --port 8000
```

Production systemd and Nginx templates are in `deploy/`. Example domains and providers are placeholders and are not ready for production use.

### Project structure

```text
.
├── local_proxy_app.py         Unified application entry point
├── local_proxy/               Local proxy, protocols, provider catalogs, lifecycle
├── proxy_static/              Classic and Vue Codex/Claude control panels
├── probe_tools/               Provider probe tools
├── provider_status/           Health worker, storage, and status page
├── config/                    Public example configuration
├── deploy/                    systemd and Nginx templates
├── scripts/                   Build, shortcut, and repository policy scripts
├── tests/                     Python, PowerShell, and JavaScript tests
└── docs/                      Designs, change records, and detailed documentation
```

### Tests

```powershell
npm ci --prefix proxy_static
npm run build --prefix proxy_static
python -m unittest discover -s tests
node --check proxy_static/classic/app.js
node --check provider_status/static/app.js
Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName }
```

## Security boundaries

- The local proxy can listen only on loopback addresses.
- The CC Switch SQLite data source is opened read-only. Imported Codex providers are stored separately at `~/.codex-local-proxy/codex-providers.sqlite3`.
- Normal status, statistics, and provider editing APIs never return complete upstream keys.
- **Copy temporary launch command** writes the current Codex provider credentials to the clipboard. Treat the result as a secret.
- Client authentication headers are removed before forwarding, then credentials for the current provider are applied.
- Token statistics and request history do not store request or response bodies.
- Private configuration, databases, logs, probe reports, virtual environments, certificates, and keys are excluded from version control by default.
- `config/providers.example.toml` contains example domains only, with no real providers or credentials.

## Open-source and commercial licensing

Codex Provider Hub is licensed under the [GNU Affero General Public License v3.0 or later](LICENSE), with SPDX identifier `AGPL-3.0-or-later`.

- Individuals and organizations may use, copy, modify, and distribute this project, including in commercial environments.
- Distribution or provision of a modified network service must satisfy the corresponding AGPL source, license, and copyright obligations.
- Derivative development and redistribution must retain the copyright notice and original project attribution in [NOTICE](NOTICE).
- A [proprietary commercial license](COMMERCIAL-LICENSE.md) is required if you want to distribute a closed-source version, provide a modified closed-source network service, or integrate the code into a proprietary product that cannot comply with the AGPL.
- Commercial use that fully complies with the AGPL does not require purchasing a commercial license.

The commercial licensing document is not itself a commercial license agreement. Follow its contact process and execute a separate agreement with the maintainers.
