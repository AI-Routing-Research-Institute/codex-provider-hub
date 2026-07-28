from __future__ import annotations

import ipaddress
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


@dataclass(frozen=True)
class ServiceConfig:
    providers: tuple[ProviderConfig, ...]
    database_path: Path
    public_database_path: Path
    temp_root: Path
    codex_bin: Path


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
        _validate_public_https_endpoint(base_url, provider_id, active_resolver)
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
            )
        )

    database_path = Path(_required_string(service, "database_path", "service"))
    public_database_path = Path(
        _required_string(service, "public_database_path", "service")
    )
    if database_path.resolve() == public_database_path.resolve():
        raise ValueError("private and public database paths must differ")

    return ServiceConfig(
        providers=tuple(providers),
        database_path=database_path,
        public_database_path=public_database_path,
        temp_root=Path(_required_string(service, "temp_root", "service")),
        codex_bin=Path(_required_string(service, "codex_bin", "service")),
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


def _validate_public_https_endpoint(
    base_url: str,
    provider_id: str,
    resolver: Resolver,
) -> None:
    error_prefix = (
        f"provider {provider_id!r} base_url must be a public HTTPS endpoint"
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
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"{error_prefix}: hostname resolution failed") from exc

    if not addresses or any(not address.is_global for address in addresses):
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
