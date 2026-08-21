import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import httpx

from local_proxy.codex import load_local_proxy_providers
from local_proxy.core import ProviderRouter
from local_proxy.provider_catalog import ProviderCatalog
from local_proxy.server import ProxyProfile, create_unified_proxy_app


def create_ccs_database(
    path: Path,
    *,
    provider_id: str = "ccs-provider",
    name: str = "CCS Provider",
    api_key: str = "fixture-api-key",
) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                is_current INTEGER NOT NULL,
                settings_config TEXT NOT NULL,
                meta TEXT,
                app_type TEXT NOT NULL,
                sort_index INTEGER,
                created_at TEXT
            );
            CREATE TABLE provider_endpoints (
                provider_id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                url TEXT
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        config = (
            'model_provider = "custom"\n'
            "[model_providers.custom]\n"
            'base_url = "https://ccs.example.test/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "OPENAI_API_KEY"\n'
        )
        connection.execute(
            "INSERT INTO providers VALUES (?, ?, 1, ?, '{}', 'codex', 0, '2026-08-17')",
            (provider_id, name, json.dumps({"config": config, "auth": {"OPENAI_API_KEY": api_key}})),
        )
        connection.execute(
            "INSERT INTO provider_endpoints VALUES (?, 'codex', ?)",
            (provider_id, "https://fallback.example.test/v1"),
        )
        connection.execute(
            "INSERT INTO settings VALUES ('common_config_codex', '')"
        )


class ProviderCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_context.cleanup)
        self.root = Path(self.temp_context.name)
        self.source = self.root / "cc-switch.db"
        self.catalog = ProviderCatalog(self.root / "codex-providers.sqlite3")

    def test_initial_import_runs_once_and_manual_import_requires_overwrite(self) -> None:
        create_ccs_database(self.source)

        initial = self.catalog.initialize(self.source)
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute(
                "UPDATE providers SET name = 'Changed in CCS' WHERE id = 'ccs-provider'"
            )
        repeated_initialization = self.catalog.initialize(self.source)
        skipped = self.catalog.import_from_cc_switch(self.source)
        overwritten = self.catalog.import_from_cc_switch(self.source, overwrite=True)

        self.assertEqual(initial["added"], 1)
        self.assertEqual(repeated_initialization["added"], 0)
        self.assertEqual(skipped, {"added": 0, "skipped": 1, "overwritten": 0})
        self.assertEqual(overwritten, {"added": 0, "skipped": 0, "overwritten": 1})
        self.assertEqual(self.catalog.get_record("ccs-provider").name, "Changed in CCS")
        moved = self.source.with_name("cc-switch-moved.db")
        self.source.rename(moved)
        self.assertTrue(moved.is_file())

    def test_create_update_preserve_and_clear_api_key(self) -> None:
        self.catalog.initialize()
        created = self.catalog.create_from_payload(
            {
                "name": "Local Provider",
                "base_url": "https://local.example.test/v1/",
                "api_key": "fixture-local-key",
                "headers": {"X-Client": "local-proxy"},
                "query_params": {"version": "1"},
            }
        )

        self.catalog.update_from_payload(
            created.provider_id,
            {
                "name": "Renamed Provider",
                "base_url": "https://renamed.example.test/v1",
                "headers": {},
                "query_params": {},
            },
        )
        preserved = load_local_proxy_providers(self.catalog)[0]
        self.catalog.update_from_payload(
            created.provider_id,
            {
                "name": "Renamed Provider",
                "base_url": "https://renamed.example.test/v1",
                "headers": {},
                "query_params": {},
                "clear_api_key": True,
            },
        )
        cleared = load_local_proxy_providers(self.catalog)[0]

        self.assertEqual(preserved.name, "Renamed Provider")
        self.assertEqual(preserved.base_url, "https://renamed.example.test/v1")
        self.assertTrue(preserved.has_credentials)
        self.assertFalse(cleared.has_credentials)

    def test_transport_defaults_to_httpx_and_persists_curl_compatibility(self) -> None:
        self.catalog.initialize()
        created = self.catalog.create_from_payload(
            {
                "name": "Transport Provider",
                "base_url": "https://transport.example.test/v1",
                "api_key": "fixture-transport-key",
                "headers": {},
                "query_params": {},
            }
        )

        default_provider = load_local_proxy_providers(self.catalog)[0]
        default_editable = self.catalog.editable_fields(created)
        updated = self.catalog.update_from_payload(
            created.provider_id,
            {
                **default_editable,
                "transport": "curl_cffi",
            },
        )
        compatible_provider = load_local_proxy_providers(self.catalog)[0]
        compatible_editable = self.catalog.editable_fields(updated)

        self.assertEqual(default_provider.transport, "httpx")
        self.assertEqual(default_editable["transport"], "httpx")
        self.assertEqual(compatible_provider.transport, "curl_cffi")
        self.assertEqual(compatible_editable["transport"], "curl_cffi")
        self.assertIn('transport = "curl_cffi"', updated.raw_config)

    def test_transport_rejects_unknown_value(self) -> None:
        self.catalog.initialize()

        with self.assertRaisesRegex(ValueError, "transport"):
            self.catalog.create_from_payload(
                {
                    "name": "Invalid Transport",
                    "base_url": "https://invalid.example.test/v1",
                    "transport": "unknown",
                    "headers": {},
                    "query_params": {},
                }
            )

    def test_editable_fields_redact_sensitive_headers_and_queries(self) -> None:
        self.catalog.initialize()
        created = self.catalog.create_from_payload(
            {
                "name": "Sensitive Provider",
                "base_url": "https://sensitive.example.test/v1",
                "api_key": "fixture-private-key",
                "headers": {
                    "Authorization": "Bearer fixture-header-key",
                    "X-Client": "visible-value",
                },
                "query_params": {"api_key": "fixture-query-key", "version": "1"},
            }
        )

        editable = self.catalog.editable_fields(created)
        serialized = json.dumps(editable)
        self.catalog.update_from_payload(
            created.provider_id,
            {
                **editable,
                "name": "Sensitive Provider Updated",
            },
        )
        loaded = load_local_proxy_providers(self.catalog)[0]

        self.assertEqual(editable["headers"]["Authorization"], "***")
        self.assertEqual(editable["query_params"]["api_key"], "***")
        self.assertNotIn("fixture-private-key", serialized)
        self.assertNotIn("fixture-header-key", serialized)
        self.assertNotIn("fixture-query-key", serialized)
        self.assertEqual(loaded.configured_headers["Authorization"], "Bearer fixture-header-key")
        self.assertEqual(loaded.default_query["api_key"], "fixture-query-key")

    def test_delete_removes_provider_and_rejects_unknown_id(self) -> None:
        self.catalog.initialize()
        created = self.catalog.create_from_payload(
            {
                "name": "Disposable Provider",
                "base_url": "https://disposable.example.test/v1",
                "headers": {},
                "query_params": {},
            }
        )

        deleted = self.catalog.delete_record(created.provider_id)

        self.assertEqual(deleted.name, "Disposable Provider")
        self.assertIsNone(self.catalog.get_record(created.provider_id))
        with self.assertRaises(KeyError):
            self.catalog.delete_record(created.provider_id)

    def test_missing_ccs_database_creates_empty_catalog_with_indexes(self) -> None:
        result = self.catalog.initialize(self.source)
        create_ccs_database(self.source)
        repeated = self.catalog.initialize(self.source)
        with closing(sqlite3.connect(self.catalog.path)) as connection, connection:
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(providers)")}

        self.assertEqual(result["added"], 0)
        self.assertEqual(repeated["added"], 0)
        self.assertEqual(self.catalog.list_records(), ())
        self.assertIn("providers_sort", indexes)


class ProviderCatalogApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.catalog = ProviderCatalog(Path(self.temp_context.name) / "providers.sqlite3")
        self.catalog.initialize()
        created = self.catalog.create_from_payload(
            {
                "name": "Initial",
                "base_url": "https://initial.example.test/v1",
                "api_key": "fixture-initial-key",
                "headers": {},
                "query_params": {},
            }
        )
        self.provider_id = created.provider_id
        self.import_modes: list[bool] = []
        self.upstream = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200))
        )
        codex = ProxyProfile(
            service_id="codex",
            service_name="codex-local-proxy",
            router=ProviderRouter(load_local_proxy_providers(self.catalog)),
            upstream_client=self.upstream,
            owns_client=False,
            reload_providers=lambda: load_local_proxy_providers(self.catalog),
            provider_catalog=self.catalog,
            provider_catalog_import=self._record_import,
        )
        claude = ProxyProfile(
            service_id="claude",
            service_name="claude-local-proxy",
            router=ProviderRouter(()),
            upstream_client=self.upstream,
            owns_client=False,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_unified_proxy_app(codex, claude)),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.upstream.aclose()
        self.temp_context.cleanup()

    def _record_import(self, overwrite: bool) -> dict[str, int]:
        self.import_modes.append(overwrite)
        return {"added": 0, "skipped": 0, "overwritten": int(overwrite)}

    async def test_catalog_api_does_not_return_keys_and_hot_reloads_router(self) -> None:
        detail = await self.client.get(f"/control/codex/api/providers/{self.provider_id}")
        forbidden = await self.client.post(
            "/control/codex/api/providers",
            json={"name": "Forbidden", "base_url": "https://forbidden.example.test/v1"},
        )
        created = await self.client.post(
            "/control/codex/api/providers",
            headers={"X-Local-Proxy-Control": "1"},
            json={
                "name": "Created",
                "base_url": "https://created.example.test/v1",
                "api_key": "fixture-created-key",
                "headers": {},
                "query_params": {},
            },
        )
        imported = await self.client.post(
            "/control/codex/api/providers/import/cc-switch",
            headers={"X-Local-Proxy-Control": "1"},
            json={"overwrite": True},
        )
        current_delete = await self.client.delete(
            f"/control/codex/api/providers/{self.provider_id}",
            headers={"X-Local-Proxy-Control": "1"},
        )
        created_id = created.json()["catalog"]["provider_id"]
        forbidden_delete = await self.client.delete(
            f"/control/codex/api/providers/{created_id}",
        )
        deleted = await self.client.delete(
            f"/control/codex/api/providers/{created_id}",
            headers={"X-Local-Proxy-Control": "1"},
        )
        missing_delete = await self.client.delete(
            f"/control/codex/api/providers/{created_id}",
            headers={"X-Local-Proxy-Control": "1"},
        )

        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.json()["has_api_key"])
        self.assertNotIn("fixture-initial-key", detail.text)
        self.assertNotIn("api_key", detail.json())
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["catalog"]["action"], "created")
        self.assertEqual(
            [provider["name"] for provider in created.json()["providers"]],
            ["Initial", "Created"],
        )
        self.assertNotIn("fixture-created-key", created.text)
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["catalog"]["overwritten"], 1)
        self.assertEqual(self.import_modes, [True])
        self.assertEqual(current_delete.status_code, 409)
        self.assertEqual(forbidden_delete.status_code, 403)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["catalog"]["action"], "deleted")
        self.assertEqual([provider["name"] for provider in deleted.json()["providers"]], ["Initial"])
        self.assertNotIn("fixture-created-key", deleted.text)
        self.assertEqual(missing_delete.status_code, 404)


if __name__ == "__main__":
    unittest.main()
