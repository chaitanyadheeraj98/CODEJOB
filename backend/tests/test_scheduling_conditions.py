"""Whitelisted condition predicates.

Each predicate is tested at its boundary day - N-1 does not fire, N does -
because an off-by-one in "no reply for 3 days" is the difference between a
useful monitor and one the user stops trusting.
"""

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Application, PremiumNumberContact, RecruiterOpportunity
from app.services.scheduling.conditions import (
    DEFAULT_DAYS,
    PREDICATE_SUBJECTS,
    PREDICATES,
    UnknownPredicate,
    describe_predicates,
    evaluate,
)

OWNER = "owner"
# A Thursday, so business-day arithmetic crosses a weekend within six days.
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class ConditionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def application(
        self,
        db: Session,
        application_id: int,
        *,
        status_changed_at: datetime,
        status: str = "contacted",
        owner_id: str = OWNER,
        next_action_at: datetime | None = None,
    ) -> Application:
        row = Application(
            id=application_id,
            owner_id=owner_id,
            resume_asset_id=application_id,
            resume_version_snapshot=1,
            resume_file_name_snapshot=f"r{application_id}.pdf",
            resume_sha256_snapshot=str(application_id % 10) * 64,
            recruiter_opportunity_id=application_id,
            recruiter_contact_id=1,
            job_title_snapshot="Java Developer",
            end_client_snapshot="Bank X",
            status=status,
            status_changed_at=status_changed_at,
            next_action_at=next_action_at,
            created_at=status_changed_at,
            updated_at=status_changed_at,
        )
        db.add(row)
        db.flush()
        return row

    def check(self, db: Session, **condition):
        return evaluate(db, owner_id=OWNER, condition=condition, now=NOW)


class WhitelistTests(ConditionTests):
    # No eval, no expression language, no user-supplied SQL.
    def test_an_unknown_predicate_raises(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(UnknownPredicate):
                self.check(db, predicate="__import__('os').system", subject_type="application")

    def test_an_empty_predicate_raises(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(UnknownPredicate):
                self.check(db, predicate="", subject_type="application")

    # Refused, never approximated: a monitor that silently watches something
    # adjacent to what was asked is worse than no monitor.
    def test_a_predicate_that_cannot_read_a_subject_is_refused_by_name(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(UnknownPredicate) as caught:
                self.check(db, predicate="no_reply_for_days", subject_type="contact")

            self.assertIn("application", str(caught.exception))

    def test_every_predicate_declares_its_subjects_and_is_described(self) -> None:
        described = {entry["predicate"] for entry in describe_predicates()}

        self.assertEqual(described, set(PREDICATES))
        for name in PREDICATES:
            self.assertTrue(PREDICATE_SUBJECTS[name])


class NoReplyTests(ConditionTests):
    def test_the_boundary_day_fires_and_the_day_before_does_not(self) -> None:
        with Session(self.engine) as db:
            # 3 business days before Thursday 3 Sept is Monday 31 Aug.
            self.application(db, 1, status_changed_at=datetime(2026, 8, 31, 11, 0, tzinfo=UTC))
            self.application(db, 2, status_changed_at=datetime(2026, 9, 1, 13, 0, tzinfo=UTC))
            db.commit()

            result = self.check(db, predicate="no_reply_for_days", subject_type="application", days=3)

            self.assertTrue(result.fired)
            self.assertEqual(result.subject_ids, ["1"])

    def test_the_default_is_the_shipped_literal(self) -> None:
        self.assertEqual(DEFAULT_DAYS["no_reply_for_days"], 3)

    # Six business days back from Thursday crosses a weekend.
    def test_business_days_skip_the_weekend(self) -> None:
        with Session(self.engine) as db:
            self.application(db, 1, status_changed_at=datetime(2026, 8, 26, 11, 0, tzinfo=UTC))
            self.application(db, 2, status_changed_at=datetime(2026, 8, 27, 13, 0, tzinfo=UTC))
            db.commit()

            result = self.check(db, predicate="no_reply_for_days", subject_type="application", days=6)

            self.assertEqual(result.subject_ids, ["1"])

    def test_an_application_with_a_next_action_is_excluded(self) -> None:
        with Session(self.engine) as db:
            self.application(
                db,
                1,
                status_changed_at=NOW - timedelta(days=30),
                next_action_at=NOW + timedelta(days=1),
            )
            db.commit()

            self.assertFalse(self.check(db, predicate="no_reply_for_days", subject_type="application").fired)

    def test_a_closed_application_is_excluded(self) -> None:
        with Session(self.engine) as db:
            self.application(db, 1, status_changed_at=NOW - timedelta(days=30), status="rejected")
            db.commit()

            self.assertFalse(self.check(db, predicate="no_reply_for_days", subject_type="application").fired)

    def test_evidence_names_the_actual_record(self) -> None:
        with Session(self.engine) as db:
            self.application(db, 7, status_changed_at=NOW - timedelta(days=30))
            db.commit()

            result = self.check(db, predicate="no_reply_for_days", subject_type="application")

            self.assertEqual(len(result.evidence), 1)
            self.assertIn("#7", result.evidence[0].normalized_to)
            self.assertIn("3 business days", result.evidence[0].right_value)

    def test_owner_scoping(self) -> None:
        with Session(self.engine) as db:
            self.application(db, 1, status_changed_at=NOW - timedelta(days=30), owner_id="other")
            db.commit()

            self.assertFalse(self.check(db, predicate="no_reply_for_days", subject_type="application").fired)


class StatusUnchangedTests(ConditionTests):
    def test_the_boundary_day_fires(self) -> None:
        with Session(self.engine) as db:
            self.application(db, 1, status_changed_at=NOW - timedelta(days=21))
            self.application(db, 2, status_changed_at=NOW - timedelta(days=20))
            db.commit()

            result = self.check(
                db, predicate="status_unchanged_for_days", subject_type="application", days=21
            )

            self.assertEqual(result.subject_ids, ["1"])

    def test_the_default_is_the_shipped_literal(self) -> None:
        self.assertEqual(DEFAULT_DAYS["status_unchanged_for_days"], 21)


class DateReachedTests(ConditionTests):
    def test_a_past_date_fires_and_a_future_one_does_not(self) -> None:
        with Session(self.engine) as db:
            self.assertTrue(self.check(db, predicate="date_reached", date="2026-09-01").fired)
            self.assertFalse(self.check(db, predicate="date_reached", date="2026-12-01").fired)

    def test_a_missing_date_is_refused(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(UnknownPredicate):
                self.check(db, predicate="date_reached")

    def test_an_unreadable_date_is_refused_rather_than_guessed(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(UnknownPredicate):
                self.check(db, predicate="date_reached", date="next Tuesday-ish")


class CountThresholdTests(ConditionTests):
    def test_the_threshold_boundary(self) -> None:
        with Session(self.engine) as db:
            for index in (1, 2, 3):
                self.application(db, index, status_changed_at=NOW)
            db.commit()

            self.assertTrue(
                self.check(db, predicate="count_threshold", subject_type="application", threshold=3).fired
            )
            self.assertFalse(
                self.check(db, predicate="count_threshold", subject_type="application", threshold=4).fired
            )

    def test_it_counts_other_subject_types(self) -> None:
        with Session(self.engine) as db:
            db.add(RecruiterOpportunity(owner_id=OWNER, recruiter_number_id=1, gmail_message_id="m1"))
            db.add(PremiumNumberContact(owner_id=OWNER, display_phone_number="+1 555 000 0000"))
            db.commit()

            for subject in ("opportunity", "contact"):
                with self.subTest(subject=subject):
                    result = self.check(
                        db, predicate="count_threshold", subject_type=subject, threshold=1
                    )
                    self.assertTrue(result.fired)

    def test_owner_scoping(self) -> None:
        with Session(self.engine) as db:
            self.application(db, 1, status_changed_at=NOW, owner_id="other")
            db.commit()

            self.assertFalse(
                self.check(db, predicate="count_threshold", subject_type="application", threshold=1).fired
            )


class DeletedSubjectTests(ConditionTests):
    # A monitor over a subject that has since been deleted must not fire and
    # must not raise.
    def test_a_deleted_application_neither_fires_nor_raises(self) -> None:
        with Session(self.engine) as db:
            row = self.application(db, 1, status_changed_at=NOW - timedelta(days=60))
            row.deleted_at = NOW
            db.commit()

            result = self.check(db, predicate="no_reply_for_days", subject_type="application")

            self.assertFalse(result.fired)
            self.assertEqual(result.subject_ids, [])


if __name__ == "__main__":
    unittest.main()
