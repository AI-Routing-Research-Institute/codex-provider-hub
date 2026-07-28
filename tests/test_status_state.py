import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone, tzinfo

from provider_status.state import (
    AvailabilityEvent,
    TargetState,
    Transition,
    aggregate_provider_state,
    time_weighted_availability,
    transition_target,
)


class NewYork2026Timezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        if value is None:
            return None
        if value.month < 3 or value.month > 11:
            return timedelta(hours=-5)
        if 3 < value.month < 11:
            return timedelta(hours=-4)
        if value.month == 3:
            return timedelta(hours=-4 if value.day > 8 or value.hour >= 3 else -5)
        if value.day < 1 or (value.day == 1 and value.hour < 1):
            return timedelta(hours=-4)
        if value.day == 1 and value.hour == 1:
            return timedelta(hours=-5 if value.fold else -4)
        return timedelta(hours=-5)

    def dst(self, value: datetime | None) -> timedelta | None:
        offset = self.utcoffset(value)
        return None if offset is None else offset - timedelta(hours=-5)

    def tzname(self, value: datetime | None) -> str | None:
        return "EDT" if self.dst(value) else "EST"


class TargetTransitionTests(unittest.TestCase):
    def test_transition_uses_supplied_intervals(self) -> None:
        healthy = transition_target(
            TargetState.HEALTHY,
            2,
            0,
            True,
            healthy_interval_seconds=37,
            unhealthy_interval_seconds=11,
        )
        degraded = transition_target(
            TargetState.HEALTHY,
            2,
            0,
            False,
            healthy_interval_seconds=37,
            unhealthy_interval_seconds=11,
        )

        self.assertEqual(healthy.next_interval_seconds, 37)
        self.assertEqual(degraded.next_interval_seconds, 11)

    def test_transition_table(self) -> None:
        cases = (
            (
                "initial success",
                TargetState.UNKNOWN,
                0,
                0,
                True,
                Transition(TargetState.HEALTHY, 1, 0, 600),
            ),
            (
                "initial failure",
                TargetState.UNKNOWN,
                0,
                0,
                False,
                Transition(TargetState.DEGRADED, 0, 1, 120),
            ),
            (
                "healthy success",
                TargetState.HEALTHY,
                1,
                0,
                True,
                Transition(TargetState.HEALTHY, 2, 0, 600),
            ),
            (
                "first healthy failure",
                TargetState.HEALTHY,
                2,
                0,
                False,
                Transition(TargetState.DEGRADED, 0, 1, 120),
            ),
            (
                "second consecutive failure",
                TargetState.DEGRADED,
                0,
                1,
                False,
                Transition(TargetState.DOWN, 0, 2, 120),
            ),
            (
                "continued failure",
                TargetState.DOWN,
                0,
                2,
                False,
                Transition(TargetState.DOWN, 0, 3, 120),
            ),
            (
                "degraded first success",
                TargetState.DEGRADED,
                0,
                1,
                True,
                Transition(TargetState.RECOVERING, 1, 0, 120),
            ),
            (
                "down first success",
                TargetState.DOWN,
                0,
                2,
                True,
                Transition(TargetState.RECOVERING, 1, 0, 120),
            ),
            (
                "second consecutive success",
                TargetState.RECOVERING,
                1,
                0,
                True,
                Transition(TargetState.HEALTHY, 2, 0, 600),
            ),
            (
                "recovery failure",
                TargetState.RECOVERING,
                1,
                0,
                False,
                Transition(TargetState.DEGRADED, 0, 1, 120),
            ),
        )

        for label, state, successes, failures, success, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    transition_target(state, successes, failures, success),
                    expected,
                )

    def test_transition_is_frozen(self) -> None:
        transition = Transition(TargetState.UNKNOWN, 0, 0, 120)

        with self.assertRaises(FrozenInstanceError):
            transition.state = TargetState.HEALTHY


class AggregateProviderStateTests(unittest.TestCase):
    def test_empty_targets_are_unknown(self) -> None:
        self.assertEqual(aggregate_provider_state(()), TargetState.UNKNOWN)

    def test_aggregates_by_remaining_availability(self) -> None:
        cases = (
            ((TargetState.HEALTHY, TargetState.HEALTHY), TargetState.HEALTHY),
            (
                (TargetState.HEALTHY, TargetState.RECOVERING),
                TargetState.RECOVERING,
            ),
            ((TargetState.RECOVERING, TargetState.RECOVERING), TargetState.RECOVERING),
            ((TargetState.HEALTHY, TargetState.DOWN), TargetState.DEGRADED),
            ((TargetState.HEALTHY, TargetState.DEGRADED), TargetState.DEGRADED),
            ((TargetState.RECOVERING, TargetState.DOWN), TargetState.DEGRADED),
            ((TargetState.DOWN, TargetState.UNKNOWN), TargetState.DEGRADED),
            ((TargetState.DOWN, TargetState.DOWN), TargetState.DOWN),
            ((TargetState.UNKNOWN, TargetState.UNKNOWN), TargetState.UNKNOWN),
        )

        for states, expected in cases:
            with self.subTest(states=states, expected=expected):
                self.assertEqual(aggregate_provider_state(states), expected)


class TimeWeightedAvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)

    def test_weights_availability_by_elapsed_time(self) -> None:
        events = (
            AvailabilityEvent(at=self.t0, success=True),
            AvailabilityEvent(at=self.t0 + timedelta(hours=6), success=False),
            AvailabilityEvent(at=self.t0 + timedelta(hours=8), success=True),
        )

        availability = time_weighted_availability(
            events,
            self.t0,
            self.t0 + timedelta(hours=10),
        )

        self.assertAlmostEqual(availability, 80.0)

    def test_spring_dst_transition_uses_absolute_elapsed_time(self) -> None:
        eastern = NewYork2026Timezone()
        start = datetime(2026, 3, 8, 0, 0, tzinfo=eastern)
        end = datetime(2026, 3, 8, 4, 0, tzinfo=eastern)
        events = (
            AvailabilityEvent(at=start, success=True),
            AvailabilityEvent(
                at=datetime(2026, 3, 8, 1, 30, tzinfo=eastern),
                success=False,
            ),
            AvailabilityEvent(
                at=datetime(2026, 3, 8, 3, 30, tzinfo=eastern),
                success=True,
            ),
        )

        availability = time_weighted_availability(events, start, end)

        self.assertAlmostEqual(availability, 200 / 3)

    def test_fall_dst_fold_distinguishes_repeated_hour(self) -> None:
        eastern = NewYork2026Timezone()
        start = datetime(2026, 11, 1, 0, 30, tzinfo=eastern)
        end = datetime(2026, 11, 1, 2, 30, tzinfo=eastern, fold=1)
        events = (
            AvailabilityEvent(at=start, success=True),
            AvailabilityEvent(
                at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=0),
                success=False,
            ),
            AvailabilityEvent(
                at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=1),
                success=True,
            ),
        )

        availability = time_weighted_availability(events, start, end)

        self.assertAlmostEqual(availability, 200 / 3)

    def test_last_event_before_window_determines_initial_state(self) -> None:
        events = (
            AvailabilityEvent(at=self.t0 - timedelta(hours=3), success=False),
            AvailabilityEvent(at=self.t0 + timedelta(hours=2), success=True),
        )

        availability = time_weighted_availability(
            events,
            self.t0,
            self.t0 + timedelta(hours=4),
        )

        self.assertAlmostEqual(availability, 50.0)

    def test_unknown_prefix_is_excluded_from_denominator(self) -> None:
        events = (AvailabilityEvent(at=self.t0 + timedelta(hours=2), success=True),)

        availability = time_weighted_availability(
            events,
            self.t0,
            self.t0 + timedelta(hours=4),
        )

        self.assertAlmostEqual(availability, 100.0)

    def test_events_at_window_boundaries_have_exact_boundary_semantics(self) -> None:
        events = (
            AvailabilityEvent(at=self.t0 - timedelta(hours=1), success=False),
            AvailabilityEvent(at=self.t0, success=True),
            AvailabilityEvent(at=self.t0 + timedelta(hours=1), success=False),
        )

        availability = time_weighted_availability(
            events,
            self.t0,
            self.t0 + timedelta(hours=1),
        )

        self.assertAlmostEqual(availability, 100.0)

    def test_event_after_window_end_is_ignored(self) -> None:
        events = (
            AvailabilityEvent(at=self.t0, success=True),
            AvailabilityEvent(at=self.t0 + timedelta(hours=2), success=False),
        )

        availability = time_weighted_availability(
            events,
            self.t0,
            self.t0 + timedelta(hours=1),
        )

        self.assertAlmostEqual(availability, 100.0)

    def test_last_input_event_wins_at_same_timestamp(self) -> None:
        events = (
            AvailabilityEvent(at=self.t0, success=False),
            AvailabilityEvent(at=self.t0, success=True),
        )

        availability = time_weighted_availability(
            events,
            self.t0,
            self.t0 + timedelta(hours=1),
        )

        self.assertAlmostEqual(availability, 100.0)

    def test_event_is_frozen(self) -> None:
        event = AvailabilityEvent(at=self.t0, success=True)

        with self.assertRaises(FrozenInstanceError):
            event.success = False

    def test_no_known_interval_returns_none(self) -> None:
        cases = (
            (),
            (AvailabilityEvent(at=self.t0 + timedelta(hours=1), success=True),),
        )

        for events in cases:
            with self.subTest(events=events):
                self.assertIsNone(
                    time_weighted_availability(
                        events,
                        self.t0,
                        self.t0 + timedelta(hours=1),
                    )
                )

    def test_rejects_invalid_window(self) -> None:
        for end in (self.t0, self.t0 - timedelta(seconds=1)):
            with self.subTest(end=end):
                with self.assertRaisesRegex(ValueError, "start must be before end"):
                    time_weighted_availability((), self.t0, end)

    def test_rejects_mixed_naive_and_aware_datetimes(self) -> None:
        naive_start = self.t0.replace(tzinfo=None)
        aware_event = AvailabilityEvent(at=self.t0, success=True)

        with self.assertRaisesRegex(ValueError, "naive and aware"):
            time_weighted_availability(
                (aware_event,),
                naive_start,
                naive_start + timedelta(hours=1),
            )

        with self.assertRaisesRegex(ValueError, "naive and aware"):
            time_weighted_availability(
                (),
                naive_start,
                self.t0 + timedelta(hours=1),
            )


if __name__ == "__main__":
    unittest.main()
