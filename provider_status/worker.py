from __future__ import annotations

import argparse
import os
import random
import shutil
import signal
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Any

from provider_status.config import ServiceConfig, load_config, read_credential
from provider_status.claude_probe import ClaudeHealthProbe
from provider_status.control import DEFAULT_CONTROL_DATABASE, ManualProbeControlStore
from provider_status.probe import (
    CodexHealthProbe,
    HealthProbeResult,
    ProviderHealthProbe,
)
from provider_status.store import ProbeRecord, StatusStore


NowFactory = Callable[[], datetime]
Sleep = Callable[[float], None]
ResultCallback = Callable[[str, str, HealthProbeResult], None]
Jitter = Callable[[float], float]
IntervalSampler = Callable[[float, float], float]

# 文件系统记录的 mtime 与 datetime.now() 存在精度差异（尤其 Windows 上目录刚创建时
# mtime 可能略晚于紧随其后取到的 now），比较陈旧度时保留一个小容差，避免把刚创建的
# 目录误判为“比 cutoff 更新”而跳过清理。
_CLEANUP_TIME_TOLERANCE_SECONDS = 1.0


class StatusWorker:
    def __init__(
        self,
        config: ServiceConfig,
        store: StatusStore,
        probe: CodexHealthProbe,
        *,
        env: Mapping[str, str] | None = None,
        now_factory: NowFactory | None = None,
        sleep: Sleep = time.sleep,
        on_result: ResultCallback | None = None,
        jitter: Jitter | None = None,
        interval_sampler: IntervalSampler | None = None,
        control_store: ManualProbeControlStore | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._probe = probe
        self._env = dict(os.environ if env is None else env)
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._on_result = on_result
        self._jitter = jitter or (lambda seconds: seconds)
        self._interval_sampler = interval_sampler
        self._control_store = control_store
        self._stop_requested = Event()
        self._providers = {
            provider.provider_id: provider for provider in config.providers
        }

    def run_due_once(self, now: datetime | None = None) -> bool:
        if self._stop_requested.is_set():
            return False
        started_at = _as_utc(now or self._now_factory())
        due_targets = self._store.list_due_targets(started_at, limit=1)
        if not due_targets:
            return False

        target = due_targets[0]
        provider = self._providers.get(target.provider_id)
        if provider is None:
            raise KeyError(f"unknown configured provider: {target.provider_id}")
        api_key = read_credential(provider.credential_name, self._env)
        result = self._probe.run(provider, target.model, api_key)
        finished_at = started_at + timedelta(milliseconds=result.latency_ms)
        self._store.record_probe(
            target.id,
            ProbeRecord(
                started_at=started_at,
                success=result.success,
                latency_ms=result.latency_ms,
                error_code=result.error_code,
                error_summary=result.error_summary,
                http_status_code=result.http_status_code,
                failure_stage=result.failure_stage,
                diagnostic_source=result.diagnostic_source,
            ),
            finished_at,
            healthy_interval_seconds=self._sample_interval(
                provider.healthy_interval_seconds,
                provider.healthy_interval_max_seconds,
            ),
            unhealthy_interval_seconds=self._sample_interval(
                provider.unhealthy_interval_seconds,
                provider.unhealthy_interval_max_seconds,
            ),
        )
        self._store.publish_read_snapshot(self._config.public_database_path)
        if self._on_result is not None:
            self._on_result(provider.provider_id, target.model, result)
        return True

    def run_manual_once(self, now: datetime | None = None) -> bool:
        if self._stop_requested.is_set() or self._control_store is None:
            return False
        started_at = _as_utc(now or self._now_factory())
        job = self._control_store.claim_next(started_at)
        if job is None:
            return False

        provider = self._providers.get(job.provider_id)
        if provider is None:
            self._control_store.fail(job.job_id, started_at, "供应商已不在当前配置中。")
            return True

        configured_models = tuple(provider.display_models or provider.models)
        models = job.requested_models or configured_models
        self._control_store.set_total_models(job.job_id, len(models))
        current_time = started_at
        published_result = False
        try:
            api_key = read_credential(provider.credential_name, self._env)
            for position, model in enumerate(models):
                result = self._probe.run(provider, model, api_key)
                finished_at = current_time + timedelta(milliseconds=result.latency_ms)
                target = self._store.get_target(provider.provider_id, model)
                scheduled = target is not None
                if target is not None:
                    self._store.record_probe(
                        target.id,
                        ProbeRecord(
                            started_at=current_time,
                            success=result.success,
                            latency_ms=result.latency_ms,
                            error_code=result.error_code,
                            error_summary=result.error_summary,
                            http_status_code=result.http_status_code,
                            failure_stage=result.failure_stage,
                            diagnostic_source=result.diagnostic_source,
                        ),
                        finished_at,
                        healthy_interval_seconds=self._sample_interval(
                            provider.healthy_interval_seconds,
                            provider.healthy_interval_max_seconds,
                        ),
                        unhealthy_interval_seconds=self._sample_interval(
                            provider.unhealthy_interval_seconds,
                            provider.unhealthy_interval_max_seconds,
                        ),
                    )
                    published_result = True
                self._control_store.record_result(
                    job.job_id,
                    model=model,
                    position=position,
                    scheduled=scheduled,
                    success=result.success,
                    latency_ms=result.latency_ms,
                    error_code=result.error_code,
                    error_summary=result.error_summary,
                    finished_at=finished_at,
                    http_status_code=result.http_status_code,
                    failure_stage=result.failure_stage,
                    diagnostic_source=result.diagnostic_source,
                )
                if self._on_result is not None:
                    self._on_result(provider.provider_id, model, result)
                current_time = finished_at
            if published_result:
                self._store.publish_read_snapshot(self._config.public_database_path)
            self._control_store.complete(job.job_id, current_time)
        except Exception as exc:
            self._control_store.fail(
                job.job_id,
                current_time,
                str(exc) or type(exc).__name__,
            )
        return True

    def run_forever(self) -> None:
        while not self._stop_requested.is_set():
            if self.run_manual_once():
                continue
            if self.run_due_once():
                continue
            self._sleep(1.0)

    def request_stop(self, signum: int | None = None, frame: Any = None) -> None:
        del signum, frame
        self._stop_requested.set()

    def _sample_interval(
        self,
        minimum: float,
        maximum: float | None,
    ) -> float:
        if self._interval_sampler is not None:
            return self._interval_sampler(minimum, maximum or minimum)
        if maximum is not None and maximum != minimum:
            return _random_interval(minimum, maximum)
        return self._jitter(minimum)


def cleanup_stale_run_directories(
    temp_root: Path,
    now: datetime,
    *,
    max_age: timedelta = timedelta(0),
) -> int:
    root = Path(temp_root)
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve()
    cutoff = _as_utc(now).timestamp() - max_age.total_seconds()
    removed = 0
    for candidate in root.iterdir():
        if (
            not candidate.name.startswith("provider-probe-")
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        try:
            if candidate.resolve().parent != root_resolved:
                continue
            # 仅当目录更新时间明确晚于 cutoff（超出容差）时才保留；默认 max_age=0 时
            # 刚创建的目录因 mtime 与 now 的精度差异落在容差内，视为陈旧予以清理。
            if candidate.stat().st_mtime - cutoff > _CLEANUP_TIME_TOLERANCE_SECONDS:
                continue
            shutil.rmtree(candidate)
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(timezone.utc)


def _random_interval(minimum: float, maximum: float) -> float:
    return random.uniform(minimum, maximum)


def _bounded_jitter(interval_seconds: float) -> float:
    """保留旧调用方的固定间隔抖动兼容性。"""
    return _random_interval(interval_seconds * 0.95, interval_seconds * 1.05)


def _print_result(
    provider_id: str,
    model: str,
    result: HealthProbeResult,
) -> None:
    status = "healthy" if result.success else result.error_code or "unknown_error"
    print(
        f"provider={provider_id} model={model} status={status} "
        f"latency_ms={result.latency_ms}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex provider status worker")
    parser.add_argument(
        "--config",
        default="/etc/codex-provider-probe/providers.toml",
    )
    parser.add_argument(
        "--control-database",
        default=str(DEFAULT_CONTROL_DATABASE),
    )
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(timezone.utc)
    config = load_config(Path(args.config))
    store = StatusStore(config.database_path)
    store.initialize(config.providers, now)
    store.delete_history_before(now - timedelta(days=90))
    store.resanitize_error_summaries()
    store.publish_read_snapshot(config.public_database_path)
    cleanup_stale_run_directories(config.temp_root, now)
    control_store = ManualProbeControlStore(Path(args.control_database))
    control_store.initialize()
    control_store.recover_interrupted_jobs(now)
    codex_probe = CodexHealthProbe(config.codex_bin, config.temp_root)
    claude_probe = (
        ClaudeHealthProbe(config.claude_bin, config.temp_root)
        if config.claude_bin is not None
        else None
    )
    probe = ProviderHealthProbe(codex_probe, claude_probe)
    worker = StatusWorker(
        config,
        store,
        probe,
        on_result=_print_result,
        interval_sampler=_random_interval,
        control_store=control_store,
    )
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    if args.once:
        worker.run_due_once(now)
        return 0
    worker.run_forever()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)


__all__ = ["StatusWorker", "cleanup_stale_run_directories", "main"]
