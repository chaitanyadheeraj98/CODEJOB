"""G3 / §13: deletion, which is the one that cannot be taken back.

The tests here are not really about deleting rows - that part is a `DELETE`
statement. They are about the three claims that make the sweep trustworthy:
that it reaches **every** table, that it survives the schema's `RESTRICT`, and
that it never commits a half-deleted account.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Application,
    CanonicalEntityTaxonomyEntry,
    ChatMessage,
    ChatSession,
    EmailConversation,
    GmailCredential,
    ProviderCredential,
    RecruiterEmail,
    User,
    UserSession,
)
from app.services import account_purge_service, account_service, gmail_credential_service
from app.services.account_purge_service import PurgeIncomplete

MINE = "usr_mine"
THEIRS = "usr_theirs"


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.now = datetime.now(UTC)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed(self, owner: str) -> User:
        user = User(email=f"{owner}@example.com", owner_id=owner)
        self.db.add(user)
        self.db.flush()
        self.db.add(UserSession(
            user_id=user.id, token_hash=f"hash-{owner}",
            created_at=self.now, expires_at=self.now + timedelta(days=1),
        ))
        email = RecruiterEmail(
            owner_id=owner, sender=f"r@{owner}.example", subject="A role", body="b",
            state="needs_review", source="gmail", external_message_id=f"m-{owner}",
            created_at=self.now, updated_at=self.now,
        )
        self.db.add(email)
        self.db.flush()
        # The RESTRICT: this row forbids deleting the mail it points at.
        self.db.add(EmailConversation(
            owner_id=owner, root_recruiter_email_id=email.id,
            external_thread_id=f"thread-{owner}",
            created_at=self.now, updated_at=self.now,
        ))
        self.db.add(Application(
            owner_id=owner, resume_asset_id=1, resume_version_snapshot=1,
            resume_file_name_snapshot="r.docx", resume_sha256_snapshot="abc",
        ))
        session = ChatSession(owner_id=owner, title="t", created_at=self.now, updated_at=self.now)
        self.db.add(session)
        self.db.flush()
        self.db.add(ChatMessage(
            session_id=session.id, role="user", content="hello", created_at=self.now,
        ))
        self.db.add(CanonicalEntityTaxonomyEntry(
            owner_id=owner, entity_type="role", canonical_name=f"{owner} role",
            aliases_json="[]", status="approved",
        ))
        self.db.add(GmailCredential(
            owner_id=owner, access_token_encrypted="cipher", refresh_token_encrypted="cipher2",
        ))
        self.db.add(ProviderCredential(owner_id=owner, provider="ollama", api_key_encrypted="k"))
        self.db.commit()
        return user

    def _purge(self, owner: str = MINE):
        with patch.object(account_service, "revoke_google_token", lambda token: None):
            return account_purge_service.purge(self.db, owner)


class ExhaustivenessTests(_Base):
    """§13: "must be exhaustive"."""

    def test_every_table_in_the_schema_resolves_to_an_owner(self):
        """The test that makes "generated, not hand-written" mean something.

        A table added next year is covered the day it appears. If one ever
        stops resolving, this fails loudly rather than the data quietly
        surviving a deletion someone was told had happened.
        """
        self.assertEqual(account_purge_service.unreachable_tables(), [])

    def test_the_two_tables_without_an_owner_id_are_still_reached(self):
        """`chat_messages` and `user_sessions` hang off an owner-scoped parent.
        A sweep keyed only on `owner_id` would leave both behind."""
        self._seed(MINE)
        self.assertEqual(
            self.db.execute(select(ChatMessage)).scalars().all().__len__(), 1
        )
        self._purge()
        self.assertEqual(self.db.execute(select(ChatMessage)).scalars().all(), [])
        self.assertEqual(self.db.execute(select(UserSession)).scalars().all(), [])

    def test_nothing_at_all_remains_for_that_owner(self):
        self._seed(MINE)
        self._purge()
        self.assertEqual(account_purge_service.remaining_rows(self.db, MINE), {})
        self.assertIsNone(
            self.db.query(User).filter(User.owner_id == MINE).one_or_none()
        )

    def test_the_restrict_constraint_does_not_stop_it_halfway(self):
        """`email_conversations.root_recruiter_email_id -> recruiter_emails` is
        ON DELETE RESTRICT. Wrong order and the purge dies mid-account."""
        self._seed(MINE)
        result = self._purge()
        self.assertIn("recruiter_emails", result.deleted)
        self.assertIn("email_conversations", result.deleted)
        self.assertEqual(account_purge_service.remaining_rows(self.db, MINE), {})

    def test_the_conversation_is_deleted_before_the_mail_it_points_at(self):
        """Asserted on the order itself, not only on the outcome, because
        SQLite is lenient about foreign keys and Postgres is not."""
        order = [table.name for table in account_purge_service.purge_order()]
        self.assertLess(order.index("email_conversations"), order.index("recruiter_emails"))

    def test_credentials_and_sessions_go_with_it(self):
        self._seed(MINE)
        self._purge()
        self.assertEqual(self.db.query(GmailCredential).count(), 0)
        self.assertEqual(self.db.query(ProviderCredential).count(), 0)
        self.assertEqual(self.db.query(UserSession).count(), 0)


class IsolationTests(_Base):
    """Deleting one account must not touch another."""

    def test_another_account_is_untouched(self):
        self._seed(MINE)
        self._seed(THEIRS)
        self._purge(MINE)

        self.assertEqual(account_purge_service.remaining_rows(self.db, MINE), {})
        survivors = account_purge_service.remaining_rows(self.db, THEIRS)
        self.assertIn("recruiter_emails", survivors)
        self.assertIn("users", survivors)
        self.assertIsNotNone(self.db.query(User).filter(User.owner_id == THEIRS).one_or_none())

    def test_their_chat_messages_survive_too(self):
        """The indirect predicate is the one that could over-reach: a join
        written slightly wrong takes everyone's messages."""
        self._seed(MINE)
        self._seed(THEIRS)
        self._purge(MINE)
        self.assertEqual(self.db.query(ChatMessage).count(), 1)


class SafetyTests(_Base):
    def test_a_surviving_row_aborts_the_whole_purge(self):
        """A partially deleted account is worse than either outcome it sits
        between, so the transaction is rolled back rather than committed."""
        self._seed(MINE)
        with patch.object(account_purge_service, "remaining_rows", return_value={"applications": 1}):
            with self.assertRaises(PurgeIncomplete):
                self._purge()

        self.db.rollback()
        self.assertIsNotNone(self.db.query(User).filter(User.owner_id == MINE).one_or_none())
        self.assertEqual(self.db.query(RecruiterEmail).count(), 1)

    def test_it_refuses_to_run_against_a_schema_it_cannot_account_for(self):
        with patch.object(account_purge_service, "unreachable_tables", return_value=["mystery"]):
            with self.assertRaises(PurgeIncomplete):
                self._purge()

    def test_a_google_failure_is_reported_and_the_purge_still_happens(self):
        """§13: record it, still purge, log loudly.

        `get_credentials` is stubbed with a usable token on purpose. The seeded
        row holds the literal string "cipher", which is not valid Fernet, so
        the real lookup raises `InvalidToken` and the revoke call is never
        reached - the first version of this test asserted the right value for
        entirely the wrong reason, and passed happily against a `_revoke_at_google`
        that could never return False.
        """
        self._seed(MINE)
        usable = SimpleNamespace(refresh_token="a-refresh-token", token="an-access-token")
        with patch.object(gmail_credential_service, "get_credentials", return_value=usable),                 patch.object(account_service, "revoke_google_token", side_effect=RuntimeError("nope")) as revoke:
            result = account_purge_service.purge(self.db, MINE)

        revoke.assert_called_once_with("a-refresh-token")
        self.assertFalse(result.google_revoked)
        self.assertEqual(account_purge_service.remaining_rows(self.db, MINE), {})

    def test_a_successful_revocation_is_reported_as_one(self):
        """Otherwise `google_revoked` could be hard-coded False and every test
        above would still pass."""
        self._seed(MINE)
        usable = SimpleNamespace(refresh_token="a-refresh-token", token="")
        with patch.object(gmail_credential_service, "get_credentials", return_value=usable),                 patch.object(account_service, "revoke_google_token", lambda token: None):
            result = account_purge_service.purge(self.db, MINE)
        self.assertTrue(result.google_revoked)

    def test_an_unreadable_credential_is_reported_rather_than_assumed_revoked(self):
        """The path the test above used to take by accident. A credential this
        app can no longer decrypt still has a live grant on someone's Google
        account, so claiming it was revoked would be a lie in the one place it
        matters."""
        self._seed(MINE)  # seeds "cipher", which is not valid Fernet
        result = self._purge()
        self.assertFalse(result.google_revoked)
        self.assertEqual(account_purge_service.remaining_rows(self.db, MINE), {})

    def test_an_account_with_no_google_credential_reports_success(self):
        """Nothing to revoke is not a failure to revoke."""
        user = User(email="bare@example.com", owner_id="usr_bare")
        self.db.add(user)
        self.db.commit()
        result = self._purge("usr_bare")
        self.assertTrue(result.google_revoked)

    def test_the_result_counts_what_it_deleted(self):
        self._seed(MINE)
        result = self._purge()
        self.assertEqual(result.deleted["users"], 1)
        self.assertGreater(result.rows, 5)


class WindowTests(_Base):
    """Choice A: the 30-day window is the product, so the trigger is time."""

    def _request_deletion(self, owner: str, *, days_ago: int) -> None:
        user = self.db.query(User).filter(User.owner_id == owner).one()
        user.disabled_at = self.now
        user.deletion_requested_at = self.now - timedelta(days=days_ago)
        self.db.commit()

    def test_an_account_past_the_window_is_due(self):
        self._seed(MINE)
        self._request_deletion(MINE, days_ago=account_service.RECOVERY_DAYS + 1)
        due = account_purge_service.accounts_due_for_purge(self.db)
        self.assertEqual([u.owner_id for u in due], [MINE])

    def test_an_account_inside_the_window_is_not(self):
        """The recovery window is a promise made to the user in G1's own
        confirmation dialog."""
        self._seed(MINE)
        self._request_deletion(MINE, days_ago=account_service.RECOVERY_DAYS - 1)
        self.assertEqual(account_purge_service.accounts_due_for_purge(self.db), [])

    def test_an_account_that_never_asked_is_never_due(self):
        self._seed(MINE)
        self.assertEqual(account_purge_service.accounts_due_for_purge(self.db), [])

    def test_a_restored_account_drops_out_of_the_queue(self):
        """Admin re-enable clears `deletion_requested_at`; that is what the
        recovery window *is*."""
        self._seed(MINE)
        self._request_deletion(MINE, days_ago=account_service.RECOVERY_DAYS + 5)
        user = self.db.query(User).filter(User.owner_id == MINE).one()
        user.disabled_at = None
        user.deletion_requested_at = None
        self.db.commit()
        self.assertEqual(account_purge_service.accounts_due_for_purge(self.db), [])


class SweepTests(_Base):
    """The trigger: time, not a button. Choice A."""

    def _request(self, owner: str, *, days_ago: int) -> None:
        user = self.db.query(User).filter(User.owner_id == owner).one()
        user.disabled_at = self.now
        user.deletion_requested_at = self.now - timedelta(days=days_ago)
        self.db.commit()

    def test_the_sweep_purges_only_what_is_due(self):
        self._seed(MINE)
        self._seed(THEIRS)
        self._request(MINE, days_ago=account_service.RECOVERY_DAYS + 1)
        self._request(THEIRS, days_ago=1)

        with patch.object(account_service, "revoke_google_token", lambda token: None):
            results = account_purge_service.purge_due_accounts(self.db)

        self.assertEqual([r.owner_id for r in results], [MINE])
        self.assertEqual(account_purge_service.remaining_rows(self.db, MINE), {})
        self.assertNotEqual(account_purge_service.remaining_rows(self.db, THEIRS), {})

    def test_one_accounts_failure_does_not_resurrect_another(self):
        """Committed per account, not per sweep. Batching them would mean one
        failure rolls back a deletion that had already succeeded."""
        self._seed(MINE)
        self._seed(THEIRS)
        self._request(MINE, days_ago=account_service.RECOVERY_DAYS + 2)
        self._request(THEIRS, days_ago=account_service.RECOVERY_DAYS + 1)

        real = account_purge_service.purge

        def explode(db, owner_id):
            if owner_id == THEIRS:
                raise RuntimeError("boom")
            return real(db, owner_id)

        with patch.object(account_purge_service, "purge", explode),                 patch.object(account_service, "revoke_google_token", lambda token: None):
            results = account_purge_service.purge_due_accounts(self.db)

        self.assertEqual([r.owner_id for r in results], [MINE])
        self.assertEqual(account_purge_service.remaining_rows(self.db, MINE), {},
                         "the account that succeeded must stay deleted")
        self.assertIsNotNone(self.db.query(User).filter(User.owner_id == THEIRS).one_or_none())

    def test_the_sweep_is_capped(self):
        """A surprise backlog drains over several ticks rather than holding one
        transaction open across the whole table."""
        owners = [f"usr_bulk_{index}" for index in range(account_purge_service.MAX_PER_SWEEP + 2)]
        for owner in owners:
            self.db.add(User(
                email=f"{owner}@example.com", owner_id=owner, disabled_at=self.now,
                deletion_requested_at=self.now - timedelta(days=account_service.RECOVERY_DAYS + 1),
            ))
        self.db.commit()

        with patch.object(account_service, "revoke_google_token", lambda token: None):
            results = account_purge_service.purge_due_accounts(self.db)
        self.assertEqual(len(results), account_purge_service.MAX_PER_SWEEP)

    def test_nothing_due_is_a_no_op(self):
        self._seed(MINE)
        self.assertEqual(account_purge_service.purge_due_accounts(self.db), [])


if __name__ == "__main__":
    unittest.main()
