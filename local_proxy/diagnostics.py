from __future__ import annotations

import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol


DIAGNOSTIC_LOG_MAX_BYTES = 5 * 1024 * 1024
DIAGNOSTIC_LOG_BACKUP_COUNT = 3
WATCHDOG_STALL_THRESHOLD_SECONDS = 2.0
WATCHDOG_CHECK_INTERVAL_SECONDS = 0.5
WATCHDOG_REPORT_COOLDOWN_SECONDS = 30.0


class WatchdogDiagnostics(Protocol):
    def stalled_for_ms(self) -> float: ...

    def observe_watchdog_event(self, stalled_ms: float) -> None: ...


class DiagnosticLog:
    """Small rotating incident log that never receives request content."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = DIAGNOSTIC_LOG_MAX_BYTES,
        backup_count: int = DIAGNOSTIC_LOG_BACKUP_COUNT,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._logger = logging.getLogger(f"codex_provider_hub.diagnostics.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._handler = RotatingFileHandler(
            self.path,
            maxBytes=max(1024, int(max_bytes)),
            backupCount=max(1, int(backup_count)),
            encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(self._handler)

    def write_event(self, event: str, **fields: Any) -> None:
        payload = {
            "timestamp": round(time.time() * 1000),
            "event": str(event)[:80],
            **fields,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        with self._lock:
            self._logger.info(encoded)
            self._handler.flush()

    def dump_threads(self, event: str, **fields: Any) -> None:
        with self._lock:
            self.write_event(event, **fields)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write("--- python thread dump begin ---\n")
                stream.flush()
                faulthandler.dump_traceback(file=stream, all_threads=True)
                stream.write("--- python thread dump end ---\n")
                stream.flush()

    def close(self) -> None:
        with self._lock:
            self._logger.removeHandler(self._handler)
            self._handler.close()


class EventLoopWatchdog:
    """Observe an event-loop heartbeat from an independent native thread."""

    def __init__(
        self,
        diagnostics: WatchdogDiagnostics,
        diagnostic_log: DiagnosticLog,
        *,
        active_requests: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
        stall_threshold_seconds: float = WATCHDOG_STALL_THRESHOLD_SECONDS,
        check_interval_seconds: float = WATCHDOG_CHECK_INTERVAL_SECONDS,
        report_cooldown_seconds: float = WATCHDOG_REPORT_COOLDOWN_SECONDS,
    ) -> None:
        self._diagnostics = diagnostics
        self._diagnostic_log = diagnostic_log
        self._active_requests = active_requests
        self._stall_threshold_ms = max(100.0, float(stall_threshold_seconds) * 1000)
        self._check_interval_seconds = max(0.05, float(check_interval_seconds))
        self._report_cooldown_seconds = max(0.1, float(report_cooldown_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="local-proxy-event-loop-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def _run(self) -> None:
        last_reported_at = 0.0
        while not self._stop.wait(self._check_interval_seconds):
            stalled_ms = self._diagnostics.stalled_for_ms()
            now = time.monotonic()
            if stalled_ms < self._stall_threshold_ms:
                continue
            if now - last_reported_at < self._report_cooldown_seconds:
                continue
            last_reported_at = now
            requests: Sequence[Mapping[str, Any]] = ()
            if self._active_requests is not None:
                try:
                    requests = tuple(self._active_requests())[:100]
                except Exception as exc:  # Diagnostics must never take down the proxy.
                    requests = ({"snapshot_error": type(exc).__name__},)
            self._diagnostics.observe_watchdog_event(stalled_ms)
            try:
                self._diagnostic_log.dump_threads(
                    "event_loop_stall",
                    stalled_ms=round(stalled_ms),
                    active_requests=requests,
                )
            except OSError:
                continue
