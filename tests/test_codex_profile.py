import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_proxy import codex_profile
from local_proxy.core import ProxyProvider


class CodexProfileTests(unittest.TestCase):
    def test_build_profile_selects_transport_per_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "cc-switch.db"
            standard_client = mock.Mock(name="httpx_client")
            compatible_client = mock.Mock(name="curl_client")
            with (
                mock.patch.object(codex_profile, "load_local_proxy_providers", return_value=()),
                mock.patch.object(
                    codex_profile.httpx,
                    "AsyncClient",
                    return_value=standard_client,
                ) as httpx_client_class,
                mock.patch.object(
                    codex_profile,
                    "CurlClient",
                    return_value=compatible_client,
                ) as curl_client_class,
            ):
                profile = codex_profile.build_codex_profile(
                    database=database,
                    port=17890,
                    data_root=Path(temp_dir) / "codex-data",
                )

        self.assertEqual(profile.service_id, "codex")
        self.assertIs(profile.upstream_client, standard_client)
        self.assertEqual(profile.additional_owned_clients, (compatible_client,))
        httpx_client_class.assert_called_once()
        curl_client_class.assert_called_once_with(
            connect_timeout_seconds=30.0,
            idle_timeout_seconds=300.0,
        )
        standard_provider = ProxyProvider(
            provider_id="standard",
            name="Standard",
            base_url="https://standard.example.test/v1",
            is_cc_switch_current=True,
        )
        compatible_provider = ProxyProvider(
            provider_id="compatible",
            name="Compatible",
            base_url="https://compatible.example.test/v1",
            is_cc_switch_current=False,
            transport="curl_cffi",
        )
        self.assertIs(profile.client_selector(standard_provider), standard_client)
        self.assertIs(profile.client_selector(compatible_provider), compatible_client)


if __name__ == "__main__":
    unittest.main()
