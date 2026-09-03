"""Job roles as a third canonical-entity type, alongside company and location.

Step 5 of the forward-only role work. It turned out to need no new machinery: in
this codebase "pending" entities are *computed* from parser_details_json on every
request, and stored rows are approve/dismiss decisions rather than candidates. So
teaching `_entity_values` to read `role_candidates` is the whole feature - the
existing routes, suppression and approval UI then work for roles unchanged.

An earlier draft added a `role_taxonomy_service.seed_pending_roles()` that wrote
`status="pending"` rows. That was the wrong shape: such rows suppress nothing
(only approved/dismissed suppress) and never appear as approved, so they would sit
in limbo beside the computed list. It was removed rather than kept as a parallel
mechanism.

`test_listing_pending_roles_does_not_modify_candidate_rows` is the mechanical
guarantee for the forward-only constraint: harvesting reads, it never writes.
"""

import json
import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RecruiterEmail
from app.services.taxonomy_learning_service import (
    ENTITY_TYPES,
    list_pending_entities,
    upsert_entity,
)

OWNER = "owner-under-test"


def _parser_details(role: str | None = None, candidates: list[str] | None = None) -> str:
    return json.dumps(
        {"ai_extractor_result": {"role": role, "role_candidates": candidates or []}},
        separators=(",", ":"),
    )


class RoleEntityTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.addCleanup(self.engine.dispose)

    def _email(self, db: Session, *, parser_details: str, owner_id: str = OWNER) -> RecruiterEmail:
        row = RecruiterEmail(
            owner_id=owner_id,
            sender="jane@example.com",
            subject="Subject",
            body="Body",
            role="Java Developer",
            location="Austin",
            state="needs_review",
            parser_details_json=parser_details,
        )
        db.add(row)
        db.flush()
        return row

    def _snapshot(self, db: Session) -> list[tuple]:
        return [
            (r.id, r.role, r.role_source, r.role_canonical, r.subject, r.parser_details_json)
            for r in db.query(RecruiterEmail).order_by(RecruiterEmail.id).all()
        ]

    def test_role_is_a_recognised_entity_type(self) -> None:
        self.assertIn("role", ENTITY_TYPES)

    def test_harvests_role_and_role_candidates(self) -> None:
        with Session(self.engine) as db:
            self._email(
                db,
                parser_details=_parser_details(
                    role="Java Full Stack Developer",
                    candidates=["Java Developer", "Full Stack Engineer"],
                ),
            )
            db.commit()
            pending = list_pending_entities(db, owner_id=OWNER, entity_type="role")

        names = {item["display_name"] for item in pending}
        self.assertEqual(
            names, {"Java Full Stack Developer", "Java Developer", "Full Stack Engineer"}
        )

    def test_occurrence_counts_rank_common_roles_first(self) -> None:
        with Session(self.engine) as db:
            for _ in range(3):
                self._email(db, parser_details=_parser_details(role="Java Developer"))
            self._email(db, parser_details=_parser_details(role="Mainframe Engineer"))
            db.commit()
            pending = list_pending_entities(db, owner_id=OWNER, entity_type="role")

        self.assertEqual(pending[0]["display_name"], "Java Developer")
        self.assertEqual(pending[0]["occurrence_count"], 3)

    def test_approved_roles_stop_appearing_as_pending(self) -> None:
        with Session(self.engine) as db:
            self._email(db, parser_details=_parser_details(role="Java Developer"))
            db.commit()
            self.assertTrue(list_pending_entities(db, owner_id=OWNER, entity_type="role"))

            upsert_entity(
                db,
                owner_id=OWNER,
                entity_type="role",
                display_name="Java Developer",
                canonical_name="Java Developer",
                aliases=[],
                occurrence_count=1,
                status="approved",
            )
            remaining = list_pending_entities(db, owner_id=OWNER, entity_type="role")

        self.assertEqual(remaining, [])

    def test_is_owner_scoped(self) -> None:
        with Session(self.engine) as db:
            self._email(db, parser_details=_parser_details(role="Java Developer"))
            self._email(
                db,
                parser_details=_parser_details(role="Someone Elses Role"),
                owner_id="someone-else",
            )
            db.commit()
            pending = list_pending_entities(db, owner_id=OWNER, entity_type="role")

        self.assertEqual({item["display_name"] for item in pending}, {"Java Developer"})

    def test_rejects_titles_that_are_not_names(self) -> None:
        """_valid_entity_name already screens placeholders and sentence-shaped text."""
        with Session(self.engine) as db:
            self._email(
                db,
                parser_details=_parser_details(
                    role="Unknown", candidates=["", "   ", "a " * 40]
                ),
            )
            db.commit()
            pending = list_pending_entities(db, owner_id=OWNER, entity_type="role")

        for item in pending:
            self.assertLessEqual(len(str(item["display_name"]).split()), 16)
            self.assertTrue(str(item["display_name"]).strip())

    def test_listing_pending_roles_does_not_modify_candidate_rows(self) -> None:
        """Forward-only: harvesting is a read. Nothing about a candidate changes."""
        with Session(self.engine) as db:
            self._email(
                db,
                parser_details=_parser_details(
                    role="Java Developer", candidates=["Senior Java Developer"]
                ),
            )
            db.commit()
            before = self._snapshot(db)
            list_pending_entities(db, owner_id=OWNER, entity_type="role")

        with Session(self.engine) as db:
            self.assertEqual(before, self._snapshot(db))


if __name__ == "__main__":
    unittest.main()
