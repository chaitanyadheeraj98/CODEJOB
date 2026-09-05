"""Upload, replace and remove the candidate profile.

The profile is a file the user uploads, not a field they type into. What that
buys is a long document; what it costs is every way a file can be wrong, so each
rejection here is asserted to say what actually failed - a message that only says
"upload failed" sends the user back to re-upload the same file blind.
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
from app.models import UserSettings

PROFILE = b"""# Chaithanya Dheeraj
- Work Authorization: H1B
- Passport: X1234567
- Notice: 2 weeks
"""


class CandidateProfileUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
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
            db.add(UserSettings(owner_id=main.settings.owner_id))
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _upload(self, name: str, payload: bytes):
        return self.client.post(
            "/settings/candidate-profile",
            files={"file": (name, payload, "text/markdown")},
        )

    def _stored(self) -> UserSettings:
        with self.SessionLocal() as db:
            return (
                db.query(UserSettings)
                .filter(UserSettings.owner_id == main.settings.owner_id)
                .one()
            )

    # --- accepting -------------------------------------------------------

    def test_uploading_stores_the_text_and_the_filename(self) -> None:
        response = self._upload("profile.md", PROFILE)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["filename"], "profile.md")
        self.assertEqual(body["characters"], len(PROFILE.decode().strip()))
        self.assertIsNotNone(body["uploaded_at"])
        # The response describes the upload; it never echoes the profile back,
        # which may hold a passport number.
        self.assertNotIn("X1234567", response.text)

        stored = self._stored()
        self.assertIn("Passport: X1234567", stored.candidate_profile_markdown)
        self.assertEqual(stored.candidate_profile_filename, "profile.md")
        self.assertIsNotNone(stored.candidate_profile_uploaded_at)

    def test_each_markdown_suffix_is_accepted(self) -> None:
        for name in ("profile.md", "profile.markdown", "profile.txt", "PROFILE.MD"):
            with self.subTest(name=name):
                self.assertEqual(self._upload(name, PROFILE).status_code, 200)

    def test_uploading_again_replaces_rather_than_appends(self) -> None:
        self._upload("profile.md", PROFILE)
        self._upload("newer.md", b"# Only this now\n")
        stored = self._stored()
        self.assertEqual(stored.candidate_profile_markdown, "# Only this now")
        self.assertEqual(stored.candidate_profile_filename, "newer.md")
        self.assertNotIn("X1234567", stored.candidate_profile_markdown)

    def test_the_stored_text_is_stripped_but_internally_intact(self) -> None:
        self._upload("profile.md", b"\n\n# Me\n\n- Rate: $58/hr\n\n")
        self.assertEqual(self._stored().candidate_profile_markdown, "# Me\n\n- Rate: $58/hr")

    def test_a_profile_at_the_exact_limit_is_accepted(self) -> None:
        response = self._upload("profile.md", b"x" * main.CANDIDATE_PROFILE_MAX_CHARS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["characters"], main.CANDIDATE_PROFILE_MAX_CHARS)

    # --- rejecting -------------------------------------------------------

    def test_a_non_markdown_extension_is_rejected_by_name(self) -> None:
        response = self._upload("resume.pdf", PROFILE)
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("resume.pdf", detail)
        self.assertIn(".md", detail)

    def test_an_empty_file_is_rejected(self) -> None:
        self.assertEqual(self._upload("profile.md", b"").status_code, 400)

    def test_a_whitespace_only_file_is_rejected(self) -> None:
        response = self._upload("profile.md", b"   \n\n\t  ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no text", response.json()["detail"])

    def test_non_utf8_bytes_are_rejected_with_a_usable_message(self) -> None:
        response = self._upload("profile.md", b"# Me\n\xff\xfe not text\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("UTF-8", response.json()["detail"])

    def test_over_the_character_limit_is_rejected_and_names_both_numbers(self) -> None:
        response = self._upload("profile.md", b"x" * (main.CANDIDATE_PROFILE_MAX_CHARS + 1))
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("20,001", detail)
        self.assertIn("20,000", detail)

    def test_an_enormous_file_is_rejected_without_decoding_it(self) -> None:
        response = self._upload("profile.md", b"x" * (main.CANDIDATE_PROFILE_MAX_BYTES + 100))
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.json()["detail"])

    def test_a_rejected_upload_leaves_the_existing_profile_alone(self) -> None:
        self._upload("profile.md", PROFILE)
        self._upload("resume.pdf", b"replacement")
        stored = self._stored()
        self.assertIn("Passport: X1234567", stored.candidate_profile_markdown)
        self.assertEqual(stored.candidate_profile_filename, "profile.md")

    # --- removing --------------------------------------------------------

    def test_removing_clears_the_text_the_filename_and_the_timestamp(self) -> None:
        self._upload("profile.md", PROFILE)
        response = self.client.delete("/settings/candidate-profile")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"filename": "", "uploaded_at": None, "characters": 0})

        stored = self._stored()
        self.assertEqual(stored.candidate_profile_markdown, "")
        self.assertEqual(stored.candidate_profile_filename, "")
        self.assertIsNone(stored.candidate_profile_uploaded_at)

    def test_removing_when_nothing_is_uploaded_is_not_an_error(self) -> None:
        self.assertEqual(self.client.delete("/settings/candidate-profile").status_code, 200)

    # --- the settings round-trip -----------------------------------------

    def test_the_settings_response_carries_the_profile_for_the_panel(self) -> None:
        self._upload("profile.md", PROFILE)
        settings_body = self.client.get("/settings").json()
        self.assertEqual(settings_body["candidate_profile_filename"], "profile.md")
        self.assertIn("Passport: X1234567", settings_body["candidate_profile_markdown"])
        self.assertIsNotNone(settings_body["candidate_profile_uploaded_at"])

    def test_a_settings_save_cannot_blank_an_uploaded_profile(self) -> None:
        """The reason the field is response-only.

        The dashboard PUTs the whole settings object it was handed, profile
        included. If the save path still wrote the field, a client one release
        behind would erase a profile nobody touched.
        """
        self._upload("profile.md", PROFILE)
        payload = self.client.get("/settings").json()
        payload["candidate_profile_markdown"] = ""
        payload["candidate_profile_filename"] = ""
        response = self.client.put("/settings", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

        stored = self._stored()
        self.assertIn("Passport: X1234567", stored.candidate_profile_markdown)
        self.assertEqual(stored.candidate_profile_filename, "profile.md")


if __name__ == "__main__":
    unittest.main()
