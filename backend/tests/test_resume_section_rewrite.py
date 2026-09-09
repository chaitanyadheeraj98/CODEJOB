"""Rewriting one section of a draft: the splitter, the tool, and the endpoint.

The property under test throughout is containment. A rewrite of "Summary" must
change Summary and nothing else - not the section after it, not the heading, not
the stored variant the draft was copied from.
"""

import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.mcp_server.tools.resume_drafts import (
    MAX_SECTION_CHARS,
    get_resume_draft,
    list_resume_drafts,
    propose_resume_section,
)
from app.models import ResumeAsset, ResumeDraft
from app.routers.resume_editor import router as resume_editor_router
from app.services.resume_render_service import (
    covers_whole_document,
    find_section,
    nested_headings,
    replace_section,
    section_digest,
    split_sections,
)

RESUME = """## Chaithanya Dheeraj N

chaithanyadheeraj1026@gmail.com | +1 940-629-6920

### Summary

- Java Full Stack Developer with 7+ years of experience.
- Strong hands-on experience with Spring Boot.

### Skills

| Languages | Java, Python |

### Experiences

#### Acme Corp

- Built services.

#### Globex

- Maintained services.

### Education Details

- BS, Computer Science.
"""


class SectionSplitterTests(unittest.TestCase):
    def test_every_heading_becomes_a_section_at_its_own_depth(self) -> None:
        sections = {item.heading: item.level for item in split_sections(RESUME)}

        self.assertEqual(sections["Chaithanya Dheeraj N"], 2)
        self.assertEqual(sections["Summary"], 3)
        self.assertEqual(sections["Acme Corp"], 4)

    def test_a_section_ends_where_the_next_one_of_its_level_begins(self) -> None:
        summary = find_section(RESUME, "Summary")

        self.assertIn("7+ years", summary.body)
        # The Skills heading below it is a sibling, so it is not swallowed.
        self.assertNotIn("Languages", summary.body)

    def test_a_section_keeps_the_subsections_underneath_it(self) -> None:
        experiences = find_section(RESUME, "Experiences")

        self.assertIn("Acme Corp", experiences.body)
        self.assertIn("Globex", experiences.body)
        self.assertNotIn("BS, Computer Science", experiences.body)

    def test_a_section_is_found_however_the_user_wrote_its_name(self) -> None:
        for spelling in ("Summary", "summary", "  SUMMARY  ", "### Summary"):
            self.assertIsNotNone(find_section(RESUME, spelling), spelling)

    def test_a_name_matching_no_single_heading_finds_nothing(self) -> None:
        self.assertIsNone(find_section(RESUME, "Publications"))
        self.assertIsNone(find_section(RESUME, ""))
        # Two roles could each be "Experience"; picking one is not this layer's call.
        doubled = RESUME + "\n### Summary\n\n- A second one.\n"
        self.assertIsNone(find_section(doubled, "Summary"))

    def test_a_rewrite_changes_one_section_and_leaves_the_rest_byte_for_byte(self) -> None:
        rewritten = replace_section(RESUME, "Summary", "- Rewritten for Cigna.")

        self.assertIn("- Rewritten for Cigna.", rewritten)
        self.assertNotIn("7+ years", rewritten)
        # The heading survives, and so does everything around it.
        self.assertIn("### Summary", rewritten)
        for untouched in ("| Languages | Java, Python |", "#### Globex", "BS, Computer Science"):
            self.assertIn(untouched, rewritten)

    def test_the_order_of_sections_is_preserved(self) -> None:
        rewritten = replace_section(RESUME, "Skills", "| Languages | Go |")

        self.assertEqual(
            [item.heading for item in split_sections(rewritten)],
            [item.heading for item in split_sections(RESUME)],
        )

    def test_rewriting_a_section_that_is_not_there_refuses_rather_than_appends(self) -> None:
        self.assertIsNone(replace_section(RESUME, "Publications", "- Nothing."))

    def test_the_top_heading_is_recognised_as_covering_the_document(self) -> None:
        name = find_section(RESUME, "Chaithanya Dheeraj N")

        self.assertTrue(covers_whole_document(RESUME, name))
        self.assertIn("Summary", nested_headings(RESUME, name))
        self.assertIn("Education Details", nested_headings(RESUME, name))

    def test_a_large_but_partial_section_is_not_blocked(self) -> None:
        """Experiences is most of a resume and nests roles, but is a real section."""
        experiences = find_section(RESUME, "Experiences")

        self.assertTrue(nested_headings(RESUME, experiences))
        self.assertFalse(covers_whole_document(RESUME, experiences))

    def test_a_leaf_section_is_never_blocked_however_large(self) -> None:
        """A one-section draft has to stay editable - replacing it destroys nothing else."""
        single = "## Summary\n\n" + ("- A long line.\n" * 200)
        section = find_section(single, "Summary")

        self.assertFalse(covers_whole_document(single, section))
        self.assertEqual(nested_headings(single, section), [])

    def test_a_digest_ignores_only_surrounding_whitespace(self) -> None:
        self.assertEqual(section_digest("- One.\n"), section_digest("\n- One.  \n\n"))
        self.assertNotEqual(section_digest("- One."), section_digest("- Two."))


class ResumeDraftToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.resume_drafts.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        with self.SessionLocal() as db:
            db.add(ResumeDraft(
                id=1, owner_id=settings.owner_id, name="R21 for Cigna",
                source_resume_id=21, content_markdown=RESUME,
            ))
            db.add(ResumeDraft(
                id=2, owner_id="someone-else", name="Theirs", content_markdown="## Not yours\n",
            ))
            db.commit()

    def stored(self, draft_id: int = 1) -> str | None:
        with self.SessionLocal() as db:
            row = db.query(ResumeDraft).filter(ResumeDraft.id == draft_id).first()
            return row.content_markdown if row else None

    def test_the_list_gives_the_ids_and_section_names_a_rewrite_needs(self) -> None:
        drafts = list_resume_drafts()["drafts"]

        self.assertEqual([item["id"] for item in drafts], [1])
        headings = [item["heading"] for item in drafts[0]["sections"]]
        self.assertIn("Summary", headings)
        self.assertIn("Experiences", headings)

    def test_the_list_carries_sizes_rather_than_the_resume_text(self) -> None:
        drafts = list_resume_drafts()["drafts"]

        self.assertNotIn("7+ years", str(drafts))
        self.assertEqual(drafts[0]["characters"], len(RESUME))

    def test_reading_one_section_returns_that_section_marked_untrusted(self) -> None:
        payload = get_resume_draft(1, section="Summary")

        self.assertEqual(payload["section"], "Summary")
        self.assertIn("7+ years", payload["untrusted_resume_data"])
        self.assertIn("<untrusted_resume_data>", payload["untrusted_resume_data"])
        self.assertNotIn("Languages", payload["untrusted_resume_data"])

    def test_reading_a_section_that_is_not_there_names_the_ones_that_are(self) -> None:
        payload = get_resume_draft(1, section="Publications")

        self.assertEqual(payload["status"], "section_not_found")
        self.assertIn("Summary", payload["sections"])

    def test_proposing_a_rewrite_changes_nothing(self) -> None:
        before = self.stored()

        payload = propose_resume_section(1, "Summary", "- Rewritten for Cigna.")

        self.assertEqual(payload["action"], "propose_resume_section")
        self.assertEqual(self.stored(), before)

    def test_the_card_shows_the_current_text_read_from_the_database(self) -> None:
        payload = propose_resume_section(1, "summary", "- Rewritten for Cigna.")

        # Named as it is stored, not as it was typed.
        self.assertEqual(payload["section"], "Summary")
        self.assertIn("7+ years", payload["current"])
        self.assertEqual(payload["replacement"], "- Rewritten for Cigna.")
        self.assertEqual(payload["base_sha256"], section_digest(find_section(RESUME, "Summary").body))
        warning = propose_resume_section(1, "Summary", "Saved $2M and cut latency 40%.", sequence_sections=["Summary", "Skills", "Invented"])
        self.assertEqual(warning["grounding"]["novel_numbers"], ["$2M", "40%"])
        self.assertEqual(warning["sequence_sections"], ["Summary", "Skills"])

    def test_a_rewrite_the_size_of_a_resume_is_refused_as_too_many_sections(self) -> None:
        payload = propose_resume_section(1, "Summary", "- x" * MAX_SECTION_CHARS)

        self.assertEqual(payload["status"], "too_long")
        self.assertEqual(payload["limit"], MAX_SECTION_CHARS)
        self.assertNotIn("action", payload)

    def test_an_empty_rewrite_asks_for_the_missing_field(self) -> None:
        self.assertEqual(propose_resume_section(1, "Summary", "   ")["missing"], ["replacement"])
        self.assertEqual(propose_resume_section(1, "", "- Text.")["missing"], ["section"])

    def test_an_unknown_draft_lists_the_ones_that_exist(self) -> None:
        payload = propose_resume_section(99, "Summary", "- Text.")

        self.assertEqual(payload["error"], "Draft not found")
        self.assertEqual([item["id"] for item in payload["drafts"]], [1])
        self.assertNotIn("action", payload)

    def test_a_draft_is_reachable_by_name_so_a_rewrite_takes_two_calls(self) -> None:
        """No list step: read by name, then propose. The turn budget allows two."""
        read = get_resume_draft(name="Cigna", section="Summary")

        self.assertEqual(read["draft_id"], 1)
        self.assertIn("7+ years", read["untrusted_resume_data"])

        payload = propose_resume_section(read["draft_id"], "Summary", "- Rewritten.")
        self.assertEqual(payload["action"], "propose_resume_section")

    def test_a_rewrite_can_name_the_draft_directly_too(self) -> None:
        payload = propose_resume_section(section="Summary", replacement="- Rewritten.", name="Cigna")

        self.assertEqual(payload["draft_id"], 1)

    def test_naming_no_draft_asks_for_one(self) -> None:
        payload = get_resume_draft()

        self.assertEqual(payload["status"], "missing_fields")
        self.assertEqual(payload["missing"], ["draft_id or name"])

    def test_a_name_matching_no_draft_lists_the_ones_that_exist(self) -> None:
        payload = get_resume_draft(name="Mainframe")

        self.assertEqual(payload["status"], "not_found")
        self.assertEqual([item["id"] for item in payload["drafts"]], [1])

    def test_two_drafts_of_the_same_variant_are_a_question_not_a_pick(self) -> None:
        """Exactly the duplicate pair the Editor shows after a retry."""
        with self.SessionLocal() as db:
            db.add(ResumeDraft(
                id=3, owner_id=settings.owner_id, name="R21 for Cigna (second try)",
                source_resume_id=21, content_markdown=RESUME,
            ))
            db.commit()

        payload = get_resume_draft(name="Cigna", section="Summary")

        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["instruction"], "Ask which one. Do not pick.")
        self.assertEqual(sorted(item["id"] for item in payload["matches"]), [1, 3])

    def test_another_owners_draft_is_not_reachable(self) -> None:
        self.assertEqual(get_resume_draft(2)["error"], "Draft not found")
        self.assertEqual(propose_resume_section(2, "Not yours", "- Text.")["error"], "Draft not found")


class ResumeSectionEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        app = FastAPI()
        app.include_router(resume_editor_router)
        app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(app)

        with self.SessionLocal() as db:
            db.add(ResumeAsset(
                id=21, owner_id=settings.owner_id, file_path="r.docx", file_name="r.docx",
                sha256="a" * 64, version=1, content_markdown=RESUME,
            ))
            db.add(ResumeDraft(
                id=1, owner_id=settings.owner_id, name="R21 for Cigna",
                source_resume_id=21, content_markdown=RESUME,
            ))
            db.commit()

    def override_db(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def digest_of(self, heading: str) -> str:
        return section_digest(find_section(RESUME, heading).body)

    def test_the_click_rewrites_only_the_named_section(self) -> None:
        response = self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Summary",
            "replacement": "- Rewritten for Cigna.",
            "base_sha256": self.digest_of("Summary"),
        })

        self.assertEqual(response.status_code, 200)
        body = response.json()["content_markdown"]
        self.assertIn("- Rewritten for Cigna.", body)
        self.assertNotIn("7+ years", body)
        self.assertIn("| Languages | Java, Python |", body)
        self.assertIn("BS, Computer Science", body)

    def test_the_rewrite_never_reaches_the_variant_the_draft_came_from(self) -> None:
        self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Summary", "replacement": "- Rewritten.", "base_sha256": self.digest_of("Summary"),
        })

        with self.SessionLocal() as db:
            variant = db.query(ResumeAsset).filter(ResumeAsset.id == 21).first()
        self.assertEqual(variant.content_markdown, RESUME)

    def test_a_section_edited_since_the_card_was_built_is_refused_not_overwritten(self) -> None:
        # The user edits the Summary in the Editor while the card sits unresolved.
        self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Summary", "replacement": "- The user's own edit.",
            "base_sha256": self.digest_of("Summary"),
        })

        stale = self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Summary", "replacement": "- The model's older rewrite.",
            "base_sha256": self.digest_of("Summary"),
        })

        self.assertEqual(stale.status_code, 409)
        self.assertIn("has changed", stale.json()["detail"])
        current = self.client.get("/resume-editor/drafts/1").json()["content_markdown"]
        self.assertIn("- The user's own edit.", current)

    def test_a_write_with_no_digest_is_allowed_so_the_editor_itself_still_works(self) -> None:
        response = self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Skills", "replacement": "| Languages | Go |",
        })

        self.assertEqual(response.status_code, 200)

    def test_an_unknown_section_is_a_404_that_names_the_real_ones(self) -> None:
        response = self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Publications", "replacement": "- Nothing.",
        })

        self.assertEqual(response.status_code, 404)
        self.assertIn("Summary", response.json()["detail"])

    def test_the_name_heading_cannot_be_used_to_replace_the_whole_resume(self) -> None:
        """The exact mistake: the name is `##`, every section is `###`, so it owns all of it."""
        response = self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Chaithanya Dheeraj N",
            "replacement": "chaithanyadheeraj1026@gmail.com | LinkedIn",
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("spans the whole draft", response.json()["detail"])
        self.assertIn("Summary", response.json()["detail"])

        untouched = self.client.get("/resume-editor/drafts/1").json()["content_markdown"]
        self.assertEqual(untouched, RESUME)

    def test_a_real_section_is_still_editable_after_the_guard(self) -> None:
        response = self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Experiences", "replacement": "#### Acme Corp\n\n- Rewritten.",
        })

        self.assertEqual(response.status_code, 200)

    def test_an_unknown_draft_is_a_404(self) -> None:
        response = self.client.patch("/resume-editor/drafts/99/section", json={
            "section": "Summary", "replacement": "- Nothing.",
        })

        self.assertEqual(response.status_code, 404)

    def test_the_draft_still_renders_after_a_rewrite(self) -> None:
        self.client.patch("/resume-editor/drafts/1/section", json={
            "section": "Summary", "replacement": "- Rewritten for **Cigna**.",
        })

        download = self.client.get("/resume-editor/drafts/1/export?fmt=md")
        self.assertEqual(download.status_code, 200)
        self.assertIn("Rewritten for **Cigna**", download.text)


if __name__ == "__main__":
    unittest.main()
