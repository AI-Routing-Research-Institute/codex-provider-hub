from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def create_database(path: Path) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as connection:
        connection.executescript(
            """
            CREATE TABLE providers (
                id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                name TEXT NOT NULL,
                settings_config TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}',
                is_current INTEGER NOT NULL DEFAULT 0,
                sort_index INTEGER,
                created_at INTEGER,
                PRIMARY KEY (id, app_type)
            );
            CREATE TABLE provider_endpoints (
                provider_id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                url TEXT
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        config = {
            "config": (
                'model_provider = "fixture"\n'
                '[model_providers.fixture]\n'
                'base_url = "https://fixture.example.invalid/v1"\n'
                'wire_api = "responses"\n'
            ),
            "auth": {},
        }
        connection.execute(
            """
            INSERT INTO providers (
                id, app_type, name, settings_config, meta,
                is_current, sort_index, created_at
            ) VALUES (?, 'codex', ?, ?, '{}', 1, 0, 0)
            """,
            ("release-smoke", "Release Smoke Test", json.dumps(config)),
        )
        connection.execute(
            "INSERT INTO settings (key, value) VALUES ('common_config_codex', '')"
        )
        connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    create_database(args.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
