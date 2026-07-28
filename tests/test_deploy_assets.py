import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeployAssetTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_worker_unit_uses_systemd_credential_and_sandbox(self) -> None:
        unit = self.read("deploy/systemd/codex-provider-worker.service")

        self.assertIn(
            "LoadCredential=provider_alpha_api_key:/etc/codex-provider-probe/secrets/provider_alpha_api_key",
            unit,
        )
        self.assertIn(
            "ConditionPathExists=/etc/codex-provider-probe/secrets/provider_primary_api_key",
            unit,
        )
        self.assertIn(
            "LoadCredential=provider_primary_api_key:/etc/codex-provider-probe/secrets/provider_primary_api_key",
            unit,
        )
        self.assertIn(
            "ConditionPathExists=/etc/codex-provider-probe/secrets/provider_gamma_api_key",
            unit,
        )
        self.assertIn(
            "LoadCredential=provider_gamma_api_key:/etc/codex-provider-probe/secrets/provider_gamma_api_key",
            unit,
        )
        self.assertIn(
            "ConditionPathExists=/etc/codex-provider-probe/secrets/provider_beta_api_key",
            unit,
        )
        self.assertIn(
            "LoadCredential=provider_beta_api_key:/etc/codex-provider-probe/secrets/provider_beta_api_key",
            unit,
        )
        self.assertIn(
            "ConditionPathExists=/etc/codex-provider-probe/secrets/provider_delta_api_key",
            unit,
        )
        self.assertIn(
            "LoadCredential=provider_delta_api_key:/etc/codex-provider-probe/secrets/provider_delta_api_key",
            unit,
        )
        self.assertIn(
            "ConditionPathExists=/etc/codex-provider-probe/secrets/provider_epsilon_api_key",
            unit,
        )
        self.assertIn(
            "LoadCredential=provider_epsilon_api_key:/etc/codex-provider-probe/secrets/provider_epsilon_api_key",
            unit,
        )
        self.assertIn(
            "ConditionPathExists=/etc/codex-provider-probe/secrets/provider_zeta_api_key",
            unit,
        )
        self.assertIn(
            "LoadCredential=provider_zeta_api_key:/etc/codex-provider-probe/secrets/provider_zeta_api_key",
            unit,
        )
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("TimeoutStopSec=110s", unit)
        self.assertIn("User=codex-provider", unit)
        self.assertIn(
            "ReadWritePaths=/var/lib/codex-provider-probe/private /var/lib/codex-provider-probe/public",
            unit,
        )
        self.assertIn("SupplementaryGroups=codex-provider-control", unit)
        self.assertIn("/var/lib/codex-provider-probe/control", unit)
        self.assertIn("UMask=0007", unit)
        self.assertIn("-m provider_status.worker", unit)
        self.assertNotIn("OPENAI_API_KEY", unit)

    def test_web_unit_has_no_credential_and_binds_only_loopback(self) -> None:
        unit = self.read("deploy/systemd/codex-provider-web.service")

        self.assertNotIn("LoadCredential", unit)
        self.assertNotIn("CREDENTIALS_DIRECTORY", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("User=codex-provider-web", unit)
        self.assertIn("Group=codex-provider-web", unit)
        self.assertIn(
            "ReadOnlyPaths=/var/lib/codex-provider-probe/public",
            unit,
        )
        self.assertIn(
            "InaccessiblePaths=/var/lib/codex-provider-probe/private",
            unit,
        )
        self.assertIn(
            "ReadWritePaths=/var/lib/codex-provider-probe/control",
            unit,
        )
        self.assertNotIn(
            "ReadWritePaths=/var/lib/codex-provider-probe/private",
            unit,
        )
        self.assertIn("SupplementaryGroups=codex-provider-control", unit)
        self.assertIn("UMask=0007", unit)
        self.assertNotIn("LoadCredential", unit)
        self.assertIn("-m provider_status.web", unit)
        self.assertIn(
            "--database /var/lib/codex-provider-probe/public/status.sqlite3",
            unit,
        )
        self.assertIn("--host 127.0.0.1", unit)
        self.assertIn("--port __WEB_PORT__", unit)

    def test_nginx_template_allows_public_manual_probes_with_rate_limits(self) -> None:
        config = self.read("deploy/nginx/codex-provider-status.conf")

        self.assertIn("server_name __PUBLIC_IP__;", config)
        self.assertIn("location = /codex-status", config)
        self.assertIn("location ^~ /codex-status/", config)
        self.assertIn("limit_except GET HEAD", config)
        self.assertIn("location ^~ /codex-status/api/manual-probes/", config)
        self.assertNotIn("__CONTROL_IP__", config)
        self.assertNotRegex(config, r"(?m)^\s*allow\s+")
        self.assertIn("limit_except POST", config)
        self.assertIn("codex_manual_probe_per_ip", config)
        self.assertIn("limit_req_zone", config)
        self.assertIn("proxy_pass http://127.0.0.1:__WEB_PORT__/;", config)
        self.assertIn("proxy_cache off;", config)
        self.assertIn("X-Content-Type-Options", config)
        self.assertNotIn("monitor.kanes.top", config)

    def test_installer_is_parameterized_idempotent_and_never_accepts_a_key(self) -> None:
        script = self.read("deploy/install_server.sh")

        self.assertIn("set -euo pipefail", script)
        self.assertIn("--source", script)
        self.assertIn("--codex-version", script)
        self.assertIn("--web-port", script)
        self.assertIn("--public-ip", script)
        self.assertNotIn("--control-ip", script)
        self.assertNotIn("CONTROL_IP", script)
        self.assertIn("systemctl daemon-reload", script)
        self.assertIn("systemctl enable codex-provider-worker.service", script)
        self.assertIn("APP_WEB_USER=codex-provider-web", script)
        self.assertIn("PRIVATE_ROOT=$DATA_ROOT/private", script)
        self.assertIn("PUBLIC_ROOT=$DATA_ROOT/public", script)
        self.assertIn("LEGACY_DATABASE=$DATA_ROOT/status.sqlite3", script)
        self.assertIn("SQLite backup migration required", script)
        self.assertIn('chmod 0755 "$APP_ROOT/app"', script)
        self.assertIn('-m 0700 "$PRIVATE_ROOT" "$PRIVATE_ROOT/tmp"', script)
        self.assertIn('-g "$APP_WEB_USER" -m 2750 "$PUBLIC_ROOT"', script)
        self.assertIn("CONTROL_GROUP=codex-provider-control", script)
        self.assertIn('if [[ ! -e $CONFIG_ROOT/providers.toml ]]', script)
        self.assertIn('-g "$CONTROL_GROUP" -m 2770 "$CONTROL_ROOT"', script)
        self.assertNotRegex(script, r"--(?:api-?key|secret|token)")
        self.assertNotIn("echo $", script)

    def test_shell_scripts_are_forced_to_lf_for_linux_archives(self) -> None:
        attributes = self.read(".gitattributes")
        installer = (ROOT / "deploy/install_server.sh").read_bytes()

        self.assertIn("*.sh text eol=lf", attributes.splitlines())
        self.assertNotIn(b"\r\n", installer)

    def test_no_deployment_asset_contains_a_literal_secret(self) -> None:
        assets = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "deploy").rglob("*")
            if path.is_file()
        )

        self.assertIsNone(re.search(r"sk-[A-Za-z0-9_-]{10,}", assets))


if __name__ == "__main__":
    unittest.main()
