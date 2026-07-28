import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from provider_status.config import ProviderConfig
from provider_status.state import TargetState
from provider_status.store import ProbeRecord, StatusStore


def make_provider(
    *,
    provider_id: str = "provider-alpha",
    name: str = "Provider Alpha",
    base_url: str = "https://alpha.example.com/v1",
    credential_name: str = "provider-alpha-api-key",
    models: tuple[str, ...] = ("gpt-5.6-sol", "gpt-5.6-terra"),
    display_models: tuple[str, ...] | None = None,
    probe_mode: str = "automatic",
) -> ProviderConfig:
    return ProviderConfig(
        provider_id=provider_id,
        name=name,
        base_url=base_url,
        credential_name=credential_name,
        models=models,
        healthy_interval_seconds=600,
        unhealthy_interval_seconds=120,
        timeout_seconds=90,
        display_models=display_models,
        probe_mode=probe_mode,
    )


class StatusStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "status.sqlite3"
        self.store = StatusStore(self.database_path)
        self.now = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)

    def record(
        self,
        target_id: int,
        at: datetime,
        *,
        success: bool,
        latency_ms: int | None = 100,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        duration = timedelta(milliseconds=latency_ms or 0)
        self.store.record_probe(
            target_id,
            ProbeRecord(
                started_at=at - duration,
                success=success,
                latency_ms=latency_ms,
                error_code=error_code,
                error_summary=error_summary,
            ),
            at,
        )

    def rows(self, sql: str, parameters: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(sql, parameters).fetchall()

    def test_initialize_is_idempotent_upserts_public_config_and_staggers_due_targets(
        self,
    ) -> None:
        provider = make_provider(credential_name="first-secret-file")
        self.store.initialize((provider,), self.now)

        first_due = self.store.list_due_targets(self.now)
        before_second = self.store.list_due_targets(
            self.now + timedelta(seconds=29),
            limit=10,
        )
        both_due = self.store.list_due_targets(
            self.now + timedelta(seconds=30),
            limit=10,
        )

        self.assertEqual([target.model for target in first_due], ["gpt-5.6-sol"])
        self.assertEqual([target.model for target in before_second], ["gpt-5.6-sol"])
        self.assertEqual(
            [target.model for target in both_due],
            ["gpt-5.6-sol", "gpt-5.6-terra"],
        )
        initial_times = [target.next_check_at for target in both_due]
        self.assertGreaterEqual(
            initial_times[1] - initial_times[0],
            timedelta(seconds=30),
        )

        updated = make_provider(
            name="Provider Alpha Updated",
            base_url="https://status.example.com/v1",
            credential_name="different-secret-file",
        )
        self.store.initialize((updated,), self.now + timedelta(days=1))

        providers = self.rows("SELECT * FROM providers")
        targets = self.rows("SELECT * FROM probe_targets ORDER BY id")
        provider_columns = {
            row["name"]
            for row in self.rows("PRAGMA table_info(providers)")
        }
        all_columns = {
            row["name"]
            for table in (
                "providers",
                "probe_targets",
                "probe_runs",
                "provider_snapshots",
            )
            for row in self.rows(f"PRAGMA table_info({table})")
        }

        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["name"], "Provider Alpha Updated")
        self.assertEqual(providers[0]["base_url"], "https://status.example.com/v1")
        self.assertEqual(len(targets), 2)
        self.assertEqual(
            [datetime.fromisoformat(row["next_check_at"]) for row in targets],
            initial_times,
        )
        self.assertNotIn("credential_name", provider_columns)
        self.assertFalse(
            {"credential", "database_path", "raw_response"} & all_columns
        )

    def test_initialize_migrates_and_updates_provider_display_order(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE providers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

        first = make_provider(provider_id="z-first", name="First")
        second = make_provider(provider_id="a-second", name="Second")
        self.store.initialize((first, second), self.now)

        self.assertEqual(
            [item["provider_id"] for item in self.store.get_public_status(7, self.now)],
            ["z-first", "a-second"],
        )
        provider_columns = {
            row["name"] for row in self.rows("PRAGMA table_info(providers)")
        }
        self.assertIn("display_order", provider_columns)

        self.store.initialize((second, first), self.now + timedelta(minutes=1))

        self.assertEqual(
            [item["provider_id"] for item in self.store.get_public_status(7, self.now)],
            ["a-second", "z-first"],
        )

    def test_initialize_disables_providers_removed_from_configuration(self) -> None:
        removed = make_provider(provider_id="removed", name="Removed")
        retained = make_provider(provider_id="retained", name="Retained")
        self.store.initialize((removed, retained), self.now)

        self.store.initialize((retained,), self.now + timedelta(minutes=1))

        self.assertEqual(
            [item["provider_id"] for item in self.store.get_public_status(7, self.now)],
            ["retained"],
        )
        removed_row = self.rows(
            "SELECT enabled FROM providers WHERE id = ?",
            ("removed",),
        )[0]
        self.assertEqual(removed_row["enabled"], 0)

    def test_public_status_falls_back_to_id_order_for_legacy_snapshot(self) -> None:
        first = make_provider(provider_id="z-first", name="First")
        second = make_provider(provider_id="a-second", name="Second")
        self.store.initialize((first, second), self.now)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("ALTER TABLE providers DROP COLUMN display_order")
            connection.commit()

        self.assertEqual(
            [item["provider_id"] for item in self.store.get_public_status(7, self.now)],
            ["a-second", "z-first"],
        )

    def test_initialize_staggers_model_added_by_later_configuration(self) -> None:
        self.store.initialize(
            (make_provider(models=("model-a",)),),
            self.now,
        )
        first_target = self.rows(
            "SELECT model, next_check_at FROM probe_targets"
        )[0]

        self.store.initialize(
            (make_provider(models=("model-a", "model-b")),),
            self.now,
        )

        targets = self.rows(
            "SELECT model, next_check_at FROM probe_targets ORDER BY id"
        )
        self.assertEqual([row["model"] for row in targets], ["model-a", "model-b"])
        self.assertEqual(targets[0]["next_check_at"], first_target["next_check_at"])
        self.assertGreaterEqual(
            datetime.fromisoformat(targets[1]["next_check_at"])
            - datetime.fromisoformat(targets[0]["next_check_at"]),
            timedelta(seconds=30),
        )

    def test_display_models_do_not_create_probe_targets(self) -> None:
        provider = make_provider(
            models=("model-a",),
            display_models=("model-a", "model-b"),
        )

        self.store.initialize((provider,), self.now)

        targets = self.rows("SELECT model FROM probe_targets ORDER BY id")
        public = self.store.get_public_provider("provider-alpha", 7, self.now)
        assert public is not None
        self.assertEqual([row["model"] for row in targets], ["model-a"])
        self.assertEqual(public["model_count"], 1)
        self.assertEqual(public["display_models"], ["model-a", "model-b"])
        self.assertEqual(
            [model["model"] for model in public["models"]],
            ["model-a"],
        )

    def test_manual_only_provider_has_no_scheduled_targets(self) -> None:
        provider = make_provider(
            models=("model-a", "model-b"),
            probe_mode="manual_only",
        )

        self.store.initialize((provider,), self.now)

        self.assertEqual(self.store.list_due_targets(self.now, limit=10), [])
        public = self.store.get_public_provider("provider-alpha", 7, self.now)
        assert public is not None
        self.assertEqual(public["probe_mode"], "manual_only")
        self.assertEqual(public["model_count"], 0)
        self.assertEqual(public["display_models"], ["model-a", "model-b"])
        self.assertEqual(public["models"], [])

    def test_initialize_removes_models_no_longer_configured(self) -> None:
        self.store.initialize(
            (make_provider(models=("gpt-5.6-sol", "gpt-5.6-terra")),),
            self.now,
        )
        terra_id = next(
            row["id"]
            for row in self.rows("SELECT id, model FROM probe_targets")
            if row["model"] == "gpt-5.6-terra"
        )
        self.record(terra_id, self.now + timedelta(seconds=30), success=True)

        self.store.initialize(
            (make_provider(models=("gpt-5.6-sol", "gpt-5.5")),),
            self.now + timedelta(minutes=1),
        )

        targets = self.rows("SELECT model FROM probe_targets ORDER BY id")
        runs = self.rows("SELECT target_id FROM probe_runs")
        self.assertEqual(
            [row["model"] for row in targets],
            ["gpt-5.6-sol", "gpt-5.5"],
        )
        self.assertEqual(runs, [])

    def test_connections_enable_wal_foreign_keys_and_busy_timeout(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)

        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO probe_runs (
                        target_id, started_at, finished_at, success, state_after
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (999, self.now.isoformat(), self.now.isoformat(), 1, "healthy"),
                )

    def test_public_queries_open_sqlite_in_read_only_mode(self) -> None:
        self.store.initialize((make_provider(),), self.now)
        real_connect = sqlite3.connect
        calls: list[tuple[object, dict[str, object]]] = []

        def tracked_connect(
            database: object,
            *args: object,
            **kwargs: object,
        ) -> sqlite3.Connection:
            calls.append((database, dict(kwargs)))
            return real_connect(database, *args, **kwargs)

        with patch("provider_status.store.sqlite3.connect", side_effect=tracked_connect):
            self.store.get_public_status(7, self.now)

        database, options = calls[0]
        self.assertIn("mode=ro", str(database))
        self.assertIs(options.get("uri"), True)

    def test_publish_read_snapshot_creates_clean_delete_journal_database(
        self,
    ) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.store.list_due_targets(self.now)[0].id
        self.record(target_id, self.now, success=True, latency_ms=120)
        public_path = Path(self.temp_dir.name) / "public" / "status.sqlite3"

        self.store.publish_read_snapshot(public_path)

        with closing(sqlite3.connect(public_path)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode, "delete")
        self.assertEqual(
            StatusStore(public_path).get_public_status(7, self.now),
            self.store.get_public_status(7, self.now),
        )
        self.assertEqual(
            list(public_path.parent.glob(f".{public_path.name}.*.tmp")),
            [],
        )

    def test_publish_read_snapshot_failure_preserves_previous_file(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        public_path = Path(self.temp_dir.name) / "public" / "status.sqlite3"
        public_path.parent.mkdir()
        public_path.write_bytes(b"previous-public-snapshot")

        with patch(
            "provider_status.store.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.store.publish_read_snapshot(public_path)

        self.assertEqual(public_path.read_bytes(), b"previous-public-snapshot")
        self.assertEqual(
            list(public_path.parent.glob(f".{public_path.name}.*.tmp")),
            [],
        )

    def test_resanitize_error_summaries_redacts_legacy_json_values(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        legacy = (
            '{"authorization":"Bearer legacy-bearer-secret",'
            '"api_key":"legacy-api-secret",'
            '"diagnostic":"legacy channel error"}'
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            target_id = connection.execute(
                "SELECT id FROM probe_targets"
            ).fetchone()[0]
            connection.execute(
                "UPDATE probe_targets SET last_error_summary = ? WHERE id = ?",
                (legacy, target_id),
            )
            connection.execute(
                """
                INSERT INTO probe_runs (
                    target_id, started_at, finished_at, success,
                    error_code, error_summary, state_after
                ) VALUES (?, ?, ?, 0, 'unknown_error', ?, 'degraded')
                """,
                (target_id, self.now.isoformat(), self.now.isoformat(), legacy),
            )
            connection.commit()

        changed = self.store.resanitize_error_summaries()

        self.assertEqual(changed, 2)
        target_summary = self.rows(
            "SELECT last_error_summary FROM probe_targets"
        )[0]["last_error_summary"]
        run_summary = self.rows("SELECT error_summary FROM probe_runs")[0][
            "error_summary"
        ]
        for summary in (target_summary, run_summary):
            self.assertNotIn("legacy-bearer-secret", summary)
            self.assertNotIn("legacy-api-secret", summary)
            self.assertIn("legacy channel error", summary)
            self.assertIn("REDACTED", summary)

    def test_record_probe_transitions_target_and_updates_schedule(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.store.list_due_targets(self.now)[0].id

        cases = (
            (False, TargetState.DEGRADED, 0, 1, 120),
            (False, TargetState.DOWN, 0, 2, 120),
            (True, TargetState.RECOVERING, 1, 0, 120),
            (True, TargetState.HEALTHY, 2, 0, 600),
        )
        for index, (success, state, successes, failures, interval) in enumerate(cases):
            checked_at = self.now + timedelta(minutes=index)
            self.record(
                target_id,
                checked_at,
                success=success,
                latency_ms=123 + index,
                error_code=None if success else "network_error",
                error_summary=None if success else "connection interrupted",
            )

            target = self.rows("SELECT * FROM probe_targets WHERE id = ?", (target_id,))[0]
            self.assertEqual(target["state"], state.value)
            self.assertEqual(target["consecutive_successes"], successes)
            self.assertEqual(target["consecutive_failures"], failures)
            self.assertEqual(
                datetime.fromisoformat(target["next_check_at"]),
                checked_at + timedelta(seconds=interval),
            )

        target = self.rows("SELECT * FROM probe_targets WHERE id = ?", (target_id,))[0]
        self.assertIsNone(target["last_error_code"])
        self.assertIsNone(target["last_error_summary"])
        self.assertEqual(len(self.rows("SELECT id FROM probe_runs")), 4)
        self.assertEqual(len(self.rows("SELECT id FROM provider_snapshots")), 4)

    def test_record_probe_rolls_back_run_when_target_update_fails(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.store.list_due_targets(self.now)[0].id
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_target_update
                BEFORE UPDATE ON probe_targets
                BEGIN
                    SELECT RAISE(ABORT, 'target update rejected');
                END
                """
            )
            connection.commit()

        with self.assertRaisesRegex(sqlite3.IntegrityError, "target update rejected"):
            self.record(target_id, self.now, success=True)

        self.assertEqual(self.rows("SELECT id FROM probe_runs"), [])
        self.assertEqual(self.rows("SELECT id FROM provider_snapshots"), [])
        target = self.rows("SELECT state FROM probe_targets WHERE id = ?", (target_id,))[0]
        self.assertEqual(target["state"], TargetState.UNKNOWN.value)

    def test_record_probe_normalizes_error_code_and_redacts_summary_before_storage(
        self,
    ) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.store.list_due_targets(self.now)[0].id
        bearer_secret = "bearer-sensitive-value"
        token_secret = "sk-" + "sensitivevalue123"
        url_secret = "https://alice:password@example.com/v1?api_key=query-secret&safe=yes"
        summary = (
            f"Authorization: Bearer {bearer_secret}; {url_secret}; {token_secret}; "
            + "diagnostic " * 40
        )

        self.record(
            target_id,
            self.now,
            success=False,
            error_code=f"Authorization: Bearer {bearer_secret}",
            error_summary=summary,
        )

        run = self.rows(
            "SELECT error_code, error_summary FROM probe_runs WHERE target_id = ?",
            (target_id,),
        )[0]
        target = self.rows(
            "SELECT last_error_code, last_error_summary FROM probe_targets WHERE id = ?",
            (target_id,),
        )[0]
        public = self.store.get_public_provider("provider-alpha", 7, self.now)
        assert public is not None
        public_model = public["models"][0]

        self.assertEqual(run["error_code"], "unknown_error")
        self.assertEqual(target["last_error_code"], "unknown_error")
        self.assertEqual(public_model["error_code"], "unknown_error")
        self.assertEqual(run["error_summary"], target["last_error_summary"])
        self.assertEqual(run["error_summary"], public_model["error_summary"])
        self.assertLessEqual(len(run["error_summary"]), 240)
        self.assertIn("[REDACTED]", run["error_summary"])
        for secret in (
            bearer_secret,
            token_secret,
            "alice",
            "password",
            "query-secret",
        ):
            self.assertNotIn(secret, run["error_summary"])

    def test_record_probe_redacts_common_secret_carriers_without_losing_diagnostics(
        self,
    ) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.store.list_due_targets(self.now)[0].id
        secret = "TOP-SECRET-" + "123456"
        cases = (
            ("x api key header", f"X-API-Key: {secret}"),
            ("plain api key", f"api_key={secret}"),
            (
                "url fragment token",
                f"https://example.com/cb#access_token={secret}",
            ),
            (
                "url query x api key",
                f"https://example.com/v1?x-api-key={secret}&mode=retry",
            ),
            ("authorization header", f"Authorization: Bearer {secret}"),
            (
                "proxy authorization header",
                f"Proxy-Authorization: Basic {secret}",
            ),
            ("cookie header", f"Cookie: session={secret}; theme=dark"),
            ("set cookie header", f"Set-Cookie: session={secret}; HttpOnly"),
            ("client secret", f"client_secret={secret}"),
            ("refresh token", f"refresh_token: {secret}"),
            ("password", f"password={secret}"),
        )

        for index, (label, carrier) in enumerate(cases):
            with self.subTest(label=label):
                checked_at = self.now + timedelta(minutes=index)
                summary = (
                    f"{carrier}\n"
                    "upstream refused connection; request_id=abc123; "
                    + "diagnostic " * 40
                )
                self.record(
                    target_id,
                    checked_at,
                    success=False,
                    error_code="network_error",
                    error_summary=summary,
                )

                run = self.rows(
                    """
                    SELECT error_summary
                    FROM probe_runs
                    WHERE target_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (target_id,),
                )[0]
                public = self.store.get_public_provider(
                    "provider-alpha",
                    7,
                    checked_at,
                )
                assert public is not None
                public_summary = public["models"][0]["error_summary"]

                self.assertNotIn(secret, run["error_summary"])
                self.assertEqual(public_summary, run["error_summary"])
                self.assertLessEqual(len(run["error_summary"]), 240)
                self.assertIn("upstream refused connection", run["error_summary"])
                self.assertIn("request_id=abc123", run["error_summary"])

    def test_record_probe_redacts_json_quoted_sensitive_keys(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.store.list_due_targets(self.now)[0].id
        summary = (
            '{"api_key":"json-api-secret",'
            '"authorization":"Bearer json-bearer-secret",'
            '"cookie":"session=json-cookie-secret",'
            '"diagnostic":"no available channel"}'
        )

        self.record(
            target_id,
            self.now,
            success=False,
            error_code="no_channel",
            error_summary=summary,
        )

        stored = self.rows("SELECT error_summary FROM probe_runs")[0][
            "error_summary"
        ]
        for secret in (
            "json-api-secret",
            "json-bearer-secret",
            "json-cookie-secret",
        ):
            self.assertNotIn(secret, stored)
        self.assertIn("no available channel", stored)
        self.assertIn("REDACTED", stored)

    def test_record_probe_redacts_all_url_query_and_fragment_values(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.store.list_due_targets(self.now)[0].id
        signed_values = (
            "signed-credential-" + "123456",
            "signed-signature-" + "123456",
            "signed-session-" + "123456",
        )
        unknown_value = "unknown-value-" + "123456"
        opaque_fragment = "opaque-fragment-" + "123456"
        cases = (
            (
                "signed url",
                "https://example.com/object?"
                f"X-Amz-Credential={signed_values[0]}&"
                f"X-Amz-Signature={signed_values[1]}&"
                f"X-Amz-Security-Token={signed_values[2]}",
                signed_values,
            ),
            (
                "unknown query parameter",
                f"https://example.com/v1?custom={unknown_value}",
                (unknown_value,),
            ),
            (
                "opaque fragment",
                f"https://example.com/cb#{opaque_fragment}",
                (opaque_fragment,),
            ),
        )

        for index, (label, url, secret_values) in enumerate(cases):
            with self.subTest(label=label):
                checked_at = self.now + timedelta(minutes=index)
                self.record(
                    target_id,
                    checked_at,
                    success=False,
                    error_code="network_error",
                    error_summary=f"callback failed: {url}",
                )

                run = self.rows(
                    """
                    SELECT error_summary
                    FROM probe_runs
                    WHERE target_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (target_id,),
                )[0]
                public = self.store.get_public_provider(
                    "provider-alpha",
                    7,
                    checked_at,
                )
                assert public is not None
                public_summary = public["models"][0]["error_summary"]

                for secret_value in secret_values:
                    self.assertNotIn(secret_value, run["error_summary"])
                    self.assertNotIn(secret_value, public_summary)
                self.assertEqual(public_summary, run["error_summary"])
                self.assertIn("REDACTED", run["error_summary"])
                self.assertLessEqual(len(run["error_summary"]), 240)

    def test_public_queries_use_one_sqlite_read_snapshot(self) -> None:
        self.store.initialize((make_provider(models=("model-old",)),), self.now)

        for method_name in ("get_public_status", "get_public_provider"):
            with self.subTest(method=method_name):
                with closing(sqlite3.connect(self.database_path)) as reset_connection:
                    reset_connection.execute(
                        "UPDATE providers SET name = 'Provider Old' WHERE id = 'provider-alpha'"
                    )
                    reset_connection.execute(
                        "UPDATE probe_targets SET model = 'model-old' WHERE provider_id = 'provider-alpha'"
                    )
                    reset_connection.commit()

                select_count = 0
                writer_committed = False

                class CoordinatedConnection(sqlite3.Connection):
                    def execute(
                        connection_self,
                        sql: str,
                        parameters: tuple[object, ...] = (),
                    ) -> sqlite3.Cursor:
                        nonlocal select_count, writer_committed
                        if sql.lstrip().upper().startswith("SELECT"):
                            select_count += 1
                            if select_count == 2:
                                with closing(
                                    sqlite3.connect(self.database_path)
                                ) as writer:
                                    writer.execute(
                                        """
                                        UPDATE providers
                                        SET name = 'Provider New'
                                        WHERE id = 'provider-alpha'
                                        """
                                    )
                                    writer.execute(
                                        """
                                        UPDATE probe_targets
                                        SET model = 'model-new'
                                        WHERE provider_id = 'provider-alpha'
                                        """
                                    )
                                    writer.commit()
                                writer_committed = True
                        return super().execute(sql, parameters)

                class CoordinatedStore(StatusStore):
                    def _connect(
                        store_self,
                        *,
                        read_only: bool = False,
                    ) -> sqlite3.Connection:
                        database: object = store_self._database_path
                        options: dict[str, object] = {}
                        if read_only:
                            database = (
                                f"{store_self._database_path.resolve().as_uri()}?mode=ro"
                            )
                            options["uri"] = True
                        connection = sqlite3.connect(
                            database,
                            timeout=5.0,
                            factory=CoordinatedConnection,
                            **options,
                        )
                        connection.row_factory = sqlite3.Row
                        if read_only:
                            connection.execute("PRAGMA query_only=ON")
                        else:
                            connection.execute("PRAGMA journal_mode=WAL")
                        connection.execute("PRAGMA foreign_keys=ON")
                        connection.execute("PRAGMA busy_timeout=5000")
                        return connection

                read_store = CoordinatedStore(self.database_path)
                if method_name == "get_public_status":
                    public = read_store.get_public_status(7, self.now)[0]
                else:
                    public = read_store.get_public_provider("provider-alpha", 7, self.now)
                    assert public is not None

                self.assertTrue(writer_committed)
                self.assertEqual(public["name"], "Provider Old")
                self.assertEqual(public["models"][0]["model"], "model-old")
                persisted = self.rows(
                    """
                    SELECT providers.name, probe_targets.model
                    FROM providers
                    JOIN probe_targets ON probe_targets.provider_id = providers.id
                    WHERE providers.id = 'provider-alpha'
                    """
                )[0]
                self.assertEqual(persisted["name"], "Provider New")
                self.assertEqual(persisted["model"], "model-new")

    def test_time_weighted_availability_is_not_biased_by_probe_frequency(self) -> None:
        start = self.now - timedelta(hours=10)
        self.store.initialize((make_provider(models=("model-a",)),), start)
        target_id = self.store.list_due_targets(start)[0].id
        self.record(target_id, start, success=True)
        failure_at = start + timedelta(hours=6)
        self.record(target_id, failure_at, success=False, error_code="network_error")
        for minutes in range(10, 120, 10):
            self.record(
                target_id,
                failure_at + timedelta(minutes=minutes),
                success=False,
                error_code="network_error",
            )
        self.record(target_id, start + timedelta(hours=8), success=True)

        public = self.store.get_public_provider("provider-alpha", 7, self.now)

        self.assertIsNotNone(public)
        assert public is not None
        self.assertAlmostEqual(public["availability"], 80.0)
        self.assertAlmostEqual(public["models"][0]["availability"], 80.0)

    def test_short_windows_use_time_weighted_availability(self) -> None:
        start = self.now - timedelta(hours=24)
        self.store.initialize((make_provider(models=("model-a",)),), start)
        target_id = self.store.list_due_targets(start)[0].id
        self.record(target_id, start, success=True)
        self.record(
            target_id,
            self.now - timedelta(hours=2),
            success=False,
            error_code="network_error",
        )

        three_hours = self.store.get_public_provider(
            "provider-alpha",
            0.125,
            self.now,
        )
        twenty_four_hours = self.store.get_public_provider(
            "provider-alpha",
            1,
            self.now,
        )

        assert three_hours is not None
        assert twenty_four_hours is not None
        self.assertAlmostEqual(three_hours["availability"], 100 / 3)
        self.assertAlmostEqual(
            three_hours["models"][0]["availability"],
            100 / 3,
        )
        self.assertAlmostEqual(twenty_four_hours["availability"], 100 * 22 / 24)
        self.assertAlmostEqual(
            twenty_four_hours["models"][0]["availability"],
            100 * 22 / 24,
        )

    def test_initialize_migrates_legacy_snapshot_windows(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE provider_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    availability_7d REAL,
                    availability_15d REAL,
                    availability_30d REAL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO provider_snapshots (
                    provider_id,
                    recorded_at,
                    state,
                    availability_7d,
                    availability_15d,
                    availability_30d
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("legacy", self.now.isoformat(), "healthy", 90.0, 91.0, 92.0),
            )
            connection.commit()

        self.store.initialize((make_provider(models=("model-a",)),), self.now)

        columns = {
            row["name"]
            for row in self.rows("PRAGMA table_info(provider_snapshots)")
        }
        legacy = self.rows(
            "SELECT provider_id, availability_7d FROM provider_snapshots"
        )
        self.assertIn("availability_3h", columns)
        self.assertIn("availability_24h", columns)
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["provider_id"], "legacy")
        self.assertEqual(legacy[0]["availability_7d"], 90.0)

    def test_public_status_is_allowlisted_and_history_is_limited_to_latest_60(
        self,
    ) -> None:
        self.store.initialize((make_provider(models=("model-a", "model-b")),), self.now)
        targets = self.rows("SELECT id, model FROM probe_targets ORDER BY id")
        first_id = targets[0]["id"]
        second_id = targets[1]["id"]
        for index in range(65):
            self.record(
                first_id,
                self.now + timedelta(minutes=index),
                success=index % 3 != 0,
                latency_ms=200 + index,
                error_code="rate_limited" if index % 3 == 0 else None,
                error_summary="retry later" if index % 3 == 0 else None,
            )
        self.record(second_id, self.now + timedelta(minutes=66), success=True, latency_ms=42)

        public_status = self.store.get_public_status(15, self.now + timedelta(minutes=67))
        public_provider = self.store.get_public_provider(
            "provider-alpha",
            15,
            self.now + timedelta(minutes=67),
        )

        self.assertEqual(public_status, [public_provider])
        assert public_provider is not None
        self.assertEqual(
            set(public_provider),
            {
                "provider_id",
                "name",
                "base_url",
                "probe_mode",
                "state",
                "availability",
                "latest_latency",
                "last_checked",
                "next_check",
                "model_count",
                "display_models",
                "models",
                "history",
            },
        )
        self.assertEqual(public_provider["provider_id"], "provider-alpha")
        self.assertEqual(public_provider["model_count"], 2)
        self.assertEqual(public_provider["latest_latency"], 42)
        self.assertEqual(len(public_provider["history"]), 60)
        self.assertEqual(
            public_provider["history"][0]["recorded_at"],
            (self.now + timedelta(minutes=66)).isoformat(),
        )
        self.assertEqual(
            set(public_provider["models"][0]),
            {
                "model",
                "state",
                "availability",
                "latest_latency",
                "last_checked",
                "next_check",
                "error_code",
                "error_summary",
                "consecutive_successes",
                "last_success_at",
                "history",
            },
        )
        models = {item["model"]: item for item in public_provider["models"]}
        self.assertEqual(len(models["model-a"]["history"]), 60)
        self.assertEqual(
            models["model-a"]["history"][0],
            {
                "recorded_at": (self.now + timedelta(minutes=64)).isoformat(),
                "state": "recovering",
                "error_code": None,
            },
        )
        self.assertEqual(
            models["model-a"]["history"][1]["error_code"],
            "rate_limited",
        )
        self.assertEqual(
            models["model-a"]["history"][-1]["recorded_at"],
            (self.now + timedelta(minutes=5)).isoformat(),
        )
        self.assertEqual(
            models["model-b"]["history"],
            [
                {
                    "recorded_at": (self.now + timedelta(minutes=66)).isoformat(),
                    "state": "healthy",
                    "error_code": None,
                }
            ],
        )
        serialized = json.dumps(public_status)
        for forbidden in (
            "credential",
            "database_path",
            str(self.database_path),
            "raw_response",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_public_model_includes_internal_sort_signals(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target_id = self.rows("SELECT id FROM probe_targets")[0]["id"]
        succeeded_at = self.now + timedelta(minutes=1)
        self.record(target_id, succeeded_at, success=True)
        self.record(
            target_id,
            self.now + timedelta(minutes=2),
            success=False,
            error_code="network_error",
        )

        provider = self.store.get_public_provider(
            "provider-alpha",
            7,
            self.now + timedelta(minutes=3),
        )

        assert provider is not None
        model = provider["models"][0]
        self.assertEqual(model["consecutive_successes"], 0)
        self.assertEqual(model["last_success_at"], succeeded_at.isoformat())

    def test_preserves_diagnostic_error_codes_in_public_status(self) -> None:
        self.store.initialize((make_provider(models=("model-a",)),), self.now)
        target = self.store.list_due_targets(self.now)[0]

        self.record(
            target.id,
            self.now,
            success=False,
            error_code="upstream_unavailable",
            error_summary="HTTP 503；上游服务暂时不可用。",
        )
        first = self.store.get_public_status(1, self.now)[0]["models"][0]
        self.assertEqual(first["error_code"], "upstream_unavailable")
        self.assertEqual(
            first["history"][0]["error_code"], "upstream_unavailable"
        )

        self.record(
            target.id,
            self.now + timedelta(minutes=2),
            success=False,
            error_code="model_unavailable",
            error_summary="供应商未开放该模型。",
        )
        second = self.store.get_public_status(
            1,
            self.now + timedelta(minutes=2),
        )[0]["models"][0]
        self.assertEqual(second["error_code"], "model_unavailable")
        self.assertEqual(
            second["history"][0]["error_code"], "model_unavailable"
        )

    def test_provider_availability_uses_worst_known_model(self) -> None:
        start = self.now - timedelta(hours=10)
        self.store.initialize((make_provider(models=("model-a", "model-b")),), start)
        targets = self.rows("SELECT id, model FROM probe_targets ORDER BY id")
        first_id = targets[0]["id"]
        second_id = targets[1]["id"]
        self.record(first_id, start, success=True)
        self.record(second_id, start, success=True)
        self.record(second_id, start + timedelta(hours=5), success=False)

        public = self.store.get_public_provider("provider-alpha", 7, self.now)

        assert public is not None
        model_availability = {
            item["model"]: item["availability"] for item in public["models"]
        }
        self.assertAlmostEqual(model_availability["model-a"], 100.0)
        self.assertAlmostEqual(model_availability["model-b"], 50.0)
        self.assertAlmostEqual(public["availability"], 50.0)

    def test_delete_history_before_removes_only_older_runs_and_snapshots(self) -> None:
        old_at = self.now - timedelta(days=91)
        recent_at = self.now - timedelta(days=89)
        cutoff = self.now - timedelta(days=90)
        self.store.initialize((make_provider(models=("model-a",)),), old_at)
        target_id = self.store.list_due_targets(old_at)[0].id
        self.record(target_id, old_at, success=True)
        self.record(target_id, recent_at, success=False)

        self.store.delete_history_before(cutoff)

        runs = self.rows("SELECT finished_at FROM probe_runs ORDER BY finished_at")
        snapshots = self.rows(
            "SELECT recorded_at FROM provider_snapshots ORDER BY recorded_at"
        )
        self.assertEqual([row["finished_at"] for row in runs], [recent_at.isoformat()])
        self.assertEqual(
            [row["recorded_at"] for row in snapshots],
            [recent_at.isoformat()],
        )

    def test_unknown_provider_and_invalid_windows_are_handled(self) -> None:
        self.store.initialize((make_provider(),), self.now)

        self.assertIsNone(self.store.get_public_provider("missing", 7, self.now))
        for window in (0, 0.5, 14, 31):
            with self.subTest(window=window):
                with self.assertRaises(ValueError):
                    self.store.get_public_status(window, self.now)
                with self.assertRaises(ValueError):
                    self.store.get_public_provider("provider-alpha", window, self.now)

        with self.assertRaises(KeyError):
            self.record(999, self.now, success=True)


if __name__ == "__main__":
    unittest.main()
