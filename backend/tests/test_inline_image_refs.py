"""`cid:` image references must not survive into a displayed body.

Outlook builds signature blocks out of inline images, so a logo arrives as
`<img src="cid:image001.jpg@01DB6B23.6E1B1E60">`. Both the HTML-to-text
conversion and the text/plain alternative leave the bare reference behind,
alone on a line between the sender's name and their company. 23 stored
messages carried one, and it reads as corruption.
"""

import unittest

from app.parsing.document_extraction import (
    clean_html_text,
    extract_gmail_reply_body,
    strip_inline_image_refs,
)

# The exact shape observed in the mailbox.
LIVE_SIGNATURE = """Thanks & Regards

Pranay Gondela

cid:image001.jpg@01DCAA2C.53A49450

Horizon Softech Inc

Bench Sales Recuiter

Ph: (470) 313-6209"""


class StripInlineImageRefsTests(unittest.TestCase):
    def test_removes_the_reference_seen_in_production(self) -> None:
        cleaned = strip_inline_image_refs(LIVE_SIGNATURE)

        self.assertNotIn("cid:", cleaned)
        self.assertIn("Pranay Gondela", cleaned)
        self.assertIn("Horizon Softech Inc", cleaned)

    def test_does_not_leave_a_gap_where_the_reference_was(self) -> None:
        cleaned = strip_inline_image_refs(LIVE_SIGNATURE)

        self.assertNotIn("\n\n\n", cleaned)

    def test_removes_the_brackets_around_a_bracketed_reference(self) -> None:
        for wrapped in ("[cid:image001.png@01DC.5A]", "(cid:image001.png@01DC.5A)", "<cid:image001.png@01DC.5A>"):
            with self.subTest(wrapped=wrapped):
                cleaned = strip_inline_image_refs(f"Regards\n\n{wrapped}\n\nAcme")

                self.assertNotIn("cid:", cleaned)
                # An empty [] left behind reads as broken markup, not as nothing.
                for bracket in "[]()<>":
                    self.assertNotIn(bracket, cleaned)

    def test_removes_several_references_in_one_body(self) -> None:
        body = "a\n\ncid:image001.jpg@01DB.6E\n\nb\n\ncid:image002.png@01DB.6E\n\nc"

        cleaned = strip_inline_image_refs(body)

        self.assertNotIn("cid:", cleaned)
        self.assertIn("a", cleaned)
        self.assertIn("c", cleaned)

    def test_is_idempotent(self) -> None:
        once = strip_inline_image_refs(LIVE_SIGNATURE)

        self.assertEqual(strip_inline_image_refs(once), once)

    def test_leaves_a_body_without_references_untouched(self) -> None:
        body = "Hi there\n\nPlease confirm the rate.\n\nThanks"

        self.assertEqual(strip_inline_image_refs(body), body)

    def test_does_not_eat_ordinary_words_containing_cid(self) -> None:
        """`cid` inside a word is prose; only the `cid:` URI scheme is a reference."""
        body = "This is incidental, and the accident was lucid."

        self.assertEqual(strip_inline_image_refs(body), body)

    def test_case_insensitive(self) -> None:
        self.assertNotIn("CID:", strip_inline_image_refs("x\n\nCID:image001.png@01DC.5A\n\ny"))


class PipelineIntegrationTests(unittest.TestCase):
    def test_an_inline_image_tag_does_not_reach_the_cleaned_html(self) -> None:
        html = '<div>Regards</div><div><img src="cid:image001.jpg@01DB6B23.6E1B1E60"></div><div>Acme Inc</div>'

        cleaned = clean_html_text(html)

        self.assertNotIn("cid:", cleaned)
        self.assertIn("Acme Inc", cleaned)

    def test_the_plain_text_alternative_is_cleaned_too(self) -> None:
        """The path with no HTML pass at all, which is why the helper is applied twice."""
        cleaned = extract_gmail_reply_body(LIVE_SIGNATURE, strip_signature=False)

        self.assertNotIn("cid:", cleaned)
        self.assertIn("Pranay Gondela", cleaned)

    def test_a_phone_number_in_the_signature_still_survives(self) -> None:
        """strip_signature=False exists for the number extractor; do not regress it."""
        cleaned = extract_gmail_reply_body(LIVE_SIGNATURE, strip_signature=False)

        self.assertIn("(470) 313-6209", cleaned)


if __name__ == "__main__":
    unittest.main()
