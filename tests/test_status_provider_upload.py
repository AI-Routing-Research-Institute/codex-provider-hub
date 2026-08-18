from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_proxy.core import ProxyProvider
from local_proxy.status_upload import (
    build_provider_upload_payload,
    StatusUploadManager,
    load_settings,
    provider_upload_preview,
)
import scripts.status_provider_import as status_import
from provider_status.config import load_config
from scripts.status_provider_import import (
    ImportErrorDetail,
    import_provider_fragment,
)


class StatusProviderUploadTests(unittest.TestCase):
    def test_bootstrap_persists_key_and_fingerprint_without_password(self) -> None:
        transports = []

        class FakeTransport:
            def __init__(self, settings, password):
                self.settings = settings
                self.password = password
                self.uploaded = None
                transports.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def bootstrap(self, public_key):
                self.public_key = public_key
                return "SHA256:test-host"

            def upload(self, payload):
                self.uploaded = payload
                return {"status": "imported"}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status-upload.json"
            manager = StatusUploadManager(path=path, ssh_factory=FakeTransport)
            with mock.patch(
                "local_proxy.status_upload._generate_keypair",
                return_value=("ssh-ed25519 public", "private-key"),
            ):
                public = manager.bootstrap("status.example", 2222, "ubuntu", "server-password")
            saved = path.read_text(encoding="utf-8")
            settings = load_settings(path)
            result = manager.upload({"provider_id": "alpha"})

        self.assertTrue(public["initialized"])
        self.assertTrue(settings.initialized)
        self.assertEqual(settings.host_key, "SHA256:test-host")
        self.assertNotIn("server-password", saved)
        self.assertEqual(transports[0].password, "server-password")
        self.assertIsNone(transports[1].password)
        self.assertEqual(result["status"], "imported")

    def test_preview_suggests_models_and_rejects_custom_headers(self) -> None:
        provider = ProxyProvider(
            provider_id="alpha",
            name="Alpha",
            base_url="https://alpha.example/v1",
            is_cc_switch_current=False,
            api_key="secret",
        )

        preview = provider_upload_preview(provider, "codex", ("gpt-5.5",))

        self.assertTrue(preview["supported"])
        self.assertEqual(preview["suggested_models"], ["gpt-5.5"])
        payload = build_provider_upload_payload(provider, "codex", ["gpt-5.5"])
        self.assertEqual(payload["provider_id"], "alpha")
        self.assertEqual(payload["credential"], "secret")
        self.assertNotIn("api_key", payload)

        custom = ProxyProvider(
            provider_id="custom",
            name="Custom",
            base_url="https://custom.example/v1",
            is_cc_switch_current=False,
            configured_headers={"X-Provider-Key": "secret"},
        )
        self.assertFalse(provider_upload_preview(custom, "codex", ())["supported"])

    def test_importer_rejects_duplicate_provider_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "providers.toml"
            config.write_text(
                """[service]
database_path = "private.sqlite3"
public_database_path = "public.sqlite3"
temp_root = "tmp"
codex_bin = "codex"

[[providers]]
id = "alpha"
name = "Existing"
base_url = "https://existing.example/v1"
credential_name = "existing.key"
models = ["gpt-5.5"]
healthy_interval_seconds = 600
unhealthy_interval_seconds = 120
timeout_seconds = 90
""",
                encoding="utf-8",
            )
            payload = {
                "provider_id": "alpha",
                "name": "Replacement",
                "base_url": "https://replacement.example/v1",
                "protocol": "codex",
                "models": ["gpt-5.5"],
                "credential_kind": "api_key",
                "credential": "new-secret",
            }

            with self.assertRaises(ImportErrorDetail) as captured:
                import_provider_fragment(payload, config_path=config, secret_root=root / "secrets")

            self.assertIn("已存在", str(captured.exception))
            self.assertFalse((root / "secrets").exists())
            self.assertEqual(load_config(config).providers[0].name, "Existing")

    def test_restart_failure_rolls_back_imported_files_and_dropin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "providers.toml"
            config.write_text(
                """[service]
database_path = "private.sqlite3"
public_database_path = "public.sqlite3"
temp_root = "tmp"
codex_bin = "codex"

[[providers]]
id = "existing"
name = "Existing"
base_url = "https://existing.example/v1"
credential_name = "existing.key"
models = ["gpt-5.5"]
healthy_interval_seconds = 600
unhealthy_interval_seconds = 120
timeout_seconds = 90
""",
                encoding="utf-8",
            )
            fragments = root / "providers.d"
            secrets = root / "secrets"
            dropin = root / "90-imported-providers.conf"
            payload = {
                "provider_id": "new-provider",
                "name": "New Provider",
                "base_url": "https://new.example/v1",
                "protocol": "codex",
                "models": ["gpt-5.5"],
                "credential_kind": "api_key",
                "credential": "new-secret",
            }
            calls = [
                mock.DEFAULT,
                status_import.subprocess.CalledProcessError(1, "systemctl"),
                mock.DEFAULT,
            ]
            with (
                mock.patch.object(status_import, "CONFIG_PATH", config),
                mock.patch.object(status_import, "FRAGMENT_ROOT", fragments),
                mock.patch.object(status_import, "SECRET_ROOT", secrets),
                mock.patch.object(status_import, "DROPIN_PATH", dropin),
                mock.patch.object(status_import.subprocess, "run", side_effect=calls),
            ):
                with self.assertRaises(status_import.subprocess.CalledProcessError):
                    status_import._import_and_restart(payload)

            self.assertFalse((fragments / "new-provider.toml").exists())
            self.assertFalse(dropin.exists())
            self.assertEqual(list(secrets.glob("*")), [])


if __name__ == "__main__":
    unittest.main()
