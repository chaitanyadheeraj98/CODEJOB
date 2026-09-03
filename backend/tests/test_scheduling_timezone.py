"""The timezone foundation.

The two DST edge cases are the point of this file. Everything else in the
product stores naive UTC and never has to think about them; a scheduler does,
twice a year, and the failure mode is silence.
"""

import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import UserSettings
from app.schemas import SettingsRequest
from app.services.scheduling.timezone import (
    UTC_ZONE,
    from_user_local,
    is_valid_timezone,
    to_user_local,
    user_zone,
    zone_for,
)

NEW_YORK = ZoneInfo("America/New_York")
OWNER = "owner-under-test"


class ZoneResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def settings_row(self, timezone: str) -> None:
        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=OWNER, timezone=timezone))
            db.commit()

    def test_a_missing_settings_row_resolves_to_utc(self) -> None:
        with self.SessionLocal() as db:
            self.assertEqual(user_zone(db, owner_id=OWNER), UTC_ZONE)

    def test_an_empty_timezone_resolves_to_utc(self) -> None:
        self.settings_row("")

        with self.SessionLocal() as db:
            self.assertEqual(user_zone(db, owner_id=OWNER), UTC_ZONE)

    # A bad zone on one task must not stop the sweep processing every other
    # task, so this degrades rather than raising.
    def test_an_unknown_timezone_resolves_to_utc_without_raising(self) -> None:
        self.settings_row("Mars/Olympus_Mons")

        with self.SessionLocal() as db:
            self.assertEqual(user_zone(db, owner_id=OWNER), UTC_ZONE)

    def test_a_real_timezone_is_returned(self) -> None:
        self.settings_row("America/New_York")

        with self.SessionLocal() as db:
            self.assertEqual(user_zone(db, owner_id=OWNER), NEW_YORK)

    def test_zone_for_accepts_known_names_and_rejects_offsets(self) -> None:
        self.assertEqual(zone_for("America/New_York"), NEW_YORK)
        # An offset is not a zone: it does not survive DST, which is the whole
        # reason this column stores IANA names.
        self.assertEqual(zone_for("-05:00"), UTC_ZONE)


class ValidationTests(unittest.TestCase):
    def test_the_schema_rejects_an_unknown_iana_name(self) -> None:
        with self.assertRaises(ValueError):
            SettingsRequest(timezone="Not/AZone")

    def test_the_schema_defaults_an_empty_value_to_utc(self) -> None:
        self.assertEqual(SettingsRequest(timezone="").timezone, "UTC")

    def test_a_real_name_is_accepted(self) -> None:
        self.assertEqual(SettingsRequest(timezone="Europe/Berlin").timezone, "Europe/Berlin")

    def test_is_valid_timezone_agrees_with_the_validator(self) -> None:
        self.assertTrue(is_valid_timezone("Asia/Kolkata"))
        self.assertFalse(is_valid_timezone("Asia/Nowhere"))
        self.assertFalse(is_valid_timezone(""))


class DaylightSavingTests(unittest.TestCase):
    """America/New_York 2026: DST begins 8 March, ends 1 November."""

    def test_a_normal_winter_morning_is_utc_minus_five(self) -> None:
        moment = from_user_local(datetime(2026, 1, 15, 9, 0), NEW_YORK)

        self.assertEqual(moment, datetime(2026, 1, 15, 14, 0, tzinfo=UTC))

    def test_a_normal_summer_morning_is_utc_minus_four(self) -> None:
        moment = from_user_local(datetime(2026, 7, 15, 9, 0), NEW_YORK)

        self.assertEqual(moment, datetime(2026, 7, 15, 13, 0, tzinfo=UTC))

    # 2:30am does not exist on the spring-forward day. Shifting forward means
    # the task runs late; the alternative is that it never runs at all.
    def test_the_skipped_hour_shifts_forward_to_the_first_valid_instant(self) -> None:
        moment = from_user_local(datetime(2026, 3, 8, 2, 30), NEW_YORK)

        self.assertEqual(moment, datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
        self.assertEqual(to_user_local(moment, NEW_YORK).hour, 3)

    # 1:30am happens twice on the autumn day. Picking the first occurrence means
    # the task runs once, at the earlier instant, rather than twice.
    def test_the_repeated_hour_picks_the_first_occurrence(self) -> None:
        moment = from_user_local(datetime(2026, 11, 1, 1, 30), NEW_YORK)

        self.assertEqual(moment, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))

    def test_round_tripping_is_stable_outside_transitions(self) -> None:
        for local in (
            datetime(2026, 1, 15, 9, 0),
            datetime(2026, 6, 30, 17, 45),
            datetime(2026, 12, 31, 23, 59),
        ):
            with self.subTest(local=local):
                back = to_user_local(from_user_local(local, NEW_YORK), NEW_YORK)
                self.assertEqual(back.replace(tzinfo=None), local)

    def test_to_user_local_treats_a_naive_instant_as_utc(self) -> None:
        local = to_user_local(datetime(2026, 1, 15, 14, 0), NEW_YORK)

        self.assertEqual(local.hour, 9)


class MigrationDefaultTests(unittest.TestCase):
    def test_a_row_created_without_a_timezone_reads_utc(self) -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        try:
            with SessionLocal() as db:
                db.add(UserSettings(owner_id=OWNER))
                db.commit()
                row = db.query(UserSettings).one()
                self.assertEqual(row.timezone, "UTC")
                self.assertEqual(row.feature_scheduling_sweep_interval_minutes, 15)
        finally:
            Base.metadata.drop_all(engine)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
