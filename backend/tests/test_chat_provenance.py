"""What the user actually typed, and what the assistant actually asked.

This is the evidence R2 and R3 are checked against, and the whole feature's
security rests on the window being what it claims: user rows, in one session,
belonging to this owner. A widened window that quietly admitted an assistant
message or an attachment row would turn "the user wrote this" into "something
in the conversation contained this", which is the property being defended.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import ChatAttachment, ChatMessage, ChatSession
from app.services.chat_provenance import (
    PROVENANCE_WINDOW,
    contains_verbatim,
    latest_exchange,
    user_supplied_document,
)

OTHER_OWNER = "someone-else"


class ChatProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _session(self, owner: str = settings.owner_id) -> int:
        with self.SessionLocal() as db:
            row = ChatSession(owner_id=owner)
            db.add(row)
            db.commit()
            return row.id

    def _say(self, session_id: int, role: str, content: str) -> int:
        with self.SessionLocal() as db:
            row = ChatMessage(session_id=session_id, role=role, content=content)
            db.add(row)
            db.commit()
            return row.id

    def test_the_user_row_is_this_turns_message_and_the_assistant_row_is_the_question(self) -> None:
        """The ordering the whole feature depends on.

        send_message commits the user's row before the agent runs and writes the
        assistant's only after, so a tool invoked mid-turn sees exactly this.
        """
        session = self._session()
        self._say(session, "user", "Draft a reply to the Java role.")
        self._say(session, "assistant", "What is your notice period?")
        self._say(session, "user", "2 weeks")

        with self.SessionLocal() as db:
            evidence = latest_exchange(db, settings.owner_id)

        self.assertTrue(evidence.found)
        self.assertEqual(evidence.user_text, "2 weeks")
        self.assertEqual(evidence.assistant_text, "What is your notice period?")

    def test_an_empty_session_finds_nothing_rather_than_returning_a_blank_match(self) -> None:
        self._session()

        with self.SessionLocal() as db:
            evidence = latest_exchange(db, settings.owner_id)

        self.assertFalse(evidence.found)
        self.assertEqual(evidence.recent_user_texts, ())

    def test_another_owners_session_is_invisible(self) -> None:
        theirs = self._session(OTHER_OWNER)
        self._say(theirs, "user", "Their passport is Z9999999")

        with self.SessionLocal() as db:
            evidence = latest_exchange(db, settings.owner_id)

        self.assertFalse(evidence.found)

    def test_the_window_is_newest_first_and_capped(self) -> None:
        session = self._session()
        for index in range(PROVENANCE_WINDOW + 3):
            self._say(session, "user", f"message {index}")

        with self.SessionLocal() as db:
            evidence = latest_exchange(db, settings.owner_id)

        self.assertEqual(len(evidence.recent_user_texts), PROVENANCE_WINDOW)
        self.assertEqual(evidence.recent_user_texts[0], f"message {PROVENANCE_WINDOW + 2}")
        self.assertEqual(evidence.user_text, evidence.recent_user_texts[0])
        self.assertNotIn("message 0", evidence.recent_user_texts)

    def test_the_window_holds_only_user_rows(self) -> None:
        """The one property that makes widening the window free.

        Widening from one message to six widens *when* the user said it and
        never *who said it* - so an assistant message, a tool payload or an
        attachment row must never appear in it.
        """
        session = self._session()
        self._say(session, "user", "Hello")
        self._say(session, "assistant", "The recruiter's number is 214-555-9999.")
        self._say(session, "tool", '{"body": "attachment text with 214-555-8888"}')
        self._say(session, "user", "Save my location")

        with self.SessionLocal() as db:
            evidence = latest_exchange(db, settings.owner_id)

        self.assertEqual(evidence.recent_user_texts, ("Save my location", "Hello"))
        for text in evidence.recent_user_texts:
            self.assertNotIn("214-555", text)

    def test_the_window_never_crosses_into_another_session(self) -> None:
        first = self._session()
        self._say(first, "user", "My passport is X1234567")
        second = self._session()
        self._say(second, "user", "Save my notice period")

        with self.SessionLocal() as db:
            evidence = latest_exchange(db, settings.owner_id)

        self.assertEqual(evidence.recent_user_texts, ("Save my notice period",))
        self.assertEqual(evidence.assistant_text, "")


class UserSuppliedDocumentTests(unittest.TestCase):
    """The other question: which stored text is about to be processed.

    Not R3 evidence, and the tests below are written to keep it that way. A job
    description is untrusted whichever way it arrived; what this has to
    guarantee is only that the bytes come from the database rather than from a
    tool argument, so a model asked to pass a 200-line JD along cannot retype it
    into something with a different rate and a recruiter address nobody wrote.
    """

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _session(self, owner: str = settings.owner_id) -> int:
        with self.SessionLocal() as db:
            row = ChatSession(owner_id=owner)
            db.add(row)
            db.commit()
            return row.id

    def _say(self, session_id: int, role: str, content: str) -> int:
        with self.SessionLocal() as db:
            row = ChatMessage(session_id=session_id, role=role, content=content)
            db.add(row)
            db.commit()
            return row.id

    def _attach(
        self,
        session_id: int,
        *,
        owner: str = settings.owner_id,
        file_name: str = "requirement.pdf",
        content: str | None = "Rate: $88/hr on C2C",
        error: str | None = None,
    ) -> int:
        with self.SessionLocal() as db:
            row = ChatAttachment(
                owner_id=owner,
                session_id=session_id,
                file_path=f"/tmp/{file_name}",
                file_name=file_name,
                mime_type="application/pdf",
                byte_size=1234,
                sha256="0" * 64,
                content_markdown=content,
                extraction_error=error,
            )
            db.add(row)
            db.commit()
            return row.id

    def test_the_newest_user_message_is_returned_as_stored(self) -> None:
        session = self._session()
        self._say(session, "user", "ingest this")
        self._say(session, "assistant", "Which one?")
        self._say(session, "user", "Rate: $88/hr on C2C\nRecruiter: priya.v@example.test")

        with self.SessionLocal() as db:
            document = user_supplied_document(db, settings.owner_id)

        assert document is not None
        self.assertEqual(document.origin, "chat_message")
        self.assertIn("$88/hr", document.text)
        self.assertIn("priya.v@example.test", document.text)
        self.assertIsNone(document.attachment_id)

    def test_an_attachment_is_read_by_id_and_never_by_name(self) -> None:
        session = self._session()
        self._say(session, "user", "here is the JD")
        attachment = self._attach(session, content="REQUIREMENT ID: QX-7731-ZULU")

        with self.SessionLocal() as db:
            document = user_supplied_document(db, settings.owner_id, attachment_id=attachment)

        assert document is not None
        self.assertEqual(document.origin, "attachment")
        self.assertEqual(document.text, "REQUIREMENT ID: QX-7731-ZULU")
        self.assertEqual(document.label, "requirement.pdf")
        self.assertEqual(document.attachment_id, attachment)

    def test_an_attachment_id_wins_over_the_message(self) -> None:
        """The user's message is usually "process the attached one", which is an
        instruction and not the document."""
        session = self._session()
        self._say(session, "user", "ingest the attached requirement please")
        attachment = self._attach(session, content="Role: Senior Platform Engineer")

        with self.SessionLocal() as db:
            document = user_supplied_document(db, settings.owner_id, attachment_id=attachment)

        assert document is not None
        self.assertEqual(document.text, "Role: Senior Platform Engineer")

    def test_another_owners_attachment_is_not_readable(self) -> None:
        theirs = self._session(OTHER_OWNER)
        attachment = self._attach(theirs, owner=OTHER_OWNER, content="Their requirement")

        with self.SessionLocal() as db:
            self.assertIsNone(
                user_supplied_document(db, settings.owner_id, attachment_id=attachment)
            )

    def test_a_failed_extraction_returns_nothing_rather_than_empty_text(self) -> None:
        """Empty text would be ingested as an empty requirement. None is a
        refusal the caller has to handle."""
        session = self._session()
        attachment = self._attach(session, content=None, error="Encrypted PDF")

        with self.SessionLocal() as db:
            self.assertIsNone(
                user_supplied_document(db, settings.owner_id, attachment_id=attachment)
            )

    def test_a_missing_attachment_returns_nothing(self) -> None:
        self._session()
        with self.SessionLocal() as db:
            self.assertIsNone(user_supplied_document(db, settings.owner_id, attachment_id=9999))

    def test_a_blank_message_returns_nothing(self) -> None:
        session = self._session()
        self._say(session, "user", "   \n  ")
        with self.SessionLocal() as db:
            self.assertIsNone(user_supplied_document(db, settings.owner_id))

    def test_an_assistant_message_is_never_the_document(self) -> None:
        """The model's own summary of a JD is exactly the text this exists to
        avoid ingesting."""
        session = self._session()
        self._say(session, "user", "Rate: $88/hr on C2C")
        self._say(session, "assistant", "Rate: $90/hr, Dallas TX, 6 months")

        with self.SessionLocal() as db:
            document = user_supplied_document(db, settings.owner_id)

        assert document is not None
        self.assertEqual(document.text, "Rate: $88/hr on C2C")

    def test_another_owners_message_is_invisible(self) -> None:
        theirs = self._session(OTHER_OWNER)
        self._say(theirs, "user", "Their requirement")

        with self.SessionLocal() as db:
            self.assertIsNone(user_supplied_document(db, settings.owner_id))


class VerbatimSubstringTests(unittest.TestCase):
    def test_case_and_runs_of_whitespace_are_normalised(self) -> None:
        self.assertTrue(contains_verbatim("My notice period is 2 WEEKS.", "2 weeks"))
        self.assertTrue(contains_verbatim("I am in  Dallas,   TX", "Dallas, TX"))

    def test_punctuation_is_not_stripped(self) -> None:
        # Stripping it is how `2 weeks` starts matching `two weeks`, and how a
        # value the user never gave becomes one the server believes they did.
        self.assertFalse(contains_verbatim("I can join after two weeks", "2 weeks"))
        self.assertFalse(contains_verbatim("H1B", "H-1B"))

    def test_an_empty_needle_never_matches(self) -> None:
        self.assertFalse(contains_verbatim("anything at all", "   "))


if __name__ == "__main__":
    unittest.main()
