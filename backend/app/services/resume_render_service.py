"""Render resume markdown into the document an employer asked for.

This replaces a five-step manual loop: convert the markdown on a third-party
site, open the result in Google Docs, run a hand-written Apps Script over it,
download, re-upload. Every constant that script set - Arial 10, a 0.25in left
margin, the skills table divider at ruler 3.00, a rule above "Summary" - is a
field on `ResumeFormatSpec`, so the layout travels as data and the rendering
happens here, once, server-side.

python-docx is already installed (unstructured[docx] depends on it) and
LibreOffice is already in the image, so `.docx` and `.pdf` both come out of
tools the backend already had. Nothing new was added to get here.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
from typing import Literal
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from pydantic import BaseModel, Field, model_validator

from app import tenancy
from app.config import settings
from app.services import admission_service
from app.services.admission_service import RESUME_RENDER_POOL, AdmissionRejected

logger = logging.getLogger(__name__)

# Was BoundedSemaphore(4) - four slots per *process*, which is four slots in
# total only while there is one API process. LibreOffice is the heaviest thing
# this service starts, so an uncoordinated 4xN is the cap that matters least
# and hurts most.

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class MarkdownSection:
    """One heading and the lines it owns."""

    heading: str
    level: int
    body: str
    # Line indices, so a rewrite splices the same span that was read.
    start: int
    end: int


def split_sections(markdown: str) -> list[MarkdownSection]:
    """Every heading in the document, with the span of lines beneath it.

    A section ends at the next heading of the same or a higher level, so
    subsections stay with the parent they belong to and rewriting "Experience"
    does not silently swallow the section after it.

    Heading depth is not assumed. Resumes here are written with the name as the
    only `##` and sections as `###`, but a resume pasted from somewhere else
    uses `#` and `##`, and both have to be editable.
    """
    lines = markdown.splitlines()
    found: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match and match.group(2).strip():
            found.append((match.group(2).strip(), len(match.group(1)), index))

    sections: list[MarkdownSection] = []
    for position, (heading, level, start) in enumerate(found):
        end = len(lines)
        for _, later_level, later_start in found[position + 1:]:
            if later_level <= level:
                end = later_start
                break
        sections.append(
            MarkdownSection(
                heading=heading,
                level=level,
                body="\n".join(lines[start + 1:end]).strip(),
                start=start,
                end=end,
            )
        )
    return sections


def find_section(markdown: str, heading: str) -> MarkdownSection | None:
    """The one section called `heading`, matched the way a person would name it.

    Case and surrounding `#` are ignored, so "summary", "Summary" and
    "### Summary" all find the same section. Returns None when the name matches
    nothing or more than one heading - picking between two sections called
    "Experience" is a question for the user, not a guess to make here.
    """
    wanted = heading.strip().lstrip("#").strip().casefold()
    if not wanted:
        return None
    matches = [item for item in split_sections(markdown) if item.heading.casefold() == wanted]
    return matches[0] if len(matches) == 1 else None


# A section this much of the document, with other sections nested inside it, is
# the document. Replacing it is a whole-resume rewrite wearing a section's name.
WHOLE_DOCUMENT_COVERAGE = 0.9


def nested_headings(markdown: str, section: MarkdownSection) -> list[str]:
    """The headings that live inside this section, and would go with it."""
    return [
        item.heading
        for item in split_sections(markdown)
        if section.start < item.start < section.end
    ]


def covers_whole_document(markdown: str, section: MarkdownSection) -> bool:
    """Would replacing this section replace most of the resume?

    Resumes here put the candidate's name at `##` and every real section at
    `###`, so the name owns the entire document: everything below is nested
    inside it. Handing that heading to a rewrite deletes the whole resume and
    leaves whatever replaced it, which is a plausible thing for a model to try
    and a catastrophic thing for it to get right by accident.

    A leaf section is never blocked however large it is - a draft that is one
    section has to stay editable, and replacing a section with nothing under it
    destroys nothing but itself.
    """
    if not nested_headings(markdown, section):
        return False
    whole = len(markdown.strip())
    return bool(whole) and len(section.body.strip()) / whole >= WHOLE_DOCUMENT_COVERAGE


def section_digest(body: str) -> str:
    """The identity of a section's text, for detecting an edit under a proposal.

    Lives beside the splitter so the side that builds a proposal and the side
    that applies it compute it the same way. A digest the two disagree on would
    reject every write.
    """
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def replace_section(markdown: str, heading: str, replacement: str) -> str | None:
    """Swap one section's body, leaving its heading and the rest of the document alone.

    Returns None when the heading names no single section, so the caller can say
    so rather than appending text nobody asked for.
    """
    section = find_section(markdown, heading)
    if section is None:
        return None
    lines = markdown.splitlines()
    body = replacement.replace("\r\n", "\n").strip().splitlines()
    # A blank line after the body keeps the next heading from running into it,
    # which is what the renderer reads to start a new block.
    rebuilt = [*lines[:section.start + 1], "", *body, "", *lines[section.end:]]
    return "\n".join(rebuilt).strip() + "\n"


def section_level(markdown: str) -> int:
    """The heading depth the resume's own sections are written at.

    The first heading is the candidate's name, whatever depth it uses, so the
    sections are the shallowest headings *below* it. Depth is not assumed
    anywhere else in this file and is not assumed here: a resume pasted in from
    somewhere else uses `#` and `##` where the ones written here use `##` and
    `###`, and both have to reorder and lay out the same way.
    """
    levels = [item.level for item in split_sections(markdown)]
    return min(levels[1:]) if len(levels) > 1 else 0


def _siblings(markdown: str, section: MarkdownSection) -> list[MarkdownSection]:
    """The sections this one can be reordered against: same depth, same parent.

    Same depth alone is not enough. Two roles written at the same level under
    different employers are not siblings, and swapping one past the other would
    move it into the wrong job.
    """
    sections = split_sections(markdown)
    parent_start = -1
    for item in sections:
        if item.level < section.level and item.start < section.start <= item.end:
            parent_start = max(parent_start, item.start)
    inside = [
        item for item in sections
        if item.level == section.level
        and (parent_start < 0 or (item.start > parent_start and item.start < _end_of(sections, parent_start)))
    ]
    return inside


def _end_of(sections: list[MarkdownSection], start: int) -> int:
    for item in sections:
        if item.start == start:
            return item.end
    return 1 << 30


def move_section(markdown: str, heading: str, offset: int) -> str | None:
    """Swap a section with the sibling `offset` places away, subsections and all.

    Returns None when the heading names no single section or when the move would
    run off either end, so the caller can say which rather than silently doing
    nothing. Whatever sits between the two blocks - a stray rule, a comment -
    stays where it is; only the two sections trade places.
    """
    section = find_section(markdown, heading)
    if section is None or not offset:
        return None
    siblings = _siblings(markdown, section)
    try:
        index = next(i for i, item in enumerate(siblings) if item.start == section.start)
    except StopIteration:
        return None
    target = index + offset
    if not 0 <= target < len(siblings):
        return None

    lines = markdown.splitlines()
    first, second = sorted([section, siblings[target]], key=lambda item: item.start)
    rebuilt = [
        *lines[: first.start],
        *lines[second.start : second.end],
        *lines[first.end : second.start],
        *lines[first.start : first.end],
        *lines[second.end :],
    ]
    return "\n".join(rebuilt).strip() + "\n"


def split_for_layout(markdown: str, spec: "ResumeFormatSpec") -> tuple[list[str], list[str], list[str]]:
    """Cut the document into header, main column and sidebar.

    The header is everything above the first section - the name and the contact
    line - and it spans both columns, because a name in a 2.2in sidebar is not a
    resume anyone recognises.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    level = section_level(markdown)
    wanted = {name.strip().casefold() for name in spec.sidebar_sections if name.strip()}
    sections = [item for item in split_sections(markdown) if item.level == level] if level else []
    if not sections:
        return lines, [], []

    header = lines[: sections[0].start]
    main: list[str] = []
    sidebar: list[str] = []
    for item in sections:
        block = lines[item.start : item.end]
        (sidebar if item.heading.casefold() in wanted else main).extend(block)
    return header, main, sidebar


_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_DIVIDER = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
# One alternation for both inline forms, so a line carrying a link and bold
# text is a single pass. Groups: 1 anchor, 2 url, 3 bold.
_INLINE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)|\*\*(.+?)\*\*")
# The conventional link blue, so a printed resume still reads as linked.
LINK_COLOR = "0563C1"


# Space between the two columns, and the narrowest the main column may become.
# A resume bullet needs room to be a sentence, not a word per line.
COLUMN_GUTTER_INCHES = 0.2
MIN_MAIN_COLUMN_INCHES = 3.0


class ResumeFormatSpec(BaseModel):
    """One employer's layout, as numbers.

    The defaults are the layout the user's own employer asked for, so a resume
    rendered without a profile still comes out the way the Apps Script left it.
    """

    model_config = {"allow_inf_nan": False}
    page_size: Literal["LETTER", "A4"] = "LETTER"
    layout: Literal["single", "two-column"] = "single"
    accent_color: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
    section_spacing_pt: float = Field(default=0, ge=0, le=48)
    compact: bool = False
    font_family: str = Field(default="Arial", min_length=1, max_length=100)
    body_font_size: float = Field(default=10, ge=6, le=48)
    name_font_size: float = Field(default=14, ge=6, le=72)
    heading_font_size: float = Field(default=10, ge=6, le=48)
    heading_bold: bool = True
    heading_uppercase: bool = False

    margin_left_inches: float = Field(default=0.25, ge=0, le=3)
    margin_right_inches: float = Field(default=0.5, ge=0, le=3)
    margin_top_inches: float = Field(default=0.5, ge=0, le=3)
    margin_bottom_inches: float = Field(default=0.5, ge=0, le=3)

    line_spacing: float = Field(default=1.0, ge=0.8, le=3)
    justify_body: bool = True
    bullet_indent_inches: float = Field(default=0.5, ge=0, le=3)
    bullet_hanging_inches: float = Field(default=0.25, ge=0, le=3)

    # A rule is drawn above each heading whose text matches one of these. The
    # list is the part of a layout that genuinely differs per employer, which is
    # why it is the part the model is asked to read off a sample.
    rule_before_sections: list[str] = Field(
        default_factory=lambda: ["Summary", "Certifications", "Skills", "Experiences", "Education Details"]
    )
    heading_space_before_pt: float = Field(default=4, ge=0, le=72)
    heading_space_after_pt: float = Field(default=2, ge=0, le=72)

    # Ruler position of the divider between the two skills columns, measured
    # from the left margin - the same number the user set in Google Docs.
    skills_divider_inches: float = Field(default=3.0, gt=0)
    skills_category_bold: bool = True
    skills_row_gap_pt: float = Field(default=6, ge=0, le=72)

    # A blank line after "Environment: ..." so the next role does not run into it.
    environment_gap_pt: float = Field(default=8, ge=0, le=72)

    # Which sections move into the sidebar of a two-column layout, by heading.
    #
    # Named rather than inferred. A sidebar is a judgement about what is
    # secondary - Skills and Certifications for one employer, Education for
    # another - and there is nothing in the markdown that says which. Guessing it
    # from heading order or section length would be wrong quietly, and this is a
    # document someone sends to a recruiter. Ignored unless layout is two-column.
    sidebar_sections: list[str] = Field(default_factory=list)
    sidebar_width_inches: float = Field(default=2.2, gt=0)

    @property
    def page_width_inches(self) -> float:
        return 210 / 25.4 if self.page_size == "A4" else 8.5

    @property
    def page_height_inches(self) -> float:
        return 297 / 25.4 if self.page_size == "A4" else 11

    @model_validator(mode="after")
    def valid_geometry(self):
        if self.skills_divider_inches >= self.usable_width_inches:
            raise ValueError("Skills divider must fit between the page margins")
        # A sidebar that takes the whole page leaves no main column, and Word
        # renders the result as one unreadable strip rather than failing.
        if self.layout == "two-column" and self.sidebar_width_inches >= self.usable_width_inches - MIN_MAIN_COLUMN_INCHES:
            raise ValueError(
                f"Sidebar must leave at least {MIN_MAIN_COLUMN_INCHES}in for the main column"
            )
        return self

    @property
    def main_width_inches(self) -> float:
        return self.usable_width_inches - self.sidebar_width_inches - COLUMN_GUTTER_INCHES

    def spacing(self, points: float) -> float:
        return points * (0.5 if self.compact else 1)

    @property
    def usable_width_inches(self) -> float:
        return self.page_width_inches - self.margin_left_inches - self.margin_right_inches


def _style_run(run, spec: ResumeFormatSpec, *, size: float, bold: bool) -> None:
    run.font.name = spec.font_family
    run.font.size = Pt(size)
    run.bold = bold


def _add_hyperlink(paragraph, anchor: str, url: str, spec: ResumeFormatSpec, *, size: float, bold: bool) -> None:
    """A real clickable link, not blue text that looks like one.

    python-docx has no API for this, so the relationship and the `w:hyperlink`
    wrapper are built by hand. Going through `part.relate_to` is what makes the
    address survive the LibreOffice conversion into the PDF - a run that is
    merely coloured and underlined arrives as decoration nobody can click.
    """
    part = paragraph.part
    element = OxmlElement("w:hyperlink")
    element.set(qn("r:id"), part.relate_to(url, RT.HYPERLINK, is_external=True))

    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    for tag, attribute, value in (
        ("w:rFonts", "w:ascii", spec.font_family),
        ("w:color", "w:val", LINK_COLOR),
        ("w:u", "w:val", "single"),
        ("w:sz", "w:val", str(int(size * 2))),  # half-points, as OOXML counts them
    ):
        node = OxmlElement(tag)
        node.set(qn(attribute), value)
        if tag == "w:rFonts":
            node.set(qn("w:hAnsi"), spec.font_family)
        properties.append(node)
    if bold:
        properties.append(OxmlElement("w:b"))
    run.append(properties)

    text_node = OxmlElement("w:t")
    text_node.text = anchor
    text_node.set(qn("xml:space"), "preserve")
    run.append(text_node)
    element.append(run)
    paragraph._p.append(element)


def _add_runs(paragraph, text: str, spec: ResumeFormatSpec, *, size: float, bold: bool = False) -> None:
    """Write `text` into `paragraph`, honouring **bold** spans and [links](url).

    One pass over the alternation, so a line can carry both without either
    pattern having to know about the other.
    """
    position = 0
    for match in _INLINE.finditer(text):
        plain = text[position:match.start()]
        if plain:
            _style_run(paragraph.add_run(plain), spec, size=size, bold=bold)
        anchor, url, strong = match.group(1), match.group(2), match.group(3)
        if url:
            _add_hyperlink(paragraph, anchor, url, spec, size=size, bold=bold)
        else:
            _style_run(paragraph.add_run(strong), spec, size=size, bold=True)
        position = match.end()
    tail = text[position:]
    if tail:
        _style_run(paragraph.add_run(tail), spec, size=size, bold=bold)


def _style_paragraph(paragraph, spec: ResumeFormatSpec, *, alignment=None, space_after_pt: float = 0) -> None:
    fmt = paragraph.paragraph_format
    fmt.line_spacing = spec.line_spacing
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(spec.spacing(space_after_pt))
    fmt.left_indent = Inches(0)
    fmt.right_indent = Inches(0)
    fmt.first_line_indent = Inches(0)
    if alignment is not None:
        paragraph.alignment = alignment


def _add_horizontal_rule(document) -> None:
    """Draw a rule as a bottom border on an empty paragraph.

    Word has no standalone horizontal-rule element; the Google Docs script's
    `insertHorizontalRule` compiles to exactly this when a document is exported.
    """
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "auto")
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _column_widths(spec: ResumeFormatSpec, column_count: int, available: float | None = None) -> list[float]:
    """Widths in inches, with the divider honoured for the two-column case.

    Beyond two columns there is no divider to honour, so the width is shared
    evenly rather than guessed at.

    `available` is the width actually on offer, which is the page between the
    margins unless this table is inside a column. The divider is a ruler
    position measured on a full-width page, so in a narrower column it is scaled
    to the same proportion rather than used as-is and overflowing.
    """
    usable = spec.usable_width_inches if available is None else available
    if column_count == 2:
        divider = spec.skills_divider_inches * (usable / spec.usable_width_inches)
        return [divider, usable - divider]
    return [usable / column_count] * column_count


def _add_table(document, rows: list[list[str]], spec: ResumeFormatSpec, available: float | None = None) -> None:
    column_count = max(len(row) for row in rows)
    widths = _column_widths(spec, column_count, available)
    table = document.add_table(rows=len(rows), cols=column_count)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False

    last_index = len(rows) - 1
    for row_index, values in enumerate(rows):
        for column_index in range(column_count):
            cell = table.cell(row_index, column_index)
            # Width has to be set on the cell as well as the column: Word reads
            # the cell's own width and ignores a column that disagrees with it.
            cell.width = Inches(widths[column_index])
            paragraph = cell.paragraphs[0]
            # The header row and the last row stay tight, exactly as the script
            # left them - a gap under the final row only pushes the next section
            # further down the page.
            gap = spec.skills_row_gap_pt if 0 < row_index < last_index else 0
            _style_paragraph(paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.LEFT, space_after_pt=gap)
            text = values[column_index] if column_index < len(values) else ""
            bold = spec.skills_category_bold and column_index == 0
            _add_runs(paragraph, text, spec, size=spec.body_font_size, bold=bold)
    for column_index, width in enumerate(widths):
        table.columns[column_index].width = Inches(width)


def _blank_paragraph(document, spec: ResumeFormatSpec, points: float) -> None:
    paragraph = document.add_paragraph()
    _style_paragraph(paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.LEFT)
    run = paragraph.add_run("")
    run.font.name = spec.font_family
    run.font.size = Pt(points)


def _render_lines(container, lines: list[str], spec: ResumeFormatSpec, *, expect_header: bool, available: float | None = None) -> None:
    """Write markdown lines into a document or a table cell.

    `container` is anything with `add_paragraph` and `add_table`, which covers
    both - that is what lets the two-column layout reuse this loop per column
    instead of growing a second renderer that would drift from this one.

    `expect_header` is off for a column: the name and contact line have already
    been written across the top, so the first heading a column sees is a section
    heading and must not be centred and set at name size.
    """
    rule_sections = {name.strip().lower() for name in spec.rule_before_sections if name.strip()}
    body_alignment = WD_ALIGN_PARAGRAPH.JUSTIFY if spec.justify_body else WD_ALIGN_PARAGRAPH.LEFT

    seen_heading = not expect_header
    header_lines = 0 if expect_header else 2
    pending_table: list[list[str]] = []
    index = 0

    while index < len(lines):
        raw = lines[index]
        index += 1
        stripped = raw.strip()

        if _TABLE_ROW.match(stripped):
            if not _TABLE_DIVIDER.match(stripped):
                pending_table.append(_cells(stripped))
            continue
        if pending_table:
            _add_table(container, pending_table, spec, available=available)
            pending_table = []

        if not stripped:
            continue

        if _RULE.match(stripped):
            _add_horizontal_rule(container)
            continue

        heading = _HEADING.match(stripped)
        if heading:
            text = heading.group(2).strip()
            if not seen_heading:
                # The first heading is the candidate's name: centred, large, and
                # never given a rule above it.
                seen_heading = True
                header_lines = 1
                paragraph = container.add_paragraph()
                _style_paragraph(paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.CENTER)
                _add_runs(paragraph, text, spec, size=spec.name_font_size, bold=True)
                continue
            if text.lower() in rule_sections:
                _add_horizontal_rule(container)
            paragraph = container.add_paragraph()
            _style_paragraph(
                paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.LEFT, space_after_pt=spec.heading_space_after_pt
            )
            paragraph.paragraph_format.space_before = Pt(spec.spacing(spec.heading_space_before_pt + spec.section_spacing_pt))
            _add_runs(
                paragraph,
                text.upper() if spec.heading_uppercase else text,
                spec,
                size=spec.heading_font_size,
                bold=spec.heading_bold,
            )
            if spec.accent_color:
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor.from_string(spec.accent_color.lstrip("#"))
            continue

        bullet = _BULLET.match(raw)
        if bullet:
            paragraph = container.add_paragraph(style="List Bullet")
            _style_paragraph(paragraph, spec, alignment=body_alignment)
            paragraph.paragraph_format.left_indent = Inches(spec.bullet_indent_inches)
            paragraph.paragraph_format.first_line_indent = Inches(-spec.bullet_hanging_inches)
            _add_runs(paragraph, bullet.group(1).strip(), spec, size=spec.body_font_size)
            continue

        if seen_heading and header_lines == 1:
            # The line under the name is the contact line, and is centred with it.
            header_lines = 2
            paragraph = container.add_paragraph()
            _style_paragraph(paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.CENTER)
            _add_runs(paragraph, stripped, spec, size=spec.body_font_size)
            continue

        paragraph = container.add_paragraph()
        _style_paragraph(paragraph, spec, alignment=body_alignment)
        _add_runs(paragraph, stripped, spec, size=spec.body_font_size)
        if stripped.lower().startswith("environment:"):
            _blank_paragraph(container, spec, spec.spacing(spec.environment_gap_pt))

    if pending_table:
        _add_table(container, pending_table, spec, available=available)


def _empty_cell(cell):
    """A fresh cell ships with one empty paragraph; it would print as a blank line."""
    for paragraph in list(cell.paragraphs):
        paragraph._p.getparent().remove(paragraph._p)
    return cell


def _add_columns(document, main: list[str], sidebar: list[str], spec: ResumeFormatSpec) -> None:
    """Lay the body out as a borderless two-cell table.

    Word has no multi-column flow that survives a round trip through LibreOffice
    and back into Word, and a section break with `w:cols` reflows the whole page
    rather than one band of it. A table is what every real two-column resume in
    .docx is built from, and it keeps the header above it spanning both.
    """
    table = document.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    widths = [spec.main_width_inches, spec.sidebar_width_inches]
    for index, (cell, lines) in enumerate(((table.cell(0, 0), main), (table.cell(0, 1), sidebar))):
        cell.width = Inches(widths[index])
        table.columns[index].width = Inches(widths[index])
        _render_lines(_empty_cell(cell), lines, spec, expect_header=False, available=widths[index])


def build_docx(markdown: str, spec: ResumeFormatSpec, target: Path) -> Path:
    """Render resume markdown to a .docx at `target`.

    The markdown accepted here is the subset a resume actually uses - headings,
    bullets, **bold**, pipe tables, rules - handled line by line. A full
    CommonMark walk would cost more code and buy nothing a resume contains.
    """
    document = Document()

    normal = document.styles["Normal"]
    normal.font.name = spec.font_family
    normal.font.size = Pt(spec.body_font_size)
    # Word keeps a separate east-asian font on the style and falls back to it for
    # any run it cannot map, which is how a document set to Arial still renders
    # half its characters in Calibri.
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), spec.font_family)

    section = document.sections[0]
    section.page_width = Inches(spec.page_width_inches)
    section.page_height = Inches(spec.page_height_inches)
    section.left_margin = Inches(spec.margin_left_inches)
    section.right_margin = Inches(spec.margin_right_inches)
    section.top_margin = Inches(spec.margin_top_inches)
    section.bottom_margin = Inches(spec.margin_bottom_inches)

    header, main, sidebar = split_for_layout(markdown, spec)
    if spec.layout == "two-column" and sidebar:
        _render_lines(document, header, spec, expect_header=True)
        _add_columns(document, main, sidebar, spec)
    else:
        # One stream, including two-column with nothing named for the sidebar:
        # an empty second column is a worse document than a single-column one.
        _render_lines(document, markdown.replace("\r\n", "\n").split("\n"), spec, expect_header=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))
    return target


def _soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _convert_document(markdown: str, spec: ResumeFormatSpec, target: Path, fmt: str) -> Path:
    binary = _soffice()
    if binary is None:
        raise RuntimeError("PDF and thumbnail rendering need LibreOffice. Download the .docx instead.")
    try:
        lease = admission_service.acquire(
            RESUME_RENDER_POOL,
            owner_id=tenancy.owner_id(),
            global_limit=settings.resume_render_max_concurrent,
            per_user_limit=settings.resume_render_max_per_user,
            # Comfortably past the 75s subprocess timeout below, so a render
            # that runs long keeps its slot and a killed one does not.
            ttl_seconds=150.0,
            local_limit=settings.resume_render_max_concurrent,
        )
    except AdmissionRejected as exc:
        raise RuntimeError("Resume rendering is busy. Try again shortly.") from exc
    try:
        with tempfile.TemporaryDirectory() as work:
            source = build_docx(markdown, spec, Path(work) / "resume.docx")
            profile = (Path(work) / "office-profile").as_uri()
            try:
                result = subprocess.run(
                    [binary, f"-env:UserInstallation={profile}", "--headless", "--convert-to",
                     fmt, "--outdir", work, str(source)],
                    capture_output=True, timeout=75, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Resume rendering timed out. Download the .docx instead.") from exc
            produced = Path(work) / f"resume.{fmt}"
            if not produced.exists():
                logger.warning("LibreOffice produced no %s (exit %s): %s", fmt, result.returncode,
                               result.stderr.decode("utf-8", "replace")[:500])
                raise RuntimeError(f"Converting the resume to {fmt.upper()} failed. Download the .docx instead.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(produced, target)
        return target
    finally:
        admission_service.release(lease)


def build_pdf(markdown: str, spec: ResumeFormatSpec, target: Path) -> Path:
    return _convert_document(markdown, spec, target, "pdf")


THUMBNAIL_SAMPLE = """# Alex Rivera
Dallas, TX | [Portfolio](https://example.com)

## Summary
Software engineer building reliable services.

## Skills
| Category | Technologies |
| --- | --- |
| Languages | Python, Java |
| Platforms | Linux, PostgreSQL |

## Experiences
### Software Engineer
- Delivered reliable services with the team.

## Education Details
Computer Science
"""


@lru_cache(maxsize=32)
def build_thumbnail(spec_json: str) -> bytes:
    spec = ResumeFormatSpec.model_validate_json(spec_json)
    with tempfile.TemporaryDirectory() as work:
        target = _convert_document(THUMBNAIL_SAMPLE, spec, Path(work) / "preview.png", "png")
        content = target.read_bytes()
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("LibreOffice returned an invalid preview image.")
        return content
