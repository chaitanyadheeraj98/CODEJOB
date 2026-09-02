"""Company gap-filling from the approved vocabulary.

The point of curating companies is that the base parser gets smarter without AI.
345 company entries were already approved on this deployment and nothing read them
- every reader of CanonicalEntityTaxonomyEntry was a settings endpoint. This wires
that curation into ingest, where `company` is empty on 82% of rows.

Two properties carry the design:

  * strictly additive - a parser-extracted company is never overwritten, so the
    taxonomy can only add information where there was none;
  * always labelled - anything filled is marked `taxonomy_matched`, so a matched
    value never passes for an extracted one downstream.
"""

import json
import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import CanonicalEntityTaxonomyEntry
from app.services.role_provenance import RoleSource
from app.services.role_taxonomy import (
    COMPANY_ENTITY_TYPE,
    LOCATION_ENTITY_TYPE,
    ROLE_ENTITY_TYPE,
    clear_role_taxonomy_cache,
    fill_entity_gaps,
    load_entity_taxonomy,
    match_entity_from_taxonomy,
)

OWNER = "owner-under-test"


class EntityGapFillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

    def _entry(
        self,
        db: Session,
        canonical: str,
        *,
        entity_type: str = COMPANY_ENTITY_TYPE,
        aliases: list[str] | None = None,
        status: str = "approved",
        owner_id: str = OWNER,
    ) -> None:
        db.add(
            CanonicalEntityTaxonomyEntry(
                owner_id=owner_id,
                entity_type=entity_type,
                canonical_name=canonical,
                aliases_json=json.dumps(aliases or []),
                occurrence_count=5,
                status=status,
            )
        )
        db.flush()

    def test_fills_an_empty_company_from_the_subject(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Smart It Frame")
            db.commit()
            filled = fill_entity_gaps(
                {"company": None, "end_client": None},
                db=db,
                owner_id=OWNER,
                subject="Java Developer role at Smart It Frame",
                body="",
            )
        self.assertEqual(filled["company"], "Smart It Frame")
        self.assertEqual(filled["company_source"], RoleSource.TAXONOMY_MATCHED)

    def test_never_overwrites_a_parser_extracted_company(self) -> None:
        """Strictly additive. The taxonomy is a floor, not a competing opinion."""
        with Session(self.engine) as db:
            self._entry(db, "Smart It Frame")
            db.commit()
            filled = fill_entity_gaps(
                {"company": "Acme Staffing"},
                db=db,
                owner_id=OWNER,
                subject="Role at Smart It Frame",
                body="",
            )
        self.assertEqual(filled["company"], "Acme Staffing")
        self.assertNotIn("company_source", filled)

    def test_leaves_company_empty_when_nothing_matches(self) -> None:
        """A miss must stay a miss - no nearest-neighbour temptation."""
        with Session(self.engine) as db:
            self._entry(db, "Smart It Frame")
            db.commit()
            filled = fill_entity_gaps(
                {"company": None},
                db=db,
                owner_id=OWNER,
                subject="Java Developer, Austin TX",
                body="Spring Boot, Kafka.",
            )
        self.assertIsNone(filled["company"])
        self.assertNotIn("company_source", filled)

    def test_only_approved_companies_are_used(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Pending Corp", status="pending")
            self._entry(db, "Dismissed Corp", status="dismissed")
            db.commit()
            for name in ("Pending Corp", "Dismissed Corp"):
                filled = fill_entity_gaps(
                    {"company": None}, db=db, owner_id=OWNER, subject=f"Role at {name}", body=""
                )
                self.assertIsNone(filled["company"], name)

    def test_single_word_companies_are_allowed_but_roles_are_not(self) -> None:
        """The word floor differs by type, and the difference is load-bearing.

        "ADP" is a real company; a one-word role like "Java" is a technology that
        would match every email mentioning the stack.
        """
        with Session(self.engine) as db:
            self._entry(db, "ADP", entity_type=COMPANY_ENTITY_TYPE)
            self._entry(db, "Java", entity_type=ROLE_ENTITY_TYPE)
            db.commit()
            companies = load_entity_taxonomy(db, owner_id=OWNER, entity_type=COMPANY_ENTITY_TYPE)
            roles = load_entity_taxonomy(db, owner_id=OWNER, entity_type=ROLE_ENTITY_TYPE)

        self.assertEqual(companies.size, 1)
        self.assertEqual(roles.size, 0)

    def test_longest_match_wins_for_locations(self) -> None:
        """Location matching works, even though ingest does not use it yet."""
        with Session(self.engine) as db:
            self._entry(db, "Dallas", entity_type=LOCATION_ENTITY_TYPE)
            self._entry(db, "Dallas, TX", entity_type=LOCATION_ENTITY_TYPE)
            db.commit()
            found = match_entity_from_taxonomy(
                db,
                owner_id=OWNER,
                entity_type=LOCATION_ENTITY_TYPE,
                text="Onsite in Dallas, TX from day one",
            )
        self.assertEqual(found, "Dallas, TX")

    def test_fills_an_empty_location(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Dallas, TX", entity_type=LOCATION_ENTITY_TYPE)
            db.commit()
            filled = fill_entity_gaps(
                {"company": None},
                db=db,
                owner_id=OWNER,
                subject="Java Developer - Dallas, TX onsite",
                body="",
                location="",
            )
        self.assertEqual(filled["location"], "Dallas, TX")
        self.assertEqual(filled["location_source"], RoleSource.TAXONOMY_MATCHED)

    def test_treats_parser_placeholders_as_gaps(self) -> None:
        """"unknown" is the parser saying it found nothing, not an answer to protect."""
        with Session(self.engine) as db:
            self._entry(db, "Remote", entity_type=LOCATION_ENTITY_TYPE)
            db.commit()
            for placeholder in ("", "unknown", "not_specified", "  "):
                filled = fill_entity_gaps(
                    {"company": None},
                    db=db,
                    owner_id=OWNER,
                    subject="Remote Java role",
                    body="",
                    location=placeholder,
                )
                self.assertEqual(filled["location"], "Remote", placeholder)

    def test_never_overwrites_a_real_location(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Remote", entity_type=LOCATION_ENTITY_TYPE)
            db.commit()
            filled = fill_entity_gaps(
                {"company": None},
                db=db,
                owner_id=OWNER,
                subject="Remote Java role",
                body="",
                location="Austin, TX",
            )
        self.assertEqual(filled["location"], "Austin, TX")
        self.assertNotIn("location_source", filled)

    def test_location_body_window_is_tighter_than_company(self) -> None:
        """A city named deep in a posting is not necessarily this job's location."""
        with Session(self.engine) as db:
            self._entry(db, "Smart It Frame", entity_type=COMPANY_ENTITY_TYPE)
            self._entry(db, "San Antonio, TX", entity_type=LOCATION_ENTITY_TYPE)
            db.commit()
            # ~450 chars in: inside company's 600 window, outside location's 300.
            padding = "filler word " * 37
            filled = fill_entity_gaps(
                {"company": None},
                db=db,
                owner_id=OWNER,
                subject="Urgent hiring",
                body=padding + " Smart It Frame office in San Antonio, TX",
                location="",
            )
        self.assertEqual(filled["company"], "Smart It Frame")
        self.assertEqual(filled["location"], "", "location must not match that deep")

    def test_is_owner_scoped(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Smart It Frame", owner_id="someone-else")
            db.commit()
            filled = fill_entity_gaps(
                {"company": None}, db=db, owner_id=OWNER, subject="Role at Smart It Frame", body=""
            )
        self.assertIsNone(filled["company"])

    def test_body_is_only_scanned_near_the_top(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Smart It Frame")
            db.commit()
            buried = ("unrelated filler " * 200) + " brought to you by Smart It Frame"
            deep = fill_entity_gaps(
                {"company": None}, db=db, owner_id=OWNER, subject="Hiring", body=buried
            )
            near = fill_entity_gaps(
                {"company": None},
                db=db,
                owner_id=OWNER,
                subject="Hiring",
                body="Smart It Frame is hiring a Java developer.",
            )
        self.assertIsNone(deep["company"])
        self.assertEqual(near["company"], "Smart It Frame")

    def test_works_with_no_model_available(self) -> None:
        """No embeddings, no network, no API key - the whole point."""
        with Session(self.engine) as db:
            self._entry(db, "Horizon Softech Inc")
            db.commit()
            filled = fill_entity_gaps(
                {"company": None},
                db=db,
                owner_id=OWNER,
                subject="Urgent: Java role",
                body="Horizon Softech Inc is looking for a developer.",
            )
        self.assertEqual(filled["company"], "Horizon Softech Inc")


if __name__ == "__main__":
    unittest.main()
