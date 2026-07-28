from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from provider_status.config import PROBE_MODE_MANUAL_ONLY
from provider_status.state import TargetState, aggregate_provider_state
from provider_status.control import (
    DEFAULT_CONTROL_DATABASE,
    ManualProbeControlStore,
    ManualProbeCooldownError,
    ManualProbeJob,
    ManualProbeQueueFullError,
    public_manual_job,
)
from provider_status.store import StatusStore


WindowName = Literal["3h", "24h", "7d", "15d", "30d"]
_WINDOW_DAYS = {"3h": 0.125, "24h": 1, "7d": 7, "15d": 15, "30d": 30}
_SORT_MODEL = "gpt-5.6-sol"
_AUTH_HTTP_401_RE = re.compile(r"\bHTTP\s+401\b", re.IGNORECASE)
_ERROR_SUMMARIES = {
    "auth_failed": "专用 Key 无效、已过期或没有访问权限。",
    "client_blocked": (
        "供应商拒绝当前 Codex 探测客户端或接入方式；不代表该模型全局不可用。"
    ),
    "no_channel": "供应商当前没有可处理该模型请求的上游线路。",
    "rate_limited": "请求触发频率限制、用量限制或额度不足。",
    "model_unavailable": "供应商明确返回该模型不存在或不支持。",
    "upstream_unavailable": "供应商上游服务暂时不可用（HTTP 502、503 或 504）。",
    "timeout": "探测请求在规定时间内没有完成。",
    "network_error": "连接供应商时发生网络、DNS、TLS 或连接中断。",
    "invalid_output": "模型已返回内容，但响应没有通过探测结果验证。",
    "unknown_error": "暂时无法识别失败原因，将等待下一次探测。",
}
_AUTH_HTTP_401_SUMMARY = (
    "供应商明确返回 HTTP 401 Unauthorized；"
    "专用 Key 无效、已过期或没有该模型访问权限。"
)
_STATIC_DIRECTORY = Path(__file__).with_name("static")


def create_app(
    database_path: Path,
    *,
    control_database_path: Path | None = None,
    now_factory: Callable[[], datetime] | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = StatusStore(Path(database_path))
    control_store = (
        ManualProbeControlStore(Path(control_database_path))
        if control_database_path is not None
        else None
    )
    if control_store is not None:
        control_store.initialize()
    get_now = now_factory or (lambda: datetime.now(timezone.utc))

    app.mount(
        "/static",
        StaticFiles(directory=_STATIC_DIRECTORY),
        name="static",
    )

    @app.middleware("http")
    async def add_public_headers(request: Any, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIRECTORY / "index.html", media_type="text/html")

    @app.get("/api/status")
    def public_status(
        response: Response,
        window: WindowName = Query(default="24h"),
    ) -> dict[str, Any]:
        now = _as_utc(get_now())
        providers = _sort_providers_by_model(
            _read_status(store, _WINDOW_DAYS[window], now),
            _SORT_MODEL,
        )
        data_status, last_checked = _freshness(providers, now)
        manual_jobs = _latest_manual_jobs(control_store, providers)
        manual_histories = _manual_histories(control_store, providers)
        response.headers["Cache-Control"] = "public, max-age=30"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return {
            "window": window,
            "generated_at": now.isoformat(),
            "data_status": data_status,
            "last_checked": last_checked.isoformat() if last_checked else None,
            "overall_state": _overall_state(providers),
            "providers": [
                _public_provider(
                    provider,
                    manual_jobs.get(str(provider.get("provider_id"))),
                    manual_histories.get(str(provider.get("provider_id")), {}),
                )
                for provider in providers
            ],
        }

    @app.get("/api/providers/{provider_id}")
    def public_provider(
        provider_id: str,
        response: Response,
        window: WindowName = Query(default="24h"),
    ) -> dict[str, Any]:
        now = _as_utc(get_now())
        try:
            provider = store.get_public_provider(
                provider_id,
                _WINDOW_DAYS[window],
                now,
            )
        except (OSError, sqlite3.Error):
            raise HTTPException(status_code=503, detail="状态数据暂不可用") from None
        if provider is None:
            raise HTTPException(status_code=404, detail="未找到该供应商")
        data_status, last_checked = _freshness([provider], now)
        manual_job = (
            control_store.latest_jobs((provider_id,)).get(provider_id)
            if control_store is not None
            else None
        )
        manual_history = (
            control_store.result_history((provider_id,)).get(provider_id, {})
            if control_store is not None
            else {}
        )
        response.headers["Cache-Control"] = "public, max-age=30"
        return {
            "window": window,
            "generated_at": now.isoformat(),
            "data_status": data_status,
            "last_checked": last_checked.isoformat() if last_checked else None,
            "provider": _public_provider(provider, manual_job, manual_history),
        }

    @app.post("/api/manual-probes/{provider_id}", status_code=202)
    def request_manual_probe(
        provider_id: str,
        response: Response,
        request_body: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        if control_store is None:
            raise HTTPException(status_code=503, detail="立即检测队列不可用")
        now = _as_utc(get_now())
        try:
            provider = store.get_public_provider(provider_id, 7, now)
        except (OSError, sqlite3.Error):
            raise HTTPException(status_code=503, detail="状态数据暂不可用") from None
        if provider is None:
            raise HTTPException(status_code=404, detail="未找到该供应商")
        requested_models = request_body.get("models")
        if (
            set(request_body) != {"models"}
            or not isinstance(requested_models, list)
            or not requested_models
            or not all(isinstance(model, str) and model for model in requested_models)
            or len(set(requested_models)) != len(requested_models)
        ):
            raise HTTPException(
                status_code=422,
                detail="models must be a non-empty list without duplicates",
            )
        display_models = tuple(provider.get("display_models") or ())
        if not display_models or any(model not in display_models for model in requested_models):
            raise HTTPException(
                status_code=422,
                detail="models contains an unconfigured display model",
            )
        selected_models = tuple(
            model for model in display_models if model in requested_models
        )
        try:
            job, created = control_store.enqueue(
                provider_id,
                now,
                requested_models=selected_models,
            )
        except ManualProbeCooldownError as exc:
            raise HTTPException(
                status_code=429,
                detail="该供应商刚刚提交过立即检测",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from None
        except ManualProbeQueueFullError:
            raise HTTPException(status_code=503, detail="立即检测队列已满") from None
        response.status_code = 202
        response.headers["Cache-Control"] = "no-store"
        payload = _public_manual_job(job)
        payload["created"] = created
        return payload

    @app.get("/api/manual-probe-jobs/{job_id}")
    def manual_probe_job(job_id: str, response: Response) -> dict[str, Any]:
        if control_store is None:
            raise HTTPException(status_code=503, detail="立即检测队列不可用")
        job = control_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="未找到该检测任务")
        response.headers["Cache-Control"] = "no-store"
        return _public_manual_job(job)

    @app.get("/healthz")
    def healthz(response: Response) -> dict[str, str]:
        now = _as_utc(get_now())
        try:
            providers = store.get_public_status(7, now)
        except (OSError, sqlite3.Error):
            response.status_code = 503
            response.headers["Cache-Control"] = "no-store"
            return {"status": "unavailable"}

        data_status, _last_checked = _freshness(providers, now)
        response.headers["Cache-Control"] = "no-store"
        return {"status": "ok", "data_status": data_status}

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex provider public status page")
    parser.add_argument(
        "--database",
        default="/var/lib/codex-provider-probe/public/status.sqlite3",
    )
    parser.add_argument(
        "--control-database",
        default=str(DEFAULT_CONTROL_DATABASE),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args(argv)
    uvicorn.run(
        create_app(
            Path(args.database),
            control_database_path=Path(args.control_database),
        ),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    return 0


def _read_status(
    store: StatusStore,
    window_days: float,
    now: datetime,
) -> list[dict[str, Any]]:
    try:
        return store.get_public_status(window_days, now)
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail="状态数据暂不可用") from None


def _sort_providers_by_model(
    providers: list[dict[str, Any]],
    model_name: str,
) -> list[dict[str, Any]]:
    indexed = list(enumerate(providers))
    indexed.sort(
        key=lambda item: _provider_sort_key(item[1], model_name, item[0])
    )
    return [provider for _index, provider in indexed]


def _provider_sort_key(
    provider: dict[str, Any],
    model_name: str,
    original_index: int,
) -> tuple[int, int, float, int]:
    model = next(
        (
            item
            for item in provider.get("models", [])
            if item.get("model") == model_name
        ),
        None,
    )
    if model is None:
        return (2, 0, 0.0, original_index)

    raw_streak = model.get("consecutive_successes")
    streak = (
        raw_streak
        if isinstance(raw_streak, int)
        and not isinstance(raw_streak, bool)
        and raw_streak >= 0
        else 0
    )
    if streak > 0:
        return (0, -streak, 0.0, original_index)

    last_success = _parse_datetime(model.get("last_success_at"))
    if last_success is not None:
        return (1, 0, -last_success.timestamp(), original_index)
    return (2, 0, 0.0, original_index)


def _overall_state(providers: list[dict[str, Any]]) -> str:
    states: list[TargetState] = []
    for provider in providers:
        if provider.get("probe_mode") == PROBE_MODE_MANUAL_ONLY:
            continue
        try:
            states.append(TargetState(provider["state"]))
        except (KeyError, ValueError):
            states.append(TargetState.UNKNOWN)
    return aggregate_provider_state(states).value


def _freshness(
    providers: list[dict[str, Any]],
    now: datetime,
) -> tuple[str, datetime | None]:
    latest_checks = [
        parsed
        for provider in providers
        if (parsed := _parse_datetime(provider.get("last_checked"))) is not None
    ]
    if not latest_checks:
        return "waiting", None
    last_checked = max(latest_checks)
    if last_checked < now - timedelta(minutes=20):
        return "stale", last_checked
    return "fresh", last_checked


def _public_provider(
    provider: dict[str, Any],
    manual_job: ManualProbeJob | None = None,
    manual_history: dict[str, tuple[dict[str, Any], ...]] | None = None,
) -> dict[str, Any]:
    public = {
        key: provider.get(key)
        for key in (
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
            "history",
        )
    }
    models = []
    for model in provider.get("models", []):
        error_code = model.get("error_code")
        models.append(
            {
                "model": model.get("model"),
                "state": model.get("state"),
                "availability": model.get("availability"),
                "latest_latency": model.get("latest_latency"),
                "last_checked": model.get("last_checked"),
                "next_check": model.get("next_check"),
                "error_code": error_code,
                "error_summary": _public_error_summary(
                    error_code,
                    model.get("error_summary"),
                ),
                "history": [
                    {
                        "recorded_at": item.get("recorded_at"),
                        "state": item.get("state"),
                        "error_code": item.get("error_code"),
                    }
                    for item in model.get("history", [])
                ],
            }
        )
    public["models"] = models
    public["manual_probe"] = (
        _public_manual_job(manual_job) if manual_job is not None else None
    )
    public["manual_history"] = {
        model: [
            {
                "success": bool(result.get("success")),
                "latency_ms": result.get("latency_ms"),
                "error_code": result.get("error_code"),
                "finished_at": result.get("finished_at"),
            }
            for result in results
        ]
        for model, results in (manual_history or {}).items()
    }
    return public


def _latest_manual_jobs(
    control_store: ManualProbeControlStore | None,
    providers: list[dict[str, Any]],
) -> dict[str, ManualProbeJob]:
    if control_store is None:
        return {}
    return control_store.latest_jobs(
        str(provider.get("provider_id")) for provider in providers
    )


def _manual_histories(
    control_store: ManualProbeControlStore | None,
    providers: list[dict[str, Any]],
) -> dict[str, dict[str, tuple[dict[str, Any], ...]]]:
    if control_store is None:
        return {}
    return control_store.result_history(
        str(provider.get("provider_id")) for provider in providers
    )


def _public_manual_job(job: ManualProbeJob) -> dict[str, Any]:
    payload = public_manual_job(job)
    payload["error_summary"] = (
        "立即检测未能完成。" if payload.get("error_summary") else None
    )
    for result in payload["results"]:
        result["error_summary"] = _public_error_summary(
            result.get("error_code"),
            result.get("error_summary"),
        )
    return payload


def _public_error_summary(
    error_code: str | None,
    sanitized_summary: Any,
) -> str | None:
    if not error_code:
        return None
    if (
        error_code == "auth_failed"
        and isinstance(sanitized_summary, str)
        and _AUTH_HTTP_401_RE.search(sanitized_summary)
    ):
        return _AUTH_HTTP_401_SUMMARY
    return _ERROR_SUMMARIES.get(error_code)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(timezone.utc)


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["create_app", "main"]
