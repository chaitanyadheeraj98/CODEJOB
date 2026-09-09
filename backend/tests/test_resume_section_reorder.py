"""Reordering sections, and the two-column layout that reads them.

Both are Phase 4: the first moves a heading and everything under it, the second
sends named headings to a sidebar. They share `split_sections`, so a change to
heading handling that breaks one should break the other here too.
"""

import json
from pathlib import Path
import tempfile

from docx import Document

from app.services.resume_render_service import (
    ResumeFormatSpec,
    build_docx,
    move_section,
    section_level,
    split_for_layout,
)

RESUME = """## Alex Rivera
alex@example.com | 555-0100

### Summary
A summary line.

### Skills
| Category | Tools |
| --- | --- |
| Core | Java |

### Experiences
#### Acme Corp
- Did a thing.
- Did another thing.

#### Globex
- Shipped something.

### Education Details
BSc, 2015
"""


class TestSectionLevel:
    def test_the_name_is_not_a_section_and_the_depth_below_it_is(self) -> None:
        assert section_level(RESUME) == 3
        # A resume pasted from elsewhere uses one # for the name and ## below it.
        assert section_level("# Alex\ncity\n\n## Summary\ntext\n") == 2
        assert section_level("## Alex\njust a name") == 0


class TestMoveSection:
    def test_moving_a_section_carries_the_lines_underneath_it(self) -> None:
        moved = move_section(RESUME, "Skills", -1)
        assert moved is not None
        assert moved.index("### Skills") < moved.index("### Summary")
        # The table travelled with its heading rather than being left behind.
        assert moved.index("| Core | Java |") < moved.index("### Summary")

    def test_a_section_takes_its_subsections_with_it(self) -> None:
        moved = move_section(RESUME, "Experiences", 1)
        assert moved is not None
        assert moved.index("#### Acme Corp") > moved.index("### Education Details")
        assert moved.index("#### Globex") > moved.index("### Education Details")
        assert moved.count("#### Acme Corp") == 1

    def test_roles_reorder_within_their_own_employer_level(self) -> None:
        moved = move_section(RESUME, "Globex", -1)
        assert moved is not None
        assert moved.index("#### Globex") < moved.index("#### Acme Corp")
        # The parent section did not move with them.
        assert moved.index("### Experiences") < moved.index("#### Globex")

    def test_the_ends_refuse_rather_than_silently_doing_nothing(self) -> None:
        assert move_section(RESUME, "Summary", -1) is None
        assert move_section(RESUME, "Education Details", 1) is None

    def test_an_unknown_or_ambiguous_heading_moves_nothing(self) -> None:
        assert move_section(RESUME, "Nope", 1) is None
        assert move_section(RESUME, "Summary", 0) is None

    def test_every_section_survives_a_move(self) -> None:
        moved = move_section(RESUME, "Skills", 1)
        assert moved is not None
        for heading in ("Summary", "Skills", "Experiences", "Education Details", "Acme Corp", "Globex"):
            assert f" {heading}" in moved


class TestSplitForLayout:
    def test_the_header_spans_both_columns_and_named_sections_go_aside(self) -> None:
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=["Skills", "Education Details"])
        header, main, sidebar = split_for_layout(RESUME, spec)
        assert "## Alex Rivera" in "\n".join(header)
        assert "alex@example.com | 555-0100" in "\n".join(header)
        assert "### Skills" in "\n".join(sidebar)
        assert "### Education Details" in "\n".join(sidebar)
        assert "### Summary" in "\n".join(main)
        assert "### Experiences" in "\n".join(main)
        # A section is in exactly one column.
        assert "### Skills" not in "\n".join(main)

    def test_the_split_matches_the_fixture_the_preview_asserts_against(self) -> None:
        # Same file the frontend test reads. The preview and the .docx have to
        # put a section in the same column, and this is the only thing that says
        # so: two splitters in two languages otherwise drift silently.
        fixture = json.loads(
            (Path(__file__).resolve().parents[2] / "dashboard/src/features/resume_tracking/editor/resumePreview.fixture.json")
            .read_text(encoding="utf-8")
        )
        expected = fixture["twoColumn"]
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=expected["sidebar_sections"])
        header, main, sidebar = split_for_layout(fixture["markdown"], spec)
        assert "\n".join(header).strip() == expected["header"]
        assert "\n".join(main).strip() == expected["main"]
        assert "\n".join(sidebar).strip() == expected["sidebar"]

    def test_matching_ignores_case_and_an_unnamed_section_stays_in_the_main_column(self) -> None:
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=["skills"])
        _, main, sidebar = split_for_layout(RESUME, spec)
        assert "### Skills" in "\n".join(sidebar)
        assert "### Education Details" in "\n".join(main)


def _render(markdown: str, spec: ResumeFormatSpec) -> Document:
    with tempfile.TemporaryDirectory() as work:
        target = Path(work) / "resume.docx"
        build_docx(markdown, spec, target)
        return Document(str(target))


class TestTwoColumnDocx:
    def test_the_body_becomes_two_cells_with_the_header_above_them(self) -> None:
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=["Skills", "Education Details"])
        document = _render(RESUME, spec)
        # One outer table for the columns; the skills table is nested in a cell.
        outer = document.tables[0]
        assert len(outer.columns) == 2
        assert "Alex Rivera" in "\n".join(p.text for p in document.paragraphs)
        main_text = outer.cell(0, 0).text
        side_text = outer.cell(0, 1).text
        assert "Summary" in main_text and "Experiences" in main_text
        assert "Skills" in side_text and "Education Details" in side_text
        assert "Skills" not in main_text

    def test_column_widths_come_from_the_spec_and_leave_a_gutter(self) -> None:
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=["Skills"], sidebar_width_inches=2.0)
        outer = _render(RESUME, spec).tables[0]
        assert round(outer.columns[1].width.inches, 2) == 2.0
        assert round(outer.columns[0].width.inches, 2) == round(spec.main_width_inches, 2)

    def test_a_column_does_not_open_with_the_cells_own_empty_paragraph(self) -> None:
        # Rules off, so the first paragraph is the heading itself: python-docx
        # gives every new cell one empty paragraph, and left in place it prints
        # as a blank line pushing both columns down.
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=["Skills"], rule_before_sections=[])
        cell = _render(RESUME, spec).tables[0].cell(0, 1)
        assert cell.paragraphs[0].text.strip() == "Skills"

    def test_a_sidebar_heading_still_gets_the_rule_its_profile_asks_for(self) -> None:
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=["Skills"], rule_before_sections=["Skills"])
        cell = _render(RESUME, spec).tables[0].cell(0, 1)
        assert cell.paragraphs[0].text.strip() == ""
        assert cell.paragraphs[1].text.strip() == "Skills"

    def test_two_column_with_nothing_named_renders_as_one_column(self) -> None:
        spec = ResumeFormatSpec(layout="two-column", sidebar_sections=[])
        document = _render(RESUME, spec)
        # Only the skills table, no outer column table wrapping the body.
        assert all(len(table.columns) == 2 and table.cell(0, 0).text == "Category" for table in document.tables)
        assert "Summary" in "\n".join(p.text for p in document.paragraphs)

    def test_single_column_is_untouched_by_sidebar_settings(self) -> None:
        named = _render(RESUME, ResumeFormatSpec(sidebar_sections=["Skills"]))
        plain = _render(RESUME, ResumeFormatSpec())
        assert [p.text for p in named.paragraphs] == [p.text for p in plain.paragraphs]
