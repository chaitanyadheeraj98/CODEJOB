"""The three write routes the confirmation card and the Settings control reach.

One route per verb, and one route for all three of the ways a user can reach an
append - the assistant's offer, a user-directed save, and the panel in Settings.
The route does not know which sent it, which is deliberate: a route that
branched on its caller would be a route with two behaviours to keep in
agreement.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import ChatAttachment, ChatSession, UserSettings
from app.services.candidate_profile_service import (
    CANDIDATE_PROFILE_MAX_CHARS,
    SAVED_HEADING,
    fingerprint,
)

PROFILE = """# Chaithanya Dheeraj
- Work Authorization: H1B
- Passport: X1234567
"""


class CandidateProfileWriteRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        with self.SessionLocal() as db:
            db.add(UserSettings(
                owner_id=main.settings.owner_id,
                candidate_profile_markdown=PROFILE,
                candidate_profile_filename="profile.md",
            ))
            db.add(ChatSession(id=1, owner_id=main.settings.owner_id))
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _settings(self) -> UserSettings:
        with self.SessionLocal() as db:
            return db.query(UserSettings).filter(
                UserSettings.owner_id == main.settings.owner_id
            ).first()

    def _append(self, entry: str, base: str | None = None):
        return self.client.post(
            "/settings/candidate-profile/entries",
            json={"entry": entry, "base_sha256": base if base is not None else fingerprint(PROFILE)},
        )

    # --- append ------------------------------------------------------------

    def test_an_append_stores_the_line_and_leaves_the_filename_alone(self) -> None:
        response = self._append("- Notice period: 2 weeks")

        self.assertEqual(response.status_code, 200)
        stored = self._settings()
        self.assertIn("- Notice period: 2 weeks", stored.candidate_profile_markdown)
        self.assertIn(SAVED_HEADING, stored.candidate_profile_markdown)
        # The filename still names the document this is an addition to.
        self.assertEqual(stored.candidate_profile_filename, "profile.md")
        self.assertIsNotNone(stored.candidate_profile_uploaded_at)
        self.assertEqual(response.json()["characters"], len(stored.candidate_profile_markdown))

    def test_the_route_accepts_an_entry_that_never_came_from_a_tool_call(self) -> None:
        """Path A3 through the same door.

        The Settings control composes the entry client-side and posts it here.
        There is no `source` parameter and no second validator: the route is the
        one place all three paths converge.
        """
        response = self._append("- Current location: Dallas, TX")

        self.assertEqual(response.status_code, 200)
        self.assertIn("- Current location: Dallas, TX", self._settings().candidate_profile_markdown)

    def test_a_second_entry_for_one_field_updates_it_rather_than_repeating_it(self) -> None:
        """The route writes, so the route is where this has to be true.

        The card previews with plan_append and the route stores with
        compose_append; both are the same call, and this is what proves the
        preview the user confirmed is the document they get.
        """
        first = self._append("- Work Authorization: H1B")
        self.assertEqual(first.status_code, 200)
        after_first = self._settings().candidate_profile_markdown

        second = self._append("- Work Authorization: GC", base=fingerprint(after_first))

        self.assertEqual(second.status_code, 200)
        saved_block = self._settings().candidate_profile_markdown.split(SAVED_HEADING, 1)[1]
        self.assertIn("- Work Authorization: GC", saved_block)
        self.assertNotIn("- Work Authorization: H1B", saved_block)
        self.assertEqual(saved_block.count("- Work Authorization:"), 1)

    def test_a_stale_fingerprint_is_a_409_and_changes_nothing(self) -> None:
        response = self._append("- Notice period: 2 weeks", base=fingerprint("something else"))

        self.assertEqual(response.status_code, 409)
        self.assertIn("changed since this was prepared", response.json()["detail"])
        self.assertEqual(self._settings().candidate_profile_markdown, PROFILE)

    def test_appending_to_an_empty_profile_is_refused_and_names_settings(self) -> None:
        with self.SessionLocal() as db:
            row = db.query(UserSettings).filter(
                UserSettings.owner_id == main.settings.owner_id
            ).first()
            row.candidate_profile_markdown = ""
            db.commit()

        response = self._append("- Notice period: 2 weeks", base=fingerprint(""))

        self.assertEqual(response.status_code, 400)
        self.assertIn("Candidate Profile", response.json()["detail"])

    def test_an_append_that_would_cross_the_limit_names_both_numbers(self) -> None:
        big = "x" * (CANDIDATE_PROFILE_MAX_CHARS - 5)
        with self.SessionLocal() as db:
            row = db.query(UserSettings).filter(
                UserSettings.owner_id == main.settings.owner_id
            ).first()
            row.candidate_profile_markdown = big
            db.commit()

        response = self._append("- Notice period: 2 weeks", base=fingerprint(big))

        self.assertEqual(response.status_code, 400)
        self.assertIn(f"{CANDIDATE_PROFILE_MAX_CHARS:,}", response.json()["detail"])
        self.assertEqual(self._settings().candidate_profile_markdown, big)

    # --- replace from a chat attachment ------------------------------------

    def _attach(self, attachment_id: int, text: str | None, name: str = "profile.md") -> None:
        with self.SessionLocal() as db:
            db.add(ChatAttachment(
                id=attachment_id, owner_id=main.settings.owner_id, session_id=1,
                file_path=f"/tmp/{name}", file_name=name, mime_type="text/markdown",
                byte_size=len(text or ""), sha256=str(attachment_id) * 64,
                content_markdown=text,
                extraction_error=None if text is not None else "unreadable",
            ))
            db.commit()

    def test_replacing_from_an_attachment_stores_its_text_and_its_name(self) -> None:
        self._attach(1, "# New profile\n- Rate: $80/hr\n")

        response = self.client.post(
            "/settings/candidate-profile/from-attachment",
            json={"attachment_id": 1, "base_sha256": fingerprint(PROFILE)},
        )

        self.assertEqual(response.status_code, 200)
        stored = self._settings()
        self.assertEqual(stored.candidate_profile_markdown, "# New profile\n- Rate: $80/hr")
        self.assertEqual(stored.candidate_profile_filename, "profile.md")

    def test_a_truncated_attachment_is_refused_rather_than_silently_losing_the_tail(self) -> None:
        cap = main.settings.chat_attachment_max_extract_chars
        self._attach(2, "y" * cap, name="long.md")

        response = self.client.post(
            "/settings/candidate-profile/from-attachment",
            json={"attachment_id": 2, "base_sha256": fingerprint(PROFILE)},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("too long to read in full", response.json()["detail"])
        self.assertEqual(self._settings().candidate_profile_markdown, PROFILE)

    def test_an_unreadable_attachment_names_the_extraction_failure(self) -> None:
        self._attach(3, None, name="scan.md")

        response = self.client.post(
            "/settings/candidate-profile/from-attachment",
            json={"attachment_id": 3, "base_sha256": fingerprint(PROFILE)},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unreadable", response.json()["detail"])

    def test_replace_rejects_a_stale_fingerprint(self) -> None:
        self._attach(4, "# New profile\n")

        response = self.client.post(
            "/settings/candidate-profile/from-attachment",
            json={"attachment_id": 4, "base_sha256": fingerprint("stale")},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._settings().candidate_profile_markdown, PROFILE)

    # --- delete ------------------------------------------------------------

    def test_a_bodyless_delete_still_works(self) -> None:
        """The Settings panel's own call, which sends no body at all.

        Adding an optional guard body must not change what the button already
        does.
        """
        response = self.client.delete("/settings/candidate-profile")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._settings().candidate_profile_markdown, "")

    def test_a_delete_carrying_a_matching_fingerprint_works(self) -> None:
        response = self.client.request(
            "DELETE",
            "/settings/candidate-profile",
            json={"base_sha256": fingerprint(PROFILE)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._settings().candidate_profile_markdown, "")

    def test_a_delete_carrying_a_stale_fingerprint_is_a_409_and_destroys_nothing(self) -> None:
        response = self.client.request(
            "DELETE",
            "/settings/candidate-profile",
            json={"base_sha256": fingerprint("stale")},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._settings().candidate_profile_markdown, PROFILE)


if __name__ == "__main__":
    unittest.main()
