"""Which rows the vendor-as-end-client script claims, and which it leaves alone.

The script destroys a stated value on several hundred rows, so what matters is
that it recognises the substitution wherever the recruiter columns have since
moved, and that it still refuses everything it says it refuses.
"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Application, RecruiterEmail, ResumeAsset
from app.services import resume_tracking_service
from scripts import blank_vendor_as_end_client as script

OWNER_ID = "owner"


class BlankVendorAsEndClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _email(self, db: Session, *, company: str, end_client: str = "") -> RecruiterEmail:
        email = RecruiterEmail(
            owner_id=OWNER_ID,
            sender="Alekya <alekya@rpatechnologyinc.com>",
            subject="Jr. Java Full stack Developer",
            body="Java",
            company=company,
            end_client=end_client,
        )
        db.add(email)
        db.flush()
        return email

    def _resume(self, db: Session) -> ResumeAsset:
        resume = ResumeAsset(
            owner_id=OWNER_ID, file_path="java.pdf", file_name="java.pdf", sha256="a" * 64,
            version=4, skills_text="Java", primary_role="Java Developer",
        )
        db.add(resume)
        db.flush()
        return resume

    def _application(self, db: Session, *, end_client: str, recruiter_company: str, dedupe_key: str) -> Application:
        """A legacy row, written the way the fallback wrote them.

        Built through the service so every column is filled, then the two
        end-client columns are set behind `clean_end_client` - which is exactly
        what the caller did before the validator existed, and the only way to
        reproduce a row carrying "Unknown".
        """
        row, _ = resume_tracking_service.create_manual_application(
            db, owner_id=OWNER_ID, resume_asset_id=self._resume(db).id,
            manual_job_title="Jr. Java Full stack Developer", manual_end_client="",
            manual_recruiter_name="Alekya", manual_recruiter_company=recruiter_company,
            manual_recruiter_email="alekya@rpatechnologyinc.com", dedupe_key=dedupe_key,
        )
        row.end_client_snapshot = end_client
        row.manual_end_client = end_client
        db.flush()
        return row

    def _clear(self, db: Session, to_clear: list[script.Change]) -> None:
        """What `main` writes under `--apply`."""
        ids = {change.application_id for change in to_clear}
        for row in db.query(Application).filter(Application.id.in_(ids)).all():
            row.end_client_snapshot = ""
            row.manual_end_client = ""
        db.commit()

    def test_the_substitution_is_found_after_the_recruiter_column_moved_on(self) -> None:
        # `resnapshot_recruiter_identity` blanked this row's company snapshot, so
        # the row no longer carries the pair the script used to match on.
        with Session(self.engine) as db:
            email = self._email(db, company="RPATECHNOLOGY INC")
            row = self._application(
                db,
                end_client="RPATECHNOLOGY INC",
                recruiter_company="Unknown",
                dedupe_key=f"recruiter_email:{email.id}",
            )
            db.commit()

            to_clear, needs_review, populated = script.scan(db)
            self.assertEqual([change.application_id for change in to_clear], [row.id])
            self.assertEqual(to_clear[0].reason, "recruiter_company_substituted")
            self.assertEqual(needs_review, [])
            self.assertEqual(populated, 1)

            self._clear(db, to_clear)
            db.refresh(row)
            self.assertEqual(row.end_client_snapshot, "")
            self.assertEqual(row.manual_end_client, "")
            self.assertEqual(script.scan(db), ([], [], 0))

    def test_unknown_is_cleared_when_the_email_named_no_company_either(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, company="")
            row = self._application(
                db,
                end_client="Unknown",
                recruiter_company="Unknown",
                dedupe_key=f"recruiter_email:{email.id}",
            )
            db.commit()

            to_clear, needs_review, _ = script.scan(db)
            self.assertEqual([change.application_id for change in to_clear], [row.id])
            self.assertEqual(needs_review, [])

    def test_a_client_the_posting_named_is_left_alone_entirely(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, company="STI Org", end_client="State of New Jersey")
            self._application(
                db,
                end_client="State of New Jersey",
                recruiter_company="Unknown",
                dedupe_key=f"recruiter_email:{email.id}",
            )
            db.commit()

            to_clear, needs_review, _ = script.scan(db)
            # Not the substituted value, so not this script's business at all -
            # not even worth a human's attention.
            self.assertEqual(to_clear, [])
            self.assertEqual(needs_review, [])

    def test_a_snapshot_that_contradicts_the_posting_goes_to_a_human(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, company="RPATECHNOLOGY INC", end_client="State of New Jersey")
            self._application(
                db,
                end_client="RPATECHNOLOGY INC",
                recruiter_company="Unknown",
                dedupe_key=f"recruiter_email:{email.id}",
            )
            db.commit()

            to_clear, needs_review, _ = script.scan(db)
            self.assertEqual(to_clear, [])
            self.assertEqual([change.reason for change in needs_review], ["posting_named_a_client"])

    def test_a_hand_logged_row_is_reported_never_cleared(self) -> None:
        with Session(self.engine) as db:
            self._application(
                db,
                end_client="RPATECHNOLOGY INC",
                recruiter_company="RPATECHNOLOGY INC",
                dedupe_key="typed-by-hand",
            )
            db.commit()

            to_clear, needs_review, _ = script.scan(db)
            self.assertEqual(to_clear, [])
            self.assertEqual([change.reason for change in needs_review], ["manual_entry"])

    def test_a_hand_logged_row_naming_a_real_client_is_not_even_reported(self) -> None:
        with Session(self.engine) as db:
            self._application(
                db,
                end_client="State of New Jersey",
                recruiter_company="RPATECHNOLOGY INC",
                dedupe_key="typed-by-hand",
            )
            db.commit()

            self.assertEqual(script.scan(db), ([], [], 1))


if __name__ == "__main__":
    unittest.main()
