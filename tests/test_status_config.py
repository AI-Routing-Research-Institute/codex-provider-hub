import os
import socket
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from provider_status.config import load_config, read_credential


PUBLIC_ADDRINFO = [
    (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        ("8.8.8.8", 443),
    ),
    (
        socket.AF_INET6,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        ("2606:4700:4700::1111", 443, 0, 0),
    ),
]


def public_resolver(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
    del host, port, args, kwargs
    return PUBLIC_ADDRINFO.copy()


def provider_block(
    *,
    provider_id: str = "provider-alpha",
    base_url: str = "https://alpha.example.com/v1",
    models: tuple[str, ...] = ("gpt-5.6-sol", "gpt-5.5"),
    display_models: tuple[str, ...] | None = None,
    healthy_interval_seconds: int = 300,
    healthy_interval_max_seconds: int | None = None,
    unhealthy_interval_seconds: int = 60,
    unhealthy_interval_max_seconds: int | None = None,
    timeout_seconds: int = 90,
    probe_mode: str | None = None,
) -> str:
    encoded_models = ", ".join(f'"{model}"' for model in models)
    encoded_display_models = (
        ""
        if display_models is None
        else "display_models = ["
        + ", ".join(f'"{model}"' for model in display_models)
        + "]\n"
    )
    healthy_max = (
        ""
        if healthy_interval_max_seconds is None
        else f"healthy_interval_max_seconds = {healthy_interval_max_seconds}\n"
    )
    unhealthy_max = (
        ""
        if unhealthy_interval_max_seconds is None
        else f"unhealthy_interval_max_seconds = {unhealthy_interval_max_seconds}\n"
    )
    probe_mode_line = "" if probe_mode is None else f'probe_mode = "{probe_mode}"\n'
    return f"""
[[providers]]
id = "{provider_id}"
name = "Provider Alpha"
base_url = "{base_url}"
credential_name = "provider-alpha-api-key"
{probe_mode_line}models = [{encoded_models}]
{encoded_display_models}healthy_interval_seconds = {healthy_interval_seconds}
{healthy_max}unhealthy_interval_seconds = {unhealthy_interval_seconds}
{unhealthy_max}timeout_seconds = {timeout_seconds}
"""


def service_config(*provider_blocks: str) -> str:
    return """
[service]
database_path = "var/private/provider-status.sqlite3"
public_database_path = "var/public/provider-status.sqlite3"
temp_root = "var/private/provider-status-tmp"
codex_bin = "C:/Users/tester/AppData/Roaming/npm/codex.cmd"
""" + "".join(provider_blocks)


class StatusConfigTests(unittest.TestCase):
    def load_text(self, text: str, *, resolver=public_resolver):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "providers.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path, resolver=resolver)

    def test_load_config_accepts_public_ly_free_endpoint_and_exact_models(self) -> None:
        config = self.load_text(service_config(provider_block()))

        self.assertEqual(
            config.database_path,
            Path("var/private/provider-status.sqlite3"),
        )
        self.assertEqual(
            config.public_database_path,
            Path("var/public/provider-status.sqlite3"),
        )
        self.assertEqual(config.temp_root, Path("var/private/provider-status-tmp"))
        self.assertEqual(
            config.codex_bin,
            Path("C:/Users/tester/AppData/Roaming/npm/codex.cmd"),
        )
        self.assertEqual(len(config.providers), 1)
        provider = config.providers[0]
        self.assertEqual(provider.provider_id, "provider-alpha")
        self.assertEqual(provider.base_url, "https://alpha.example.com/v1")
        self.assertEqual(provider.models, ("gpt-5.6-sol", "gpt-5.5"))
        self.assertEqual(provider.display_models, provider.models)
        self.assertEqual(provider.probe_mode, "automatic")

    def test_accepts_manual_only_probe_mode_and_rejects_unknown_mode(self) -> None:
        provider = self.load_text(
            service_config(provider_block(probe_mode="manual_only"))
        ).providers[0]

        self.assertEqual(provider.probe_mode, "manual_only")
        with self.assertRaisesRegex(ValueError, "probe_mode"):
            self.load_text(
                service_config(provider_block(probe_mode="sometimes"))
            )

    def test_load_config_accepts_unmonitored_display_model(self) -> None:
        config = self.load_text(
            service_config(
                provider_block(
                    models=("gpt-5.6-sol",),
                    display_models=("gpt-5.6-sol", "gpt-5.5"),
                )
            )
        )

        provider = config.providers[0]
        self.assertEqual(provider.models, ("gpt-5.6-sol",))
        self.assertEqual(
            provider.display_models,
            ("gpt-5.6-sol", "gpt-5.5"),
        )

    def test_rejects_invalid_display_models(self) -> None:
        invalid_blocks = (
            provider_block(display_models=()),
            provider_block(display_models=("gpt-5.6-sol", "")),
            provider_block(
                display_models=("gpt-5.6-sol", "gpt-5.6-sol", "gpt-5.5")
            ),
            provider_block(
                models=("gpt-5.6-sol", "gpt-5.5"),
                display_models=("gpt-5.6-sol",),
            ),
        )

        for block in invalid_blocks:
            with self.subTest(block=block):
                with self.assertRaisesRegex(ValueError, "display_models"):
                    self.load_text(service_config(block))

    def test_loaded_config_records_are_frozen(self) -> None:
        config = self.load_text(service_config(provider_block()))

        with self.assertRaises(FrozenInstanceError):
            config.database_path = Path("other.sqlite3")
        with self.assertRaises(FrozenInstanceError):
            config.providers[0].name = "Other"

    def test_rejects_same_private_and_public_database_path(self) -> None:
        text = service_config(provider_block()).replace(
            'public_database_path = "var/public/provider-status.sqlite3"',
            'public_database_path = "var/private/provider-status.sqlite3"',
        )

        with self.assertRaisesRegex(ValueError, "database paths must differ"):
            self.load_text(text)

    def test_rejects_private_http_endpoint_with_public_https_message(self) -> None:
        text = service_config(
            provider_block(base_url="http://192.168.47.130:8317/v1")
        )

        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            self.load_text(text)

    def test_rejects_http_endpoint_even_when_dns_is_public(self) -> None:
        text = service_config(provider_block(base_url="http://example.com/v1"))

        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            self.load_text(text)

    def test_rejects_localhost_even_when_resolver_claims_it_is_public(self) -> None:
        text = service_config(provider_block(base_url="https://localhost/v1"))

        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            self.load_text(text)

    def test_rejects_private_and_link_local_ip_literals(self) -> None:
        invalid_urls = (
            "https://10.0.0.1/v1",
            "https://169.254.10.20/v1",
            "https://[fd00::1]/v1",
            "https://[fe80::1]/v1",
        )

        for base_url in invalid_urls:
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(ValueError, "public HTTPS"):
                    self.load_text(service_config(provider_block(base_url=base_url)))

    def test_rejects_dns_answer_if_any_address_is_not_globally_routable(self) -> None:
        def rebinding_resolver(
            host: str, port: int, *args: object, **kwargs: object
        ) -> list[tuple]:
            del host, port, args, kwargs
            return PUBLIC_ADDRINFO[:1] + [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("192.168.47.130", 443),
                )
            ]

        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            self.load_text(
                service_config(provider_block()),
                resolver=rebinding_resolver,
            )

    def test_rejects_duplicate_provider_id(self) -> None:
        text = service_config(
            provider_block(),
            provider_block(provider_id="provider-alpha", base_url="https://example.com/v1"),
        )

        with self.assertRaisesRegex(ValueError, "duplicate provider id"):
            self.load_text(text)

    def test_rejects_duplicate_models(self) -> None:
        text = service_config(
            provider_block(models=("gpt-5.6-sol", "gpt-5.6-sol"))
        )

        with self.assertRaisesRegex(ValueError, "duplicate model"):
            self.load_text(text)

    def test_rejects_empty_model(self) -> None:
        text = service_config(provider_block(models=("gpt-5.6-sol", "")))

        with self.assertRaisesRegex(ValueError, "model.*empty"):
            self.load_text(text)

    def test_rejects_empty_model_list(self) -> None:
        text = service_config(provider_block(models=()))

        with self.assertRaisesRegex(ValueError, "models must not be empty"):
            self.load_text(text)

    def test_rejects_non_positive_intervals(self) -> None:
        invalid_blocks = (
            provider_block(healthy_interval_seconds=0),
            provider_block(healthy_interval_max_seconds=0),
            provider_block(unhealthy_interval_seconds=-1),
            provider_block(unhealthy_interval_max_seconds=-1),
            provider_block(timeout_seconds=0),
        )

        for block in invalid_blocks:
            with self.subTest(block=block):
                with self.assertRaisesRegex(ValueError, "must be positive"):
                    self.load_text(service_config(block))

    def test_loads_interval_ranges_and_defaults_missing_maxima_to_minima(self) -> None:
        ranged = self.load_text(
            service_config(
                provider_block(
                    healthy_interval_seconds=600,
                    healthy_interval_max_seconds=1200,
                    unhealthy_interval_seconds=120,
                    unhealthy_interval_max_seconds=300,
                )
            )
        ).providers[0]
        fixed = self.load_text(service_config(provider_block())).providers[0]

        self.assertEqual(ranged.healthy_interval_max_seconds, 1200)
        self.assertEqual(ranged.unhealthy_interval_max_seconds, 300)
        self.assertEqual(
            fixed.healthy_interval_max_seconds,
            fixed.healthy_interval_seconds,
        )
        self.assertEqual(
            fixed.unhealthy_interval_max_seconds,
            fixed.unhealthy_interval_seconds,
        )

    def test_rejects_interval_maximum_below_minimum(self) -> None:
        invalid_blocks = (
            provider_block(
                healthy_interval_seconds=600,
                healthy_interval_max_seconds=599,
            ),
            provider_block(
                unhealthy_interval_seconds=120,
                unhealthy_interval_max_seconds=119,
            ),
        )

        for block in invalid_blocks:
            with self.subTest(block=block):
                with self.assertRaisesRegex(ValueError, "must be at least"):
                    self.load_text(service_config(block))

    def test_rejects_timeout_longer_than_systemd_cleanup_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most 90 seconds"):
            self.load_text(
                service_config(provider_block(timeout_seconds=91))
            )

    def test_rejects_endpoint_credentials_query_and_fragment(self) -> None:
        invalid_urls = (
            "https://user:password@alpha.example.com/v1",
            "https://alpha.example.com/v1?debug=1",
            "https://alpha.example.com/v1#fragment",
            "https://alpha.example.com/v1?",
            "https://alpha.example.com/v1#",
            "https://alpha.example.com/v1?#",
        )

        for base_url in invalid_urls:
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(ValueError, "public HTTPS"):
                    self.load_text(service_config(provider_block(base_url=base_url)))

    def test_example_matches_ubuntu_deployment_defaults(self) -> None:
        example_path = Path(__file__).parents[1] / "config" / "providers.example.toml"
        config = load_config(example_path, resolver=public_resolver)

        self.assertEqual(
            [provider.provider_id for provider in config.providers],
            [
                "provider-primary",
                "provider-alpha",
                "provider-beta",
                "provider-gamma",
                "provider-delta",
                "provider-epsilon",
                "provider-zeta",
            ],
        )
        self.assertEqual(
            config.codex_bin,
            Path("/opt/codex-provider-probe/runtime/node_modules/.bin/codex"),
        )
        self.assertEqual(
            config.database_path,
            Path("/var/lib/codex-provider-probe/private/status.sqlite3"),
        )
        self.assertEqual(
            config.public_database_path,
            Path("/var/lib/codex-provider-probe/public/status.sqlite3"),
        )
        self.assertEqual(
            config.temp_root,
            Path("/var/lib/codex-provider-probe/private/tmp"),
        )
        jun_provider = config.providers[0]
        self.assertEqual(jun_provider.name, "Provider Primary")
        self.assertEqual(jun_provider.base_url, "https://primary.example.com/v1")
        self.assertEqual(
            jun_provider.credential_name,
            "provider_primary_api_key",
        )
        self.assertEqual(jun_provider.models, ("gpt-5.6-sol", "gpt-5.5"))
        self.assertEqual(jun_provider.display_models, jun_provider.models)
        self.assertEqual(jun_provider.healthy_interval_seconds, 600)
        self.assertEqual(jun_provider.healthy_interval_max_seconds, 1200)
        self.assertEqual(jun_provider.unhealthy_interval_seconds, 120)
        self.assertEqual(jun_provider.unhealthy_interval_max_seconds, 300)
        self.assertEqual(jun_provider.timeout_seconds, 90)
        self.assertTrue(
            all(provider.probe_mode == "automatic" for provider in config.providers)
        )
        ly_free = config.providers[1]
        self.assertEqual(ly_free.name, "Provider Alpha")
        self.assertEqual(ly_free.base_url, "https://alpha.example.com/v1")
        self.assertEqual(ly_free.credential_name, "provider_alpha_api_key")
        self.assertEqual(ly_free.models, ("gpt-5.6-sol", "gpt-5.5"))
        self.assertEqual(ly_free.display_models, ly_free.models)
        self.assertEqual(ly_free.healthy_interval_seconds, 600)
        self.assertEqual(ly_free.healthy_interval_max_seconds, 1200)
        self.assertEqual(ly_free.unhealthy_interval_seconds, 120)
        self.assertEqual(ly_free.unhealthy_interval_max_seconds, 300)
        self.assertEqual(ly_free.timeout_seconds, 90)
        wuming_welfare = config.providers[2]
        self.assertEqual(wuming_welfare.name, "Provider Beta")
        self.assertEqual(
            wuming_welfare.base_url,
            "https://beta.example.com/v1",
        )
        self.assertEqual(
            wuming_welfare.credential_name,
            "provider_beta_api_key",
        )
        self.assertEqual(wuming_welfare.models, ("gpt-5.6-sol",))
        self.assertEqual(
            wuming_welfare.display_models,
            ("gpt-5.6-sol", "gpt-5.5"),
        )
        self.assertEqual(wuming_welfare.healthy_interval_seconds, 600)
        self.assertEqual(wuming_welfare.healthy_interval_max_seconds, 1200)
        self.assertEqual(wuming_welfare.unhealthy_interval_seconds, 120)
        self.assertEqual(wuming_welfare.unhealthy_interval_max_seconds, 300)
        self.assertEqual(wuming_welfare.timeout_seconds, 90)
        any_router = config.providers[3]
        self.assertEqual(any_router.name, "Provider Gamma")
        self.assertEqual(any_router.base_url, "https://gamma.example.com/v1")
        self.assertEqual(any_router.credential_name, "provider_gamma_api_key")
        self.assertEqual(any_router.models, ("gpt-5.6-sol", "gpt-5.5"))
        self.assertEqual(any_router.display_models, any_router.models)
        self.assertEqual(any_router.healthy_interval_seconds, 600)
        self.assertEqual(any_router.healthy_interval_max_seconds, 1200)
        self.assertEqual(any_router.unhealthy_interval_seconds, 120)
        self.assertEqual(any_router.unhealthy_interval_max_seconds, 300)
        self.assertEqual(any_router.timeout_seconds, 90)
        provider_delta = config.providers[4]
        self.assertEqual(provider_delta.name, "Provider Delta")
        self.assertEqual(provider_delta.base_url, "https://delta.example.com/v1")
        self.assertEqual(provider_delta.credential_name, "provider_delta_api_key")
        self.assertEqual(provider_delta.models, ("gpt-5.6-sol",))
        self.assertEqual(
            provider_delta.display_models,
            ("gpt-5.6-sol", "gpt-5.5"),
        )
        self.assertEqual(provider_delta.healthy_interval_seconds, 600)
        self.assertEqual(provider_delta.healthy_interval_max_seconds, 1200)
        self.assertEqual(provider_delta.unhealthy_interval_seconds, 120)
        self.assertEqual(provider_delta.unhealthy_interval_max_seconds, 300)
        self.assertEqual(provider_delta.timeout_seconds, 90)
        provider_epsilon = config.providers[5]
        self.assertEqual(provider_epsilon.name, "Provider Epsilon")
        self.assertEqual(provider_epsilon.base_url, "https://epsilon.example.com/v1")
        self.assertEqual(provider_epsilon.credential_name, "provider_epsilon_api_key")
        self.assertEqual(provider_epsilon.models, ("gpt-5.6-sol",))
        self.assertEqual(
            provider_epsilon.display_models,
            ("gpt-5.6-sol", "gpt-5.5"),
        )
        foye_api = config.providers[6]
        self.assertEqual(foye_api.name, "Provider Zeta")
        self.assertEqual(foye_api.base_url, "https://zeta.example.com/v1")
        self.assertEqual(foye_api.credential_name, "provider_zeta_api_key")
        self.assertEqual(foye_api.models, ("gpt-5.6-sol",))
        self.assertEqual(
            foye_api.display_models,
            ("gpt-5.6-sol", "gpt-5.5"),
        )
        self.assertNotIn("sk-", example_path.read_text(encoding="utf-8"))

    def test_python_310_tomli_fallback_is_declared(self) -> None:
        project_root = Path(__file__).parents[1]
        backend_source = (project_root / "probe_codex_cc_switch.py").read_text(
            encoding="utf-8"
        )
        config_source = (project_root / "provider_status" / "config.py").read_text(
            encoding="utf-8"
        )
        requirements = (project_root / "requirements-status.txt").read_text(
            encoding="utf-8"
        )

        for source in (backend_source, config_source):
            self.assertIn("except ModuleNotFoundError", source)
            self.assertIn("import tomli as tomllib", source)
        self.assertIn('python_version < "3.11"', requirements)


class CredentialTests(unittest.TestCase):
    def test_reads_only_named_credential_from_credentials_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            credentials_dir = root / "credentials"
            credentials_dir.mkdir()
            (credentials_dir / "provider-alpha-api-key").write_text(
                "secret-value\r\n",
                encoding="utf-8",
            )
            (root / "provider-alpha-api-key").write_text("wrong-value", encoding="utf-8")

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                value = read_credential(
                    "provider-alpha-api-key",
                    {"CREDENTIALS_DIRECTORY": str(credentials_dir)},
                )
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(value, "secret-value")

    def test_rejects_empty_credential(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            credentials_dir = Path(temp_dir)
            (credentials_dir / "empty-key").write_text("\r\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "empty"):
                read_credential(
                    "empty-key",
                    {"CREDENTIALS_DIRECTORY": str(credentials_dir)},
                )

    def test_rejects_credential_name_that_escapes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_names = ("../outside-key", "D:outside-key")
            for name in invalid_names:
                with self.subTest(name=name):
                    with self.assertRaisesRegex(ValueError, "credential name"):
                        read_credential(
                            name,
                            {"CREDENTIALS_DIRECTORY": temp_dir},
                        )


if __name__ == "__main__":
    unittest.main()
