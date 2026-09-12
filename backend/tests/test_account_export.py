"""G2 / §13: the export, and the two ways it could betray someone.

It could hand back a **key to the account** - a credential or a session - along
with the contents. Or it could hand back **someone else's** rows. The tests
that matter here are the ones checking those two things, not the ones checking
that an application comes out the other end.
"""

import json
import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import settings
from app.models import (
    Application,
    Base,
    CanonicalEntityTaxonomyEntry,
    ChatMessage,
    ChatSession,
    GmailCredential,
    RecruiterEmail,
    User,
)
from app.services import account_export_service

MINE = "usr_mine"
THEIRS = "usr_theirs"


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed(self, owner: str, *, subject: str = "A role") -> None:
        now = datetime.now(UTC)
        self.db.add(RecruiterEmail(
            owner_id=owner, sender=f"recruiter@{owner}.example", subject=subject,
            body="the body of the message", state="needs_review", source="gmail",
            external_message_id=f"msg-{owner}-{subject}", created_at=now, updated_at=now,
            tracking_token=f"track-{owner}",
        ))
        # Every NOT NULL column with no default has to be supplied; the
        # snapshot fields are the resume this application was sent with.
        self.db.add(Application(
            owner_id=owner, resume_asset_id=1, resume_version_snapshot=1,
            resume_file_name_snapshot=f"{owner}.docx", resume_sha256_snapshot="abc",
            recruiter_company_snapshot=f"{owner} Corp", job_title_snapshot="Engineer",
        ))
        session = ChatSession(owner_id=owner, title=f"{owner} chat", created_at=now, updated_at=now)
        self.db.add(session)
        self.db.flush()
        self.db.add(ChatMessage(
            session_id=session.id, role="user", content=f"{owner} asked something",
            created_at=now,
        ))
        self.db.add(CanonicalEntityTaxonomyEntry(
            owner_id=owner, entity_type="role", canonical_name=f"{owner} Role",
            aliases_json="[]", status="approved",
        ))
        self.db.commit()

    def _export(self, owner: str) -> dict:
        return json.loads("".join(account_export_service.stream_export(self.db, owner)))


class ContentsTests(_Base):
    def test_it_is_valid_json_with_every_section_present(self):
        self._seed(MINE)
        document = self._export(MINE)
        self.assertEqual(document["owner_id"], MINE)
        self.assertEqual(document["format_version"], account_export_service.FORMAT_VERSION)
        self.assertEqual(
            set(document["sections"]),
            {name for name, _ in account_export_service.SECTIONS},
        )

    def test_an_empty_account_still_produces_a_valid_document(self):
        """Someone who signed in once and left must get a file, not a 500."""
        document = self._export(MINE)
        self.assertTrue(all(rows == [] for rows in document["sections"].values()))

    def test_the_three_things_13_names_are_all_there(self):
        self._seed(MINE)
        sections = self._export(MINE)["sections"]
        self.assertEqual(len(sections["applications"]), 1)
        self.assertEqual(len(sections["recruiter_emails"]), 1)
        self.assertEqual(len(sections["entity_taxonomy"]), 1)

    def test_the_message_bodies_are_included(self):
        """It is their mail. An export that omits the contents is the complaint
        this feature exists to answer."""
        self._seed(MINE)
        email = self._export(MINE)["sections"]["recruiter_emails"][0]
        self.assertEqual(email["body"], "the body of the message")

    def test_chat_messages_come_through_the_session_join(self):
        """`chat_messages` is the one exported table with no `owner_id`; it
        reaches its account through `session_id`."""
        self._seed(MINE)
        messages = self._export(MINE)["sections"]["chat_messages"]
        self.assertEqual([m["content"] for m in messages], ["usr_mine asked something"])

    def test_datetimes_survive_as_strings(self):
        self._seed(MINE)
        row = self._export(MINE)["sections"]["recruiter_emails"][0]
        self.assertIsInstance(row["created_at"], str)


class LeakTests(_Base):
    """The two ways this endpoint could betray someone."""

    def test_another_owners_rows_never_appear(self):
        """The one that matters most. Everything else is a feature; this is the
        guarantee."""
        self._seed(MINE, subject="mine")
        self._seed(THEIRS, subject="theirs")

        document = self._export(MINE)
        blob = json.dumps(document)

        self.assertNotIn(THEIRS, blob)
        self.assertNotIn("theirs", blob)
        for name, rows in document["sections"].items():
            for row in rows:
                if "owner_id" in row:
                    self.assertEqual(row["owner_id"], MINE, f"{name} leaked an owner")

    def test_no_credential_or_session_table_is_exported(self):
        """Credentials and sessions are keys to the account, not contents of
        it. Asserted against the section list so adding one is deliberate."""
        exported_tables = {model.__table__.name for _, model in account_export_service.SECTIONS}
        self.assertEqual(exported_tables & account_export_service.NEVER_EXPORTED, set())

    def test_no_exported_column_holds_token_material(self):
        """Checked against the real columns of the real exported tables.

        Not a substring rule: that would strip `chat_turn.prompt_tokens`, which
        is a count, while saying nothing useful about a capability spelled some
        other way. This asserts the specific known one is gone and that no
        encrypted-credential column ever joins the list.
        """
        self._seed(MINE)
        document = self._export(MINE)
        email = document["sections"]["recruiter_emails"][0]
        self.assertNotIn("tracking_token", email, "the open-tracking capability must not ship")

        for _, model in account_export_service.SECTIONS:
            for column in model.__table__.columns:
                self.assertNotIn(
                    "encrypted", column.name,
                    f"{model.__table__.name}.{column.name} looks like credential material",
                )

    def test_the_gmail_credential_table_is_not_reachable_through_a_section(self):
        self.db.add(GmailCredential(
            owner_id=MINE, access_token_encrypted="cipher-a", refresh_token_encrypted="cipher-b",
        ))
        self.db.commit()
        blob = json.dumps(self._export(MINE))
        self.assertNotIn("cipher-a", blob)
        self.assertNotIn("cipher-b", blob)

    def test_the_derived_machinery_is_left_behind(self):
        """1.53 GB of a 1.93 GB export, measured on one real account, against
        31 MB of actual message bodies. An embedding is not a conversation."""
        self._seed(MINE)
        email = self._export(MINE)["sections"]["recruiter_emails"][0]
        for column in ("semantic_embedding", "resume_picker_candidates_json",
                       "resume_picker_breakdown_json", "parser_details_json"):
            self.assertNotIn(column, email, f"{column} is machinery, not correspondence")

    def test_what_people_actually_asked_for_survives_the_trimming(self):
        """The counterweight to the denylist. Trimming further should have to
        argue with this rather than quietly drop the part that matters."""
        self._seed(MINE)
        sections = self._export(MINE)["sections"]
        for table, required in account_export_service.MUST_KEEP.items():
            section = next(
                name for name, model in account_export_service.SECTIONS
                if model.__table__.name == table
            )
            row = sections[section][0]
            for column in required:
                self.assertIn(column, row, f"{table}.{column} must survive the denylist")

    def test_no_must_keep_column_is_also_denied(self):
        """The two lists could contradict each other silently."""
        for table, required in account_export_service.MUST_KEEP.items():
            denied = account_export_service.DENIED_COLUMNS.get(table, frozenset())
            self.assertEqual(required & denied, set(), f"{table} both keeps and denies a column")

    def test_every_section_is_owner_scoped(self):
        """A section with no owner scope would export the whole table to
        whoever asked. Enumerated rather than trusted."""
        for name, model in account_export_service.SECTIONS:
            scoped = hasattr(model, "owner_id") or model is ChatMessage
            self.assertTrue(scoped, f"{name} has no owner scope")


class StreamingTests(_Base):
    def test_the_document_is_produced_in_more_than_one_chunk(self):
        """Streaming is the point: one mailbox must not decide how much this
        process allocates."""
        self._seed(MINE)
        chunks = list(account_export_service.stream_export(self.db, MINE))
        self.assertGreater(len(chunks), len(account_export_service.SECTIONS))

    def test_a_failing_section_does_not_abort_the_download(self):
        """A half-written export is still their data. The section closes and
        the rest continues."""
        self._seed(MINE)
        real = account_export_service._rows

        def explode(db, model, owner_id):
            if model is Application:
                raise RuntimeError("boom")
            return real(db, model, owner_id)

        with patch.object(account_export_service, "_rows", explode):
            document = self._export(MINE)

        self.assertEqual(document["sections"]["applications"], [])
        self.assertEqual(len(document["sections"]["recruiter_emails"]), 1)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        main.app.dependency_overrides[main.get_db] = self._session
        self.client = TestClient(main.app)

    def _session(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        main.app.dependency_overrides.pop(main.get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _post(self, user):
        self.client.cookies.set(settings.session_cookie_name, "a-session-token")
        try:
            with patch.object(main.auth_service, "resolve_session", return_value=user):
                return self.client.post("/account/export")
        finally:
            self.client.cookies.clear()

    def test_a_signed_in_user_gets_a_json_attachment(self):
        user = User(email="a@example.com", owner_id=MINE)
        with patch.object(settings, "feature_auth_enabled", True):
            response = self._post(user)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertIn("codejob-export-", response.headers["content-disposition"])
        self.assertEqual(json.loads(response.text)["owner_id"], MINE)

    def test_an_anonymous_caller_is_refused(self):
        """End to end, though the refusal comes from the session middleware,
        which 401s any non-public path without a session before the route is
        reached. `require_account_user` is what the sign-in-off test below
        exercises; this one asserts the outer guarantee.
        """
        with patch.object(settings, "feature_auth_enabled", True):
            response = self._post(None)
        self.assertEqual(response.status_code, 401)

    def test_it_is_absent_when_sign_in_is_off(self):
        with patch.object(settings, "feature_auth_enabled", False):
            response = self._post(None)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
