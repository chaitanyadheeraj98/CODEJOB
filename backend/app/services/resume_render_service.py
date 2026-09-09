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
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# US Letter. The Apps Script did its arithmetic against this width too - the
# right margin there was never typed, it was 8.5 minus the left margin minus the
# right ruler position.
PAGE_WIDTH_INCHES = 8.5

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


class ResumeFormatSpec(BaseModel):
    """One employer's layout, as numbers.

    The defaults are the layout the user's own employer asked for, so a resume
    rendered without a profile still comes out the way the Apps Script left it.
    """

    font_family: str = "Arial"
    body_font_size: float = 10
    name_font_size: float = 14
    heading_font_size: float = 10
    heading_bold: bool = True
    heading_uppercase: bool = False

    margin_left_inches: float = 0.25
    margin_right_inches: float = 0.5
    margin_top_inches: float = 0.5
    margin_bottom_inches: float = 0.5

    line_spacing: float = 1.0
    justify_body: bool = True
    bullet_indent_inches: float = 0.5
    bullet_hanging_inches: float = 0.25

    # A rule is drawn above each heading whose text matches one of these. The
    # list is the part of a layout that genuinely differs per employer, which is
    # why it is the part the model is asked to read off a sample.
    rule_before_sections: list[str] = Field(
        default_factory=lambda: ["Summary", "Certifications", "Skills", "Experiences", "Education Details"]
    )
    heading_space_before_pt: float = 4
    heading_space_after_pt: float = 2

    # Ruler position of the divider between the two skills columns, measured
    # from the left margin - the same number the user set in Google Docs.
    skills_divider_inches: float = 3.0
    skills_category_bold: bool = True
    skills_row_gap_pt: float = 6

    # A blank line after "Environment: ..." so the next role does not run into it.
    environment_gap_pt: float = 8

    @property
    def usable_width_inches(self) -> float:
        return PAGE_WIDTH_INCHES - self.margin_left_inches - self.margin_right_inches


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
    fmt.space_after = Pt(space_after_pt)
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


def _column_widths(spec: ResumeFormatSpec, column_count: int) -> list[float]:
    """Widths in inches, with the divider honoured for the two-column case.

    Beyond two columns there is no divider to honour, so the width is shared
    evenly rather than guessed at.
    """
    if column_count == 2:
        return [spec.skills_divider_inches, spec.usable_width_inches - spec.skills_divider_inches]
    return [spec.usable_width_inches / column_count] * column_count


def _add_table(document, rows: list[list[str]], spec: ResumeFormatSpec) -> None:
    column_count = max(len(row) for row in rows)
    widths = _column_widths(spec, column_count)
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
    section.left_margin = Inches(spec.margin_left_inches)
    section.right_margin = Inches(spec.margin_right_inches)
    section.top_margin = Inches(spec.margin_top_inches)
    section.bottom_margin = Inches(spec.margin_bottom_inches)

    rule_sections = {name.strip().lower() for name in spec.rule_before_sections if name.strip()}
    body_alignment = WD_ALIGN_PARAGRAPH.JUSTIFY if spec.justify_body else WD_ALIGN_PARAGRAPH.LEFT

    lines = markdown.replace("\r\n", "\n").split("\n")
    seen_heading = False
    header_lines = 0
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
            _add_table(document, pending_table, spec)
            pending_table = []

        if not stripped:
            continue

        if _RULE.match(stripped):
            _add_horizontal_rule(document)
            continue

        heading = _HEADING.match(stripped)
        if heading:
            text = heading.group(2).strip()
            if not seen_heading:
                # The first heading is the candidate's name: centred, large, and
                # never given a rule above it.
                seen_heading = True
                header_lines = 1
                paragraph = document.add_paragraph()
                _style_paragraph(paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.CENTER)
                _add_runs(paragraph, text, spec, size=spec.name_font_size, bold=True)
                continue
            if text.lower() in rule_sections:
                _add_horizontal_rule(document)
            paragraph = document.add_paragraph()
            _style_paragraph(
                paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.LEFT, space_after_pt=spec.heading_space_after_pt
            )
            paragraph.paragraph_format.space_before = Pt(spec.heading_space_before_pt)
            _add_runs(
                paragraph,
                text.upper() if spec.heading_uppercase else text,
                spec,
                size=spec.heading_font_size,
                bold=spec.heading_bold,
            )
            continue

        bullet = _BULLET.match(raw)
        if bullet:
            paragraph = document.add_paragraph(style="List Bullet")
            _style_paragraph(paragraph, spec, alignment=body_alignment)
            paragraph.paragraph_format.left_indent = Inches(spec.bullet_indent_inches)
            paragraph.paragraph_format.first_line_indent = Inches(-spec.bullet_hanging_inches)
            _add_runs(paragraph, bullet.group(1).strip(), spec, size=spec.body_font_size)
            continue

        if seen_heading and header_lines == 1:
            # The line under the name is the contact line, and is centred with it.
            header_lines = 2
            paragraph = document.add_paragraph()
            _style_paragraph(paragraph, spec, alignment=WD_ALIGN_PARAGRAPH.CENTER)
            _add_runs(paragraph, stripped, spec, size=spec.body_font_size)
            continue

        paragraph = document.add_paragraph()
        _style_paragraph(paragraph, spec, alignment=body_alignment)
        _add_runs(paragraph, stripped, spec, size=spec.body_font_size)
        if stripped.lower().startswith("environment:"):
            _blank_paragraph(document, spec, spec.environment_gap_pt)

    if pending_table:
        _add_table(document, pending_table, spec)

    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))
    return target


def _soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def build_pdf(markdown: str, spec: ResumeFormatSpec, target: Path) -> Path:
    """Render to PDF by way of the .docx, using the LibreOffice already in the image.

    Going through Word format rather than straight to PDF is deliberate: the
    .docx is the artefact an employer asked for, so the PDF is a print of the
    same document rather than a second rendering that could drift from it.
    """
    binary = _soffice()
    if binary is None:
        raise RuntimeError(
            "PDF export needs LibreOffice, which is not on PATH. Download the .docx instead."
        )

    with tempfile.TemporaryDirectory() as work:
        source = build_docx(markdown, spec, Path(work) / "resume.docx")
        result = subprocess.run(
            [binary, "--headless", "--convert-to", "pdf", "--outdir", work, str(source)],
            capture_output=True,
            timeout=120,
            check=False,
        )
        produced = Path(work) / "resume.pdf"
        if not produced.exists():
            logger.warning(
                "LibreOffice produced no PDF (exit %s): %s",
                result.returncode,
                result.stderr.decode("utf-8", "replace")[:500],
            )
            raise RuntimeError("Converting the resume to PDF failed. Download the .docx instead.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, target)
    return target
