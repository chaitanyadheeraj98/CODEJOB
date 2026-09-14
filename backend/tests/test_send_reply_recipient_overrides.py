"""The envelope is now the assistant's to change, end to end.

Two properties are load-bearing and each has its own test here:

- **The card shows what will be sent.** The tool resolves To and CC and puts
  the resolved values on the card, so "I've updated it to go to Kartheek" is
  either true on the card or not said at all.
- **The server sends only what the card could have honestly shown.** The
  request body arrives from a card a model populated, so the route validates
  the overrides again rather than trusting them.

The regression tests matter as much as the new ones: with no override supplied,
every value must reach Gmail exactly as it did before this existed.
"""

import json
import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.mcp_server.tools.email_actions import propose_send_email
from app.models import ProductivityEvent, RecruiterEmail


class ReplyHarness(unittest.TestCase):
    """Fixtures only, so neither suite below re-runs the other's tests."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            with self.SessionLocal() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.previous_actions_enabled = main.settings.feature_chat_actions_enabled
        self.previous_chat_enabled = main.settings.feature_chat_enabled
        main.settings.feature_chat_enabled = True
        main.settings.feature_chat_actions_enabled = True
        self.patch = patch("app.mcp_server.tools.email_actions.SessionLocal", self.SessionLocal)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        main.settings.feature_chat_actions_enabled = self.previous_actions_enabled
        main.settings.feature_chat_enabled = self.previous_chat_enabled
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _candidate(self, *, recipient: str | None = "nkumar@ibusinesssolution.com") -> int:
        with self.SessionLocal() as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Recruiter <recruiter@example.com>",
                subject="Java Developer",
                body="Need Java",
                role="Java Engineer",
                state="needs_review",
                recipient_email=recipient,
                cc_email="kartheek@horizonsoftech.net, hr@horizonsoftech.net",
                external_thread_id="thread-1",
            )
            db.add(row)
            db.commit()
            return row.id

    def _send(self, email_id: int, **body: object) -> tuple[object, list[dict[str, object]]]:
        calls: list[dict[str, object]] = []
        with patch.object(
            main,
            "send_reply_with_attachment",
            side_effect=lambda **kwargs: (calls.append(kwargs), "gmail-1")[1],
        ):
            response = self.client.post(
                f"/candidates/{email_id}/send-chat-reply",
                json={"body": "Thanks", **body},
            )
        return response, calls


class RecipientOverrideTests(ReplyHarness):
    # --- the tool: the card shows the envelope ---------------------------

    def test_no_override_leaves_the_envelope_exactly_as_it_was(self) -> None:
        proposal = propose_send_email(self._candidate(), "Hello")
        self.assertEqual(proposal["to"], "nkumar@ibusinesssolution.com")
        self.assertEqual(proposal["cc"], "kartheek@horizonsoftech.net, hr@horizonsoftech.net")
        self.assertFalse(proposal["to_changed"])
        self.assertFalse(proposal["cc_changed"])

    def test_to_override_moves_the_address_on_the_card(self) -> None:
        # The reported bug: the assistant agreed to write to Kartheek and the
        # card kept saying nkumar. The card is the only place a user can catch
        # that, so the card is what this asserts.
        proposal = propose_send_email(
            self._candidate(), "Hello", to_override="kartheek@horizonsoftech.net"
        )
        self.assertEqual(proposal["to"], "kartheek@horizonsoftech.net")
        self.assertTrue(proposal["to_changed"])

    def test_clear_cc_is_distinguishable_from_leaving_the_cc_alone(self) -> None:
        email_id = self._candidate()
        cleared = propose_send_email(email_id, "Hello", clear_cc=True)
        self.assertEqual(cleared["cc"], "")
        self.assertTrue(cleared["cc_changed"])

        untouched = propose_send_email(email_id, "Hello", cc_override="")
        self.assertEqual(untouched["cc"], "kartheek@horizonsoftech.net, hr@horizonsoftech.net")
        self.assertFalse(untouched["cc_changed"])

    def test_cc_override_replaces_the_list(self) -> None:
        proposal = propose_send_email(
            self._candidate(), "Hello", cc_override=" hr@horizonsoftech.net "
        )
        self.assertEqual(proposal["cc"], "hr@horizonsoftech.net")
        self.assertTrue(proposal["cc_changed"])

    def test_a_malformed_override_produces_no_proposal_at_all(self) -> None:
        # Not a card with a bad address on it. A card is a thing the user is
        # invited to confirm, and nothing here is confirmable.
        proposal = propose_send_email(self._candidate(), "Hello", to_override="kartheek")
        self.assertIn("error", proposal)
        self.assertNotIn("action", proposal)

    def test_an_override_supplies_the_recipient_a_row_never_had(self) -> None:
        proposal = propose_send_email(
            self._candidate(recipient=None), "Hello", to_override="kartheek@horizonsoftech.net"
        )
        self.assertEqual(proposal["action"], "send_email")
        self.assertEqual(proposal["to"], "kartheek@horizonsoftech.net")

    def test_still_refuses_when_there_is_no_recipient_and_no_override(self) -> None:
        proposal = propose_send_email(self._candidate(recipient=None), "Hello")
        self.assertEqual(proposal["missing"], ["recipient_email"])

    # --- the route: the server sends what the card showed ----------------

    def test_route_sends_the_overridden_envelope(self) -> None:
        response, calls = self._send(
            self._candidate(),
            to="kartheek@horizonsoftech.net",
            cc="hr@horizonsoftech.net",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["to"], "kartheek@horizonsoftech.net")
        self.assertEqual(calls[0]["cc"], "hr@horizonsoftech.net")

    def test_route_sends_no_cc_when_the_card_said_none(self) -> None:
        response, calls = self._send(self._candidate(), cc="")
        self.assertEqual(response.status_code, 200, response.text)
        # Falsy is what gmail_client checks before writing a Cc header.
        self.assertEqual(calls[0]["cc"], "")

    def test_route_keeps_the_thread_addresses_when_nothing_is_supplied(self) -> None:
        # The regression guard. Every existing caller omits these fields.
        response, calls = self._send(self._candidate())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["to"], "nkumar@ibusinesssolution.com")
        self.assertEqual(calls[0]["cc"], "kartheek@horizonsoftech.net, hr@horizonsoftech.net")

    def test_route_refuses_a_malformed_override_and_sends_nothing(self) -> None:
        response, calls = self._send(self._candidate(), to="kartheek")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(calls, [])

    def test_route_refuses_an_override_that_only_looks_like_a_list(self) -> None:
        response, calls = self._send(
            self._candidate(), to="a@example.com, b@example.com"
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(calls, [])

    def test_route_still_refuses_an_empty_recipient(self) -> None:
        response, calls = self._send(self._candidate(), to="")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(calls, [])

    def test_the_event_records_that_it_moved_but_not_where(self) -> None:
        response, _ = self._send(
            self._candidate(), to="kartheek@horizonsoftech.net", cc=""
        )
        self.assertEqual(response.status_code, 200, response.text)
        with self.SessionLocal() as db:
            event = db.query(ProductivityEvent).one()
            metadata = json.loads(event.metadata_json)
            self.assertTrue(metadata["recipient_overridden"])
            self.assertTrue(metadata["cc_overridden"])
            # No addresses in the record: an event row is a log line, and user
            # content does not go in log lines.
            self.assertNotIn("kartheek@horizonsoftech.net", event.metadata_json)


if __name__ == "__main__":
    unittest.main()


class ReplyResumeAttachmentTests(ReplyHarness):
    """A resume on a reply, reached the same way as on a composed email.

    The reply route attached stored documents and nothing else, so "reply with
    my resume attached" was a request the assistant could agree to and not
    carry out - the same shape of failure as the envelope it could not move.
    """

    def _resume(self, name: str = "Chaithanya_Java.pdf") -> int:
        from app.models import ResumeAsset

        with self.SessionLocal() as db:
            row = ResumeAsset(
                owner_id=main.settings.owner_id,
                file_path=f"/data/resumes/{name}",
                file_name=name,
                mime_type="application/pdf",
                sha256="c" * 64,
            )
            db.add(row)
            db.commit()
            return row.id

    def test_the_card_names_the_resume(self) -> None:
        resume_id = self._resume()
        proposal = propose_send_email(self._candidate(), "Hello", resume_id=resume_id)
        self.assertEqual(proposal["resume_id"], resume_id)
        self.assertEqual(proposal["resume_name"], "Chaithanya_Java.pdf")

    def test_an_unknown_resume_id_produces_no_card(self) -> None:
        proposal = propose_send_email(self._candidate(), "Hello", resume_id=999)
        self.assertIn("Unknown resume id", proposal["error"])
        self.assertNotIn("action", proposal)

    def test_another_owners_resume_is_refused(self) -> None:
        from app.models import ResumeAsset

        with self.SessionLocal() as db:
            row = ResumeAsset(
                owner_id="someone-else",
                file_path="/data/resumes/theirs.pdf",
                file_name="theirs.pdf",
                mime_type="application/pdf",
                sha256="d" * 64,
            )
            db.add(row)
            db.commit()
            foreign_id = row.id
        proposal = propose_send_email(self._candidate(), "Hello", resume_id=foreign_id)
        self.assertIn("Unknown resume id", proposal["error"])

    def test_the_document_refusal_points_at_the_resume_store(self) -> None:
        proposal = propose_send_email(self._candidate(), "Hello", document_ids=[21])
        self.assertIn("resume_id", proposal["note"])

    def test_the_route_attaches_it(self) -> None:
        resume_id = self._resume()
        response, calls = self._send(self._candidate(), resume_id=resume_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item.display_name for item in calls[0]["attachments"]], ["Chaithanya_Java.pdf"]
        )

    def test_the_route_refuses_one_that_vanished_between_card_and_click(self) -> None:
        response, calls = self._send(self._candidate(), resume_id=999)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(calls, [])

    def test_a_reply_without_a_resume_sends_no_attachments_at_all(self) -> None:
        response, calls = self._send(self._candidate())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(calls[0]["attachments"])
