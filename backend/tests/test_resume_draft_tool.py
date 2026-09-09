"""propose_resume_draft prepares a draft. It must never create one.

The invariant this file guards is the one the Editor is built on: a stored
variant is a file plus the text extracted from it, and nothing the chatbot does
may write underneath either. Proposing is a read.
"""

import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools.resumes import get_resume, propose_resume_draft
from app.models import ResumeAsset, ResumeDraft

OTHER_OWNER = "someone-else"

JAVA_TEXT = "# Chaithanya Dheeraj\n\n## Summary\n\nJava, Spring Boot, AWS.\n"
CLOUD_TEXT = "# Chaithanya Dheeraj\n\n## Summary\n\nTerraform, Kubernetes.\n"


class ResumeDraftToolTests(unittest.TestCase):
    def test_from_scratch_proposes_only_a_reviewable_header_and_empty_sections(self) -> None:
        before = self.snapshot()
        proposal = propose_resume_draft(from_scratch=True, candidate_name="Alex Rivera", contact_line="alex@example.com")
        self.assertTrue(proposal["from_scratch"])
        self.assertIn("# Alex Rivera\nalex@example.com", proposal["initial_content"])
        self.assertIn("## Experience", proposal["initial_content"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(propose_resume_draft(from_scratch=True)["status"], "missing_fields")
        self.assertEqual(propose_resume_draft(from_scratch=True, source_resume_id=1)["status"], "invalid_source")

    def test_from_scratch_proposes_only_a_reviewable_header_and_empty_sections(self) -> None:
        before = self.snapshot()
        proposal = propose_resume_draft(from_scratch=True, candidate_name="Alex Rivera", contact_line="alex@example.com")
        self.assertTrue(proposal["from_scratch"])
        self.assertIn("# Alex Rivera\nalex@example.com", proposal["initial_content"])
        self.assertIn("## Experience", proposal["initial_content"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(propose_resume_draft(from_scratch=True)["status"], "missing_fields")
        self.assertEqual(propose_resume_draft(from_scratch=True, source_resume_id=1)["status"], "invalid_source")

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.resumes.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        with self.SessionLocal() as db:
            db.add(ResumeAsset(
                id=1, owner_id=settings.owner_id, file_path="java.pdf", file_name="java.pdf",
                sha256="a" * 64, version=1, variant_label="Java Banking",
                primary_role="Java Developer", content_markdown=JAVA_TEXT,
            ))
            db.add(ResumeAsset(
                id=2, owner_id=settings.owner_id, file_path="cloud.pdf", file_name="cloud.pdf",
                sha256="b" * 64, version=1, variant_label="Cloud Platform",
                primary_role="Cloud Engineer", content_markdown=CLOUD_TEXT,
            ))
            # Uploaded, but nothing could be read out of it.
            db.add(ResumeAsset(
                id=3, owner_id=settings.owner_id, file_path="scan.pdf", file_name="scan.pdf",
                sha256="c" * 64, version=1, variant_label="Scanned Copy",
                primary_role="Java Developer", content_markdown=None,
            ))
            # A second label starting "Java", so "my Java resume" is a question
            # rather than a pick.
            db.add(ResumeAsset(
                id=5, owner_id=settings.owner_id, file_path="health.pdf", file_name="health.pdf",
                sha256="e" * 64, version=1, variant_label="Java Healthcare",
                primary_role="Java Developer", content_markdown=JAVA_TEXT,
            ))
            db.add(ResumeAsset(
                id=4, owner_id=OTHER_OWNER, file_path="theirs.pdf", file_name="theirs.pdf",
                sha256="d" * 64, version=1, variant_label="Java Banking",
                primary_role="Java Developer", content_markdown="Not yours.",
            ))
            db.commit()

    def snapshot(self) -> tuple[int, list[tuple[int, str | None]]]:
        """Every draft, and the text of every variant. Neither may move."""
        with self.SessionLocal() as db:
            return (
                db.query(ResumeDraft).count(),
                sorted((row.id, row.content_markdown) for row in db.query(ResumeAsset).all()),
            )

    def test_proposing_a_draft_writes_nothing(self) -> None:
        before = self.snapshot()

        propose_resume_draft(source_resume_id=1)
        propose_resume_draft(variant="Cloud Platform")
        propose_resume_draft(variant="Java Banking", name="R01 tailored for Cigna")

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(before[0], 0)

    def test_the_card_names_the_variant_and_the_size_of_what_it_copies(self) -> None:
        payload = propose_resume_draft(source_resume_id=1)

        self.assertEqual(payload["action"], "propose_resume_draft")
        self.assertEqual(payload["source_resume_id"], 1)
        self.assertEqual(payload["source_variant_code"], "R01")
        self.assertEqual(payload["source_file_name"], "java.pdf")
        self.assertEqual(payload["source_variant_label"], "Java Banking")
        # The count is the real length, not an estimate: it is the only thing on
        # the card that says how much text is being copied.
        self.assertEqual(payload["source_characters"], len(JAVA_TEXT))

    def test_the_card_never_carries_the_resume_text(self) -> None:
        payload = propose_resume_draft(source_resume_id=1)

        self.assertNotIn("content_markdown", payload)
        self.assertNotIn(JAVA_TEXT, str(payload))

    def test_a_variant_can_be_named_the_same_ways_get_resume_accepts(self) -> None:
        by_label = propose_resume_draft(variant="Java Banking")
        by_role = propose_resume_draft(variant="Cloud Engineer")
        by_file = propose_resume_draft(variant="cloud.pdf")

        self.assertEqual(by_label["source_resume_id"], 1)
        self.assertEqual(by_role["source_resume_id"], 2)
        self.assertEqual(by_file["source_resume_id"], 2)

    def test_the_default_name_is_the_one_the_editor_would_have_stored(self) -> None:
        self.assertEqual(propose_resume_draft(source_resume_id=1)["name"], "R01 Java Banking")
        # A caller's own name wins, trimmed.
        self.assertEqual(
            propose_resume_draft(source_resume_id=1, name="  R01 for Cigna  ")["name"],
            "R01 for Cigna",
        )

    def test_a_variant_with_no_label_falls_back_to_copy(self) -> None:
        with self.SessionLocal() as db:
            db.add(ResumeAsset(
                id=6, owner_id=settings.owner_id, file_path="bare.pdf", file_name="bare.pdf",
                sha256="f" * 64, version=1, content_markdown="# Bare\n",
            ))
            db.commit()

        self.assertEqual(propose_resume_draft(source_resume_id=6)["name"], "R06 copy")

    def test_naming_no_resume_asks_for_one_rather_than_picking(self) -> None:
        payload = propose_resume_draft()

        self.assertEqual(payload["status"], "missing_fields")
        self.assertEqual(payload["missing"], ["resume_id or variant"])
        self.assertNotIn("action", payload)

    def test_an_unknown_name_lists_the_ones_that_exist(self) -> None:
        payload = propose_resume_draft(variant="Mainframe")

        self.assertEqual(payload["status"], "not_found")
        self.assertIn("Java Banking", payload["known"])
        self.assertNotIn("action", payload)

    def test_a_name_matching_several_variants_asks_which_and_does_not_pick(self) -> None:
        payload = propose_resume_draft(variant="Java")

        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["instruction"], "Ask which one. Do not pick.")
        self.assertEqual(
            sorted(match["id"] for match in payload["matches"]), [1, 5]
        )
        self.assertNotIn("action", payload)

    def test_a_resume_no_text_was_read_out_of_is_refused_not_copied_empty(self) -> None:
        payload = propose_resume_draft(source_resume_id=3)

        self.assertIn("No text could be extracted", payload["error"])
        self.assertNotIn("action", payload)

    def test_another_owners_resume_is_not_reachable_by_id_or_by_name(self) -> None:
        by_id = propose_resume_draft(source_resume_id=4)

        self.assertEqual(by_id["error"], "Resume not found")
        self.assertNotIn("action", by_id)
        # The shared label resolves to this owner's variants only.
        self.assertEqual(propose_resume_draft(variant="Java Banking")["source_resume_id"], 1)

    def test_reading_and_drafting_refuse_a_bad_name_identically(self) -> None:
        """One resolver, so "my Mainframe resume" fails the same way in both."""
        for read, draft in (
            (get_resume(variant="Mainframe"), propose_resume_draft(variant="Mainframe")),
            (get_resume(variant="Java"), propose_resume_draft(variant="Java")),
            (get_resume(), propose_resume_draft()),
        ):
            self.assertEqual(read["status"], draft["status"])


if __name__ == "__main__":
    unittest.main()
