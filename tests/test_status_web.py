import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from provider_status.config import ProviderConfig
from provider_status.control import ManualProbeControlStore, ManualProbeJob
from provider_status.store import ProbeRecord, StatusStore
from provider_status.web import (
    _overall_state,
    _sort_providers_by_model,
    create_app,
    main,
)


def make_provider(
    *,
    provider_id: str = "provider-alpha",
    name: str = "Provider Alpha",
    models: tuple[str, ...] = ("gpt-5.6-sol", "gpt-5.6-terra"),
    probe_mode: str = "automatic",
) -> ProviderConfig:
    return ProviderConfig(
        provider_id=provider_id,
        name=name,
        base_url="https://alpha.example.com/v1",
        credential_name="provider_alpha_api_key",
        models=models,
        healthy_interval_seconds=600,
        unhealthy_interval_seconds=120,
        timeout_seconds=90,
        probe_mode=probe_mode,
    )


class StatusWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_context.name)
        self.database = self.root / "status.sqlite3"
        self.control_database = self.root / "control" / "manual.sqlite3"
        self.now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        store = StatusStore(self.database)
        store.initialize((make_provider(),), self.now - timedelta(minutes=2))
        targets = store.list_due_targets(self.now, limit=10)
        for index, target in enumerate(targets):
            store.record_probe(
                target.id,
                ProbeRecord(
                    started_at=self.now - timedelta(seconds=4 - index),
                    success=index == 0,
                    latency_ms=3800 + index * 100,
                    error_code=None if index == 0 else "no_channel",
                    error_summary=None
                    if index == 0
                    else "No available channel; Authorization: Bearer hidden-secret",
                ),
                self.now - timedelta(seconds=2 - index),
            )
        self.client = TestClient(
            create_app(
                self.database,
                control_database_path=self.control_database,
                now_factory=lambda: self.now,
            )
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp_context.cleanup()

    def test_index_uses_relative_assets_for_ip_subpath_proxy(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Codex 渠道监测", response.text)
        self.assertIn("当前可用情况", response.text)
        self.assertIn('id="overall-detail"', response.text)
        self.assertIn('href="static/styles.css?v=15"', response.text)
        self.assertIn('src="static/app.js?v=16"', response.text)
        self.assertNotIn("api_key", response.text.casefold())
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_theme_bootstrap_precedes_styles_and_defines_all_modes(self) -> None:
        index = self.client.get("/")
        bootstrap = self.client.get("/static/theme-init.js")
        css = self.client.get("/static/styles.css")

        self.assertEqual(bootstrap.status_code, 200)
        self.assertLess(
            index.text.index('src="static/theme-init.js?v=1"'),
            index.text.index('href="static/styles.css?v=15"'),
        )
        self.assertIn(
            '<meta name="color-scheme" content="light dark">',
            index.text,
        )
        self.assertIn(
            'const STORAGE_KEY = "codex-status-theme";',
            bootstrap.text,
        )
        self.assertIn(
            'new Set(["light", "dark", "system"])',
            bootstrap.text,
        )
        self.assertIn("localStorage.getItem", bootstrap.text)
        self.assertIn("localStorage.setItem", bootstrap.text)
        self.assertIn(
            'root.removeAttribute("data-theme")',
            bootstrap.text,
        )
        self.assertIn(
            'root.setAttribute("data-theme", normalized)',
            bootstrap.text,
        )
        self.assertIn(':root[data-theme="dark"]', css.text)
        self.assertIn(':root[data-theme="light"]', css.text)
        self.assertIn("@media (prefers-color-scheme: dark)", css.text)
        self.assertIn(':root:not([data-theme])', css.text)

    def test_theme_menu_is_accessible_persistent_and_responsive(self) -> None:
        index = self.client.get("/")
        script = self.client.get("/static/app.js")
        css = self.client.get("/static/styles.css")

        self.assertIn('id="theme-button"', index.text)
        self.assertIn('aria-haspopup="menu"', index.text)
        self.assertIn('id="theme-menu"', index.text)
        self.assertEqual(index.text.count('role="menuitemradio"'), 3)
        for value, label in (
            ("light", "浅色模式"),
            ("dark", "深色模式"),
            ("system", "跟随系统"),
        ):
            self.assertIn(f'data-theme-value="{value}"', index.text)
            self.assertIn(label, index.text)
        for icon in ("light", "dark", "system"):
            self.assertIn(f'data-theme-trigger-icon="{icon}"', index.text)
        self.assertIn('id="system-theme-status"', index.text)
        self.assertIn('src="static/app.js?v=16"', index.text)

        for expected in (
            "function applyThemeSelection",
            "function openThemeMenu",
            "function closeThemeMenu",
            'systemThemeQuery.addEventListener("change"',
            'event.key === "ArrowDown"',
            'event.key === "ArrowUp"',
            'event.key === "Home"',
            'event.key === "End"',
            'event.key === "Escape"',
            'setAttribute("aria-checked"',
            'setAttribute("aria-expanded"',
        ):
            self.assertIn(expected, script.text)

        self.assertIn(".theme-picker", css.text)
        self.assertIn(".theme-menu", css.text)
        self.assertIn(".theme-option.is-active", css.text)
        theme_menu_rule = css.text.split(".theme-menu {", 1)[1].split("}", 1)[0]
        self.assertIn("max-width: calc(100vw - 24px);", theme_menu_rule)

    def test_hidden_theme_trigger_icons_are_not_laid_out(self) -> None:
        css = self.client.get("/static/styles.css")

        self.assertIn(".theme-trigger-icon[hidden] {", css.text)
        hidden_icon_rule = css.text.split(
            ".theme-trigger-icon[hidden] {", 1
        )[1].split("}", 1)[0]
        self.assertIn("display: none;", hidden_icon_rule)

    def test_theme_trigger_icons_toggle_the_svg_hidden_attribute(self) -> None:
        script = self.client.get("/static/app.js")

        self.assertRegex(
            script.text,
            r'icon\.toggleAttribute\(\s*"hidden",',
        )
        self.assertNotIn("icon.hidden =", script.text)

    def test_theme_menu_keeps_roving_focus_and_closes_when_focus_leaves(self) -> None:
        script = self.client.get("/static/app.js")

        self.assertIn("function focusThemeOption", script.text)
        self.assertIn('themePicker.addEventListener("focusout"', script.text)
        focusout_handler = script.text.split(
            'themePicker.addEventListener("focusout"', 1
        )[1].split("});", 1)[0]
        self.assertIn("closeThemeMenu();", focusout_handler)

    def test_focus_ring_uses_full_contrast_theme_color(self) -> None:
        css = self.client.get("/static/styles.css")

        focus_rule = css.text.split("button:focus-visible {", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn("outline: 3px solid var(--focus);", focus_rule)
        self.assertNotIn("transparent", focus_rule)

    def test_status_api_is_read_only_allowlisted_and_cacheable(self) -> None:
        response = self.client.get("/api/status?window=7d")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "public, max-age=30")
        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        payload = response.json()
        self.assertEqual(payload["window"], "7d")
        self.assertEqual(payload["overall_state"], "degraded")
        self.assertEqual(payload["providers"][0]["provider_id"], "provider-alpha")
        self.assertEqual(payload["providers"][0]["model_count"], 2)
        self.assertEqual(payload["providers"][0]["probe_mode"], "automatic")
        self.assertEqual(payload["providers"][0]["manual_history"], {})
        self.assertEqual(
            payload["providers"][0]["display_models"],
            ["gpt-5.6-sol", "gpt-5.6-terra"],
        )
        models = payload["providers"][0]["models"]
        self.assertTrue(all("history" in model for model in models))
        self.assertEqual([len(model["history"]) for model in models], [1, 1])
        self.assertEqual(models[1]["error_code"], "no_channel")
        self.assertEqual(
            models[1]["error_summary"],
            "供应商当前没有可处理该模型请求的上游线路。",
        )
        self.assertEqual(models[1]["history"][0]["error_code"], "no_channel")
        serialized = json.dumps(payload).casefold()
        for forbidden in (
            "credential",
            "authorization",
            "hidden-secret",
            "database_path",
            "openai_api_key",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(self.client.post("/api/status").status_code, 405)

    def test_manual_probe_api_enqueues_selected_models(self) -> None:
        response = self.client.post(
            "/api/manual-probes/provider-alpha",
            json={"models": ["gpt-5.6-terra"]},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = response.json()
        self.assertEqual(payload["provider_id"], "provider-alpha")
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["requested_models"], ["gpt-5.6-terra"])
        self.assertTrue(payload["created"])
        duplicate = self.client.post(
            "/api/manual-probes/provider-alpha",
            json={"models": ["gpt-5.6-sol"]},
        )
        self.assertEqual(duplicate.status_code, 202)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(duplicate.json()["job_id"], payload["job_id"])
        status = self.client.get(f"/api/manual-probe-jobs/{payload['job_id']}")
        self.assertEqual(status.status_code, 200)
        self.assertNotIn("credential", status.text.casefold())
        self.assertEqual(
            self.client.post(
                "/api/manual-probes/unknown",
                json={"models": ["gpt-5.6-sol"]},
            ).status_code,
            404,
        )

    def test_manual_probe_api_rejects_invalid_model_selections(self) -> None:
        for body in (
            {},
            {"models": []},
            {"models": ["gpt-5.6-sol", "gpt-5.6-sol"]},
            {"models": ["not-configured"]},
            {"models": ["gpt-5.6-sol"], "extra": True},
        ):
            with self.subTest(body=body):
                response = self.client.post("/api/manual-probes/provider-alpha", json=body)
                self.assertEqual(response.status_code, 422)

    def test_status_exposes_sanitized_manual_only_model_result(self) -> None:
        control = ManualProbeControlStore(self.control_database)
        job, _ = control.enqueue("provider-alpha", self.now)
        control.claim_next(self.now)
        control.set_total_models(job.job_id, 2)
        control.record_result(
            job.job_id,
            model="gpt-5.5",
            position=1,
            scheduled=False,
            success=False,
            latency_ms=1500,
            error_code="model_unavailable",
            error_summary="raw upstream secret",
            finished_at=self.now + timedelta(seconds=2),
        )
        control.complete(job.job_id, self.now + timedelta(seconds=2))

        payload = self.client.get("/api/status").json()
        manual = payload["providers"][0]["manual_probe"]
        self.assertEqual(manual["status"], "completed")
        self.assertEqual(manual["results"][0]["model"], "gpt-5.5")
        self.assertEqual(
            manual["results"][0]["error_summary"],
            "供应商明确返回该模型不存在或不支持。",
        )
        history = payload["providers"][0]["manual_history"]["gpt-5.5"]
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["success"])
        self.assertEqual(
            history[0]["error_summary"],
            "供应商明确返回该模型不存在或不支持。",
        )
        self.assertNotIn("raw upstream", json.dumps(payload))

    def test_status_api_sorts_real_probe_data_without_exposing_sort_signals(
        self,
    ) -> None:
        database = self.root / "sorted-status.sqlite3"
        store = StatusStore(database)
        provider_ids = ("recent", "older", "one", "three", "never")
        store.initialize(
            tuple(
                make_provider(
                    provider_id=provider_id,
                    name=provider_id.title(),
                    models=("gpt-5.6-sol",),
                )
                for provider_id in provider_ids
            ),
            self.now - timedelta(hours=2),
        )
        targets = {
            target.provider_id: target.id
            for target in store.list_due_targets(self.now, limit=20)
        }

        def record(provider_id: str, at: datetime, success: bool) -> None:
            store.record_probe(
                targets[provider_id],
                ProbeRecord(
                    started_at=at - timedelta(seconds=1),
                    success=success,
                    latency_ms=100,
                    error_code=None if success else "network_error",
                ),
                at,
            )

        record("recent", self.now - timedelta(minutes=15), True)
        record("recent", self.now - timedelta(minutes=5), False)
        record("older", self.now - timedelta(minutes=60), True)
        record("older", self.now - timedelta(minutes=3), False)
        record("one", self.now - timedelta(minutes=2), True)
        for minutes in (30, 20, 10):
            record("three", self.now - timedelta(minutes=minutes), True)

        with TestClient(
            create_app(database, now_factory=lambda: self.now)
        ) as client:
            response = client.get("/api/status?window=24h")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [provider["provider_id"] for provider in payload["providers"]],
            ["three", "one", "recent", "older", "never"],
        )
        serialized = json.dumps(payload)
        for internal_key in ("consecutive_successes", "last_success_at"):
            self.assertNotIn(internal_key, serialized)

    def test_status_api_sorts_manual_probe_success_before_missing_success(
        self,
    ) -> None:
        database = self.root / "manual-sort-status.sqlite3"
        control_database = self.root / "manual-sort-control.sqlite3"
        store = StatusStore(database)
        store.initialize(
            (
                make_provider(
                    provider_id="automatic-down",
                    name="Any Router",
                    models=("gpt-5.6-sol",),
                ),
                make_provider(
                    provider_id="manual-only",
                    name="佛爷API",
                    models=("gpt-5.6-sol", "gpt-5.5"),
                    probe_mode="manual_only",
                ),
            ),
            self.now - timedelta(hours=2),
        )
        targets = {
            target.provider_id: target.id
            for target in store.list_due_targets(self.now, limit=20)
        }
        store.record_probe(
            targets["automatic-down"],
            ProbeRecord(
                started_at=self.now - timedelta(minutes=5, seconds=1),
                success=False,
                latency_ms=None,
                error_code="no_channel",
            ),
            self.now - timedelta(minutes=5),
        )
        control = ManualProbeControlStore(control_database)
        control.initialize()
        job, _ = control.enqueue(
            "manual-only",
            self.now - timedelta(minutes=4),
            requested_models=("gpt-5.6-sol",),
        )
        control.claim_next(self.now - timedelta(minutes=4))
        control.set_total_models(job.job_id, 1)
        control.record_result(
            job.job_id,
            model="gpt-5.6-sol",
            position=1,
            scheduled=False,
            success=True,
            latency_ms=1200,
            error_code=None,
            error_summary=None,
            finished_at=self.now - timedelta(minutes=3),
        )
        control.complete(job.job_id, self.now - timedelta(minutes=3))

        with TestClient(
            create_app(
                database,
                control_database_path=control_database,
                now_factory=lambda: self.now,
            )
        ) as client:
            response = client.get("/api/status?window=24h")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [provider["provider_id"] for provider in payload["providers"]],
            ["manual-only", "automatic-down"],
        )
        manual = payload["providers"][0]["manual_probe"]
        self.assertEqual(manual["results"][0]["model"], "gpt-5.6-sol")
        self.assertTrue(manual["results"][0]["success"])
        serialized = json.dumps(payload)
        for internal_key in ("consecutive_successes", "last_success_at"):
            self.assertNotIn(internal_key, serialized)

    def test_status_api_explains_diagnostic_error_codes(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                """
                UPDATE probe_targets
                SET last_error_code = ?, last_error_summary = ?
                WHERE model = ?
                """,
                ("model_unavailable", "raw model detail", "gpt-5.6-sol"),
            )
            connection.execute(
                """
                UPDATE probe_targets
                SET last_error_code = ?, last_error_summary = ?,
                    last_http_status_code = ?, last_failure_stage = ?,
                    last_diagnostic_source = ?
                WHERE model = ?
                """,
                (
                    "upstream_unavailable",
                    "raw upstream detail",
                    520,
                    "provider_response",
                    "direct_responses",
                    "gpt-5.6-terra",
                ),
            )

        models = self.client.get("/api/status").json()["providers"][0]["models"]
        summaries = {model["model"]: model["error_summary"] for model in models}
        self.assertEqual(
            summaries["gpt-5.6-sol"],
            "供应商明确返回该模型不存在或不支持。",
        )
        self.assertEqual(
            summaries["gpt-5.6-terra"],
            "HTTP 520 · Cloudflare 已接收请求，但供应商源站返回异常。",
        )

    def test_status_api_allowlists_auth_401_without_exposing_stored_detail(
        self,
    ) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                """
                UPDATE probe_targets
                SET last_error_code = ?, last_error_summary = ?
                WHERE model = ?
                """,
                (
                    "auth_failed",
                    "HTTP 401 Unauthorized; raw upstream detail secret-401",
                    "gpt-5.6-sol",
                ),
            )
            connection.execute(
                """
                UPDATE probe_targets
                SET last_error_code = ?, last_error_summary = ?,
                    last_http_status_code = ?, last_failure_stage = ?,
                    last_diagnostic_source = ?
                WHERE model = ?
                """,
                (
                    "auth_failed",
                    "invalid API key; raw upstream detail secret-generic",
                    999,
                    "secret-stage",
                    "secret-source",
                    "gpt-5.6-terra",
                ),
            )

        response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        models = {
            model["model"]: model
            for model in response.json()["providers"][0]["models"]
        }
        self.assertEqual(
            models["gpt-5.6-sol"]["error_summary"],
            (
                "供应商明确返回 HTTP 401 Unauthorized；"
                "专用 Key 无效、已过期或没有该模型访问权限。"
            ),
        )
        self.assertEqual(
            models["gpt-5.6-terra"]["error_summary"],
            "专用 Key 无效、已过期或没有访问权限。",
        )
        self.assertIsNone(models["gpt-5.6-terra"]["http_status_code"])
        self.assertIsNone(models["gpt-5.6-terra"]["failure_stage"])
        self.assertIsNone(models["gpt-5.6-terra"]["diagnostic_source"])
        self.assertNotIn("raw upstream detail", response.text)
        self.assertNotIn("secret-401", response.text)
        self.assertNotIn("secret-generic", response.text)
        self.assertNotIn("secret-stage", response.text)
        self.assertNotIn("secret-source", response.text)

    def test_provider_detail_and_window_validation(self) -> None:
        default_status = self.client.get("/api/status")
        three_hours = self.client.get("/api/status?window=3h")
        twenty_four_hours = self.client.get("/api/status?window=24h")
        detail = self.client.get("/api/providers/provider-alpha?window=15d")

        self.assertEqual(default_status.status_code, 200)
        self.assertEqual(default_status.json()["window"], "24h")
        self.assertEqual(three_hours.status_code, 200)
        self.assertEqual(three_hours.json()["window"], "3h")
        self.assertEqual(twenty_four_hours.status_code, 200)
        self.assertEqual(twenty_four_hours.json()["window"], "24h")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["window"], "15d")
        self.assertEqual(len(detail.json()["provider"]["models"]), 2)
        self.assertEqual(
            self.client.get("/api/providers/missing?window=7d").status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/status?window=1d").status_code, 422)

    def test_healthz_reports_fresh_data_without_internal_paths(self) -> None:
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["data_status"], "fresh")
        self.assertNotIn(str(self.database), response.text)

    def test_status_api_marks_old_probe_data_stale_with_actual_check_time(
        self,
    ) -> None:
        stale_now = self.now + timedelta(minutes=21)
        with TestClient(
            create_app(self.database, now_factory=lambda: stale_now)
        ) as client:
            response = client.get("/api/status?window=7d")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data_status"], "stale")
        self.assertEqual(
            payload["last_checked"],
            (self.now - timedelta(seconds=1)).isoformat(),
        )
        self.assertNotEqual(payload["last_checked"], payload["generated_at"])

    def test_static_assets_contain_expected_responsive_controls(self) -> None:
        index = self.client.get("/")
        css = self.client.get("/static/styles.css")
        script = self.client.get("/static/app.js")

        self.assertEqual(index.status_code, 200)
        window_buttons = (
            'data-window="3h">3 小时',
            'data-window="24h">24 小时',
            'data-window="7d">7 天',
            'data-window="15d">15 天',
            'data-window="30d">30 天',
        )
        positions = [index.text.find(button) for button in window_buttons]
        self.assertTrue(all(position >= 0 for position in positions))
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            'class="window-button is-active" type="button" data-window="24h"',
            index.text,
        )
        self.assertNotIn("detail-panel", index.text)
        self.assertEqual(css.status_code, 200)
        self.assertEqual(css.headers["cache-control"], "no-cache")
        normalized_css = css.text.replace("\r\n", "\n")
        self.assertIn("@media", css.text)
        self.assertIn("minmax", css.text)
        self.assertIn("model_unavailable", script.text)
        self.assertIn("upstream_unavailable", script.text)
        self.assertIn("width: min(1560px, calc(100% - 40px));", css.text)
        provider_list_rule = css.text.split(".provider-list {", 1)[1].split("}", 1)[0]
        self.assertIn(
            "grid-template-columns: repeat(3, minmax(0, 1fr));",
            provider_list_rule,
        )
        provider_card_rule = css.text.split(
            ".provider-card {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("display: flex;", provider_card_rule)
        self.assertIn("flex-direction: column;", provider_card_rule)
        provider_card_main_rule = css.text.split(
            ".provider-card-main {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("flex: 1;", provider_card_main_rule)
        self.assertIn("@media (max-width: 1279px)", css.text)
        self.assertIn("@media (max-width: 899px)", css.text)
        model_summary_rule = css.text.split(
            ".model-strip-summary {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) auto;",
            model_summary_rule,
        )
        self.assertIn(
            "grid-template-rows: minmax(34px, auto) auto;",
            model_summary_rule,
        )
        self.assertIn("align-items: start;", model_summary_rule)
        model_reason_rule = css.text.split(
            ".model-strip-reason {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("min-width: 0;", model_reason_rule)
        self.assertIn("overflow: hidden;", model_reason_rule)
        self.assertIn("text-overflow: ellipsis;", model_reason_rule)
        self.assertIn("white-space: nowrap;", model_reason_rule)
        self.assertIn("grid-column: 1 / -1;", css.text)
        self.assertIn(".window-tabs {\n    width: 100%;", normalized_css)
        self.assertIn(".window-button {\n    flex: 1;", normalized_css)
        self.assertIn(".model-strip-list", css.text)
        self.assertIn(".model-strip", css.text)
        self.assertIn(".model-strip-history", css.text)
        self.assertIn(".overall-detail", css.text)
        history_bar_rule = css.text.split(".history-bar {", 1)[1].split("}", 1)[0]
        self.assertNotIn("background:", history_bar_rule)
        for state in ("healthy", "degraded", "recovering", "down", "unknown"):
            self.assertIn(f".state-bar-{state}", css.text)
        self.assertNotIn("linear-gradient", css.text)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(script.headers["cache-control"], "no-cache")
        self.assertIn("60", script.text)
        self.assertIn("api/status", script.text)
        self.assertIn("payload.last_checked", script.text)
        self.assertIn("data_status", script.text)
        self.assertIn("statusRequestSequence", script.text)
        self.assertIn("requestSequence !== statusRequestSequence", script.text)
        self.assertIn("function modelStrip", script.text)
        self.assertIn("const overallStateLabels", script.text)
        self.assertIn("const providerStateLabels", script.text)
        self.assertIn("const modelStateLabels", script.text)
        self.assertIn("const errorCodeLabels", script.text)
        for error_label in (
            "当前客户端被拒绝",
            "供应商无可用线路",
            "专用 Key 被拒绝",
            "频率或额度受限",
            "请求超时",
            "无法连接供应商",
            "响应格式异常",
            "未识别的探测错误",
        ):
            self.assertIn(error_label, script.text)
        self.assertNotIn("Key 验证失败", script.text)
        self.assertIn("function summarizeModels", script.text)
        self.assertIn('let selectedWindow = "24h";', script.text)
        self.assertIn('"3h": "3 小时"', script.text)
        self.assertIn('"24h": "24 小时"', script.text)
        self.assertNotIn('responseWindow.replace("d", "")', script.text)
        self.assertIn("全部模型可用", script.text)
        self.assertIn("部分服务异常", script.text)
        self.assertIn("暂无可用模型", script.text)
        self.assertIn("部分模型异常", script.text)
        self.assertIn("暂不可用", script.text)
        self.assertIn("displayModels.map(modelStrip)", script.text)
        self.assertIn("renderHistory(model.history, model.model)", script.text)
        self.assertNotIn("renderHistory(provider.history)", script.text)
        self.assertIn("historyIndexAtClientX", script.text)
        self.assertIn("history-detail-layer", script.text)
        self.assertIn("history-detail-status", script.text)
        self.assertIn("model-strip-reason", script.text)
        self.assertIn("原因：", script.text)
        self.assertIn("pointerType", script.text)
        self.assertIn('setAttribute("role", "dialog")', script.text)
        self.assertIn('setAttribute("aria-label"', script.text)
        self.assertIn('addEventListener("keydown"', script.text)
        self.assertIn(".history-detail-layer", css.text)
        self.assertIn("@media (pointer: coarse)", css.text)
        self.assertNotIn("bar.title =", script.text)
        self.assertNotIn("strip.title =", script.text)
        self.assertNotIn("detailRequestSequence", script.text)
        self.assertNotIn("查看模型详情", script.text)
        self.assertNotIn("innerHTML", script.text)

    def test_configured_display_models_render_unmonitored_placeholders(self) -> None:
        script = self.client.get("/static/app.js")
        css = self.client.get("/static/styles.css")

        self.assertEqual(script.status_code, 200)
        self.assertEqual(css.status_code, 200)
        self.assertNotIn("unmonitoredModelPlaceholders", script.text)
        self.assertNotIn('"provider-beta"', script.text)
        self.assertIn("provider.display_models", script.text)

    def test_multi_model_manual_probe_uses_responsive_model_picker(self) -> None:
        script = self.client.get("/static/app.js")
        styles = self.client.get("/static/styles.css")

        self.assertIn("showModelPicker(provider, button, models)", script.text)
        self.assertIn('body: JSON.stringify({ models })', script.text)
        self.assertIn('models.length === 1', script.text)
        self.assertIn("window.innerHeight - 170", script.text)
        self.assertIn(".model-picker-layer", styles.text)
        self.assertIn("align-items: flex-end", styles.text)
        self.assertIn("monitored: false", script.text)
        self.assertIn('unmonitored: "未监测"', script.text)
        self.assertIn('"本站未启用该模型探测"', script.text)
        self.assertIn("function modelsForDisplay", script.text)
        self.assertIn("function manualProbeButton", script.text)
        self.assertIn("api/manual-probes/", script.text)
        self.assertIn("provider-card-actions", script.text)
        self.assertIn("manual-probe-button", styles.text)
        self.assertIn("probe-mode-badge", styles.text)
        self.assertIn('manualMode ? "点击检测" : "自动检测"', script.text)
        self.assertIn('const PROBE_MODE_MANUAL_ONLY = "manual_only"', script.text)
        self.assertIn('metric("上次检测结果"', script.text)
        self.assertIn("最近点击检测", script.text)
        self.assertIn("function renderUnmonitoredHistory", script.text)
        self.assertIn("const displayModels = modelsForDisplay(provider)", script.text)
        self.assertIn("displayModels.map(modelStrip)", script.text)
        self.assertIn("summarizeModels(payload.providers)", script.text)
        self.assertIn("const monitoredProviderStateLabels", script.text)
        self.assertIn("history-bars-static", script.text)
        self.assertIn(
            'target.closest(".history-bars:not(.history-bars-static)")',
            script.text,
        )
        self.assertIn(".history-bars-static", styles.text)
        self.assertIn(".status-unmonitored", styles.text)

    def test_manual_probe_feedback_uses_accessible_responsive_toasts(self) -> None:
        index = self.client.get("/")
        script = self.client.get("/static/app.js")
        css = self.client.get("/static/styles.css")

        self.assertIn('id="toast-region"', index.text)
        self.assertIn('aria-live="polite"', index.text)
        self.assertIn('aria-atomic="true"', index.text)
        self.assertIn("function showToast", script.text)
        self.assertIn("function dismissToast", script.text)
        self.assertIn('tone === "error" ? "alert" : "status"', script.text)
        self.assertIn('title: "已进入优先检测队列"', script.text)
        self.assertIn('toastTone: "warning"', script.text)
        self.assertIn('toastTone: "error"', script.text)
        self.assertIn('setAttribute("aria-label", "关闭提示")', script.text)
        self.assertNotIn("当前网络没有立即检测权限", script.text)
        toast_region_rule = css.text.split(".toast-region {", 1)[1].split("}", 1)[0]
        self.assertIn("position: fixed;", toast_region_rule)
        self.assertIn("right: 20px;", toast_region_rule)
        self.assertIn("pointer-events: none;", toast_region_rule)
        self.assertIn(".toast-success", css.text)
        self.assertIn(".toast-warning", css.text)
        self.assertIn(".toast-error", css.text)
        mobile_rules = css.text.split("@media (max-width: 720px)", 1)[1]
        self.assertIn("right: 12px;", mobile_rules)
        self.assertIn("left: 12px;", mobile_rules)
        self.assertIn("width: auto;", mobile_rules)

    def test_mobile_history_detail_is_anchored_and_site_icons_are_served(self) -> None:
        index = self.client.get("/")
        css = self.client.get("/static/styles.css")
        script = self.client.get("/static/app.js")
        favicon = self.client.get("/static/favicon.svg")
        apple_icon = self.client.get("/static/apple-touch-icon.png")

        self.assertIn(
            '<link rel="icon" href="static/favicon.svg" type="image/svg+xml">',
            index.text,
        )
        self.assertIn(
            '<link rel="apple-touch-icon" href="static/apple-touch-icon.png">',
            index.text,
        )
        self.assertEqual(favicon.status_code, 200)
        self.assertEqual(favicon.headers["content-type"], "image/svg+xml")
        self.assertEqual(apple_icon.status_code, 200)
        self.assertEqual(apple_icon.headers["content-type"], "image/png")

        self.assertNotIn(
            "if (!historyDetailLayer || historyDetailLayer.hidden || isCoarsePointer())",
            script.text,
        )
        self.assertIn('window.addEventListener("scroll"', script.text)
        self.assertIn("history-detail-balance", script.text)
        self.assertNotIn("left: 12px !important;", css.text)
        self.assertIn(
            "grid-template-columns: 40px minmax(0, 1fr) 40px;",
            css.text,
        )
        detail_content_rule = css.text.split(
            ".history-detail-content {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("text-align: center;", detail_content_rule)


class OverallStateTests(unittest.TestCase):
    def test_is_down_only_when_every_provider_is_down(self) -> None:
        self.assertEqual(
            _overall_state([{"state": "healthy"}, {"state": "down"}]),
            "degraded",
        )
        self.assertEqual(
            _overall_state([{"state": "down"}, {"state": "down"}]),
            "down",
        )

    def test_manual_only_provider_does_not_affect_overall_state(self) -> None:
        self.assertEqual(
            _overall_state(
                [
                    {"state": "healthy", "probe_mode": "automatic"},
                    {"state": "down", "probe_mode": "manual_only"},
                ]
            ),
            "healthy",
        )


class ProviderSortTests(unittest.TestCase):
    @staticmethod
    def provider_rank(
        provider_id: str,
        streak: object,
        last_success_at: object,
    ) -> dict[str, object]:
        return {
            "provider_id": provider_id,
            "models": [
                {
                    "model": "gpt-5.6-sol",
                    "consecutive_successes": streak,
                    "last_success_at": last_success_at,
                }
            ],
        }

    @staticmethod
    def manual_job(
        provider_id: str,
        *,
        model: str,
        success: bool,
        finished_at: str,
    ) -> ManualProbeJob:
        finished = datetime.fromisoformat(finished_at)
        return ManualProbeJob(
            job_id=f"{provider_id}-job",
            provider_id=provider_id,
            status="completed",
            requested_at=finished - timedelta(minutes=1),
            started_at=finished - timedelta(minutes=1),
            finished_at=finished,
            total_models=1,
            completed_models=1,
            error_summary=None,
            results=(
                {
                    "model": model,
                    "success": success,
                    "finished_at": finished_at,
                },
            ),
        )

    def test_provider_sort_uses_streak_then_recent_success(self) -> None:
        providers = [
            self.provider_rank("recent", 0, "2026-07-24T05:00:00+00:00"),
            self.provider_rank("older", 0, "2026-07-23T05:00:00+00:00"),
            self.provider_rank("one", 1, "2026-07-24T04:00:00+00:00"),
            self.provider_rank("three", 3, "2026-07-24T03:00:00+00:00"),
            self.provider_rank("never", 0, None),
            {"provider_id": "missing", "models": []},
        ]
        original = list(providers)

        sorted_providers = _sort_providers_by_model(providers, "gpt-5.6-sol")

        self.assertEqual(
            [provider["provider_id"] for provider in sorted_providers],
            ["three", "one", "recent", "older", "never", "missing"],
        )
        self.assertEqual(providers, original)

    def test_provider_sort_preserves_order_for_ties_and_invalid_values(self) -> None:
        providers = [
            self.provider_rank("first", 2, "2026-07-24T05:00:00+00:00"),
            self.provider_rank("second", 2, "2026-07-24T06:00:00+00:00"),
            self.provider_rank("negative", -1, "invalid"),
            self.provider_rank("boolean", True, None),
        ]

        sorted_providers = _sort_providers_by_model(providers, "gpt-5.6-sol")

        self.assertEqual(
            [provider["provider_id"] for provider in sorted_providers],
            ["first", "second", "negative", "boolean"],
        )

    def test_provider_sort_uses_manual_success_when_model_is_missing(self) -> None:
        providers = [
            self.provider_rank("automatic-success", 0, "2026-07-23T05:00:00+00:00"),
            self.provider_rank("automatic-never", 0, None),
            {"provider_id": "manual-success", "models": []},
            {"provider_id": "manual-failed", "models": []},
            {"provider_id": "manual-other-model", "models": []},
        ]
        manual_jobs = {
            "manual-success": self.manual_job(
                "manual-success",
                model="gpt-5.6-sol",
                success=True,
                finished_at="2026-07-24T05:01:00+00:00",
            ),
            "manual-failed": self.manual_job(
                "manual-failed",
                model="gpt-5.6-sol",
                success=False,
                finished_at="2026-07-24T05:03:00+00:00",
            ),
            "manual-other-model": self.manual_job(
                "manual-other-model",
                model="gpt-5.5",
                success=True,
                finished_at="2026-07-24T05:05:00+00:00",
            ),
        }

        sorted_providers = _sort_providers_by_model(
            providers,
            "gpt-5.6-sol",
            manual_jobs,
        )

        self.assertEqual(
            [provider["provider_id"] for provider in sorted_providers],
            [
                "automatic-success",
                "manual-success",
                "automatic-never",
                "manual-failed",
                "manual-other-model",
            ],
        )


class MissingDatabaseTests(unittest.TestCase):
    def test_healthz_is_unavailable_without_initialized_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "missing.sqlite3"
            with TestClient(create_app(database)) as client:
                response = client.get("/healthz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertFalse(database.exists())


class WebMainTests(unittest.TestCase):
    @patch("provider_status.web.uvicorn.run")
    @patch("provider_status.web.create_app")
    def test_main_defaults_to_public_snapshot_database(
        self,
        create_app_mock,
        run,
    ) -> None:
        application = object()
        create_app_mock.return_value = application

        result = main([])

        self.assertEqual(result, 0)
        create_app_mock.assert_called_once_with(
            Path("/var/lib/codex-provider-probe/public/status.sqlite3"),
            control_database_path=Path(
                "/var/lib/codex-provider-probe/control/manual-probes.sqlite3"
            ),
        )
        self.assertIs(run.call_args.args[0], application)

    @patch("provider_status.web.uvicorn.run")
    def test_main_starts_uvicorn_on_requested_loopback_port(self, run) -> None:
        result = main(
            [
                "--database",
                "status.sqlite3",
                "--control-database",
                "manual.sqlite3",
                "--host",
                "127.0.0.1",
                "--port",
                "18765",
            ]
        )

        self.assertEqual(result, 0)
        app = run.call_args.args[0]
        self.assertIsInstance(app, type(create_app(Path("status.sqlite3"))))
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 18765)


if __name__ == "__main__":
    unittest.main()
