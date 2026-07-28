from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


HEALTHY_INTERVAL_SECONDS = 600
UNHEALTHY_INTERVAL_SECONDS = 120


class TargetState(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    RECOVERING = "recovering"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class Transition:
    state: TargetState
    consecutive_successes: int
    consecutive_failures: int
    next_interval_seconds: float


@dataclass(frozen=True)
class AvailabilityEvent:
    at: datetime
    success: bool


def transition_target(
    state: TargetState,
    consecutive_successes: int,
    consecutive_failures: int,
    success: bool,
    *,
    healthy_interval_seconds: float = HEALTHY_INTERVAL_SECONDS,
    unhealthy_interval_seconds: float = UNHEALTHY_INTERVAL_SECONDS,
) -> Transition:
    if success:
        successes = consecutive_successes + 1
        failures = 0
        if state is TargetState.UNKNOWN:
            next_state = TargetState.HEALTHY
        elif state in (TargetState.DEGRADED, TargetState.DOWN):
            next_state = TargetState.RECOVERING
        elif state is TargetState.RECOVERING and successes >= 2:
            next_state = TargetState.HEALTHY
        else:
            next_state = state
    else:
        successes = 0
        failures = consecutive_failures + 1
        next_state = (
            TargetState.DOWN if failures >= 2 else TargetState.DEGRADED
        )

    interval = (
        healthy_interval_seconds
        if next_state is TargetState.HEALTHY
        else unhealthy_interval_seconds
    )
    return Transition(next_state, successes, failures, interval)


def aggregate_provider_state(states: Iterable[TargetState]) -> TargetState:
    values = tuple(states)
    if not values or all(state is TargetState.UNKNOWN for state in values):
        return TargetState.UNKNOWN
    if all(state is TargetState.HEALTHY for state in values):
        return TargetState.HEALTHY
    if all(
        state in (TargetState.HEALTHY, TargetState.RECOVERING)
        for state in values
    ):
        return TargetState.RECOVERING
    if all(state is TargetState.DOWN for state in values):
        return TargetState.DOWN
    return TargetState.DEGRADED


def time_weighted_availability(
    events: Sequence[AvailabilityEvent],
    start: datetime,
    end: datetime,
) -> float | None:
    is_aware = _validate_datetime_awareness(events, start, end)
    if is_aware:
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        normalized_events = tuple(
            (event.at.astimezone(timezone.utc), event.success) for event in events
        )
    else:
        normalized_events = tuple((event.at, event.success) for event in events)
    if start >= end:
        raise ValueError("start must be before end")

    current: bool | None = None
    cursor = start
    known_seconds = 0.0
    available_seconds = 0.0

    for event_at, event_success in sorted(normalized_events, key=lambda item: item[0]):
        if event_at < start:
            current = event_success
            continue
        if event_at > end:
            break

        elapsed = (event_at - cursor).total_seconds()
        if current is not None:
            known_seconds += elapsed
            if current:
                available_seconds += elapsed
        cursor = event_at
        current = event_success

    if current is not None:
        elapsed = (end - cursor).total_seconds()
        known_seconds += elapsed
        if current:
            available_seconds += elapsed

    if known_seconds == 0:
        return None
    return available_seconds / known_seconds * 100.0


def _validate_datetime_awareness(
    events: Sequence[AvailabilityEvent],
    start: datetime,
    end: datetime,
) -> bool:
    datetimes = (start, end, *(event.at for event in events))
    awareness = tuple(
        value.tzinfo is not None and value.utcoffset() is not None
        for value in datetimes
    )
    if any(flag != awareness[0] for flag in awareness[1:]):
        raise ValueError("naive and aware datetimes cannot be mixed")
    return awareness[0]
