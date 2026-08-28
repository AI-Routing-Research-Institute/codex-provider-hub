import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from local_proxy.core import RetryPolicy
from local_proxy.shared_settings import (
    SharedRuntimeCoordinator,
    SharedSettingsStore,
    load_protocol_settings,
    load_shared_settings,
    migrate_runtime_data,
    protocol_settings_path,
    protocol_usage_database_path,
    save_protocol_settings,
    shared_settings_path,
)


def write_marker_database(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()


def read_marker_database(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT value FROM marker").fetchone()[0]


class FakeProfile:
    def __init__(self, service_id: str, *, providers: tuple[str, ...] | None = None) -> None:
        self.service_id = service_id
        self.loaded_providers = providers if providers is not None else (f"{service_id}-a",)
        self.applied: list[tuple[Path, tuple[str, ...]]] = []
        self.retry_policy_store = None
        self.health_status_url_store = None
        self.on_retry_policy_changed = None
        self.runtime_settings_snapshot = None
        self.on_runtime_settings_changed = None
        self.validate_runtime_database = None
        self.load_runtime_database = self._load_runtime_database
        self.apply_runtime_database = self._apply_runtime_database
        self.database_validation_summary = lambda loaded: {
            "provider_count": len(loaded),
        }
        self.runtime_metadata = lambda: {
            "settings_file": f"~/.codex-local-proxy/{service_id}-settings.json",
        }

    def _load_runtime_database(self, path: Path) -> tuple[str, ...]:
        return self.loaded_providers

    def _apply_runtime_database(
        self,
        path: Path,
        providers: tuple[str, ...],
    ) -> None:
        self.applied.append((path, providers))


class SharedRuntimeCoordinatorTests(unittest.TestCase):
    def test_changes_from_either_profile_are_shared_and_refresh_both(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "cc-switch.db"
            database.touch()
            store = SharedSettingsStore(
                path=shared_settings_path(root),
                settings={
                    "port": 17890,
                    "database_path": str(database),
                    "retry": {},
                    "health_status_url": None,
                },
            )
            codex = FakeProfile("codex")
            claude = FakeProfile("claude", providers=())
            SharedRuntimeCoordinator(store, (codex, claude), active_port=17890)

            codex_result = codex.on_runtime_settings_changed(
                {
                    "port": 18888,
                    "database_path": str(database),
                    "health_status_url": "https://status.example.test/api/status",
                }
            )

            self.assertTrue(codex_result["restart_required"])
            self.assertEqual(claude.runtime_settings_snapshot()["configured_port"], 18888)
            self.assertEqual(
                claude.runtime_settings_snapshot()["health_status_url"],
                "https://status.example.test/api/status",
            )
            self.assertEqual(codex.applied, [(database.resolve(), ("codex-a",))])
            self.assertEqual(claude.applied, [(database.resolve(), ())])
            self.assertIs(codex.retry_policy_store, claude.retry_policy_store)
            self.assertIs(codex.health_status_url_store, claude.health_status_url_store)

            claude.on_runtime_settings_changed(
                {
                    "port": 19999,
                    "database_path": str(database),
                    "health_status_url": None,
                }
            )
            self.assertEqual(codex.runtime_settings_snapshot()["configured_port"], 19999)
            self.assertEqual(len(codex.applied), 2)
            self.assertEqual(len(claude.applied), 2)

            policy = RetryPolicy(max_attempts=9, strategy="fixed")
            claude.on_retry_policy_changed(policy)
            self.assertEqual(codex.retry_policy_store.get(), policy)
            persisted = load_shared_settings(shared_settings_path(root))
            self.assertEqual(persisted["port"], 19999)
            self.assertEqual(persisted["retry"]["max_attempts"], 9)

    def test_database_validation_accepts_a_protocol_with_no_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "cc-switch.db"
            database.touch()
            store = SharedSettingsStore(path=shared_settings_path(root))
            codex = FakeProfile("codex")
            claude = FakeProfile("claude", providers=())
            SharedRuntimeCoordinator(store, (codex, claude), active_port=17890)

            result = claude.validate_runtime_database(str(database))

            self.assertEqual(result["provider_count"], 0)
            self.assertEqual(result["protocols"]["codex"]["provider_count"], 1)
            self.assertEqual(result["protocols"]["claude"]["provider_count"], 0)

    def test_database_load_failure_does_not_change_shared_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "original.db"
            candidate = root / "candidate.db"
            original.touch()
            candidate.touch()
            store = SharedSettingsStore(
                path=shared_settings_path(root),
                settings={"port": 17890, "database_path": str(original)},
            )
            codex = FakeProfile("codex")
            claude = FakeProfile("claude")

            def reject_database(path: Path) -> tuple[str, ...]:
                raise sqlite3.DatabaseError("invalid")

            claude.load_runtime_database = reject_database
            SharedRuntimeCoordinator(store, (codex, claude), active_port=17890)

            with self.assertRaisesRegex(ValueError, "数据库结构不兼容"):
                codex.on_runtime_settings_changed(
                    {
                        "port": 18888,
                        "database_path": str(candidate),
                        "health_status_url": None,
                    }
                )

            self.assertEqual(store.snapshot()["port"], 17890)
            self.assertEqual(codex.applied, [])
            self.assertEqual(claude.applied, [])


class RuntimeMigrationTests(unittest.TestCase):
    def test_protocol_button_visibility_defaults_on_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = protocol_settings_path("codex", Path(temp_dir))

            self.assertTrue(load_protocol_settings(path)["show_provider_launch_command"])
            self.assertTrue(load_protocol_settings(path)["show_status_upload"])
            save_protocol_settings(
                {
                    "show_provider_launch_command": False,
                    "show_status_upload": False,
                },
                path,
            )

            self.assertFalse(load_protocol_settings(path)["show_provider_launch_command"])
            self.assertFalse(load_protocol_settings(path)["show_status_upload"])

    def test_migrates_both_protocols_with_codex_shared_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / ".codex-local-proxy"
            claude_legacy = root / ".claude-local-proxy"
            target.mkdir()
            claude_legacy.mkdir()
            (target / "settings.json").write_text(
                json.dumps(
                    {
                        "port": 18888,
                        "database_path": "~/codex.db",
                        "retry": {"max_attempts": 8},
                        "health_status_url": "https://codex-status.example.test/api",
                        "selected_provider_id": "codex-a",
                        "provider_order": ["codex-b", "codex-a"],
                        "hidden_provider_ids": ["codex-c"],
                    }
                ),
                encoding="utf-8",
            )
            (claude_legacy / "settings.json").write_text(
                json.dumps(
                    {
                        "port": 17891,
                        "database_path": "~/claude.db",
                        "retry": {"max_attempts": 2},
                        "health_status_url": "https://claude-status.example.test/api",
                        "selected_provider_id": "claude-a",
                        "provider_order": ["claude-a"],
                    }
                ),
                encoding="utf-8",
            )
            write_marker_database(target / "usage.sqlite3", "codex")
            write_marker_database(claude_legacy / "usage.sqlite3", "claude")

            migrated = migrate_runtime_data(
                target=target,
                codex_source=target,
                claude_source=claude_legacy,
            )

            self.assertEqual(
                set(migrated),
                {
                    "shared-settings.json",
                    "codex-settings.json",
                    "claude-settings.json",
                    "codex-usage.sqlite3",
                    "claude-usage.sqlite3",
                },
            )
            shared = load_shared_settings(shared_settings_path(target))
            self.assertEqual(shared["port"], 18888)
            self.assertTrue(shared["database_path"].endswith("codex.db"))
            self.assertEqual(shared["retry"]["max_attempts"], 8)
            self.assertEqual(
                shared["health_status_url"],
                "https://codex-status.example.test/api",
            )
            codex = load_protocol_settings(protocol_settings_path("codex", target))
            claude = load_protocol_settings(protocol_settings_path("claude", target))
            self.assertEqual(codex["selected_provider_id"], "codex-a")
            self.assertEqual(codex["provider_order"], ["codex-b", "codex-a"])
            self.assertEqual(codex["hidden_provider_ids"], ["codex-c"])
            self.assertEqual(claude["selected_provider_id"], "claude-a")
            self.assertEqual(read_marker_database(protocol_usage_database_path("codex", target)), "codex")
            self.assertEqual(read_marker_database(protocol_usage_database_path("claude", target)), "claude")
            self.assertFalse((target / "settings.json").exists())
            self.assertFalse((target / "usage.sqlite3").exists())
            self.assertFalse((target / "legacy").exists())
            self.assertFalse((claude_legacy / "settings.json").exists())
            self.assertFalse((claude_legacy / "usage.sqlite3").exists())
            self.assertFalse(claude_legacy.exists())

            before = {
                path.name: path.read_bytes()
                for path in target.iterdir()
                if path.is_file()
            }
            repeated = migrate_runtime_data(
                target=target,
                codex_source=target,
                claude_source=claude_legacy,
            )
            after = {
                path.name: path.read_bytes()
                for path in target.iterdir()
                if path.is_file()
            }
            self.assertEqual(repeated, ())
            self.assertEqual(after, before)

    def test_existing_targets_are_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / ".codex-local-proxy"
            claude_legacy = root / ".claude-local-proxy"
            target.mkdir()
            claude_legacy.mkdir()
            (target / "settings.json").write_text('{"port": 18888}', encoding="utf-8")
            (claude_legacy / "settings.json").write_text('{"port": 17891}', encoding="utf-8")
            write_marker_database(target / "usage.sqlite3", "old-codex")
            write_marker_database(claude_legacy / "usage.sqlite3", "old-claude")
            shared_settings_path(target).write_text('{"schema_version": 1, "port": 19999}', encoding="utf-8")
            protocol_settings_path("codex", target).write_text('{"schema_version": 1, "selected_provider_id": "new-codex"}', encoding="utf-8")
            protocol_settings_path("claude", target).write_text('{"schema_version": 1, "selected_provider_id": "new-claude"}', encoding="utf-8")
            write_marker_database(protocol_usage_database_path("codex", target), "new-codex")
            write_marker_database(protocol_usage_database_path("claude", target), "new-claude")

            migrated = migrate_runtime_data(
                target=target,
                codex_source=target,
                claude_source=claude_legacy,
            )

            self.assertEqual(migrated, ())
            self.assertEqual(load_shared_settings(shared_settings_path(target))["port"], 19999)
            self.assertEqual(
                load_protocol_settings(protocol_settings_path("codex", target))["selected_provider_id"],
                "new-codex",
            )
            self.assertEqual(
                load_protocol_settings(protocol_settings_path("claude", target))["selected_provider_id"],
                "new-claude",
            )
            self.assertEqual(read_marker_database(protocol_usage_database_path("codex", target)), "new-codex")
            self.assertEqual(read_marker_database(protocol_usage_database_path("claude", target)), "new-claude")


if __name__ == "__main__":
    unittest.main()
