"""A hyperlink has to survive the whole way, or it is not a hyperlink.

A resume's contact line is one word - "LinkedIn" - carrying an address. The
address lived in the .docx, was read correctly by the extractor's library, and
was then dropped by our own code, which kept only `element.text`. Every format
downstream rendered what it was given: the word, with nothing behind it.

So these tests follow the address rather than any one layer: out of the .docx
metadata, into the markdown, into the .docx we build, and into the PDF that is
printed from it.
"""

import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ["DEBUG"] = "false"

from app.parsing.document_extraction import _with_links, elements_to_markdown
from app.services.resume_render_service import (
    LINK_COLOR,
    ResumeFormatSpec,
    build_docx,
)

URL = "https://www.linkedin.com/in/chaithanyaraj/"
CONTACT = "chaithanyadheeraj1026@gmail.com | +1 940-629-6920 | LinkedIn"


def element(text: str, links: list[dict] | None = None, category: str = "NarrativeText"):
    return SimpleNamespace(
        text=text,
        category=category,
        metadata=SimpleNamespace(links=links, category_depth=0, text_as_html=""),
    )


class LinkPreservationTests(unittest.TestCase):
    def test_the_address_is_woven_back_into_the_anchor_it_belongs_to(self) -> None:
        result = _with_links(CONTACT, element(CONTACT, [
            {"text": "LinkedIn", "url": URL, "start_index": 52},
        ]))

        self.assertEqual(
            result,
            f"chaithanyadheeraj1026@gmail.com | +1 940-629-6920 | [LinkedIn]({URL})",
        )

    def test_several_links_on_one_line_all_survive(self) -> None:
        text = "GitHub | LinkedIn"
        result = _with_links(text, element(text, [
            {"text": "GitHub", "url": "https://github.com/x", "start_index": 0},
            {"text": "LinkedIn", "url": URL, "start_index": 9},
        ]))

        self.assertEqual(result, f"[GitHub](https://github.com/x) | [LinkedIn]({URL})")

    def test_a_stale_start_index_falls_back_to_finding_the_anchor(self) -> None:
        result = _with_links(CONTACT, element(CONTACT, [
            {"text": "LinkedIn", "url": URL, "start_index": 999},
        ]))

        self.assertIn(f"[LinkedIn]({URL})", result)

    def test_an_anchor_that_appears_nowhere_is_left_alone_not_guessed_at(self) -> None:
        result = _with_links(CONTACT, element(CONTACT, [
            {"text": "Portfolio", "url": "https://example.com", "start_index": 3},
        ]))

        self.assertEqual(result, CONTACT)

    def test_a_bare_url_anchor_is_not_wrapped_into_itself(self) -> None:
        text = f"See {URL}"
        result = _with_links(text, element(text, [
            {"text": URL, "url": URL, "start_index": 4},
        ]))

        self.assertEqual(result, text)

    def test_text_that_is_already_linked_is_not_linked_twice(self) -> None:
        text = f"[LinkedIn]({URL})"
        self.assertEqual(
            _with_links(text, element(text, [{"text": "LinkedIn", "url": URL, "start_index": 1}])),
            text,
        )

    def test_an_element_with_no_links_is_returned_untouched(self) -> None:
        self.assertEqual(_with_links(CONTACT, element(CONTACT)), CONTACT)
        self.assertEqual(_with_links(CONTACT, element(CONTACT, [])), CONTACT)

    def test_links_survive_the_walk_from_elements_to_markdown(self) -> None:
        markdown = elements_to_markdown([
            element("Chaithanya Dheeraj N", category="Title"),
            element(CONTACT, [{"text": "LinkedIn", "url": URL, "start_index": 52}]),
        ])

        self.assertIn(f"[LinkedIn]({URL})", markdown)


class RenderedLinkTests(unittest.TestCase):
    def render(self, markdown: str) -> tuple[str, str]:
        """Build a .docx and return (document.xml, its relationships)."""
        with TemporaryDirectory() as work:
            target = build_docx(markdown, ResumeFormatSpec(), Path(work) / "r.docx")
            with zipfile.ZipFile(target) as archive:
                return (
                    archive.read("word/document.xml").decode(),
                    archive.read("word/_rels/document.xml.rels").decode(),
                )

    def test_a_link_becomes_a_relationship_and_not_merely_blue_text(self) -> None:
        body, rels = self.render(f"# Name\n\n{CONTACT.replace('LinkedIn', f'[LinkedIn]({URL})')}\n")

        self.assertIn(URL, rels)
        self.assertIn('TargetMode="External"', rels)
        self.assertIn("<w:hyperlink", body)
        self.assertIn("LinkedIn", body)
        # The address is never printed as visible text - the anchor word is.
        self.assertNotIn(f"{URL}</w:t>", body)

    def test_the_anchor_is_styled_as_a_link(self) -> None:
        body, _ = self.render(f"# Name\n\n[LinkedIn]({URL})\n")

        self.assertIn(LINK_COLOR, body)
        self.assertIn("<w:u ", body)

    def test_the_text_around_a_link_still_renders(self) -> None:
        body, _ = self.render(f"# Name\n\n{CONTACT.replace('LinkedIn', f'[LinkedIn]({URL})')}\n")

        self.assertIn("chaithanyadheeraj1026@gmail.com", body)
        self.assertIn("940-629-6920", body)

    def test_bold_still_works_beside_a_link(self) -> None:
        body, rels = self.render(f"# Name\n\n- Built **microservices**, see [LinkedIn]({URL}).\n")

        self.assertIn("<w:b", body)
        self.assertIn("microservices", body)
        self.assertIn(URL, rels)

    def test_a_link_inside_a_bullet_and_inside_a_skills_table_both_survive(self) -> None:
        markdown = (
            f"# Name\n\n## Experiences\n\n- Shipped it, see [LinkedIn]({URL}).\n\n"
            f"## Skills\n\n| Category | Skills |\n| --- | --- |\n| Profile | [LinkedIn]({URL}) |\n"
        )
        body, rels = self.render(markdown)

        self.assertEqual(body.count("<w:hyperlink"), 2)
        self.assertIn(URL, rels)

    def test_a_mailto_link_is_carried_too(self) -> None:
        _, rels = self.render("# Name\n\n[me](mailto:me@example.com)\n")

        self.assertIn("mailto:me@example.com", rels)

    def test_a_document_with_no_links_gains_no_relationships(self) -> None:
        body, rels = self.render("# Name\n\n- A plain bullet.\n")

        self.assertNotIn("<w:hyperlink", body)
        self.assertNotIn("hyperlink", rels.lower())


if __name__ == "__main__":
    unittest.main()
