"""The enforcement. Each case here is a clause in the approved specification.

`<user_profile>` is the one block in the system prompt the model is told to
believe, so a model that could write it could write text it will later treat as
authoritative and that steers every mail drafted as the user. The control is
that the model is a typist: the label comes from a fixed registry, the value has
to appear in a message the user themselves typed, and on the assistant's own
initiative it also has to have asked for that field first.

The laundering cases (8, 9, 10) are the point of the file. Each runs on *both*
paths, because the second way in must not be the way around.
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
from app.mcp_server.tools.candidate_profile import propose_profile_update
from app.models import ChatAttachment, ChatMessage, ChatSession, RecruiterEmail, UserSettings

PROFILE = """# Chaithanya Dheeraj
- Work Authorization: H1B
- Passport: X1234567
"""


class ProfileWriteToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.candidate_profile.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=settings.owner_id, candidate_profile_markdown=PROFILE))
            db.add(ChatSession(id=1, owner_id=settings.owner_id))
            db.commit()

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _say(self, role: str, content: str, session_id: int = 1) -> int:
        with self.SessionLocal() as db:
            row = ChatMessage(session_id=session_id, role=role, content=content)
            db.add(row)
            db.commit()
            return row.id

    def _profile(self) -> str:
        with self.SessionLocal() as db:
            row = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
            return row.candidate_profile_markdown

    def _clear_profile(self) -> None:
        with self.SessionLocal() as db:
            row = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
            row.candidate_profile_markdown = ""
            db.commit()

    # --- 1. the registry is the vocabulary ---------------------------------

    def test_an_unknown_field_is_refused_and_the_known_list_is_named(self) -> None:
        self._say("assistant", "What is your favourite colour?")
        self._say("user", "Blue")

        result = propose_profile_update("append", field="Favourite colour", value="Blue")

        self.assertIn("error", result)
        self.assertIn("Notice period", result["fields"])

    # --- 2, 3, 4. R2 is scoped to A1, not deleted -------------------------

    def test_an_unsolicited_offer_is_refused_when_the_assistant_never_asked(self) -> None:
        self._say("assistant", "Here is a draft reply.")
        self._say("user", "By the way my notice period is 2 weeks")

        result = propose_profile_update("append", field="Notice period", value="2 weeks")

        self.assertEqual(result["status"], "not_asked")
        self.assertEqual(result["field"], "Notice period")

    def test_the_identical_call_is_accepted_when_the_user_asked_for_it(self) -> None:
        """Path A2 - the case that proves R2 was scoped rather than removed.

        The user is both author and initiator, so there is nothing being
        harvested and nothing for the ask-first rule to protect.
        """
        self._say("assistant", "Here is a draft reply.")
        self._say("user", "Save this to my profile: my notice period is 2 weeks")

        result = propose_profile_update(
            "append", field="Notice period", value="2 weeks", user_asked=True
        )

        self.assertEqual(result["action"], "propose_profile_update")
        self.assertEqual(result["entry"], "- Notice period: 2 weeks")
        self.assertEqual(result["provenance"], "user_directed")

    def test_user_asked_without_a_save_request_is_refused(self) -> None:
        # A legibility check, not the security control: it stops the model
        # quietly reclassifying an ordinary answer as a user instruction.
        self._say("assistant", "Here is a draft reply.")
        self._say("user", "My notice period is 2 weeks")

        result = propose_profile_update(
            "append", field="Notice period", value="2 weeks", user_asked=True
        )

        self.assertEqual(result["status"], "no_save_request")

    def test_the_verb_and_the_destination_may_be_words_apart(self) -> None:
        """The regression. This exact message was refused in production.

        The first gate matched contiguous phrases, so "add to my profile" was an
        instruction and *"add Notice Period: 2 weeks to profile.md"* was not.
        The refusal was expected to self-heal - the model relays it, the user
        rephrases - and instead the model announced a card that did not exist,
        so the user was left clicking at nothing.
        """
        self._say("assistant", "Here is a draft reply.")
        self._say("user", "add\nNotice Period: 2 weeks to profile,md")

        result = propose_profile_update(
            "append", field="Notice period", value="2 weeks", user_asked=True
        )

        self.assertEqual(result["action"], "propose_profile_update")
        self.assertEqual(result["entry"], "- Notice period: 2 weeks")

    def test_correcting_a_value_is_an_instruction_too(self) -> None:
        """The second regression. Also refused in production.

        The gate held only filing verbs, so "add X to my profile" was an
        instruction and "edit Work Authorization: H1B to GC" was not. Correcting
        a value one already gave is a different verb from filing a new one, and
        two misses in two sessions is the enumerated list failing rather than
        two unlucky gaps.
        """
        for message in (
            "edit Work Authorization: H1B to GC",
            "change work authorization to GC from the current H1B in profile.md",
            "correct my work authorization, it is GC now",
        ):
            with self.subTest(message=message):
                self._say("assistant", "Here is a draft reply.")
                self._say("user", message)

                result = propose_profile_update(
                    "append", field="Work Authorization", value="GC", user_asked=True
                )

                self.assertEqual(result["action"], "propose_profile_update")
                self.assertEqual(result["entry"], "- Work Authorization: GC")

    def test_a_correction_replaces_the_saved_line_rather_than_sitting_under_it(self) -> None:
        """One field, one line, and the card names what it overwrites.

        Two `- Work Authorization:` lines inside `<user_profile>` leave the model
        no way to tell which half is current.
        """
        with self.SessionLocal() as db:
            row = db.query(UserSettings).filter_by(owner_id=settings.owner_id).one()
            row.candidate_profile_markdown = f"{PROFILE}\n## Saved from chat\n- Work Authorization: H1B\n"
            db.commit()
        self._say("assistant", "Here is a draft reply.")
        self._say("user", "change my work authorization to GC")

        result = propose_profile_update(
            "append", field="Work Authorization", value="GC", user_asked=True
        )

        self.assertEqual(result["replaces"], ["- Work Authorization: H1B"])
        self.assertNotIn(
            "- Work Authorization: H1B",
            str(result["resulting_profile"]).split("## Saved from chat", 1)[1],
        )

    def test_a_statement_in_the_users_own_text_is_reported_not_rewritten(self) -> None:
        """PROFILE's `- Work Authorization: H1B` is above the saved heading.

        It is the user's document. Editing it from here is what the trust
        boundary forbids, so the card reports the conflict instead and leaves
        the resolution to them.
        """
        self._say("assistant", "Here is a draft reply.")
        self._say("user", "change my work authorization to GC")

        result = propose_profile_update(
            "append", field="Work Authorization", value="GC", user_asked=True
        )

        self.assertEqual(result["replaces"], [])
        self.assertEqual(result["conflicts"], ["- Work Authorization: H1B"])
        self.assertIn("- Work Authorization: H1B", str(result["resulting_profile"]))

    def test_a_filing_verb_without_a_destination_is_still_not_an_instruction(self) -> None:
        """What stops the widened gate from reading every answer as a request.

        "add" alone is how people answer, not how they instruct. Naming where it
        goes is the whole difference.
        """
        self._say("assistant", "Here is a draft reply.")
        self._say("user", "add 2 weeks")

        result = propose_profile_update(
            "append", field="Notice period", value="2 weeks", user_asked=True
        )

        self.assertEqual(result["status"], "no_save_request")

    # --- 5. the window differs by path, and only by path -------------------

    def test_a_value_two_messages_back_is_accepted_on_a2_and_refused_on_a1(self) -> None:
        """What makes "save that to my profile" work at all.

        Both messages in the window are ones the user typed, so widening it
        widens when they said it and never who said it.
        """
        self._say("user", "I'm in Dallas, TX")
        self._say("assistant", "Noted.")
        self._say("user", "save that to my profile")

        accepted = propose_profile_update(
            "append", field="Current location", value="Dallas, TX", user_asked=True
        )
        self.assertEqual(accepted["entry"], "- Current location: Dallas, TX")

        refused = propose_profile_update("append", field="Current location", value="Dallas, TX")
        # On A1 the window is the single most recent message, which does not
        # contain it - and the assistant never asked for the field either.
        self.assertIn(refused["status"], ("not_asked", "needs_clarification"))

    # --- 6, 7. ambiguity refuses, it does not guess ------------------------

    def test_a_value_in_no_user_message_needs_clarification(self) -> None:
        self._say("assistant", "What is your notice period?")
        self._say("user", "Let me check and get back to you")

        result = propose_profile_update("append", field="Notice period", value="2 weeks")

        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["field"], "Notice period")

    def test_the_notice_period_example_behaves_exactly_as_specified(self) -> None:
        """§1.4's own example, end to end.

        "2 weeks" is not a substring of "I can join after two weeks", so field
        mode is refused until the user says it plainly; verbatim mode stores the
        sentence they actually wrote.
        """
        self._say("assistant", "What is your notice period?")
        self._say("user", "I can join after two weeks")

        structured = propose_profile_update("append", field="Notice period", value="2 weeks")
        self.assertEqual(structured["status"], "needs_clarification")

        verbatim = propose_profile_update(
            "append",
            field="Notice period",
            value="I can join after two weeks",
            verbatim=True,
        )
        self.assertEqual(
            verbatim["entry"],
            '- Notice period (in the user\'s words): "I can join after two weeks"',
        )

    # --- 8, 9, 10. the laundering cases, on both paths ---------------------

    def test_a_recruiter_supplied_value_is_refused_on_both_paths(self) -> None:
        """The laundering test, and running it twice is the point.

        A recruiter's phone number lives in a recruiter_emails row. It is in no
        message the user typed, so no path can carry it into the block the model
        is told to believe.
        """
        with self.SessionLocal() as db:
            db.add(RecruiterEmail(
                owner_id=settings.owner_id,
                sender="Recruiter <r@acme.com>",
                subject="Java role",
                body="Call me on 214-555-9999 about this requirement.",
                state="needs_review",
            ))
            db.commit()
        self._say("assistant", "What is your phone number?")
        self._say("user", "please save the recruiter's phone number to my profile")

        assistant_asked = propose_profile_update("append", field="Phone", value="214-555-9999")
        user_directed = propose_profile_update(
            "append", field="Phone", value="214-555-9999", user_asked=True
        )

        self.assertEqual(assistant_asked["status"], "needs_clarification")
        self.assertEqual(user_directed["status"], "needs_clarification")

    def test_a_value_only_in_a_chat_attachment_is_refused_on_both_paths(self) -> None:
        with self.SessionLocal() as db:
            db.add(ChatAttachment(
                owner_id=settings.owner_id, session_id=1, file_path="/tmp/x.md",
                file_name="jd.md", mime_type="text/markdown", byte_size=10,
                sha256="c" * 64, content_markdown="Rate for this role is $95/hr",
            ))
            db.commit()
        self._say("assistant", "What is your rate?")
        # Deliberately does not repeat the value: a user who types it themselves
        # is its author, which is §7.3's accepted residual and not this test.
        self._say("user", "save the rate from that attachment to my profile")

        # The attachment's text is a separate row and never lands in
        # user_message.content, so neither path can see it.
        self.assertEqual(
            propose_profile_update("append", field="Rate", value="$95/hr")["status"],
            "needs_clarification",
        )
        self.assertEqual(
            propose_profile_update("append", field="Rate", value="$95/hr", user_asked=True)["status"],
            "needs_clarification",
        )

    def test_a_value_only_in_an_assistant_message_is_refused_on_both_paths(self) -> None:
        # The window is user rows only. A model quoting itself back is not
        # provenance, whichever path it takes - and note the refusal is R3's, not
        # R2's: the assistant did name the field, so this is the provenance check
        # doing the work rather than the ask-first check.
        self._say("assistant", "Your rate looks like $95/hr based on the role.")
        self._say("user", "save that rate to my profile")

        self.assertEqual(
            propose_profile_update("append", field="Rate", value="$95/hr")["status"],
            "needs_clarification",
        )
        self.assertEqual(
            propose_profile_update("append", field="Rate", value="$95/hr", user_asked=True)["status"],
            "needs_clarification",
        )

    # --- 11, 12. the remaining append rules --------------------------------

    def test_appending_to_an_empty_profile_is_refused_and_names_settings(self) -> None:
        self._clear_profile()
        self._say("assistant", "What is your notice period?")
        self._say("user", "2 weeks")

        result = propose_profile_update("append", field="Notice period", value="2 weeks")

        self.assertIn("no profile to add to", result["error"])
        self.assertIn("Candidate Profile", result["remedy"])

    def test_the_payload_carries_the_complete_resulting_document(self) -> None:
        self._say("assistant", "What is your notice period?")
        self._say("user", "2 weeks")

        result = propose_profile_update("append", field="Notice period", value="2 weeks")

        # Not a diff and not a summary: this is what the card shows, because it
        # is what will become trusted profile text.
        self.assertIn("- Work Authorization: H1B", result["resulting_profile"])
        self.assertIn("- Passport: X1234567", result["resulting_profile"])
        self.assertIn("- Notice period: 2 weeks", result["resulting_profile"])
        self.assertEqual(result["existing_profile"], PROFILE)
        self.assertEqual(len(result["base_sha256"]), 64)
        # The model contributed a registry label and nothing else.
        self.assertEqual(result["authored_by"], "user")

    # --- 13. the tool writes nothing, on any path --------------------------

    def test_the_tool_mutates_nothing(self) -> None:
        before = self._profile()
        self._say("assistant", "What is your notice period?")
        self._say("user", "2 weeks")

        propose_profile_update("append", field="Notice period", value="2 weeks")
        propose_profile_update("append", field="Notice period", value="2 weeks", user_asked=True)
        propose_profile_update("append", field="Favourite colour", value="Blue")
        propose_profile_update("append", field="Rate", value="$95/hr")
        propose_profile_update("delete")
        propose_profile_update("replace", attachment_id=1)
        propose_profile_update("nonsense")

        self.assertEqual(self._profile(), before)

    # --- 14, 15. replace and delete ----------------------------------------

    def test_replace_refuses_an_attachment_that_was_truncated_on_extraction(self) -> None:
        """§2.3's silent-truncation trap.

        Extraction caps at exactly the profile's own limit, so a too-long file
        arrives *at* the limit and would pass validation with its tail gone. The
        user would be told the replace worked and would have lost the end of it.
        """
        cap = settings.chat_attachment_max_extract_chars
        with self.SessionLocal() as db:
            db.add(ChatAttachment(
                id=7, owner_id=settings.owner_id, session_id=1, file_path="/tmp/p.md",
                file_name="profile.md", mime_type="text/markdown", byte_size=cap,
                sha256="d" * 64, content_markdown="y" * cap,
            ))
            db.commit()

        result = propose_profile_update("replace", attachment_id=7)

        self.assertIn("too long", result["error"])

    def test_replace_reads_the_row_itself_rather_than_trusting_supplied_text(self) -> None:
        with self.SessionLocal() as db:
            db.add(ChatAttachment(
                id=8, owner_id=settings.owner_id, session_id=1, file_path="/tmp/p.md",
                file_name="profile.md", mime_type="text/markdown", byte_size=20,
                sha256="e" * 64, content_markdown="# New profile\n- Rate: $80/hr\n",
            ))
            db.commit()

        result = propose_profile_update("replace", attachment_id=8)

        self.assertEqual(result["source_file_name"], "profile.md")
        self.assertEqual(result["resulting_profile"], "# New profile\n- Rate: $80/hr\n")
        self.assertEqual(result["existing_profile"], PROFILE)

    def test_another_owners_attachment_is_not_found(self) -> None:
        with self.SessionLocal() as db:
            db.add(ChatAttachment(
                id=9, owner_id="someone-else", session_id=1, file_path="/tmp/t.md",
                file_name="theirs.md", mime_type="text/markdown", byte_size=10,
                sha256="f" * 64, content_markdown="# Theirs",
            ))
            db.commit()

        self.assertIn("not found", propose_profile_update("replace", attachment_id=9)["error"])

    def test_delete_returns_the_complete_existing_text(self) -> None:
        result = propose_profile_update("delete")

        self.assertEqual(result["existing_profile"], PROFILE)
        self.assertEqual(result["characters_before"], len(PROFILE))
        self.assertNotIn("resulting_profile", result)

    def test_an_unknown_operation_is_refused_and_the_verbs_are_named(self) -> None:
        result = propose_profile_update("exfiltrate")

        self.assertIn("error", result)
        self.assertEqual(result["operations"], ["append", "replace", "delete"])


if __name__ == "__main__":
    unittest.main()
