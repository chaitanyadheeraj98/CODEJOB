"""F1: a turn records whose it was.

`ChatTurn` reached its user only through `session_id` -> `chat_sessions`, so
"this user's last 50 turns" - the shape every §12 observability question takes
- was a join before it was a query.

§12.1 is the line this must not cross: identifiers, timings, counts and error
codes only. `owner_id` is an opaque identifier, which is exactly why it is the
right thing to denormalise and message content is not.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tenancy
from app.models import Base, ChatTurn
from app.services.chat_service import ChatService


class ChatTurnOwnerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _record(self, **overrides):
        values = {
            "session_id": 1, "message_id": None, "requested_model": "auto",
            "prompt_sha256": "abc",
        }
        values.update(overrides)
        return ChatService._record_turn(self.db, **values)

    def test_a_turn_is_stamped_with_the_caller(self):
        reset = tenancy.set_owner_id("usr_the_caller")
        try:
            self._record()
        finally:
            tenancy.reset_owner_id(reset)
        self.assertEqual(self.db.query(ChatTurn).one().owner_id, "usr_the_caller")

    def test_two_users_turns_are_separable_without_a_join(self):
        """The reason for the column."""
        for owner in ("usr_a", "usr_b", "usr_a"):
            reset = tenancy.set_owner_id(owner)
            try:
                self._record()
            finally:
                tenancy.reset_owner_id(reset)

        mine = self.db.query(ChatTurn).filter(ChatTurn.owner_id == "usr_a").count()
        self.assertEqual(mine, 2)

    def test_an_explicit_owner_is_not_overwritten(self):
        """`setdefault`, so a caller that knows better still wins."""
        reset = tenancy.set_owner_id("usr_context")
        try:
            self._record(owner_id="usr_explicit")
        finally:
            tenancy.reset_owner_id(reset)
        self.assertEqual(self.db.query(ChatTurn).one().owner_id, "usr_explicit")

    def test_a_rejected_turn_is_stamped_too(self):
        """admission_rejected is the row §12 reads to size capacity, so it is
        no use if it cannot be attributed to a user."""
        reset = tenancy.set_owner_id("usr_busy")
        try:
            self._record(failure_code="admission_rejected")
        finally:
            tenancy.reset_owner_id(reset)
        row = self.db.query(ChatTurn).one()
        self.assertEqual(row.owner_id, "usr_busy")
        self.assertEqual(row.failure_code, "admission_rejected")

    def test_the_turn_carries_no_message_content(self):
        """§12.1: identifiers, timings, counts and error codes only.

        An allowlist rather than a search for suspicious substrings: the
        substring version passes `message_id` and `prompt_tokens` as offenders
        while a column called `q` holding the user's question would sail
        through. Every name here is an id, a timing, a count, a code or a flag.

        Adding a column means adding it here, which is the point - it forces
        the question "is this content?" at the moment someone could get it
        wrong. Once 100 mailboxes share one database, a telemetry table that
        renders their mail is the highest-value target in the product.
        """
        allowed = {
            "id", "owner_id", "session_id", "message_id",          # identifiers
            "model", "requested_model", "attempts", "failed_over",  # routing
            "prompt_tokens", "completion_tokens", "tool_calls",     # counts
            "duration_ms", "time_to_first_token_ms", "created_at",  # timings
            "failure_code", "interrupted", "cancelled",             # outcomes
            "budget_exhausted", "mcp_cached",
            "prompt_sha256",  # a hash of the prompt, never the prompt
        }
        actual = {c.name for c in ChatTurn.__table__.columns}
        unexpected = actual - allowed
        self.assertEqual(
            unexpected, set(),
            f"new telemetry column(s) {unexpected}: confirm they are not content, then allow them here",
        )
