"""Push-watch state on the Gmail credential row, and when it must be forgotten.

One column carries the risk. `gmail_history_id` is a position in one specific
mailbox, and Gmail will happily be asked to resume from it. Leave it behind on
a row that has started describing a different mailbox and the best outcome is a
404; there is no reading of it that is correct.

So the shape of this file is: the cursor survives the things that are not a
change of mailbox - a token refresh, and the weekly re-consent that
Testing-status OAuth forces - and does not survive the things that are.
"""

import unittest
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GmailCredential
from app.services import gmail_credential_service as service

KEY = Fernet.generate_key().decode()
OWNER = "usr_watch"
CURSOR = "987654321"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _credentials(*, token="access-1", refresh="refresh-1") -> Credentials:
    return Credentials(
        token=token,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        client_secret="secret",
        scopes=SCOPES,
    )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        from app.db import Base

        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self._previous_key = settings.credential_encryption_key
        settings.credential_encryption_key = KEY

    def tearDown(self) -> None:
        settings.credential_encryption_key = self._previous_key
        self.db.close()
        self.engine.dispose()

    def _connected(self, *, email="first@example.com", subject="sub-1") -> GmailCredential:
        service.save_credentials(
            self.db, OWNER, _credentials(), google_email=email, google_subject=subject
        )
        return self._watched()

    def _watched(self, *, expires_in=timedelta(days=6)) -> GmailCredential:
        """A row mid-life: watch registered, one notification already handled."""
        row = service.get_row(self.db, OWNER)
        assert row is not None
        now = datetime.now(UTC)
        row.gmail_history_id = CURSOR
        row.gmail_watch_expiration_at = now + expires_in
        row.gmail_watch_renewed_at = now
        row.gmail_last_notification_at = now
        row.gmail_last_event_processed_at = now
        row.gmail_watch_last_error = ""
        self.db.flush()
        return row


class PreservationTests(_Base):
    def test_a_token_refresh_keeps_the_cursor(self) -> None:
        """Refreshing an access token is not a mailbox change. Clearing here
        would re-baseline the watch roughly every hour, and every re-baseline
        is a window of history nobody processes."""
        self._connected()

        service.mark_refreshed(self.db, OWNER, _credentials(token="access-2"))

        row = service.get_row(self.db, OWNER)
        self.assertEqual(row.gmail_history_id, CURSOR)
        self.assertIsNotNone(row.gmail_watch_expiration_at)

    def test_re_consenting_the_same_mailbox_keeps_the_cursor(self) -> None:
        """The weekly reconnect. Same person, same mailbox, new tokens."""
        self._connected()

        service.save_credentials(
            self.db, OWNER, _credentials(token="access-2"),
            google_email="first@example.com", google_subject="sub-1",
        )

        self.assertEqual(service.get_row(self.db, OWNER).gmail_history_id, CURSOR)

    def test_a_save_that_carries_no_identity_keeps_the_cursor(self) -> None:
        """Not every save knows the address - `users.getProfile` lives on the
        other side of the module boundary and its result is passed in. An
        absent identifier is no information, and no information is not a
        reason to throw away a working cursor."""
        self._connected()

        service.save_credentials(self.db, OWNER, _credentials(token="access-2"))

        self.assertEqual(service.get_row(self.db, OWNER).gmail_history_id, CURSOR)


class ClearingTests(_Base):
    def test_connecting_a_different_mailbox_clears_the_cursor(self) -> None:
        self._connected()

        service.save_credentials(
            self.db, OWNER, _credentials(),
            google_email="second@example.com", google_subject="sub-1",
        )

        row = service.get_row(self.db, OWNER)
        self.assertIsNone(row.gmail_history_id)
        self.assertIsNone(row.gmail_watch_expiration_at)
        self.assertIsNone(row.gmail_last_notification_at)
        self.assertIsNone(row.gmail_last_event_processed_at)

    def test_a_different_google_subject_clears_the_cursor(self) -> None:
        """The subject is the stable identifier; someone can rename a Google
        address. A new subject behind a familiar-looking address is a different
        account, and it is the case the address check cannot see."""
        self._connected()

        service.save_credentials(
            self.db, OWNER, _credentials(),
            google_email="first@example.com", google_subject="sub-2",
        )

        self.assertIsNone(service.get_row(self.db, OWNER).gmail_history_id)

    def test_a_first_connection_has_nothing_to_clear(self) -> None:
        """An empty stored address is not a previous mailbox."""
        service.save_credentials(
            self.db, OWNER, _credentials(), google_email="first@example.com"
        )

        row = service.get_row(self.db, OWNER)
        self.assertEqual(row.google_email, "first@example.com")
        self.assertIsNone(row.gmail_history_id)

    def test_revoking_clears_the_watch_state(self) -> None:
        """A revoked connection cannot fetch history. Keeping the cursor would
        leave Settings able to claim an active watch on a dead credential."""
        self._connected()

        service.mark_revoked(self.db, OWNER, "token revoked at Google")

        row = service.get_row(self.db, OWNER)
        self.assertIsNone(row.gmail_history_id)
        self.assertIsNone(row.gmail_watch_expiration_at)
        self.assertIsNone(row.gmail_watch_renewed_at)

    def test_clearing_can_record_why(self) -> None:
        """Losing the watch is usually the symptom of something worth saying.
        Blanking the explanation at the same moment leaves Settings silent."""
        row = self._connected()

        service.clear_watch_state(row, error="watch_expired")

        self.assertEqual(row.gmail_watch_last_error, "watch_expired")
        self.assertIsNone(row.gmail_history_id)

    def test_clearing_does_not_commit_on_its_caller_s_behalf(self) -> None:
        """It runs inside someone else's unit of work. A flush here would
        write out whatever else that caller had in progress."""
        row = self._connected()
        self.db.expire_all()

        service.clear_watch_state(row)

        self.assertIn(row, self.db.dirty)


class StatusTests(_Base):
    def test_an_unexpired_watch_reads_as_active(self) -> None:
        self._connected()
        status = service.connection_status(self.db, OWNER)
        self.assertTrue(status.watch_active)
        self.assertTrue(status.has_history_cursor)

    def test_an_expired_watch_reads_as_inactive(self) -> None:
        """Gmail stops delivering at the expiry whether or not anything here
        noticed. Seven days is the whole budget, so an hour is safely past."""
        self._connected()
        self._watched(expires_in=timedelta(hours=-1))

        self.assertFalse(service.connection_status(self.db, OWNER).watch_active)

    def test_a_row_with_no_watch_reads_as_inactive(self) -> None:
        service.save_credentials(self.db, OWNER, _credentials(), google_email="a@example.com")

        status = service.connection_status(self.db, OWNER)
        self.assertFalse(status.watch_active)
        self.assertFalse(status.has_history_cursor)

    def test_a_revoked_row_reads_as_inactive_even_with_time_left(self) -> None:
        """Guards rows written before revocation cleared this state."""
        row = self._connected()
        row.revoked_at = datetime.now(UTC)
        self.db.flush()

        self.assertFalse(service.connection_status(self.db, OWNER).watch_active)

    def test_the_status_never_carries_the_history_cursor(self) -> None:
        """A mailbox position has no use in a status response and every
        opportunity to reach a browser through one. The UI needs to know a
        cursor exists, which is a boolean."""
        self._connected()

        status = service.connection_status(self.db, OWNER)

        self.assertNotIn(CURSOR, repr(status))
        self.assertTrue(status.has_history_cursor)

    def test_the_status_still_carries_no_token_material(self) -> None:
        """The type gained seven fields; it must not have gained a route for
        the thing it was created to keep out."""
        self._connected()

        blob = repr(service.connection_status(self.db, OWNER))

        self.assertNotIn("refresh-1", blob)
        self.assertNotIn("access-1", blob)
        row = service.get_row(self.db, OWNER)
        self.assertNotIn(row.refresh_token_encrypted, blob)


if __name__ == "__main__":
    unittest.main()
