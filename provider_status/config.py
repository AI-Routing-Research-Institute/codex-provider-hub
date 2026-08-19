from __future__ import annotations

import ipaddress
import json
import math
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


Resolver = Callable[..., Iterable[Any]]
PROBE_MODE_AUTOMATIC = "automatic"
PROBE_MODE_MANUAL_ONLY = "manual_only"
_PROBE_MODES = frozenset({PROBE_MODE_AUTOMATIC, PROBE_MODE_MANUAL_ONLY})
PROBE_CLIENT_CODEX = "codex"
PROBE_CLIENT_CLAUDE = "claude"
_PROBE_CLIENTS = frozenset({PROBE_CLIENT_CODEX, PROBE_CLIENT_CLAUDE})


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    name: str
    base_url: str
    credential_name: str
    models: tuple[str, ...]
    healthy_interval_seconds: float
    unhealthy_interval_seconds: float
    timeout_seconds: float
    healthy_interval_max_seconds: float | None = None
    unhealthy_interval_max_seconds: float | None = None
    display_models: tuple[str, ...] | None = None
    probe_mode: str = PROBE_MODE_AUTOMATIC
    model_clients: tuple[tuple[str, str], ...] = ()
    claude_base_url: str | None = None
    credential_kind: str = "api_key"

    def probe_client(self, model: str) -> str:
        return dict(self.model_clients).get(model, PROBE_CLIENT_CODEX)


@dataclass(frozen=True)
class ServiceConfig:
    providers: tuple[ProviderConfig, ...]
    database_path: Path
    public_database_path: Path
    temp_root: Path
    codex_bin: Path
    claude_bin: Path | None = None


def read_credential(name: str, env: Mapping[str, str]) -> str:
    windows_name = PureWindowsPath(name) if isinstance(name, str) else None
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or bool(windows_name and (windows_name.drive or windows_name.root))
    ):
        raise ValueError("credential name must be a single file name")

    credentials_directory = env.get("CREDENTIALS_DIRECTORY")
    if not credentials_directory:
        raise ValueError("CREDENTIALS_DIRECTORY is required")

    credential_path = Path(credentials_directory) / name
    try:
        value = credential_path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ValueError(f"unable to read credential {name!r}") from exc

    if not value.strip():
        raise ValueError(f"credential {name!r} is empty")
    return value


def load_config(
    path: str | Path,
    resolver: Resolver | None = None,
) -> ServiceConfig:
    config_path = Path(path)
    with config_path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)
    fragments_directory = config_path.with_name("providers.d")
    service_table = raw_config.get("service")
    if isinstance(raw_config.get("providers"), list):
        fragment_providers = raw_config["providers"]
    elif isinstance(service_table, dict):
        fragment_providers = service_table.setdefault("providers", [])
    else:
        fragment_providers = raw_config.setdefault("providers", [])
    if not isinstance(fragment_providers, list):
        raise ValueError("providers must be a non-empty array of tables")
    if fragments_directory.is_dir():
        for fragment_path in sorted(fragments_directory.glob("*.toml")):
            with fragment_path.open("rb") as fragment_file:
                fragment = tomllib.load(fragment_file)
            values = fragment.get("providers")
            if not isinstance(values, list):
                raise ValueError(f"{fragment_path.name}: providers must be an array")
            fragment_providers.extend(values)

    service = raw_config.get("service", raw_config)
    if not isinstance(service, dict):
        raise ValueError("service must be a table")

    raw_providers = raw_config.get("providers")
    if raw_providers is None and service is not raw_config:
        raw_providers = service.get("providers")
    if not isinstance(raw_providers, list) or not raw_providers:
        raise ValueError("providers must be a non-empty array of tables")

    active_resolver = resolver or socket.getaddrinfo
    providers: list[ProviderConfig] = []
    provider_ids: set[str] = set()
    for index, raw_provider in enumerate(raw_providers):
        if not isinstance(raw_provider, dict):
            raise ValueError(f"providers[{index}] must be a table")

        provider_id = _provider_id(raw_provider, index)
        if provider_id in provider_ids:
            raise ValueError(f"duplicate provider id: {provider_id}")
        provider_ids.add(provider_id)

        base_url = _required_string(raw_provider, "base_url", provider_id)
        validate_public_https_endpoint(
            base_url,
            provider_id,
            active_resolver,
            allow_resolution_failure=True,
        )
        healthy_interval_seconds = _positive_number(
            raw_provider,
            "healthy_interval_seconds",
            provider_id,
        )
        unhealthy_interval_seconds = _positive_number(
            raw_provider,
            "unhealthy_interval_seconds",
            provider_id,
        )
        models = _models(raw_provider, provider_id)
        model_clients = _model_clients(raw_provider, provider_id, models)
        claude_base_url = _optional_string(raw_provider, "claude_base_url")
        if claude_base_url is not None:
            validate_public_https_endpoint(
                claude_base_url,
                provider_id,
                active_resolver,
                field_name="claude_base_url",
                allow_resolution_failure=True,
            )
        if (
            any(client == PROBE_CLIENT_CLAUDE for _, client in model_clients)
            and claude_base_url is None
        ):
            raise ValueError(
                f"provider {provider_id!r} claude_base_url is required for "
                "Claude models"
            )

        providers.append(
            ProviderConfig(
                provider_id=provider_id,
                name=_required_string(raw_provider, "name", provider_id),
                base_url=base_url,
                credential_name=_required_string(
                    raw_provider,
                    "credential_name",
                    provider_id,
                ),
                models=models,
                healthy_interval_seconds=healthy_interval_seconds,
                healthy_interval_max_seconds=_interval_maximum(
                    healthy_interval_seconds,
                    _optional_positive_number(
                        raw_provider,
                        "healthy_interval_max_seconds",
                        provider_id,
                    ),
                    "healthy_interval_max_seconds",
                    provider_id,
                ),
                unhealthy_interval_seconds=unhealthy_interval_seconds,
                unhealthy_interval_max_seconds=_interval_maximum(
                    unhealthy_interval_seconds,
                    _optional_positive_number(
                        raw_provider,
                        "unhealthy_interval_max_seconds",
                        provider_id,
                    ),
                    "unhealthy_interval_max_seconds",
                    provider_id,
                ),
                timeout_seconds=_timeout_seconds(
                    raw_provider,
                    provider_id,
                ),
                display_models=_display_models(
                    raw_provider,
                    provider_id,
                    models,
                ),
                probe_mode=_probe_mode(raw_provider, provider_id),
                model_clients=model_clients,
                claude_base_url=claude_base_url,
                credential_kind=_credential_kind(raw_provider, provider_id),
            )
        )

    database_path = Path(_required_string(service, "database_path", "service"))
    public_database_path = Path(
        _required_string(service, "public_database_path", "service")
    )
    if database_path.resolve() == public_database_path.resolve():
        raise ValueError("private and public database paths must differ")

    claude_bin_value = _optional_string(service, "claude_bin")
    if claude_bin_value is None and any(
        provider.probe_client(model) == PROBE_CLIENT_CLAUDE
        for provider in providers
        for model in provider.models
    ):
        raise ValueError("service claude_bin is required for Claude models")

    order_path = fragments_directory / ".order.json"
    if order_path.is_file():
        try:
            order = json.loads(order_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            order = []
        if isinstance(order, list):
            positions = {value: index for index, value in enumerate(order) if isinstance(value, str)}
            providers.sort(key=lambda item: (positions.get(item.provider_id, len(positions)), item.provider_id))

    return ServiceConfig(
        providers=tuple(providers),
        database_path=database_path,
        public_database_path=public_database_path,
        temp_root=Path(_required_string(service, "temp_root", "service")),
        codex_bin=Path(_required_string(service, "codex_bin", "service")),
        claude_bin=Path(claude_bin_value) if claude_bin_value is not None else None,
    )


def _provider_id(raw_provider: Mapping[str, Any], index: int) -> str:
    configured_id = raw_provider.get("id")
    explicit_provider_id = raw_provider.get("provider_id")
    if configured_id is not None and explicit_provider_id is not None:
        if configured_id != explicit_provider_id:
            raise ValueError(f"providers[{index}] has conflicting id fields")
    value = configured_id if configured_id is not None else explicit_provider_id
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"providers[{index}] id must be a non-empty string")
    return value.strip()


def _required_string(
    values: Mapping[str, Any],
    key: str,
    context: str,
) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} {key} must be a non-empty string")
    return value.strip()


def _credential_kind(raw_provider: Mapping[str, Any], provider_id: str) -> str:
    value = raw_provider.get("credential_kind", "api_key")
    if value not in {"api_key", "auth_token"}:
        raise ValueError(f"provider {provider_id!r} credential_kind is invalid")
    return str(value)


def _optional_string(values: Mapping[str, Any], key: str) -> str | None:
    if key not in values:
        return None
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _models(raw_provider: Mapping[str, Any], provider_id: str) -> tuple[str, ...]:
    raw_models = raw_provider.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ValueError(f"provider {provider_id!r} models must not be empty")

    models: list[str] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise ValueError(f"provider {provider_id!r} model must not be empty")
        model = raw_model.strip()
        if model in seen:
            raise ValueError(f"provider {provider_id!r} has duplicate model: {model}")
        seen.add(model)
        models.append(model)
    return tuple(models)


def _model_clients(
    raw_provider: Mapping[str, Any],
    provider_id: str,
    models: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    raw_clients = raw_provider.get("model_clients")
    if raw_clients is None:
        return ()
    if not isinstance(raw_clients, dict):
        raise ValueError(f"provider {provider_id!r} model_clients must be a table")

    unknown_models = [model for model in raw_clients if model not in models]
    if unknown_models:
        raise ValueError(
            f"provider {provider_id!r} model_clients contains unconfigured models: "
            f"{', '.join(str(model) for model in unknown_models)}"
        )

    clients: list[tuple[str, str]] = []
    for model in models:
        if model not in raw_clients:
            continue
        client = raw_clients[model]
        if not isinstance(client, str) or client not in _PROBE_CLIENTS:
            allowed = ", ".join(sorted(_PROBE_CLIENTS))
            raise ValueError(
                f"provider {provider_id!r} model_clients[{model!r}] must be "
                f"one of: {allowed}"
            )
        clients.append((model, client))
    return tuple(clients)


def _display_models(
    raw_provider: Mapping[str, Any],
    provider_id: str,
    models: tuple[str, ...],
) -> tuple[str, ...]:
    if "display_models" not in raw_provider:
        return models

    raw_display_models = raw_provider.get("display_models")
    if not isinstance(raw_display_models, list) or not raw_display_models:
        raise ValueError(
            f"provider {provider_id!r} display_models must not be empty"
        )

    display_models: list[str] = []
    seen: set[str] = set()
    for raw_model in raw_display_models:
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise ValueError(
                f"provider {provider_id!r} display_models entry must not be empty"
            )
        model = raw_model.strip()
        if model in seen:
            raise ValueError(
                f"provider {provider_id!r} has duplicate display_models entry: "
                f"{model}"
            )
        seen.add(model)
        display_models.append(model)

    missing_models = [model for model in models if model not in seen]
    if missing_models:
        raise ValueError(
            f"provider {provider_id!r} display_models must include configured "
            f"models: {', '.join(missing_models)}"
        )
    return tuple(display_models)


def _probe_mode(raw_provider: Mapping[str, Any], provider_id: str) -> str:
    value = raw_provider.get("probe_mode", PROBE_MODE_AUTOMATIC)
    if not isinstance(value, str) or value not in _PROBE_MODES:
        allowed = ", ".join(sorted(_PROBE_MODES))
        raise ValueError(
            f"provider {provider_id!r} probe_mode must be one of: {allowed}"
        )
    return value


def _positive_number(
    values: Mapping[str, Any],
    key: str,
    context: str,
) -> float:
    value = values.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"provider {context!r} {key} must be positive")
    return float(value)


def _optional_positive_number(
    values: Mapping[str, Any],
    key: str,
    context: str,
) -> float | None:
    if key not in values:
        return None
    return _positive_number(values, key, context)


def _interval_maximum(
    minimum: float,
    maximum: float | None,
    key: str,
    provider_id: str,
) -> float:
    if maximum is None:
        return minimum
    if maximum < minimum:
        raise ValueError(
            f"provider {provider_id!r} {key} must be at least "
            f"{minimum:g} seconds"
        )
    return maximum


def _timeout_seconds(
    values: Mapping[str, Any],
    provider_id: str,
) -> float:
    value = _positive_number(values, "timeout_seconds", provider_id)
    if value > 90:
        raise ValueError(
            f"provider {provider_id!r} timeout_seconds must be at most 90 seconds"
        )
    return value


def validate_public_https_endpoint(
    base_url: str,
    provider_id: str,
    resolver: Resolver,
    *,
    field_name: str = "base_url",
    allow_resolution_failure: bool = False,
) -> None:
    error_prefix = (
        f"provider {provider_id!r} {field_name} must be a public HTTPS endpoint"
    )
    try:
        parsed = urlsplit(base_url)
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError(f"{error_prefix}: invalid URL") from exc

    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in base_url
        or "#" in base_url
    ):
        raise ValueError(error_prefix)

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError(error_prefix)

    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None and not literal_address.is_global:
        raise ValueError(error_prefix)

    try:
        answers = resolver(hostname, port, type=socket.SOCK_STREAM)
        addresses = tuple(_resolved_addresses(answers))
    except OSError as exc:
        if allow_resolution_failure:
            return
        raise ValueError(f"{error_prefix}: hostname resolution failed") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{error_prefix}: invalid hostname resolution result") from exc

    if not addresses:
        if allow_resolution_failure:
            return
        raise ValueError(f"{error_prefix}: hostname resolution returned no addresses")
    if any(not address.is_global for address in addresses):
        raise ValueError(error_prefix)


def _resolved_addresses(answers: Iterable[Any]) -> Iterable[ipaddress._BaseAddress]:
    for answer in answers:
        if isinstance(answer, (str, bytes)):
            address_text = answer.decode() if isinstance(answer, bytes) else answer
        elif isinstance(answer, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            yield answer
            continue
        else:
            try:
                address_text = answer[4][0]
            except (IndexError, KeyError, TypeError) as exc:
                raise ValueError("invalid resolver answer") from exc
        yield ipaddress.ip_address(address_text)
