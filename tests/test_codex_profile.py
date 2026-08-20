import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_proxy import codex_profile


class CodexProfileTests(unittest.TestCase):
    def test_build_profile_uses_shared_curl_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "cc-switch.db"
            with (
                mock.patch.object(codex_profile, "load_local_proxy_providers", return_value=()),
                mock.patch.object(codex_profile, "CurlClient") as client_class,
            ):
                profile = codex_profile.build_codex_profile(
                    database=database,
                    port=17890,
                    data_root=Path(temp_dir) / "codex-data",
                )

        self.assertEqual(profile.service_id, "codex")
        client_class.assert_called_once_with(
            connect_timeout_seconds=30.0,
            idle_timeout_seconds=300.0,
        )


if __name__ == "__main__":
    unittest.main()
