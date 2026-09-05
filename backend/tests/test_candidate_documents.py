"""The document locker in Profile Settings.

Files a recruiter asks for - passport, work authorization, W2 - kept so the
assistant can attach them by name. Separate from `attachment_assets`, which the
automatic pipeline sends with every draft it approves: these have no enabled
flag, because nothing here goes out unless the user names it and confirms.

Any format is accepted on purpose. The bytes are forwarded verbatim and never
parsed, so what is enforced is size and ownership, not content type.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.mcp_server.tools.candidate_documents import list_candidate_documents
from app.models import CandidateDocument, UserSettings


class CandidateDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            with self.SessionLocal() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)

        self.storage = tempfile.TemporaryDirectory()
        self.previous_storage = main.settings.candidate_document_storage_dir
        main.settings.candidate_document_storage_dir = self.storage.name
        self.tool_patch = patch(
            "app.mcp_server.tools.candidate_documents.SessionLocal", self.SessionLocal
        )
        self.tool_patch.start()

        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=main.settings.owner_id))
            db.commit()

    def tearDown(self) -> None:
        self.tool_patch.stop()
        main.settings.candidate_document_storage_dir = self.previous_storage
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.storage.cleanup()

    def _upload(self, *files: tuple[str, bytes, str]):
        return self.client.post(
            "/settings/documents",
            files=[("files", (name, payload, mime)) for name, payload, mime in files],
        )

    def _rows(self) -> list[CandidateDocument]:
        with self.SessionLocal() as db:
            return db.query(CandidateDocument).order_by(CandidateDocument.id).all()

    # --- upload ----------------------------------------------------------

    def test_uploads_any_format_and_never_returns_the_bytes(self) -> None:
        """A passport scan is stored to be forwarded, not to be read back."""
        response = self._upload(
            ("passport.pdf", b"%PDF-1.4 scan", "application/pdf"),
            ("w2.xlsx", b"PK\x03\x04 spreadsheet", "application/vnd.ms-excel"),
            ("notes.weird", b"\x00\x01\x02 binary", "application/octet-stream"),
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([item["file_name"] for item in body], ["passport.pdf", "w2.xlsx", "notes.weird"])
        self.assertEqual(body[0]["mime_type"], "application/pdf")
        self.assertEqual(body[0]["file_size"], len(b"%PDF-1.4 scan"))
        # No content, no path: the response describes the file and nothing more.
        self.assertNotIn("file_path", body[0])
        self.assertNotIn("content", body[0])
        self.assertNotIn("scan", response.text)

        for row in self._rows():
            self.assertTrue(Path(row.file_path).exists())
        self.assertEqual([row.label for row in self._rows()], ["", "", ""])

    def test_the_stored_name_cannot_escape_the_storage_directory(self) -> None:
        response = self._upload(("../../etc/passwd", b"root:x:0:0", "text/plain"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]["file_name"], "passwd")
        stored = Path(self._rows()[0].file_path).resolve()
        self.assertEqual(stored.parent, Path(self.storage.name).resolve())

    def test_an_empty_file_is_refused_by_name(self) -> None:
        response = self._upload(("blank.pdf", b"", "application/pdf"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("blank.pdf", response.json()["detail"])
        self.assertEqual(self._rows(), [])

    def test_an_oversized_file_is_refused_and_names_the_limit(self) -> None:
        previous = main.settings.candidate_document_max_bytes
        main.settings.candidate_document_max_bytes = 4 * 1024 * 1024
        try:
            response = self._upload(("huge.pdf", b"x" * (4 * 1024 * 1024 + 1), "application/pdf"))
        finally:
            main.settings.candidate_document_max_bytes = previous
        self.assertEqual(response.status_code, 413)
        detail = response.json()["detail"]
        self.assertIn("huge.pdf", detail)
        self.assertIn("4MB", detail)

    def test_one_bad_file_stores_none_of_the_batch(self) -> None:
        """A multi-file pick either lands whole or not at all.

        Half a batch is the worst outcome: the user sees an error, re-picks all
        three, and silently ends up with duplicates of the two that worked.
        """
        previous = main.settings.candidate_document_max_bytes
        main.settings.candidate_document_max_bytes = 1024
        try:
            response = self._upload(
                ("good.pdf", b"small", "application/pdf"),
                ("huge.pdf", b"x" * 2048, "application/pdf"),
            )
        finally:
            main.settings.candidate_document_max_bytes = previous
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self._rows(), [])
        self.assertEqual(list(Path(self.storage.name).iterdir()), [])

    def test_uploading_the_same_document_twice_keeps_both(self) -> None:
        """Replacing a document means deleting the old one deliberately.

        Identical bytes are a re-upload of an unchanged file *or* two copies the
        user wants; guessing which would silently drop one.
        """
        self._upload(("passport.pdf", b"same bytes", "application/pdf"))
        self._upload(("passport.pdf", b"same bytes", "application/pdf"))
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].sha256, rows[1].sha256)
        self.assertNotEqual(rows[0].file_path, rows[1].file_path)

    # --- label -----------------------------------------------------------

    def test_the_label_is_what_the_assistant_matches(self) -> None:
        document_id = self._upload(("CD_scan_0412.pdf", b"scan", "application/pdf")).json()[0]["id"]
        response = self.client.patch(
            f"/settings/documents/{document_id}", json={"label": "  my   passport  "}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["label"], "my passport")
        # The file name is untouched: the recruiter gets what was uploaded.
        self.assertEqual(response.json()["file_name"], "CD_scan_0412.pdf")

        found = list_candidate_documents("passport")
        self.assertEqual([item["id"] for item in found["documents"]], [document_id])

    def test_an_over_long_label_is_rejected_rather_than_silently_cut(self) -> None:
        document_id = self._upload(("a.pdf", b"a", "application/pdf")).json()[0]["id"]
        response = self.client.patch(f"/settings/documents/{document_id}", json={"label": "x" * 400})
        self.assertEqual(response.status_code, 422)

    def test_labelling_an_unknown_document_is_a_404(self) -> None:
        self.assertEqual(
            self.client.patch("/settings/documents/9999", json={"label": "x"}).status_code, 404
        )

    # --- delete ----------------------------------------------------------

    def test_delete_removes_the_row_and_the_file(self) -> None:
        document_id = self._upload(("passport.pdf", b"scan", "application/pdf")).json()[0]["id"]
        path = Path(self._rows()[0].file_path)
        self.assertTrue(path.exists())

        response = self.client.delete(f"/settings/documents/{document_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["deleted"])
        self.assertEqual(self._rows(), [])
        self.assertFalse(path.exists())

    def test_deleting_an_unknown_document_is_a_404(self) -> None:
        self.assertEqual(self.client.delete("/settings/documents/9999").status_code, 404)

    # --- listing ---------------------------------------------------------

    def test_settings_bootstrap_carries_the_documents(self) -> None:
        self._upload(("passport.pdf", b"scan", "application/pdf"))
        payload = self.client.get("/settings/bootstrap").json()
        self.assertEqual([item["file_name"] for item in payload["documents"]], ["passport.pdf"])
        # The panel's other list is a different feature and must not absorb these.
        self.assertEqual(payload["attachments"], [])

    def test_the_list_endpoint_is_newest_first(self) -> None:
        self._upload(("first.pdf", b"1", "application/pdf"))
        self._upload(("second.pdf", b"2", "application/pdf"))
        names = [item["file_name"] for item in self.client.get("/settings/documents").json()]
        self.assertEqual(names, ["second.pdf", "first.pdf"])

    # --- the tool the assistant uses --------------------------------------

    def test_the_tool_matches_label_and_file_name_case_insensitively(self) -> None:
        passport = self._upload(("CD_scan_0412.pdf", b"a", "application/pdf")).json()[0]["id"]
        self.client.patch(f"/settings/documents/{passport}", json={"label": "Passport"})
        w2 = self._upload(("2024_W2.pdf", b"b", "application/pdf")).json()[0]["id"]

        self.assertEqual([item["id"] for item in list_candidate_documents("PASSPORT")["documents"]], [passport])
        self.assertEqual([item["id"] for item in list_candidate_documents("w2")["documents"]], [w2])

        everything = list_candidate_documents()
        self.assertEqual(everything["count"], 2)
        self.assertEqual(everything["total_on_file"], 2)

    def test_the_tool_matches_the_words_the_user_actually_types(self) -> None:
        """Found by driving the real UI: "my passport" missed a "passport".

        The user says "attach my passport and the W2", and a whole-phrase
        substring match finds neither. Each meaningful word is matched
        separately so one call answers the whole request.
        """
        passport = self._upload(("CD_scan_0412.pdf", b"a", "application/pdf")).json()[0]["id"]
        self.client.patch(f"/settings/documents/{passport}", json={"label": "passport"})
        w2 = self._upload(("2024_wage_stmt.pdf", b"b", "application/pdf")).json()[0]["id"]
        self.client.patch(f"/settings/documents/{w2}", json={"label": "W2"})
        self._upload(("degree.pdf", b"c", "application/pdf"))

        one = list_candidate_documents("my passport")
        self.assertEqual([item["id"] for item in one["documents"]], [passport])

        both = list_candidate_documents("attach my passport and the W2 please")
        self.assertEqual({item["id"] for item in both["documents"]}, {passport, w2})
        # The filler words are reported as dropped, so a miss is explainable.
        self.assertEqual(both["matched_on"], ["passport", "w2"])

    def test_a_query_of_nothing_but_filler_lists_everything(self) -> None:
        self._upload(("passport.pdf", b"a", "application/pdf"))
        self._upload(("degree.pdf", b"b", "application/pdf"))
        result = list_candidate_documents("please attach my documents")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["matched_on"], [])

    def test_punctuation_around_a_word_does_not_break_the_match(self) -> None:
        passport = self._upload(("passport.pdf", b"a", "application/pdf")).json()[0]["id"]
        result = list_candidate_documents("send the passport, please")
        self.assertEqual([item["id"] for item in result["documents"]], [passport])

    def test_the_tool_reports_a_miss_without_offering_a_substitute(self) -> None:
        """The prompt tells the assistant to say what is on file instead.

        A tool that fell back to "here is everything" on no match would put a
        degree certificate one confirmation click away from a passport request.
        """
        self._upload(("passport.pdf", b"a", "application/pdf"))
        result = list_candidate_documents("marriage certificate")
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["total_on_file"], 1)

    def test_the_tool_never_returns_a_path_or_content(self) -> None:
        self._upload(("passport.pdf", b"X1234567", "application/pdf"))
        document = list_candidate_documents()["documents"][0]
        self.assertEqual(
            set(document), {"id", "label", "file_name", "mime_type", "file_size"}
        )

    def test_documents_are_scoped_to_the_owner(self) -> None:
        self._upload(("mine.pdf", b"a", "application/pdf"))
        with self.SessionLocal() as db:
            db.add(
                CandidateDocument(
                    owner_id="somebody-else",
                    file_path="/tmp/theirs.pdf",
                    file_name="theirs.pdf",
                    sha256="0" * 64,
                    file_size=1,
                )
            )
            db.commit()
        self.assertEqual(
            [item["file_name"] for item in self.client.get("/settings/documents").json()],
            ["mine.pdf"],
        )
        self.assertEqual(
            [item["file_name"] for item in list_candidate_documents()["documents"]], ["mine.pdf"]
        )


if __name__ == "__main__":
    unittest.main()
