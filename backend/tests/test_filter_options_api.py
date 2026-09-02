import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import EmailConversation, RecruiterEmail, RecruiterOpportunity, UserSettings


class FilterOptionsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        with Session(self.engine) as db:
            db.add(UserSettings(owner_id=main.settings.owner_id))
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _email(
        self,
        db: Session,
        *,
        owner_id: str | None = None,
        state: str = "needs_review",
        role: str = "Java Developer",
        location: str = "Austin",
        sender: str = "Jane <jane@example.com>",
        interview_type: str | None = "Video",
        marked: bool = False,
        is_source_parent: bool = False,
    ) -> RecruiterEmail:
        row = RecruiterEmail(
            owner_id=owner_id or main.settings.owner_id,
            sender=sender,
            subject="Role",
            body="Body",
            role=role,
            location=location,
            state=state,
            interview_type=interview_type,
            marked_for_tracking=marked,
            is_source_parent=is_source_parent,
        )
        db.add(row)
        db.flush()
        return row

    def test_rejects_unknown_and_unallowlisted_fields(self) -> None:
        for params in (
            {"bucket": "missing", "field": "role"},
            {"bucket": "needs_review", "field": "missing"},
            {"bucket": "needs_review", "field": "body"},
        ):
            self.assertEqual(self.client.get("/filter-options", params=params).status_code, 404)
        self.assertEqual(self.client.get("/filter-options", params={"bucket": "needs_review", "field": "role", "limit": 0}).status_code, 422)
        self.assertEqual(self.client.get("/filter-options", params={"bucket": "needs_review", "field": "role", "limit": 999}).status_code, 422)

    def test_returns_distinct_sorted_owner_scoped_nonempty_values(self) -> None:
        with Session(self.engine) as db:
            self._email(db, role="Zulu")
            self._email(db, role="Alpha")
            self._email(db, role="Alpha")
            self._email(db, role="")
            self._email(db, role="Other owner", owner_id="someone-else")
            db.commit()

        response = self.client.get("/filter-options", params={"bucket": "needs_review", "field": "role"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["values"], ["Alpha", "Zulu"])

    def test_query_is_case_insensitive_and_escapes_wildcards(self) -> None:
        with Session(self.engine) as db:
            self._email(db, location="Austin")
            self._email(db, location="Boston")
            db.commit()

        response = self.client.get("/filter-options", params={"bucket": "needs_review", "field": "location", "q": "AUS"})
        self.assertEqual(response.json()["values"], ["Austin"])
        wildcard = self.client.get("/filter-options", params={"bucket": "needs_review", "field": "location", "q": "%"})
        self.assertEqual(wildcard.json()["values"], [])

    def test_ranks_by_match_position_and_frequency_not_alphabetically(self) -> None:
        """The picker must surface the roles people actually have.

        Regression: ordering alphabetically under a LIMIT returned the head of the
        alphabet, so digit-prefixed subject lines ("2 Requirements :: ...") filled
        the window and "Java Developer" - by far the most common role - never
        appeared at all.
        """
        subject_line = "2 Requirements :: Java Software Engineer :: Senior SAP Consultant"
        with Session(self.engine) as db:
            for _ in range(3):
                self._email(db, role="Java Developer")
            self._email(db, role="Advanced Java Concepts")
            self._email(db, role="AI Engineer with Java")
            self._email(db, role=subject_line)
            db.commit()

        values = self.client.get(
            "/filter-options",
            params={"bucket": "needs_review", "field": "role", "q": "java"},
        ).json()["values"]

        self.assertEqual(values[0], "Java Developer", f"most common prefix match should lead: {values}")
        self.assertLess(
            values.index("Java Developer"),
            values.index(subject_line),
            f"a one-off subject line must not outrank a real role: {values}",
        )
        # Still a complete answer - ranking reorders, it never hides.
        self.assertIn(subject_line, values)

    def test_suggestions_stay_literal_substrings_of_their_rows(self) -> None:
        """Ranking must not rewrite values, or a click returns nothing."""
        with Session(self.engine) as db:
            self._email(db, role="Java Developer<br /><br />Location: Austin")
            db.commit()

        values = self.client.get(
            "/filter-options",
            params={"bucket": "needs_review", "field": "role", "q": "java"},
        ).json()["values"]
        self.assertEqual(values, ["Java Developer<br /><br />Location: Austin"])

        listed = self.client.get(
            "/candidates",
            params={"state": "needs_review", "role": values[0], "limit": 10},
        )
        self.assertEqual(listed.json()["total"], 1, "every suggestion must match at least its own row")

    def test_picker_excludes_source_parents(self) -> None:
        """A multi-requirement container's subject must not be offered as a job title.

        Source parents hold the raw subject in `role` by design (the AI extractor is
        skipped for manifest status "multiple"); their real roles live on the expanded
        children. Suggesting the container is what produced the
        "3 Requirements :: ..." entries in the picker.
        """
        parent_role = "3 Requirements :: Java Software Engineer :: SAP QM :: QA Lead"
        with Session(self.engine) as db:
            self._email(db, role="Java Developer")
            self._email(db, role=parent_role, is_source_parent=True)
            db.commit()

        values = self.client.get(
            "/filter-options",
            params={"bucket": "needs_review", "field": "role", "q": "java"},
        ).json()["values"]
        self.assertIn("Java Developer", values)
        self.assertNotIn(parent_role, values)

    def test_bookmarked_bucket_also_excludes_source_parents(self) -> None:
        with Session(self.engine) as db:
            self._email(db, role="Java Developer", marked=True)
            self._email(db, role="9 Requirements :: Java :: SAP", marked=True, is_source_parent=True)
            db.commit()

        values = self.client.get(
            "/filter-options",
            params={"bucket": "appts_bookmarked", "field": "role"},
        ).json()["values"]
        self.assertEqual(values, ["Java Developer"])

    def test_candidate_buckets_are_isolated_by_state(self) -> None:
        with Session(self.engine) as db:
            self._email(db, state="needs_review", location="Austin")
            self._email(db, state="approved_sent", location="Boston")
            db.commit()

        response = self.client.get("/filter-options", params={"bucket": "needs_review", "field": "location"})
        self.assertEqual(response.json()["values"], ["Austin"])

    def test_inbox_options_require_an_owner_scoped_conversation(self) -> None:
        with Session(self.engine) as db:
            visible = self._email(db, role="Visible")
            self._email(db, role="Ghost")
            foreign = self._email(db, role="Foreign", owner_id="someone-else")
            db.add_all([
                EmailConversation(owner_id=main.settings.owner_id, root_recruiter_email_id=visible.id, external_thread_id="visible"),
                EmailConversation(owner_id="someone-else", root_recruiter_email_id=foreign.id, external_thread_id="foreign"),
            ])
            db.commit()

        response = self.client.get("/filter-options", params={"bucket": "inbox_conversations", "field": "role"})
        self.assertEqual(response.json()["values"], ["Visible"])

    def test_sent_company_matches_candidate_filter_column(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, state="approved_sent")
            db.add(RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=1,
                source_email_id=email.id,
                gmail_message_id="sent-company",
                end_client="Acme Corp",
            ))
            db.commit()

        options = self.client.get("/filter-options", params={"bucket": "approved_sent", "field": "company"})
        self.assertEqual(options.json()["values"], ["Acme Corp"])
        candidates = self.client.get("/candidates", params={"state": "approved_sent", "company": "Acme Corp"})
        self.assertEqual(candidates.status_code, 200)
        self.assertEqual(candidates.json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
