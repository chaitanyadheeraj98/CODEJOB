"""Schedule computation, including the DST test that was unimplementable
before a timezone existed.

America/New_York 2026: DST begins Sunday 8 March, ends Sunday 1 November.
EST is UTC-5, EDT is UTC-4 - so the same "9am" is 14:00 UTC in winter and
13:00 UTC in summer. A scheduler that stores one of those and calls it "9am"
is wrong for half the year.
"""

import unittest
from datetime import UTC, datetime

from app.services.scheduling.schedule import (
    SWEEP_FLOOR_MINUTES,
    InvalidSchedule,
    ScheduleSpec,
    describe,
    granularity_note,
    next_run_after,
    validate_cron,
)

WEEKDAY_9AM = "0 9 * * 1-5"


def weekdays_in_new_york(cron: str = WEEKDAY_9AM) -> ScheduleSpec:
    return ScheduleSpec(schedule_kind="recurring", cron_expression=cron, timezone="America/New_York")


class OneTimeTests(unittest.TestCase):
    def test_a_future_time_is_returned_unchanged(self) -> None:
        target = datetime(2026, 9, 10, 13, 0, tzinfo=UTC)
        spec = ScheduleSpec(schedule_kind="once", run_at=target)

        self.assertEqual(next_run_after(spec, datetime(2026, 9, 3, 0, 0, tzinfo=UTC)), target)

    def test_a_past_time_has_no_next_run(self) -> None:
        spec = ScheduleSpec(schedule_kind="once", run_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC))

        self.assertIsNone(next_run_after(spec, datetime(2026, 9, 3, 0, 0, tzinfo=UTC)))

    def test_a_one_time_task_with_no_time_has_no_next_run(self) -> None:
        spec = ScheduleSpec(schedule_kind="once", run_at=None)

        self.assertIsNone(next_run_after(spec, datetime(2026, 9, 3, 0, 0, tzinfo=UTC)))


class ClocklessKindsTests(unittest.TestCase):
    def test_condition_and_none_have_no_clock_trigger(self) -> None:
        for kind in ("condition", "none"):
            with self.subTest(kind=kind):
                spec = ScheduleSpec(schedule_kind=kind)
                self.assertIsNone(next_run_after(spec, datetime(2026, 9, 3, tzinfo=UTC)))


class DaylightSavingTests(unittest.TestCase):
    """The test the plan asked for three drafts running."""

    def test_the_same_cron_yields_different_utc_instants_across_the_transition(self) -> None:
        spec = weekdays_in_new_york()

        before = next_run_after(spec, datetime(2026, 3, 5, 20, 0, tzinfo=UTC))
        after = next_run_after(spec, datetime(2026, 3, 6, 15, 0, tzinfo=UTC))

        # Friday 6 March is still EST: 9am local is 14:00 UTC.
        self.assertEqual(before, datetime(2026, 3, 6, 14, 0, tzinfo=UTC))
        # Monday 9 March is EDT: the same 9am local is now 13:00 UTC.
        self.assertEqual(after, datetime(2026, 3, 9, 13, 0, tzinfo=UTC))
        self.assertNotEqual(before.hour, after.hour)

    def test_the_autumn_transition_moves_the_hour_back(self) -> None:
        spec = weekdays_in_new_york()

        before = next_run_after(spec, datetime(2026, 10, 29, 20, 0, tzinfo=UTC))
        after = next_run_after(spec, datetime(2026, 11, 1, 20, 0, tzinfo=UTC))

        self.assertEqual(before, datetime(2026, 10, 30, 13, 0, tzinfo=UTC))
        self.assertEqual(after, datetime(2026, 11, 2, 14, 0, tzinfo=UTC))

    # 2:30am does not exist on 8 March. The task must run late, not never.
    def test_a_task_in_the_skipped_hour_still_fires(self) -> None:
        spec = ScheduleSpec(
            schedule_kind="recurring", cron_expression="30 2 * * *", timezone="America/New_York"
        )

        moment = next_run_after(spec, datetime(2026, 3, 8, 6, 0, tzinfo=UTC))

        self.assertIsNotNone(moment)
        self.assertEqual(moment, datetime(2026, 3, 8, 7, 30, tzinfo=UTC))

    # 1:30am happens twice on 1 November; it must produce one run, not two.
    def test_the_repeated_hour_fires_once(self) -> None:
        spec = ScheduleSpec(
            schedule_kind="recurring", cron_expression="30 1 * * *", timezone="America/New_York"
        )

        first = next_run_after(spec, datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
        second = next_run_after(spec, first)

        self.assertEqual(first, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        # The next run is the following day, not the repeated hour an hour later.
        self.assertEqual(second, datetime(2026, 11, 2, 6, 30, tzinfo=UTC))

    def test_utc_schedules_are_unaffected(self) -> None:
        spec = ScheduleSpec(schedule_kind="recurring", cron_expression=WEEKDAY_9AM, timezone="UTC")

        moment = next_run_after(spec, datetime(2026, 3, 6, 15, 0, tzinfo=UTC))

        self.assertEqual(moment, datetime(2026, 3, 9, 9, 0, tzinfo=UTC))


class BadZoneTests(unittest.TestCase):
    # A bad zone must not stop the sweep: it falls back to UTC rather than
    # raising inside a loop that has other tasks to process.
    def test_an_unknown_zone_falls_back_to_utc_instead_of_raising(self) -> None:
        spec = ScheduleSpec(
            schedule_kind="recurring", cron_expression=WEEKDAY_9AM, timezone="Mars/Olympus_Mons"
        )

        moment = next_run_after(spec, datetime(2026, 3, 6, 15, 0, tzinfo=UTC))

        self.assertEqual(moment, datetime(2026, 3, 9, 9, 0, tzinfo=UTC))

    def test_an_unusable_expression_yields_no_next_run_rather_than_raising(self) -> None:
        spec = ScheduleSpec(schedule_kind="recurring", cron_expression="not a cron")

        self.assertIsNone(next_run_after(spec, datetime(2026, 9, 3, tzinfo=UTC)))


class ValidateCronTests(unittest.TestCase):
    def test_a_standard_expression_is_accepted(self) -> None:
        self.assertEqual(validate_cron(" 0 9 * * 1-5 "), "0 9 * * 1-5")

    def test_an_empty_expression_is_refused(self) -> None:
        with self.assertRaises(InvalidSchedule):
            validate_cron("")

    def test_shorthand_is_refused_by_name(self) -> None:
        with self.assertRaises(InvalidSchedule) as caught:
            validate_cron("@reboot")

        self.assertIn("@reboot", str(caught.exception))

    def test_a_six_field_seconds_expression_is_refused(self) -> None:
        with self.assertRaises(InvalidSchedule) as caught:
            validate_cron("*/30 * * * * *")

        self.assertIn("5-field", str(caught.exception))

    # Accepting an expression you will not obey is worse than refusing it.
    def test_a_sub_floor_frequency_is_refused_and_names_the_floor(self) -> None:
        with self.assertRaises(InvalidSchedule) as caught:
            validate_cron("* * * * *")

        self.assertIn(str(SWEEP_FLOOR_MINUTES), str(caught.exception))

    def test_the_floor_itself_is_allowed(self) -> None:
        self.assertEqual(validate_cron("*/5 * * * *"), "*/5 * * * *")

    def test_nonsense_is_refused(self) -> None:
        with self.assertRaises(InvalidSchedule):
            validate_cron("99 99 * * *")


class DescribeTests(unittest.TestCase):
    def test_a_weekday_schedule_reads_as_english_and_names_the_zone(self) -> None:
        text = describe(weekdays_in_new_york())

        self.assertEqual(text, "Every weekday at 9:00 AM (America/New_York)")

    def test_every_day_is_distinguished_from_every_weekday(self) -> None:
        spec = ScheduleSpec(
            schedule_kind="recurring", cron_expression="30 18 * * *", timezone="Europe/Berlin"
        )

        self.assertEqual(describe(spec), "Every day at 6:30 PM (Europe/Berlin)")

    def test_a_single_weekday_is_named(self) -> None:
        spec = ScheduleSpec(schedule_kind="recurring", cron_expression="0 8 * * 1", timezone="UTC")

        self.assertEqual(describe(spec), "Every Monday at 8:00 AM (UTC)")

    def test_a_monthly_day_is_described(self) -> None:
        spec = ScheduleSpec(schedule_kind="recurring", cron_expression="0 7 15 * *", timezone="UTC")

        self.assertEqual(describe(spec), "On day 15 of each month at 7:00 AM (UTC)")

    # A wrong paraphrase is worse than a raw expression, so anything with a
    # step or a list is quoted verbatim.
    def test_an_expression_it_cannot_paraphrase_is_quoted_verbatim(self) -> None:
        spec = ScheduleSpec(
            schedule_kind="recurring", cron_expression="*/15 9-17 * * 1-5", timezone="UTC"
        )

        self.assertIn("*/15 9-17 * * 1-5", describe(spec))

    def test_a_one_time_schedule_states_its_date_and_zone(self) -> None:
        spec = ScheduleSpec(
            schedule_kind="once",
            run_at=datetime(2026, 9, 10, 13, 0, tzinfo=UTC),
            timezone="America/New_York",
        )

        text = describe(spec)

        self.assertIn("Thursday 10 September 2026", text)
        self.assertIn("9:00 AM", text)
        self.assertIn("America/New_York", text)

    def test_a_checklist_says_it_has_no_schedule(self) -> None:
        self.assertIn("No schedule", describe(ScheduleSpec(schedule_kind="none")))

    def test_a_condition_task_says_it_is_checked_on_the_sweep(self) -> None:
        self.assertIn("condition", describe(ScheduleSpec(schedule_kind="condition")))

    def test_the_granularity_note_states_the_floor(self) -> None:
        self.assertIn(str(SWEEP_FLOOR_MINUTES), granularity_note())


if __name__ == "__main__":
    unittest.main()
