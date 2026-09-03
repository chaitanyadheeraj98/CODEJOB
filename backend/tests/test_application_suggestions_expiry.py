"""W3: reconciling the two pending-work systems.

Two shipped defects are pinned here.

The first is the suppression guard. `_pending_suggestion` refused to create a
newer suggestion while an older one was pending, so one unreviewed item silently
suppressed every future item of that type for that application - and with
nothing ever expiring, forever. Newest-wins replaces it.

The second is expiry itself. This codebase ships two `expires_at` columns that
are written, displayed, and never compared against a clock. These tests compare.
"""

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    SUGGESTION_RETENTION_HOURS,
    Application,
    ApplicationSuggestion,
)
from app.services import application_intelligence_service as service
from app.services.application_service import ApplicationValidationError, accept_suggestion

OWNER = "owner"


def application(application_id: int = 1, *, status: str = "contacted") -> Application:
    now = datetime.now(UTC)
    return Application(
        id=application_id,
        owner_id=OWNER,
        resume_asset_id=application_id,
        resume_version_snapshot=1,
        resume_file_name_snapshot=f"resume-{application_id}.pdf",
        resume_sha256_snapshot=str(application_id % 10) * 64,
        recruiter_opportunity_id=application_id,
        recruiter_contact_id=1,
        job_title_snapshot="Java Developer",
        end_client_snapshot="Bank X",
        status=status,
        status_changed_at=now,
        created_at=now,
        updated_at=now,
    )


class SuggestionExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def reminder(self, db: Session, row: Application, *, reason: str, next_action_type: str = "Follow up"):
        return service._create_reminder(
            db,
            row,
            suggestion_type="next_action",
            reason=reason,
            next_action_type=next_action_type,
            next_action_at=datetime.now(UTC),
        )


class SupersedeTests(SuggestionExpiryTests):
    def test_a_different_claim_supersedes_the_pending_one(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()

            first = self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()
            second = self.reminder(db, row, reason="No update in 6 business days")
            db.flush()

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(first.status, "expired")
            self.assertEqual(first.expiry_reason, "superseded")
            self.assertIsNotNone(first.resolved_at)
            self.assertEqual(second.status, "pending")

    # The sweep recomputes identical predicates every pass. Expiring and
    # recreating the same claim would churn the table and, worse, reset
    # created_at - destroying the only signal that says "this has been waiting
    # three weeks".
    def test_the_same_claim_recomputed_leaves_the_original_standing(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()

            first = self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()
            original_created_at = first.created_at
            again = self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()

            self.assertIsNone(again)
            self.assertEqual(first.status, "pending")
            self.assertEqual(first.created_at, original_created_at)
            self.assertEqual(db.query(ApplicationSuggestion).count(), 1)

    # suggested_next_action_at is set to `now` on every sweep. If it counted as
    # a difference, every recomputation would look like new information and the
    # churn guard above would never fire.
    def test_a_changing_next_action_timestamp_is_not_a_difference(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()

            self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()
            later = service._create_reminder(
                db,
                row,
                suggestion_type="next_action",
                reason="No reply in 3 business days",
                next_action_type="Follow up",
                next_action_at=datetime.now(UTC) + timedelta(days=1),
            )

            self.assertIsNone(later)

    def test_a_non_superseding_type_keeps_the_existing_item(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()
            existing = ApplicationSuggestion(
                owner_id=OWNER,
                application_id=row.id,
                suggestion_type="status_change",
                status="pending",
                reason="Matched phrase: 'offer'",
                created_at=datetime.now(UTC),
            )
            db.add(existing)
            db.flush()

            expired, should_create = service._supersede_pending(
                db, OWNER, row.id, "status_change", reason="Something else", next_action_type=None
            )

            self.assertEqual(expired, 0)
            self.assertFalse(should_create)
            self.assertEqual(existing.status, "pending")

    def test_a_stale_prompt_supersedes_only_on_a_changed_reason(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()
            service._create_reminder(
                db, row, suggestion_type="stale_prompt", reason="No activity in 3 weeks"
            )
            db.flush()

            unchanged = service._create_reminder(
                db, row, suggestion_type="stale_prompt", reason="No activity in 3 weeks"
            )
            changed = service._create_reminder(
                db, row, suggestion_type="stale_prompt", reason="No activity in 6 weeks"
            )
            db.flush()

            self.assertIsNone(unchanged)
            self.assertIsNotNone(changed)
            statuses = sorted(row.status for row in db.query(ApplicationSuggestion).all())
            self.assertEqual(statuses, ["expired", "pending"])


class RetentionTests(SuggestionExpiryTests):
    def test_a_created_suggestion_carries_an_expiry(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()

            created = self.reminder(db, row, reason="No reply in 3 business days")

            self.assertIsNotNone(created.expires_at)
            self.assertEqual(
                created.expires_at - created.created_at,
                timedelta(hours=SUGGESTION_RETENTION_HOURS),
            )

    # The F7 check: the column is compared against a clock, not merely stored.
    def test_the_sweep_expires_a_pending_row_past_its_window(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()
            created = self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()

            expired = service.expire_stale_suggestions(
                db, owner_id=OWNER, now=created.expires_at + timedelta(minutes=1)
            )

            self.assertEqual(expired, 1)
            self.assertEqual(created.status, "expired")
            self.assertEqual(created.expiry_reason, "not_reviewed")

    def test_a_row_inside_its_window_is_untouched(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()
            created = self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()

            expired = service.expire_stale_suggestions(
                db, owner_id=OWNER, now=created.created_at + timedelta(hours=1)
            )

            self.assertEqual(expired, 0)
            self.assertEqual(created.status, "pending")

    def test_expiry_is_owner_scoped(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()
            created = self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()

            expired = service.expire_stale_suggestions(
                db, owner_id="somebody-else", now=created.expires_at + timedelta(days=1)
            )

            self.assertEqual(expired, 0)
            self.assertEqual(created.status, "pending")


class ApprovalRefusalTests(SuggestionExpiryTests):
    # accept_suggestion already refuses anything that is not "pending". Adding
    # "expired" to the status vocabulary makes expiry enforceable for free -
    # this test is what proves the claim rather than assuming it.
    def test_accepting_an_expired_suggestion_is_refused(self) -> None:
        with Session(self.engine) as db:
            row = application()
            db.add(row)
            db.flush()
            created = self.reminder(db, row, reason="No reply in 3 business days")
            db.flush()
            service.expire_stale_suggestions(
                db, owner_id=OWNER, now=created.expires_at + timedelta(days=1)
            )
            db.flush()

            with self.assertRaises(ApplicationValidationError):
                accept_suggestion(db, created, application=row)


if __name__ == "__main__":
    unittest.main()
