"""The defence that makes a changeable envelope safe to have.

The model reads recruiter mail, recruiter mail is untrusted, and untrusted text
that can pick a recipient can ask for the user's details to go somewhere the
user never chose. Refusing every new address would answer that and take the
ordinary case with it - "CC the other recruiter at the same firm" is a request
people actually make.

So the address is graded and the grade sets the price. These tests pin the two
halves that make that a defence rather than a decoration: a colleague at a
domain already on the thread costs nothing, and a stranger cannot be sent to
without a human act the model has no way to perform.
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
from app.models import (
    EmailConversation,
    EmailReplyMessage,
    ProductivityEvent,
    RecruiterEmail,
    UserSettings,
)
from app.services import recipient_trust


class RecipientTrustTests(unittest.TestCase):
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

    def _candidate(self) -> int:
        with self.SessionLocal() as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Kartheek <kartheek@horizonsoftech.net>",
                subject="Java Developer",
                body="Need Java",
                role="Java Engineer",
                state="needs_review",
                recipient_email="nkumar@ibusinesssolution.com",
                cc_email="hr@horizonsoftech.net",
                external_thread_id="thread-1",
            )
            db.add(row)
            db.commit()
            return row.id

    def _send(self, email_id: int, **body: object):
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

    # --- grading ---------------------------------------------------------

    def test_an_address_on_the_row_is_already_known(self) -> None:
        with self.SessionLocal() as db:
            email = db.get(RecruiterEmail, self._candidate())
            grades = recipient_trust.grade(
                db, email, ["kartheek@horizonsoftech.net", "hr@horizonsoftech.net"]
            )
        self.assertEqual(set(grades.values()), {recipient_trust.THREAD})

    def test_case_does_not_make_a_known_address_a_stranger(self) -> None:
        with self.SessionLocal() as db:
            email = db.get(RecruiterEmail, self._candidate())
            grades = recipient_trust.grade(db, email, ["Kartheek@HorizonSoftech.NET"])
        self.assertEqual(grades["Kartheek@HorizonSoftech.NET"], recipient_trust.THREAD)

    def test_a_colleague_at_a_thread_domain_costs_nothing(self) -> None:
        # The case the whole grading scheme exists to keep cheap. An injected
        # instruction cannot reach this branch without naming a domain the user
        # already corresponds with, and that domain already receives the mail.
        with self.SessionLocal() as db:
            email = db.get(RecruiterEmail, self._candidate())
            grades = recipient_trust.grade(db, email, ["payroll@horizonsoftech.net"])
        self.assertEqual(grades["payroll@horizonsoftech.net"], recipient_trust.DOMAIN)

    def test_an_address_from_the_thread_history_counts(self) -> None:
        email_id = self._candidate()
        with self.SessionLocal() as db:
            conversation = EmailConversation(
                owner_id=main.settings.owner_id,
                external_thread_id="thread-1",
                recruiter_email_snapshot="kartheek@horizonsoftech.net",
            )
            db.add(conversation)
            db.flush()
            db.add(
                EmailReplyMessage(
                    owner_id=main.settings.owner_id,
                    conversation_id=conversation.id,
                    external_message_id="m-1",
                    sender="Anita <anita@clientcorp.com>",
                    to_header="me@example.com",
                    cc_header="legal@clientcorp.com",
                )
            )
            db.commit()
            email = db.get(RecruiterEmail, email_id)
            grades = recipient_trust.grade(db, email, ["anita@clientcorp.com", "legal@clientcorp.com"])
        self.assertEqual(set(grades.values()), {recipient_trust.THREAD})

    def test_the_owners_configured_employer_ccs_count(self) -> None:
        email_id = self._candidate()
        with self.SessionLocal() as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    default_employer_cc_emails="bench@myemployer.com",
                )
            )
            db.commit()
            email = db.get(RecruiterEmail, email_id)
            grades = recipient_trust.grade(db, email, ["bench@myemployer.com"])
        self.assertEqual(grades["bench@myemployer.com"], recipient_trust.THREAD)

    def test_another_owners_thread_does_not_vouch_for_an_address(self) -> None:
        # Tenancy holds here or the grade is worthless: an address known to
        # somebody else is not an address this user has ever seen.
        email_id = self._candidate()
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id="someone-else",
                    sender="Stranger <stranger@elsewhere.com>",
                    subject="Other",
                    body="Other",
                    role="Other",
                    state="needs_review",
                    recipient_email="stranger@elsewhere.com",
                    external_thread_id="thread-1",
                )
            )
            db.commit()
            email = db.get(RecruiterEmail, email_id)
            grades = recipient_trust.grade(db, email, ["stranger@elsewhere.com"])
        self.assertEqual(grades["stranger@elsewhere.com"], recipient_trust.NEW)

    def test_only_what_the_assistant_changed_is_graded(self) -> None:
        with self.SessionLocal() as db:
            email = db.get(RecruiterEmail, self._candidate())
            changed = recipient_trust.changed_addresses(
                email, "nkumar@ibusinesssolution.com", "hr@horizonsoftech.net, new@elsewhere.com"
            )
        self.assertEqual(changed, ["new@elsewhere.com"])

    # --- the card asks ---------------------------------------------------

    def test_the_card_names_the_stranger_and_asks_for_confirmation(self) -> None:
        proposal = propose_send_email(
            self._candidate(), "Hello", cc_override="stranger@elsewhere.com"
        )
        self.assertEqual(proposal["unknown_recipients"], ["stranger@elsewhere.com"])
        self.assertTrue(proposal["requires_recipient_confirmation"])

    def test_the_card_asks_for_nothing_extra_for_a_colleague(self) -> None:
        proposal = propose_send_email(
            self._candidate(), "Hello", to_override="payroll@horizonsoftech.net"
        )
        self.assertEqual(proposal["unknown_recipients"], [])
        self.assertFalse(proposal["requires_recipient_confirmation"])

    def test_resolving_documents_does_not_erase_the_recipient_warning(self) -> None:
        # The two lists were briefly the same local name. A proposal with
        # documents attached would have reported no unknown recipients at all.
        proposal = propose_send_email(
            self._candidate(), "Hello", document_ids=[], cc_override="stranger@elsewhere.com"
        )
        self.assertEqual(proposal["unknown_recipients"], ["stranger@elsewhere.com"])

    # --- the server refuses ----------------------------------------------

    def test_the_server_refuses_a_stranger_without_an_explicit_act(self) -> None:
        response, calls = self._send(self._candidate(), cc="stranger@elsewhere.com")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("stranger@elsewhere.com", response.json()["detail"])
        self.assertEqual(calls, [])

    def test_the_same_request_goes_through_once_a_human_says_so(self) -> None:
        response, calls = self._send(
            self._candidate(), cc="stranger@elsewhere.com", confirm_new_recipients=True
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["cc"], "stranger@elsewhere.com")

    def test_a_colleague_needs_no_confirmation_at_the_server_either(self) -> None:
        response, calls = self._send(self._candidate(), to="payroll@horizonsoftech.net")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["to"], "payroll@horizonsoftech.net")

    def test_the_threads_own_addresses_are_never_graded(self) -> None:
        # The regression that would break failed-mapping sends: the row's own
        # recipient must not have to prove itself.
        response, calls = self._send(self._candidate())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["to"], "nkumar@ibusinesssolution.com")

    def test_the_refusal_is_not_recorded_as_a_send(self) -> None:
        self._send(self._candidate(), cc="stranger@elsewhere.com")
        with self.SessionLocal() as db:
            self.assertEqual(db.query(ProductivityEvent).count(), 0)

    def test_the_event_says_it_was_redirected_without_saying_where(self) -> None:
        response, _ = self._send(
            self._candidate(), to="stranger@elsewhere.com", confirm_new_recipients=True
        )
        self.assertEqual(response.status_code, 200, response.text)
        with self.SessionLocal() as db:
            event = db.query(ProductivityEvent).one()
            self.assertTrue(json.loads(event.metadata_json)["recipient_overridden"])
            self.assertNotIn("stranger@elsewhere.com", event.metadata_json)


if __name__ == "__main__":
    unittest.main()
