"""Drafting a resume and downloading it in an employer's layout.

Two things are asserted here that the feature cannot be correct without.

The layout numbers are literal - 0.25in left margin, Arial 10, a 3.00in first
column - because they are the ones the user was setting by hand in Google Docs,
and a rendering that quietly drifts off them is indistinguishable from one that
works until an employer opens it.

The other is that a stored variant is never written to. A variant's text is what
the matcher and the chatbot argue from and its file is what the recruiter
receives; if editing could change one without the other, the app would recommend
a resume nobody was ever sent.
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import ResumeAsset, ResumeDraft, ResumeFormatProfile
from app.routers import resume_editor
from app.schemas import resume_variant_code
from app.services import resume_format_profile_service
from app.services.resume_render_service import ResumeFormatSpec, build_docx, split_sections

WORDPROCESSING_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

RESUME_MARKDOWN = """# Chaithanya Dheeraj
Dallas, TX | 555-0100 | me@example.com

## Summary
Java developer with **8 years** building **Spring Boot** microservices.

## Skills
| Category | Technologies |
| --- | --- |
| Languages | Java, TypeScript |
| Frameworks | Spring Boot, Angular |
| Cloud | GCP, Terraform |

## Experiences
- Built reusable **Angular** components with routing and forms.
- Optimized **Oracle** queries, cutting latency 40%.

Environment: Java, Spring Boot, Oracle, GCP

## Education Details
B.Tech, Computer Science
"""


def rule_count(document: Document) -> int:
    """Rules are bottom borders on empty paragraphs - Word has no rule element."""
    return sum(1 for p in document.paragraphs if p._p.find(f".//{WORDPROCESSING_NS}pBdr") is not None)


class ResumeRenderTests(unittest.TestCase):
    def test_a4_accent_spacing_and_compact_reach_the_word_document(self) -> None:
        spec = ResumeFormatSpec(page_size="A4", accent_color="#245b78", section_spacing_pt=8, compact=True)
        document = self._render(spec=spec)
        self.assertAlmostEqual(document.sections[0].page_width.inches, 210 / 25.4, places=2)
        self.assertAlmostEqual(document.sections[0].page_height.inches, 297 / 25.4, places=2)
        self.assertAlmostEqual(sum(c.width.inches for c in document.tables[0].columns), spec.usable_width_inches, places=2)
        heading = next(p for p in document.paragraphs if p.text == "Summary")
        self.assertEqual(str(heading.runs[0].font.color.rgb), "245B78")
        self.assertEqual(heading.paragraph_format.space_before.pt, 6)
        self.assertEqual(heading.paragraph_format.space_after.pt, 1)
        self.assertEqual(document.tables[0].cell(1, 0).paragraphs[0].paragraph_format.space_after.pt, 3)
        measured = resume_format_profile_service.measure_docx(self.work / "out.docx", ResumeFormatSpec())
        self.assertEqual(measured.page_size, "A4")
        self.assertAlmostEqual(measured.usable_width_inches, spec.usable_width_inches, places=2)

    def test_preview_fixture_uses_the_same_headings_as_the_server_splitter(self) -> None:
        fixture = json.loads((Path(__file__).parents[2] / "dashboard/src/features/resume_tracking/editor/resumePreview.fixture.json").read_text(encoding="utf-8"))
        self.assertEqual([section.heading for section in split_sections(fixture["markdown"])], fixture["headings"])

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="render-test-"))

    def tearDown(self) -> None:
        for path in self.work.glob("*"):
            path.unlink()
        self.work.rmdir()

    def _render(self, markdown: str = RESUME_MARKDOWN, spec: ResumeFormatSpec | None = None) -> Document:
        target = build_docx(markdown, spec or ResumeFormatSpec(), self.work / "out.docx")
        return Document(str(target))

    def test_default_spec_reproduces_the_page_geometry_the_apps_script_set(self) -> None:
        document = self._render()
        section = document.sections[0]
        self.assertAlmostEqual(section.left_margin.inches, 0.25, places=2)
        self.assertAlmostEqual(section.right_margin.inches, 0.5, places=2)
        self.assertEqual(document.styles["Normal"].font.name, "Arial")
        self.assertAlmostEqual(document.styles["Normal"].font.size.pt, 10.0, places=1)

    def test_skills_table_splits_at_the_divider_and_ends_at_the_right_ruler(self) -> None:
        table = self._render().tables[0]
        widths = [round(column.width.inches, 2) for column in table.columns]
        # 3.00 divider, 7.75 right ruler: the two columns are 3.00 and 4.75.
        self.assertEqual(widths, [3.0, 4.75])
        self.assertAlmostEqual(sum(widths), ResumeFormatSpec().usable_width_inches, places=2)

    def test_skills_categories_are_bold_and_rows_are_spaced_except_the_last(self) -> None:
        table = self._render().tables[0]
        self.assertTrue(table.cell(1, 0).paragraphs[0].runs[0].bold)
        self.assertFalse(table.cell(1, 1).paragraphs[0].runs[0].bold)
        self.assertAlmostEqual(table.cell(1, 0).paragraphs[0].paragraph_format.space_after.pt, 6.0, places=1)
        last = table.rows[-1].cells[0].paragraphs[0]
        self.assertAlmostEqual(last.paragraph_format.space_after.pt, 0.0, places=1)

    def test_name_and_contact_line_are_centred_and_the_name_is_larger(self) -> None:
        paragraphs = self._render().paragraphs
        self.assertEqual(paragraphs[0].text, "Chaithanya Dheeraj")
        self.assertEqual(str(paragraphs[0].alignment), "CENTER (1)")
        self.assertAlmostEqual(paragraphs[0].runs[0].font.size.pt, 14.0, places=1)
        self.assertTrue(paragraphs[0].runs[0].bold)
        self.assertEqual(str(paragraphs[1].alignment), "CENTER (1)")

    def test_a_rule_is_drawn_only_above_the_sections_the_profile_names(self) -> None:
        # Summary, Skills, Experiences and Education Details are in the sample;
        # Certifications is in the default list but not in this resume.
        self.assertEqual(rule_count(self._render()), 4)
        spec = ResumeFormatSpec(rule_before_sections=["Skills"])
        self.assertEqual(rule_count(self._render(spec=spec)), 1)

    def test_the_name_never_gets_a_rule_even_when_it_matches_a_section(self) -> None:
        spec = ResumeFormatSpec(rule_before_sections=["Chaithanya Dheeraj", "Skills"])
        self.assertEqual(rule_count(self._render(spec=spec)), 1)

    def test_bullets_carry_the_hanging_indent_and_keep_their_bold_keywords(self) -> None:
        paragraphs = [p for p in self._render().paragraphs if p.style.name == "List Bullet"]
        self.assertEqual(len(paragraphs), 2)
        self.assertAlmostEqual(paragraphs[0].paragraph_format.left_indent.inches, 0.5, places=2)
        self.assertAlmostEqual(paragraphs[0].paragraph_format.first_line_indent.inches, -0.25, places=2)
        self.assertEqual([run.text for run in paragraphs[0].runs if run.bold], ["Angular"])

    def test_environment_lines_get_a_gap_so_the_next_role_does_not_run_into_them(self) -> None:
        texts = [p.text for p in self._render().paragraphs]
        index = texts.index("Environment: Java, Spring Boot, Oracle, GCP")
        self.assertEqual(texts[index + 1], "")

    def test_an_empty_document_still_renders(self) -> None:
        self.assertEqual(rule_count(self._render(markdown="")), 0)


class ResumeFormatProfileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="profile-test-"))

    def tearDown(self) -> None:
        for path in self.work.glob("*"):
            path.unlink()
        self.work.rmdir()

    def test_measuring_a_sample_recovers_the_layout_it_was_written_with(self) -> None:
        written = ResumeFormatSpec(
            font_family="Calibri",
            body_font_size=11,
            margin_left_inches=1.0,
            margin_right_inches=0.75,
            skills_divider_inches=2.5,
        )
        sample = build_docx(RESUME_MARKDOWN, written, self.work / "sample.docx")
        measured = resume_format_profile_service.measure_docx(sample, ResumeFormatSpec())
        self.assertEqual(measured.font_family, "Calibri")
        self.assertAlmostEqual(measured.body_font_size, 11.0, places=1)
        self.assertAlmostEqual(measured.margin_left_inches, 1.0, places=2)
        self.assertAlmostEqual(measured.margin_right_inches, 0.75, places=2)
        self.assertAlmostEqual(measured.skills_divider_inches, 2.5, places=2)

    def test_a_heading_the_model_invented_is_dropped(self) -> None:
        raw = json.dumps({"sections": ["Summary", "Publications", "Skills"]})
        kept = resume_format_profile_service._validate_sections(raw, RESUME_MARKDOWN)
        self.assertEqual(kept, ["Summary", "Skills"])

    def test_a_fenced_reply_is_read_and_a_broken_one_falls_back_to_nothing(self) -> None:
        fenced = "```json\n" + json.dumps({"sections": ["Skills"]}) + "\n```"
        self.assertEqual(resume_format_profile_service._validate_sections(fenced, RESUME_MARKDOWN), ["Skills"])
        self.assertEqual(resume_format_profile_service._validate_sections("not json", RESUME_MARKDOWN), [])

    def test_an_unreachable_model_leaves_the_default_sections_in_place(self) -> None:
        def unavailable() -> None:
            raise RuntimeError("ollama is down")

        original = resume_format_profile_service.build_chat_llm
        resume_format_profile_service.build_chat_llm = unavailable
        try:
            sample = build_docx(RESUME_MARKDOWN, ResumeFormatSpec(), self.work / "sample.docx")
            spec = resume_format_profile_service.spec_from_sample(sample, "sample.docx")
        finally:
            resume_format_profile_service.build_chat_llm = original
        self.assertEqual(spec.rule_before_sections, ResumeFormatSpec().rule_before_sections)


class ResumeEditorApiTests(unittest.TestCase):
    def test_a_draft_remembers_its_profile_and_survives_profile_deletion(self) -> None:
        draft = self._draft_from_variant()
        spec = ResumeFormatSpec(page_size="A4", accent_color="#245b78", compact=True, section_spacing_pt=4)
        profile = self.client.post("/resume-editor/profiles", data={"name": "A4 custom", "spec_json": spec.model_dump_json()})
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["spec"], spec.model_dump())
        profile_id = profile.json()["id"]
        saved = self.client.put(f'/resume-editor/drafts/{draft["id"]}', json={"format_profile_id": profile_id}).json()
        self.assertEqual(saved["format_profile_id"], profile_id)
        self.assertEqual([s["heading"] for s in saved["sections"]], [s.heading for s in split_sections(draft["content_markdown"])])
        import io
        exported = self.client.get(f'/resume-editor/drafts/{draft["id"]}/export?fmt=docx')
        self.assertEqual(exported.status_code, 200)
        self.assertAlmostEqual(Document(io.BytesIO(exported.content)).sections[0].page_width.inches, 210 / 25.4, places=2)
        self.client.delete(f"/resume-editor/profiles/{profile_id}")
        self.assertEqual(self.client.get(f'/resume-editor/drafts/{draft["id"]}/export?fmt=docx').status_code, 200)
        cleared = self.client.put(f'/resume-editor/drafts/{draft["id"]}', json={"format_profile_id": None}).json()
        self.assertIsNone(cleared["format_profile_id"])

    def test_profile_binding_and_thumbnails_are_owner_scoped_and_geometry_is_validated(self) -> None:
        draft = self._draft_from_variant()
        with self.SessionLocal() as db:
            profile = ResumeFormatProfile(owner_id="someone-else", name="Private", spec_json="{}")
            db.add(profile)
            db.commit()
            profile_id = profile.id
        self.assertEqual(self.client.put(f'/resume-editor/drafts/{draft["id"]}', json={"format_profile_id": profile_id}).status_code, 404)
        self.assertEqual(self.client.get(f"/resume-editor/profiles/{profile_id}/preview.png").status_code, 404)
        # "two-column" is a supported layout since the sidebar family landed; what
        # is still refused is a sidebar that leaves no main column to write in.
        for invalid in (
            {"accent_color": "url(javascript:bad)"},
            {"skills_divider_inches": 20},
            {"line_spacing": 0},
            {"layout": "three-column"},
            {"layout": "two-column", "sidebar_width_inches": 7},
        ):
            self.assertEqual(self.client.post("/resume-editor/profiles", data={"name": "Invalid", "spec_json": json.dumps(invalid)}).status_code, 422)

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
        self.storage = tempfile.mkdtemp(prefix="editor-store-")
        self.original_storage = main.settings.resume_storage_dir
        main.settings.resume_storage_dir = self.storage

        with self.SessionLocal() as db:
            resume = ResumeAsset(
                owner_id=main.settings.owner_id,
                file_path=f"{self.storage}/resume.pdf",
                file_name="resume-java.pdf",
                mime_type="application/pdf",
                sha256="a" * 64,
                version=1,
                skills_text="java",
                variant_label="Java / Banking",
                content_markdown=RESUME_MARKDOWN,
            )
            db.add(resume)
            db.commit()
            self.resume_id = resume.id

    def tearDown(self) -> None:
        main.settings.resume_storage_dir = self.original_storage
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _stored_resume(self) -> ResumeAsset:
        with self.SessionLocal() as db:
            return db.get(ResumeAsset, self.resume_id)

    def _draft_from_variant(self) -> dict:
        response = self.client.post("/resume-editor/drafts", json={"source_resume_id": self.resume_id})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _reorder(self, draft_id: int, section: str, direction: str, digest: str = ""):
        return self.client.post(
            f"/resume-editor/drafts/{draft_id}/sections/reorder",
            json={"section": section, "direction": direction, "base_sha256": digest},
        )

    def test_reordering_moves_a_section_and_reports_the_new_order(self) -> None:
        draft = self._draft_from_variant()
        response = self._reorder(draft["id"], "Skills", "up")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        headings = [item["heading"] for item in body["sections"]]
        self.assertLess(headings.index("Skills"), headings.index("Summary"))
        # The table under the heading travelled with it.
        self.assertLess(body["content_markdown"].index("| Languages"), body["content_markdown"].index("## Summary"))

    def test_reordering_never_reaches_the_variant_the_draft_came_from(self) -> None:
        draft = self._draft_from_variant()
        self._reorder(draft["id"], "Skills", "up")
        self.assertEqual(self._stored_resume().content_markdown, RESUME_MARKDOWN)

    def test_a_section_at_the_end_of_its_level_says_so_rather_than_doing_nothing(self) -> None:
        draft = self._draft_from_variant()
        response = self._reorder(draft["id"], "Summary", "up")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already the first", response.json()["detail"])

    def test_reordering_a_section_that_is_not_there_lists_the_ones_that_are(self) -> None:
        draft = self._draft_from_variant()
        response = self._reorder(draft["id"], "Publications", "down")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Summary", response.json()["detail"])

    def test_a_reorder_written_against_stale_text_is_refused(self) -> None:
        draft = self._draft_from_variant()
        response = self._reorder(draft["id"], "Skills", "up", digest="b" * 64)
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed since", response.json()["detail"])

    def test_a_draft_from_a_variant_copies_its_text_and_names_where_it_came_from(self) -> None:
        draft = self._draft_from_variant()
        self.assertIn("Spring Boot", draft["content_markdown"])
        self.assertEqual(draft["source_resume_id"], self.resume_id)
        self.assertEqual(draft["source_variant_code"], resume_variant_code(self.resume_id))
        self.assertIn("Java / Banking", draft["name"])

    def test_editing_a_draft_never_reaches_the_variant_it_came_from(self) -> None:
        before = self._stored_resume()
        original_text, original_path, original_sha = before.content_markdown, before.file_path, before.sha256

        draft = self._draft_from_variant()
        saved = self.client.put(
            f"/resume-editor/drafts/{draft['id']}",
            json={"content_markdown": "# Someone Else\n\n## Summary\nRewritten."},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn("Rewritten", saved.json()["content_markdown"])

        after = self._stored_resume()
        self.assertEqual(after.content_markdown, original_text)
        self.assertEqual(after.file_path, original_path)
        self.assertEqual(after.sha256, original_sha)

    def test_no_route_on_this_router_writes_underneath_a_resume(self) -> None:
        # A structural guard, not a behavioural one: the invariant is that the
        # editor cannot touch a variant at all, and a future endpoint that broke
        # it would pass every other test in this file.
        writes = [
            route
            for route in main.app.routes
            if getattr(route, "path", "").startswith("/resume-editor")
            and set(getattr(route, "methods", set())) & {"POST", "PUT", "PATCH", "DELETE"}
        ]
        self.assertTrue(writes)
        for route in writes:
            self.assertNotIn("/resumes/", route.path, f"{route.path} writes under a resume")

    def test_a_draft_can_start_empty_and_be_written_from_nothing(self) -> None:
        created = self.client.post("/resume-editor/drafts", json={"name": "Vendor A tailored"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["content_markdown"], "")
        self.assertIsNone(created.json()["source_resume_id"])
        self.assertEqual(created.json()["name"], "Vendor A tailored")

    def test_drafting_from_a_variant_that_does_not_exist_is_a_404(self) -> None:
        response = self.client.post("/resume-editor/drafts", json={"source_resume_id": 9999})
        self.assertEqual(response.status_code, 404)

    def test_the_list_carries_sizes_rather_than_every_draft_in_full(self) -> None:
        self._draft_from_variant()
        listed = self.client.get("/resume-editor/drafts").json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["character_count"], len(RESUME_MARKDOWN.strip()))
        self.assertNotIn("content_markdown", listed[0])

    def test_a_draft_outlives_the_variant_it_was_copied_from(self) -> None:
        draft = self._draft_from_variant()
        with self.SessionLocal() as db:
            db.delete(db.get(ResumeAsset, self.resume_id))
            db.commit()

        reloaded = self.client.get(f"/resume-editor/drafts/{draft['id']}").json()
        self.assertIn("Spring Boot", reloaded["content_markdown"])
        self.assertEqual(reloaded["source_resume_id"], self.resume_id)
        # The code is dropped rather than shown pointing at a variant that is gone.
        self.assertEqual(reloaded["source_variant_code"], "")

    def test_an_oversized_draft_is_refused_with_the_limit_in_the_message(self) -> None:
        draft = self._draft_from_variant()
        response = self.client.put(
            f"/resume-editor/drafts/{draft['id']}",
            json={"content_markdown": "x" * (resume_editor.MAX_CONTENT_CHARS + 1)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(str(resume_editor.MAX_CONTENT_CHARS), response.json()["detail"])

    def test_a_deleted_draft_is_gone_and_its_variant_is_untouched(self) -> None:
        draft = self._draft_from_variant()
        self.assertEqual(self.client.delete(f"/resume-editor/drafts/{draft['id']}").status_code, 200)
        self.assertEqual(self.client.get("/resume-editor/drafts").json(), [])
        self.assertEqual(self.client.get(f"/resume-editor/drafts/{draft['id']}").status_code, 404)
        self.assertEqual(self._stored_resume().content_markdown, RESUME_MARKDOWN)

    def test_markdown_download_returns_the_draft_text_under_the_draft_name(self) -> None:
        draft = self.client.post(
            "/resume-editor/drafts",
            json={"name": "Vendor A / Java", "content_markdown": RESUME_MARKDOWN},
        ).json()
        response = self.client.get(f"/resume-editor/drafts/{draft['id']}/export?fmt=md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Spring Boot", response.text)
        self.assertIn("Vendor_A_Java.md", response.headers["content-disposition"])

    def test_word_download_is_a_readable_document_in_the_profile_layout(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                ResumeFormatProfile(
                    owner_id=main.settings.owner_id,
                    name="Vendor A",
                    spec_json=ResumeFormatSpec(
                        margin_left_inches=1.0, rule_before_sections=["Skills"]
                    ).model_dump_json(),
                )
            )
            db.commit()
            profile_id = db.query(ResumeFormatProfile).one().id

        draft = self._draft_from_variant()
        response = self.client.get(
            f"/resume-editor/drafts/{draft['id']}/export?fmt=docx&profile_id={profile_id}"
        )
        self.assertEqual(response.status_code, 200)
        work = Path(tempfile.mkdtemp(prefix="export-test-"))
        target = work / "out.docx"
        target.write_bytes(response.content)
        document = Document(str(target))
        self.assertAlmostEqual(document.sections[0].left_margin.inches, 1.0, places=2)
        self.assertEqual(rule_count(document), 1)
        target.unlink()
        work.rmdir()

    def test_downloading_does_not_create_a_variant(self) -> None:
        draft = self._draft_from_variant()
        self.client.get(f"/resume-editor/drafts/{draft['id']}/export?fmt=docx")
        with self.SessionLocal() as db:
            self.assertEqual(db.query(ResumeAsset).count(), 1)
            self.assertEqual(db.query(ResumeDraft).count(), 1)

    def test_downloading_an_empty_draft_says_what_is_missing(self) -> None:
        draft = self.client.post("/resume-editor/drafts", json={"name": "Blank"}).json()
        response = self.client.get(f"/resume-editor/drafts/{draft['id']}/export?fmt=md")
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"])

    def test_an_unknown_format_is_rejected_before_any_rendering(self) -> None:
        draft = self._draft_from_variant()
        response = self.client.get(f"/resume-editor/drafts/{draft['id']}/export?fmt=rtf")
        self.assertEqual(response.status_code, 422)

    def test_a_profile_is_created_by_measuring_an_uploaded_sample(self) -> None:
        sample_dir = Path(tempfile.mkdtemp(prefix="sample-"))
        sample = build_docx(
            RESUME_MARKDOWN,
            ResumeFormatSpec(font_family="Georgia", margin_left_inches=0.75),
            sample_dir / "vendor.docx",
        )

        def unavailable() -> None:
            raise RuntimeError("ollama is down")

        original = resume_format_profile_service.build_chat_llm
        resume_format_profile_service.build_chat_llm = unavailable
        try:
            response = self.client.post(
                "/resume-editor/profiles",
                files={
                    "file": (
                        "vendor.docx",
                        sample.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"name": "Vendor A", "make_default": "true"},
            )
        finally:
            resume_format_profile_service.build_chat_llm = original
            sample.unlink()
            sample_dir.rmdir()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "Vendor A")
        self.assertTrue(body["is_default"])
        self.assertEqual(body["spec"]["font_family"], "Georgia")
        self.assertAlmostEqual(body["spec"]["margin_left_inches"], 0.75, places=2)

    def test_a_sample_in_an_unsupported_format_is_refused(self) -> None:
        response = self.client.post(
            "/resume-editor/profiles",
            files={"file": ("layout.png", b"\x89PNG", "image/png")},
            data={"name": "Vendor A"},
        )
        self.assertEqual(response.status_code, 400)

    def test_only_one_profile_is_the_default_at_a_time(self) -> None:
        with self.SessionLocal() as db:
            db.add_all(
                [
                    ResumeFormatProfile(
                        owner_id=main.settings.owner_id, name="A", spec_json="{}", is_default=True
                    ),
                    ResumeFormatProfile(owner_id=main.settings.owner_id, name="B", spec_json="{}"),
                ]
            )
            db.commit()
            second = db.query(ResumeFormatProfile).filter(ResumeFormatProfile.name == "B").one().id

        response = self.client.patch(f"/resume-editor/profiles/{second}", json={"is_default": True})
        self.assertEqual(response.status_code, 200)
        listed = self.client.get("/resume-editor/profiles").json()
        self.assertEqual([item["name"] for item in listed if item["is_default"]], ["B"])

    def test_a_profile_stored_before_a_field_existed_still_renders(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                ResumeFormatProfile(
                    owner_id=main.settings.owner_id,
                    name="Old",
                    spec_json=json.dumps({"font_family": "Times New Roman"}),
                )
            )
            db.commit()
            profile_id = db.query(ResumeFormatProfile).one().id

        body = self.client.get("/resume-editor/profiles").json()[0]
        self.assertEqual(body["spec"]["font_family"], "Times New Roman")
        self.assertEqual(body["spec"]["skills_divider_inches"], ResumeFormatSpec().skills_divider_inches)
        draft = self._draft_from_variant()
        export = self.client.get(
            f"/resume-editor/drafts/{draft['id']}/export?fmt=docx&profile_id={profile_id}"
        )
        self.assertEqual(export.status_code, 200)

    def test_unreadable_profile_json_does_not_break_the_list(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                ResumeFormatProfile(owner_id=main.settings.owner_id, name="Broken", spec_json="{oops")
            )
            db.commit()
        body = self.client.get("/resume-editor/profiles").json()
        self.assertEqual(body[0]["spec"]["font_family"], ResumeFormatSpec().font_family)

    def test_deleting_a_profile_leaves_the_draft_downloadable(self) -> None:
        with self.SessionLocal() as db:
            db.add(ResumeFormatProfile(owner_id=main.settings.owner_id, name="A", spec_json="{}"))
            db.commit()
            profile_id = db.query(ResumeFormatProfile).one().id

        draft = self._draft_from_variant()
        self.assertEqual(self.client.delete(f"/resume-editor/profiles/{profile_id}").status_code, 200)
        self.assertEqual(self.client.get("/resume-editor/profiles").json(), [])
        self.assertEqual(
            self.client.get(f"/resume-editor/drafts/{draft['id']}/export?fmt=md").status_code, 200
        )
        self.assertEqual(
            self.client.get(
                f"/resume-editor/drafts/{draft['id']}/export?fmt=docx&profile_id={profile_id}"
            ).status_code,
            404,
        )


class ResumeDraftPublishTests(ResumeEditorApiTests):
    """Saving a draft as a new variant.

    The variant this creates has to be indistinguishable from one the user
    uploaded by hand - same extraction, same embedding, same versioning - because
    everything downstream reads variants without knowing where they came from.
    """

    def _publish(self, draft_id: int, **over) -> object:
        payload = {"file_name": "Vendor Alpha Java", "variant_label": "Vendor Alpha, Java", **over}
        return self.client.post(f"/resume-editor/drafts/{draft_id}/publish", json=payload)

    def test_publishing_adds_a_variant_beside_the_one_it_was_copied_from(self) -> None:
        draft = self._draft_from_variant()
        response = self._publish(draft["id"])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # Spaces become underscores: the name is used as a path component.
        self.assertEqual(body["file_name"], "Vendor_Alpha_Java.docx")
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["variant_code"], resume_variant_code(body["resume_id"]))

        with self.SessionLocal() as db:
            self.assertEqual(db.query(ResumeAsset).count(), 2)
            source = db.get(ResumeAsset, self.resume_id)
            created = db.get(ResumeAsset, body["resume_id"])
        # The source is untouched, including the flag that decides what gets sent.
        self.assertEqual(source.content_markdown, RESUME_MARKDOWN)
        self.assertFalse(source.is_current)
        self.assertTrue(created.is_current)
        self.assertTrue(created.is_enabled)
        self.assertEqual(created.variant_label, "Vendor Alpha, Java")

    def test_the_stored_text_is_read_back_out_of_the_stored_file(self) -> None:
        # The invariant, asserted end to end: the variant's text is what the
        # extractor found in the very bytes that will be attached to an email.
        draft = self._draft_from_variant()
        created_id = self._publish(draft["id"]).json()["resume_id"]
        with self.SessionLocal() as db:
            created = db.get(ResumeAsset, created_id)

        stored = Path(created.file_path)
        self.assertTrue(stored.exists())
        self.assertEqual(created.sha256, hashlib.sha256(stored.read_bytes()).hexdigest())
        self.assertIn("Chaithanya Dheeraj", created.content_markdown or "")
        self.assertIn("Spring Boot", created.content_markdown or "")

    def test_the_draft_survives_publishing_and_is_not_renamed(self) -> None:
        draft = self._draft_from_variant()
        self._publish(draft["id"])
        reloaded = self.client.get(f"/resume-editor/drafts/{draft['id']}").json()
        self.assertEqual(reloaded["name"], draft["name"])
        self.assertEqual(reloaded["content_markdown"], draft["content_markdown"])

    def test_reusing_the_draft_name_is_refused_so_the_two_stay_distinguishable(self) -> None:
        draft = self._draft_from_variant()
        response = self._publish(draft["id"], file_name=draft["name"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("different name", response.json()["detail"])
        self.assertIn(draft["name"], response.json()["detail"])

    def test_the_draft_name_is_refused_whatever_its_casing_or_extension(self) -> None:
        draft = self._draft_from_variant()
        for attempt in (draft["name"].upper(), f"  {draft['name']}  ", f"{draft['name']}.docx"):
            with self.subTest(attempt=attempt):
                response = self._publish(draft["id"], file_name=attempt)
                self.assertEqual(response.status_code, 400)
                self.assertIn("different name", response.json()["detail"])

    def test_a_name_another_variant_already_uses_is_refused_by_code(self) -> None:
        draft = self._draft_from_variant()
        self.assertEqual(self._publish(draft["id"]).status_code, 200)
        again = self._publish(draft["id"])
        self.assertEqual(again.status_code, 400)
        self.assertIn("already called Vendor_Alpha_Java.docx", again.json()["detail"])

    def test_an_empty_name_is_refused(self) -> None:
        draft = self._draft_from_variant()
        self.assertEqual(self._publish(draft["id"], file_name="   ").status_code, 400)

    def test_an_empty_draft_cannot_become_a_variant(self) -> None:
        blank = self.client.post("/resume-editor/drafts", json={"name": "Blank"}).json()
        response = self._publish(blank["id"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(ResumeAsset).count(), 1)

    def test_the_variant_is_rendered_in_the_profile_that_was_chosen(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                ResumeFormatProfile(
                    owner_id=main.settings.owner_id,
                    name="Vendor A",
                    spec_json=ResumeFormatSpec(
                        margin_left_inches=1.0, rule_before_sections=["Skills"]
                    ).model_dump_json(),
                )
            )
            db.commit()
            profile_id = db.query(ResumeFormatProfile).one().id

        draft = self._draft_from_variant()
        created_id = self._publish(draft["id"], profile_id=profile_id).json()["resume_id"]
        with self.SessionLocal() as db:
            created = db.get(ResumeAsset, created_id)
        document = Document(created.file_path)
        self.assertAlmostEqual(document.sections[0].left_margin.inches, 1.0, places=2)
        self.assertEqual(rule_count(document), 1)

    def test_publishing_against_a_profile_that_is_gone_is_a_404(self) -> None:
        draft = self._draft_from_variant()
        response = self._publish(draft["id"], profile_id=9999)
        self.assertEqual(response.status_code, 404)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(ResumeAsset).count(), 1)

    def test_a_format_that_is_not_a_document_is_rejected(self) -> None:
        draft = self._draft_from_variant()
        response = self._publish(draft["id"], fmt="md")
        self.assertEqual(response.status_code, 422)

    def test_publishing_goes_through_the_same_function_an_upload_does(self) -> None:
        # Named explicitly: the requirement is that extraction and embedding do
        # not get a second implementation, and only this catches a copy-paste.
        calls: list[dict] = []
        original = main.store_resume_asset

        def recording(db, **kwargs):
            calls.append(kwargs)
            return original(db, **kwargs)

        main.store_resume_asset = recording
        try:
            draft = self._draft_from_variant()
            self.assertEqual(self._publish(draft["id"]).status_code, 200)
        finally:
            main.store_resume_asset = original

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["file_name"], "Vendor_Alpha_Java.docx")
        self.assertEqual(calls[0]["variant_label"], "Vendor Alpha, Java")


if __name__ == "__main__":
    unittest.main()
