import unittest

from app.ai.draft_formatting import draft_text_to_html


class DraftFormattingTests(unittest.TestCase):
    def test_bold_and_paragraph_rendering(self) -> None:
        text = "I use **Java** and **Spring Boot**."
        html = draft_text_to_html(text)
        self.assertIn("<p>", html)
        self.assertIn("<strong>Java</strong>", html)
        self.assertIn("<strong>Spring Boot</strong>", html)

    def test_bullets_render_as_list(self) -> None:
        text = "- **Java**\n- **REST APIs**\n- Docker"
        html = draft_text_to_html(text)
        self.assertIn("<ul>", html)
        self.assertIn("<li><strong>Java</strong></li>", html)
        self.assertIn("<li><strong>REST APIs</strong></li>", html)
        self.assertIn("<li>Docker</li>", html)

    def test_signature_preserves_line_breaks(self) -> None:
        text = "Best regards,\nChaithanya Dheeraj N\n[PHONE] +1 940-629-6920"
        html = draft_text_to_html(text)
        self.assertIn("Best regards,<br>Chaithanya Dheeraj N<br>[PHONE] +1 940-629-6920", html)

    def test_size_wrapper_applies_requested_font_size(self) -> None:
        html = draft_text_to_html("Hello", draft_text_size="large")
        self.assertTrue(html.startswith('<div style="font-size:20px;line-height:1.5;">'))
        self.assertIn("<p>Hello</p>", html)


if __name__ == "__main__":
    unittest.main()
