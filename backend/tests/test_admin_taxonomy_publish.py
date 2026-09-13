"""§14 H2: publish the base taxonomy for review, and write to nobody.

*"Exports the calling admin's overlay in base-artefact format and returns it
for review. It writes to nobody. The developer commits the file. Deliberate,
reviewable, revertible."*

Two claims carry this endpoint, and both are tested here rather than asserted
in a docstring: that it **writes nothing**, and that what an admin reviews is
**the same bytes** a developer commits.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import settings
from app.db import Base
from app.models import CanonicalEntityTaxonomyEntry, JobIntentTaxonomyEntry, User
from scripts import export_base_taxonomy

ADMIN = "usr_admin"
OTHER = "usr_other"


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
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
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _entity(self, owner: str, name: str, *, status: str = "approved", entity_type: str = "role"):
        self.db.add(CanonicalEntityTaxonomyEntry(
            owner_id=owner, entity_type=entity_type, canonical_name=name,
            aliases_json="[]", status=status,
        ))
        self.db.commit()

    def _intent(self, owner: str, phrase: str, *, status: str = "approved"):
        self.db.add(JobIntentTaxonomyEntry(
            owner_id=owner, phrase=phrase, normalized_phrase=phrase.lower(),
            polarity="recruiter_jd", status=status, confidence_aggregate=0.8,
        ))
        self.db.commit()

    def _publish(self, user):
        self.client.cookies.set(settings.session_cookie_name, "a-session-token")
        try:
            with patch.object(settings, "feature_auth_enabled", True), \
                    patch.object(main.auth_service, "resolve_session", return_value=user):
                return self.client.post("/admin/taxonomy/publish")
        finally:
            self.client.cookies.clear()

    def _admin(self):
        return User(email="a@example.com", owner_id=ADMIN, is_admin=True)


class ContentsTests(_Base):
    def test_an_admin_gets_the_artefact_and_its_counts(self):
        self._entity(ADMIN, "Prompt Engineer")
        self._intent(ADMIN, "hiring now")
        response = self._publish(self._admin())

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["owner_id"], ADMIN)
        self.assertEqual(body["counts"]["role"], 1)
        self.assertEqual(body["counts"]["job_intent"], 1)
        self.assertEqual(
            set(body["files"]),
            {"roles.json", "companies.json", "locations.json", "job_intent.json"},
        )

    def test_only_approved_entries_are_published(self):
        self._entity(ADMIN, "Approved Role")
        self._entity(ADMIN, "Pending Role", status="pending")
        files = self._publish(self._admin()).json()["files"]
        names = [entry["canonical_name"] for entry in files["roles.json"]["entries"]]
        self.assertEqual(names, ["Approved Role"])

    def test_another_owners_overlay_is_never_published(self):
        """The calling admin's own vocabulary, resolved from the session. An
        `owner_id` parameter here would make this a way to read someone else's."""
        self._entity(ADMIN, "Mine")
        self._entity(OTHER, "Theirs")
        files = self._publish(self._admin()).json()["files"]
        names = [entry["canonical_name"] for entry in files["roles.json"]["entries"]]
        self.assertEqual(names, ["Mine"])

    def test_an_owner_id_parameter_is_ignored(self):
        self._entity(ADMIN, "Mine")
        self._entity(OTHER, "Theirs")
        self.client.cookies.set(settings.session_cookie_name, "a-session-token")
        try:
            with patch.object(settings, "feature_auth_enabled", True), \
                    patch.object(main.auth_service, "resolve_session", return_value=self._admin()):
                response = self.client.post(f"/admin/taxonomy/publish?owner_id={OTHER}")
        finally:
            self.client.cookies.clear()
        self.assertEqual(response.json()["owner_id"], ADMIN)

    def test_a_contact_identifier_never_reaches_the_artefact(self):
        """The base ships to everyone. An address that wandered into one
        account's vocabulary must not arrive in a hundred others'."""
        self._entity(ADMIN, "recruiter@acme.example")
        self._entity(ADMIN, "Legitimate Role")
        files = self._publish(self._admin()).json()["files"]
        names = [entry["canonical_name"] for entry in files["roles.json"]["entries"]]
        self.assertEqual(names, ["Legitimate Role"])


class WritesNothingTests(_Base):
    """"It writes to nobody." The whole point of the endpoint."""

    def test_it_creates_no_files(self):
        self._entity(ADMIN, "Prompt Engineer")
        with TemporaryDirectory() as tmp:
            before = set(Path(tmp).rglob("*"))
            with patch.object(export_base_taxonomy, "_write_json") as write:
                self._publish(self._admin())
            self.assertEqual(set(Path(tmp).rglob("*")), before)
        write.assert_not_called()

    def test_it_changes_no_rows(self):
        self._entity(ADMIN, "Prompt Engineer")
        self._entity(OTHER, "Theirs")
        before = {
            (row.owner_id, row.canonical_name, row.status)
            for row in self.db.query(CanonicalEntityTaxonomyEntry).all()
        }
        self._publish(self._admin())
        self.db.expire_all()
        after = {
            (row.owner_id, row.canonical_name, row.status)
            for row in self.db.query(CanonicalEntityTaxonomyEntry).all()
        }
        self.assertEqual(before, after)

    def test_it_does_not_bump_the_version(self):
        """`VERSION` belongs to the committed artefact. Publishing a review
        copy that incremented it would number a release nobody shipped."""
        self._entity(ADMIN, "Prompt Engineer")
        body = self._publish(self._admin()).json()
        self.assertNotIn("VERSION", body["files"])


class NoDriftTests(_Base):
    """What an admin reviews must be what a developer commits."""

    def test_the_reviewed_payload_matches_what_the_cli_writes(self):
        """The reason `build_base_taxonomy` was extracted rather than the
        endpoint assembling its own. Two code paths producing "the same"
        artefact is precisely the drift review exists to catch."""
        self._entity(ADMIN, "Prompt Engineer")
        self._entity(ADMIN, "Acme Corp", entity_type="company")
        self._intent(ADMIN, "hiring now")

        reviewed = self._publish(self._admin()).json()["files"]

        with TemporaryDirectory() as tmp:
            export_base_taxonomy.export_base_taxonomy(
                self.db, owner_id=ADMIN, output_dir=Path(tmp)
            )
            for file_name, payload in reviewed.items():
                on_disk = json.loads((Path(tmp) / file_name).read_text(encoding="utf-8"))
                self.assertEqual(on_disk, payload, f"{file_name} drifted")


class GateTests(_Base):
    """Admin-only, on the `is_admin` database column like the rest of /admin."""

    def test_a_signed_in_non_admin_is_refused(self):
        plain = User(email="b@example.com", owner_id=OTHER, is_admin=False)
        self.assertEqual(self._publish(plain).status_code, 403)

    def test_an_anonymous_caller_is_refused(self):
        self.assertEqual(self._publish(None).status_code, 401)

    def test_it_is_absent_when_sign_in_is_off(self):
        self.client.cookies.set(settings.session_cookie_name, "a-session-token")
        try:
            with patch.object(settings, "feature_auth_enabled", False):
                response = self.client.post("/admin/taxonomy/publish")
        finally:
            self.client.cookies.clear()
        self.assertEqual(response.status_code, 404)

    def test_it_is_not_caught_by_the_user_taxonomy_off_switch(self):
        """E3's guard hides the routes a *user* edits their own taxonomy with.
        This one is an admin export of rows that still exist, so switching user
        editing off must not take it down too."""
        self.assertFalse("/admin/taxonomy/publish".startswith(main.USER_TAXONOMY_PATH_PREFIXES))


if __name__ == "__main__":
    unittest.main()
