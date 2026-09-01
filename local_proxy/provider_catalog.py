"""Independent local Codex provider catalog backed by SQLite."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import tomllib
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import probe_codex_cc_switch as cc_switch

from local_proxy.core import ProviderConfigurationError, _normalize_base_url


CATALOG_SCHEMA_VERSION = 1
INITIAL_IMPORT_KEY = "initial_import_done"
COMMON_CONFIG_KEY = "common_config_codex"


@dataclass(frozen=True)
class CatalogProvider:
    provider_id: str
    name: str
    is_current: bool
    endpoint_url: str | None
    common_config_enabled: bool
    raw_config: str
    auth: Mapping[str, Any]
    meta: Mapping[str, Any]
    sort_index: int | None
    created_at: float

    @property
    def is_api_provider(self) -> bool:
        return bool(self.endpoint_url) or bool(self.raw_config.strip())

    def as_probe_record(self) -> cc_switch.ProviderRecord:
        return cc_switch.ProviderRecord(
            provider_id=self.provider_id,
            name=self.name,
            is_current=self.is_current,
            endpoint_url=self.endpoint_url,
            common_config_enabled=self.common_config_enabled,
            raw_config=self.raw_config,
            auth=dict(self.auth),
            meta=dict(self.meta),
        )


class ProviderCatalog:
    """Own the local Codex provider data and optional CCS import source."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        self._schema_ready = False

    def initialize(self, source: Path | None = None) -> dict[str, int | str]:
        self._ensure_schema()
        with self._lock, closing(self._connect()) as connection:
            initial_done = self._meta_value(connection, INITIAL_IMPORT_KEY) == "1"
        if initial_done:
            return {"added": 0, "skipped": 0, "overwritten": 0}

        result: dict[str, int | str] = {"added": 0, "skipped": 0, "overwritten": 0}
        records: list[cc_switch.ProviderRecord] = []
        common_config = ""
        if source is not None and Path(source).is_file():
            try:
                records = _load_ccs_records(Path(source))
                common_config = _load_ccs_common_config(Path(source))
            except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
                result["error"] = f"initial import skipped: {type(exc).__name__}"

        if records:
            imported = self._import_records(records, common_config, overwrite=False)
            result.update(imported)
        with self._lock, closing(self._connect()) as connection, connection:
            self._set_meta(connection, INITIAL_IMPORT_KEY, "1")
            self._set_meta(connection, "schema_version", str(CATALOG_SCHEMA_VERSION))
        return result

    def list_records(self) -> tuple[CatalogProvider, ...]:
        self._ensure_schema()
        query = """
            SELECT provider_id, name, is_current, endpoint_url,
                   common_config_enabled, raw_config, auth_json, meta_json,
                   sort_index, created_at
            FROM providers
            ORDER BY sort_index IS NULL, sort_index, created_at, name, provider_id
        """
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(query).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def get_record(self, provider_id: str) -> CatalogProvider | None:
        self._ensure_schema()
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT provider_id, name, is_current, endpoint_url,
                       common_config_enabled, raw_config, auth_json, meta_json,
                       sort_index, created_at
                FROM providers WHERE provider_id = ?
                """,
                (provider_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def common_config(self) -> str:
        self._ensure_schema()
        with self._lock, closing(self._connect()) as connection:
            return self._meta_value(connection, COMMON_CONFIG_KEY) or ""

    def create_from_payload(self, payload: Mapping[str, Any]) -> CatalogProvider:
        values = self._validated_payload(payload)
        provider_id = str(payload.get("provider_id") or f"local-{uuid.uuid4().hex}").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", provider_id):
            raise ValueError("provider_id contains unsupported characters")
        record = self._record_from_values(
            provider_id=provider_id,
            values=values,
            is_current=False,
            existing_auth={},
            existing_env_key="OPENAI_API_KEY",
        )
        with self._lock, closing(self._connect()) as connection, connection:
            if connection.execute(
                "SELECT 1 FROM providers WHERE provider_id = ?", (provider_id,)
            ).fetchone():
                raise ValueError("provider_id already exists")
            sort_index = self._next_sort_index(connection)
            self._insert_record(connection, record, sort_index=sort_index)
        return self.get_record(provider_id)  # type: ignore[return-value]

    def update_from_payload(
        self,
        provider_id: str,
        payload: Mapping[str, Any],
    ) -> CatalogProvider:
        existing = self.get_record(provider_id)
        if existing is None:
            raise KeyError(provider_id)
        values = self._validated_payload(payload)
        current_root = self._effective_root(existing)
        current_config = self._select_provider_table(current_root)
        record = self._record_from_values(
            provider_id=provider_id,
            values=values,
            is_current=existing.is_current,
            existing_auth=dict(existing.auth),
            existing_env_key=str(current_config.get("env_key") or "OPENAI_API_KEY"),
            existing_record=existing,
            existing_config=current_config,
            existing_model=str(current_root.get("model") or "") or None,
        )
        with self._lock, closing(self._connect()) as connection, connection:
            self._replace_record(
                connection,
                record,
                sort_index=existing.sort_index,
                created_at=existing.created_at,
            )
        return self.get_record(provider_id)  # type: ignore[return-value]

    def delete_record(self, provider_id: str) -> CatalogProvider:
        self._ensure_schema()
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT provider_id, name, is_current, endpoint_url,
                       common_config_enabled, raw_config, auth_json, meta_json,
                       sort_index, created_at
                FROM providers WHERE provider_id = ?
                """,
                (provider_id,),
            ).fetchone()
            if row is None:
                raise KeyError(provider_id)
            deleted = self._row_to_record(row)
            connection.execute("DELETE FROM providers WHERE provider_id = ?", (provider_id,))
        return deleted

    def import_from_cc_switch(self, source: Path, *, overwrite: bool = False) -> dict[str, int | str]:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"CC Switch database not found: {source_path}")
        records = _load_ccs_records(source_path)
        common_config = _load_ccs_common_config(source_path)
        result = self._import_records(records, common_config, overwrite=overwrite)
        with self._lock, closing(self._connect()) as connection, connection:
            self._set_meta(connection, INITIAL_IMPORT_KEY, "1")
        return result

    def editable_fields(self, provider: CatalogProvider) -> dict[str, Any]:
        root = self._effective_root(provider)
        config = self._select_provider_table(root)
        return {
            "provider_id": provider.provider_id,
            "name": provider.name,
            "base_url": str(config.get("base_url") or provider.endpoint_url or ""),
            "wire_api": str(config.get("wire_api") or "responses"),
            "transport": (
                str(config.get("transport"))
                if config.get("transport") in {"httpx", "curl_cffi"}
                else "httpx"
            ),
            "headers": _redact_mapping(_string_mapping(config.get("http_headers"))),
            "query_params": _redact_mapping(_string_mapping(config.get("query_params"))),
            "model": str(root.get("model") or ""),
            "has_api_key": bool(self._api_key(provider, str(config.get("env_key") or "OPENAI_API_KEY"))),
            "managed_locally": True,
        }

    def _import_records(
        self,
        records: list[cc_switch.ProviderRecord],
        common_config: str,
        *,
        overwrite: bool,
    ) -> dict[str, int | str]:
        result: dict[str, int | str] = {"added": 0, "skipped": 0, "overwritten": 0}
        invalid = 0
        with self._lock, closing(self._connect()) as connection, connection:
            for record in records:
                if not record.is_api_provider:
                    continue
                try:
                    raw_config = _canonical_import_config(record, common_config)
                except (ProviderConfigurationError, tomllib.TOMLDecodeError, TypeError, ValueError):
                    invalid += 1
                    continue
                existing = connection.execute(
                    "SELECT sort_index, created_at FROM providers WHERE provider_id = ?",
                    (record.provider_id,),
                ).fetchone()
                if existing is not None and not overwrite:
                    result["skipped"] = int(result["skipped"]) + 1
                    continue
                catalog_record = CatalogProvider(
                    provider_id=record.provider_id,
                    name=record.name,
                    is_current=record.is_current,
                    endpoint_url=record.endpoint_url,
                    common_config_enabled=False,
                    raw_config=raw_config,
                    auth=dict(record.auth),
                    meta={
                        **dict(record.meta),
                        "managedLocally": True,
                        "commonConfigEnabled": False,
                    },
                    sort_index=(
                        int(existing[0])
                        if existing is not None and existing[0] is not None
                        else self._next_sort_index(connection)
                    ),
                    created_at=(
                        float(existing[1])
                        if existing is not None and existing[1] is not None
                        else time.time()
                    ),
                )
                if existing is None:
                    self._insert_record(connection, catalog_record, sort_index=catalog_record.sort_index)
                    result["added"] = int(result["added"]) + 1
                else:
                    self._replace_record(
                        connection,
                        catalog_record,
                        sort_index=catalog_record.sort_index,
                        created_at=catalog_record.created_at,
                    )
                    result["overwritten"] = int(result["overwritten"]) + 1
        if invalid:
            result["invalid"] = invalid
        return result

    def _record_from_values(
        self,
        *,
        provider_id: str,
        values: Mapping[str, Any],
        is_current: bool,
        existing_auth: Mapping[str, Any],
        existing_env_key: str,
        existing_record: CatalogProvider | None = None,
        existing_config: Mapping[str, Any] | None = None,
        existing_model: str | None = None,
    ) -> CatalogProvider:
        api_key = values.get("api_key")
        clear_api_key = bool(values.get("clear_api_key"))
        if clear_api_key:
            auth: dict[str, Any] = {}
        elif api_key is None:
            auth = dict(existing_auth)
        else:
            auth = {"OPENAI_API_KEY": api_key}
        env_key = "OPENAI_API_KEY" if api_key is not None else existing_env_key
        headers = dict(values["headers"])
        query_params = dict(values["query_params"])
        if existing_config is not None:
            previous_headers = _string_mapping(existing_config.get("http_headers"))
            previous_query = _string_mapping(existing_config.get("query_params"))
            for key, value in previous_headers.items():
                if headers.get(key) == "***":
                    headers[key] = value
            for key, value in previous_query.items():
                if query_params.get(key) == "***":
                    query_params[key] = value
        raw_model = values.get("model")
        model = str(raw_model).strip()[:240] if isinstance(raw_model, str) and raw_model.strip() else None
        if raw_model is None and existing_model:
            model = existing_model[:240]
        raw_config = _build_raw_config(
            name=str(values["name"]),
            base_url=str(values["base_url"]),
            wire_api=str(values["wire_api"]),
            transport=str(values["transport"]),
            headers=headers,
            query_params=query_params,
            env_key=env_key if auth else None,
            model=model,
        )
        meta = dict(existing_record.meta) if existing_record is not None else {}
        meta.update({"managedLocally": True, "commonConfigEnabled": False})
        return CatalogProvider(
            provider_id=provider_id,
            name=str(values["name"]),
            is_current=is_current,
            endpoint_url=str(values["base_url"]),
            common_config_enabled=False,
            raw_config=raw_config,
            auth=auth,
            meta=meta,
            sort_index=None,
            created_at=time.time(),
        )

    def _validated_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        base_url = payload.get("base_url")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        normalized_url = _normalize_base_url(base_url)
        wire_api = payload.get("wire_api", "responses")
        if wire_api != "responses":
            raise ValueError("wire_api must be responses")
        transport = payload.get("transport", "httpx")
        if transport not in {"httpx", "curl_cffi"}:
            raise ValueError("transport must be httpx or curl_cffi")
        headers = _string_mapping(payload.get("headers", {}), strict=True)
        query_params = _string_mapping(payload.get("query_params", {}), strict=True)
        api_key = payload.get("api_key")
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            api_key = None
        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            raise ValueError("model must be a string")
        return {
            "name": name.strip()[:240],
            "base_url": normalized_url,
            "wire_api": wire_api,
            "transport": transport,
            "headers": headers,
            "query_params": query_params,
            "api_key": api_key.strip() if isinstance(api_key, str) else None,
            "clear_api_key": payload.get("clear_api_key") is True,
            "model": model.strip()[:240] if isinstance(model, str) else None,
        }

    def _effective_config(self, provider: CatalogProvider) -> dict[str, Any]:
        return self._select_provider_table(self._effective_root(provider))

    def _effective_root(self, provider: CatalogProvider) -> dict[str, Any]:
        try:
            effective = cc_switch.build_effective_config(
                provider.as_probe_record(), self.common_config()
            )
            config = tomllib.loads(effective) if effective.strip() else {}
            return dict(config) if isinstance(config, dict) else {}
        except (tomllib.TOMLDecodeError, TypeError, ValueError):
            return {}

    @staticmethod
    def _select_provider_table(root: Mapping[str, Any]) -> dict[str, Any]:
        providers = root.get("model_providers")
        selected = root.get("model_provider")
        selected_config = providers.get(selected) if isinstance(providers, dict) else None
        if not isinstance(selected_config, dict) and isinstance(providers, dict) and len(providers) == 1:
            selected_config = next(iter(providers.values()))
        return dict(selected_config) if isinstance(selected_config, dict) else {}

    @staticmethod
    def _api_key(provider: CatalogProvider, env_key: str) -> str | None:
        value = provider.auth.get(env_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        value = provider.auth.get("OPENAI_API_KEY")
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _ensure_schema(self) -> None:
        with self._lock:
            if self._schema_ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            with closing(self._connect()) as connection, connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS providers (
                        provider_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        is_current INTEGER NOT NULL DEFAULT 0,
                        endpoint_url TEXT,
                        common_config_enabled INTEGER NOT NULL DEFAULT 0,
                        raw_config TEXT NOT NULL DEFAULT '',
                        auth_json TEXT NOT NULL DEFAULT '{}',
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        sort_index INTEGER,
                        created_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS providers_sort
                        ON providers(sort_index, created_at, name);
                    CREATE TABLE IF NOT EXISTS catalog_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._schema_ready = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _meta_value(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM catalog_meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row is not None else None

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO catalog_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @staticmethod
    def _next_sort_index(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COALESCE(MAX(sort_index), -1) + 1 FROM providers").fetchone()
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _insert_record(
        connection: sqlite3.Connection,
        record: CatalogProvider,
        *,
        sort_index: int | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO providers(
                provider_id, name, is_current, endpoint_url,
                common_config_enabled, raw_config, auth_json, meta_json,
                sort_index, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.provider_id,
                record.name,
                int(record.is_current),
                record.endpoint_url,
                int(record.common_config_enabled),
                record.raw_config,
                json.dumps(dict(record.auth), ensure_ascii=False),
                json.dumps(dict(record.meta), ensure_ascii=False),
                sort_index,
                record.created_at,
            ),
        )

    @staticmethod
    def _replace_record(
        connection: sqlite3.Connection,
        record: CatalogProvider,
        *,
        sort_index: int | None,
        created_at: float,
    ) -> None:
        connection.execute(
            """
            UPDATE providers SET name = ?, is_current = ?, endpoint_url = ?,
                common_config_enabled = ?, raw_config = ?, auth_json = ?,
                meta_json = ?, sort_index = ?, created_at = ?
            WHERE provider_id = ?
            """,
            (
                record.name,
                int(record.is_current),
                record.endpoint_url,
                int(record.common_config_enabled),
                record.raw_config,
                json.dumps(dict(record.auth), ensure_ascii=False),
                json.dumps(dict(record.meta), ensure_ascii=False),
                sort_index,
                created_at,
                record.provider_id,
            ),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CatalogProvider:
        try:
            auth = json.loads(row["auth_json"] or "{}")
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderConfigurationError("local catalog contains invalid JSON") from exc
        if not isinstance(auth, dict) or not isinstance(meta, dict):
            raise ProviderConfigurationError("local catalog contains invalid provider metadata")
        return CatalogProvider(
            provider_id=str(row["provider_id"]),
            name=str(row["name"]),
            is_current=bool(row["is_current"]),
            endpoint_url=row["endpoint_url"],
            common_config_enabled=bool(row["common_config_enabled"]),
            raw_config=str(row["raw_config"] or ""),
            auth=auth,
            meta=meta,
            sort_index=int(row["sort_index"]) if row["sort_index"] is not None else None,
            created_at=float(row["created_at"]),
        )


def _build_raw_config(
    *,
    name: str,
    base_url: str,
    wire_api: str,
    transport: str,
    headers: Mapping[str, str],
    query_params: Mapping[str, str],
    env_key: str | None,
    model: str | None = None,
) -> str:
    lines = [
        'model_provider = "custom"',
    ]
    if model:
        lines.append(f"model = {_toml_string(model)}")
    lines.append("")
    lines.extend(
        (
            "[model_providers.custom]",
            f"name = {_toml_string(name)}",
            f"base_url = {_toml_string(base_url)}",
            f"wire_api = {_toml_string(wire_api)}",
            f"transport = {_toml_string(transport)}",
        )
    )
    if env_key:
        lines.append(f"env_key = {_toml_string(env_key)}")
    if headers:
        lines.extend(("", "[model_providers.custom.http_headers]"))
        lines.extend(f"{_toml_key(key)} = {_toml_string(value)}" for key, value in headers.items())
    if query_params:
        lines.extend(("", "[model_providers.custom.query_params]"))
        lines.extend(f"{_toml_key(key)} = {_toml_string(value)}" for key, value in query_params.items())
    return "\n".join(lines) + "\n"


def _load_ccs_records(path: Path) -> list[cc_switch.ProviderRecord]:
    query = """
        SELECT p.id, p.name, p.is_current, p.settings_config, p.meta, pe.url
        FROM providers AS p
        LEFT JOIN provider_endpoints AS pe
          ON pe.provider_id = p.id AND pe.app_type = p.app_type
        WHERE p.app_type = 'codex'
        ORDER BY p.sort_index IS NULL, p.sort_index, p.created_at, p.name
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query).fetchall()
    records: list[cc_switch.ProviderRecord] = []
    for row in rows:
        payload = json.loads(row["settings_config"] or "{}")
        auth = payload.get("auth") or {}
        meta = json.loads(row["meta"]) if row["meta"] else {}
        if not isinstance(auth, dict) or not isinstance(meta, dict):
            raise ValueError(f"Provider {row['name']} contains invalid metadata")
        records.append(
            cc_switch.ProviderRecord(
                provider_id=str(row["id"]),
                name=str(row["name"]),
                is_current=bool(row["is_current"]),
                endpoint_url=row["url"],
                common_config_enabled=bool(meta.get("commonConfigEnabled")),
                raw_config=str(payload.get("config") or "").strip(),
                auth=auth,
                meta=meta,
            )
        )
    return records


def _canonical_import_config(record: cc_switch.ProviderRecord, common_config: str) -> str:
    raw_config = cc_switch.build_effective_config(record, common_config)
    config = tomllib.loads(raw_config) if raw_config.strip() else {}
    providers = config.get("model_providers")
    selected = config.get("model_provider")
    provider_config = providers.get(selected) if isinstance(providers, dict) else None
    if not isinstance(provider_config, dict) and isinstance(providers, dict) and len(providers) == 1:
        provider_config = next(iter(providers.values()))
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    base_url = provider_config.get("base_url") or record.endpoint_url
    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderConfigurationError("provider has no base_url")
    _normalize_base_url(base_url)
    return raw_config


def _load_ccs_common_config(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT value FROM settings WHERE key = 'common_config_codex'"
        ).fetchone()
    return (row["value"] if row else "") or ""


def _toml_key(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else _toml_string(value)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _string_mapping(value: Any, *, strict: bool = False) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        if strict:
            raise ValueError("headers and query_params must be objects")
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(item, str):
            if strict:
                raise ValueError("headers and query_params must contain strings")
            continue
        result[key.strip()[:160]] = item[:2000]
    return result


def _redact_mapping(values: Mapping[str, str]) -> dict[str, str]:
    return {
        key: "***" if _looks_sensitive_name(key) else value
        for key, value in values.items()
    }


def _looks_sensitive_name(value: str) -> bool:
    normalized = value.casefold().replace("_", "-")
    return any(
        token in normalized
        for token in ("authorization", "api-key", "apikey", "token", "secret", "credential")
    )
