"""The transcript export, and what it must never put in a document.

The rendering itself is `resume_render_service`'s, already covered by its own
tests. What is new here is the decision about *what a conversation says on
paper*, so that is what these assert.
"""

import os
import unittest
import unittest.mock
from types import SimpleNamespace

os.environ["DEBUG"] = "false"

from app.ai.chat.agent import chat_models, selectable_models
from app.config import settings
from app.services.chat_export_service import (
    TRANSCRIPT_SPEC,
    download_name,
    transcript_markdown,
)


def _session(title="Compare Sarah and David", session_id=7):
    return SimpleNamespace(id=session_id, title=title)


def _msg(role, content, tool_name=None, answered_by=None):
    return SimpleNamespace(role=role, content=content, tool_name=tool_name, answered_by=answered_by)


class ChatExportTests(unittest.TestCase):
    def test_a_tool_payload_is_named_but_never_printed(self) -> None:
        """A proposal card is up to 20,000 characters of JSON written for a model.

        None of it belongs in a document a person reads, but the fact that the
        step happened does - otherwise the transcript shows the assistant
        answering questions it never looked anything up for.
        """
        markdown = transcript_markdown(
            _session(),
            [_msg("tool", '{"action":"render_candidate_table","rows":[{"secret":"x"}]}',
                  tool_name="render_candidate_table")],
        )
        self.assertNotIn("secret", markdown)
        self.assertNotIn("{", markdown)
        self.assertIn("render candidate table", markdown)

    def test_user_markdown_cannot_restructure_the_document(self) -> None:
        """"# 1 on the shortlist" is a sentence, not a heading.

        Left unescaped it becomes an H1 in Word and the export reads as though
        the user's message were a section of the report.
        """
        markdown = transcript_markdown(
            _session(),
            [_msg("user", "# 1 on the shortlist\n- and David second")],
        )
        body = markdown.split("**You**", 1)[1]
        self.assertIn("\\# 1 on the shortlist", body)
        self.assertIn("\\- and David second", body)

    def test_the_assistant_keeps_its_own_structure(self) -> None:
        """The opposite rule, and the reason the two are escaped differently:
        the assistant's headings and tables are the document's content."""
        reply = "## Ranking\n\n| Name | Score |\n| --- | --- |\n| Sarah | 82 |"
        markdown = transcript_markdown(_session(), [_msg("assistant", reply)])
        self.assertIn("## Ranking", markdown)
        self.assertIn("| Sarah | 82 |", markdown)

    def test_a_fallback_answer_says_so_on_paper_too(self) -> None:
        markdown = transcript_markdown(
            _session(),
            [_msg("assistant", "Sarah leads.", answered_by="minimax-m3:cloud"),
             _msg("assistant", "David is second.")],
        )
        self.assertIn("**Assistant** (minimax-m3:cloud)", markdown)
        self.assertIn("\n**Assistant**\n", markdown)

    def test_an_empty_conversation_still_exports_a_document(self) -> None:
        markdown = transcript_markdown(_session(), [])
        self.assertIn("# Compare Sarah and David", markdown)
        self.assertIn("no messages yet", markdown)

    def test_the_filename_survives_a_title_full_of_punctuation(self) -> None:
        self.assertEqual(
            download_name(_session('"205201dd3fb8$44089b50$cc19d1f0$@horizons"'), "pdf"),
            "205201dd3fb8-44089b50-cc19d1f0-horizons.pdf",
        )
        self.assertEqual(download_name(_session("", 12), "md"), "chat-12.md")

    def test_the_transcript_page_is_a_document_not_a_resume(self) -> None:
        # The resume default is tuned to fit one dense page; a transcript is
        # read like a report and printed on Letter with ordinary margins.
        self.assertEqual(TRANSCRIPT_SPEC.margin_left_inches, 1.0)
        self.assertEqual(TRANSCRIPT_SPEC.page_width_inches, 8.5)


class SelectableModelTests(unittest.TestCase):
    def test_the_picker_offers_more_than_the_automatic_ladder(self) -> None:
        """`chat_models()` answers "what will this turn try"; pinning the picker
        to it meant the user could only ever choose one of three rungs."""
        ladder = chat_models()
        offered = selectable_models()
        self.assertEqual(offered[:len(ladder)], ladder)
        self.assertGreater(len(offered), len(ladder))
        self.assertIn("gpt-oss:120b-cloud", offered)
        self.assertIn("nemotron-3-ultra:cloud", offered)

    def test_the_list_is_deduped_and_configurable(self) -> None:
        with unittest.mock.patch.object(settings, "ollama_selectable_models", "a,b, a ,,b"):
            with unittest.mock.patch.object(settings, "ollama_chat_model", "a"):
                with unittest.mock.patch.object(settings, "ollama_chat_model_fallback", ""):
                    with unittest.mock.patch.object(settings, "ollama_chat_model_fallback2", ""):
                        self.assertEqual(selectable_models(), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
