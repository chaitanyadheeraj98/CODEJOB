"""`company_source` / `location_source` must describe every outcome, not just fills.

Before this, only `fill_entity_gaps`' own fills were labelled. NULL therefore
collapsed two opposite cases together - "the parser extracted this" and "there is
nothing here" - and the column inverted: on 39 production rows ingested after
provenance shipped, 30 had a real company and none were labelled, while the single
labelled location was "IN", a two-letter fragment the taxonomy had filled.

The table below is the contract. Each record states its inputs and the exact value
*and* label expected out, so a change in either is a failing assertion rather than
a silent drift. Three properties are under test:

  * extracted values are labelled `extracted` and never overwritten;
  * a placeholder is a gap, not a value - it stays unlabelled unless filled;
  * a taxonomy fill still reads `taxonomy_matched`, unchanged by this work.
"""

import json
import os
import unittest
from dataclasses import dataclass, field

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
    LOCATION_PLACEHOLDERS,
    clear_role_taxonomy_cache,
    fill_entity_gaps,
)

OWNER = "owner-under-test"


@dataclass(frozen=True)
class Record:
    """One controlled input with its fully specified expected output."""

    name: str
    company_in: str | None
    location_in: str | None
    subject: str
    expected_company: str | None
    expected_company_source: str | None
    expected_location: str
    expected_location_source: str | None
    approved: tuple[tuple[str, str], ...] = field(default=())


# Drawn from real shapes seen in production ingest, with the taxonomy state that
# makes each case distinct. "Smart It Frame" is approved so cases 4-6 can exercise
# the matcher without depending on the live vocabulary.
RECORDS: tuple[Record, ...] = (
    Record(
        name="both extracted",
        company_in="Vyze Inc",
        location_in="Coppell, TX, US",
        subject="Java Developer at Vyze",
        expected_company="Vyze Inc",
        expected_company_source=RoleSource.EXTRACTED,
        expected_location="Coppell, TX, US",
        expected_location_source=RoleSource.EXTRACTED,
    ),
    Record(
        name="location only, company genuinely absent",
        company_in=None,
        location_in="New York",
        subject="Backend Engineer",
        expected_company=None,
        expected_company_source=None,
        expected_location="New York",
        expected_location_source=RoleSource.EXTRACTED,
    ),
    Record(
        name="placeholder location stays a gap",
        company_in=None,
        location_in="unknown",
        subject="Backend Engineer",
        expected_company=None,
        expected_company_source=None,
        expected_location="unknown",
        expected_location_source=None,
    ),
    Record(
        name="empty location is a gap, not an extraction",
        company_in="Vyze Inc",
        location_in="",
        subject="Java Developer",
        expected_company="Vyze Inc",
        expected_company_source=RoleSource.EXTRACTED,
        expected_location="",
        expected_location_source=None,
    ),
    Record(
        name="taxonomy fills an absent company",
        company_in=None,
        location_in="Austin, TX",
        subject="Java Developer at Smart It Frame",
        expected_company="Smart It Frame",
        expected_company_source=RoleSource.TAXONOMY_MATCHED,
        expected_location="Austin, TX",
        expected_location_source=RoleSource.EXTRACTED,
    ),
    Record(
        name="extraction wins over an available match, and is labelled extracted",
        company_in="Vyze Inc",
        location_in="Coppell, TX",
        subject="Java Developer at Smart It Frame",
        expected_company="Vyze Inc",
        expected_company_source=RoleSource.EXTRACTED,
        expected_location="Coppell, TX",
        expected_location_source=RoleSource.EXTRACTED,
    ),
)


class EntityProvenanceLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

    def _approve(self, db: Session, canonical: str, entity_type: str) -> None:
        db.add(
            CanonicalEntityTaxonomyEntry(
                owner_id=OWNER,
                entity_type=entity_type,
                canonical_name=canonical,
                aliases_json=json.dumps([]),
                occurrence_count=5,
                status="approved",
            )
        )
        db.flush()

    def _run(self, db: Session, record: Record) -> dict[str, str | None]:
        return fill_entity_gaps(
            {"company": record.company_in, "end_client": None},
            db=db,
            owner_id=OWNER,
            subject=record.subject,
            body="",
            location=record.location_in,
        )

    def test_each_record_produces_its_expected_value_and_label(self) -> None:
        with Session(self.engine) as db:
            self._approve(db, "Smart It Frame", COMPANY_ENTITY_TYPE)
            db.commit()
            for record in RECORDS:
                with self.subTest(record.name):
                    filled = self._run(db, record)

                    self.assertEqual(filled.get("company"), record.expected_company)
                    self.assertEqual(
                        filled.get("company_source"), record.expected_company_source
                    )
                    self.assertEqual(filled.get("location"), record.expected_location)
                    self.assertEqual(
                        filled.get("location_source"), record.expected_location_source
                    )

    def test_null_now_means_exactly_one_thing(self) -> None:
        """The property the whole change exists for.

        Across the table, a NULL source must never sit beside a real value. If it
        does, the ambiguity that made the column unusable is back.
        """
        with Session(self.engine) as db:
            self._approve(db, "Smart It Frame", COMPANY_ENTITY_TYPE)
            db.commit()
            for record in RECORDS:
                with self.subTest(record.name):
                    filled = self._run(db, record)

                    if (filled.get("company") or "").strip():
                        self.assertIsNotNone(
                            filled.get("company_source"),
                            "a real company must never carry a NULL source",
                        )
                    if (filled.get("location") or "").strip().lower() not in LOCATION_PLACEHOLDERS:
                        self.assertIsNotNone(
                            filled.get("location_source"),
                            "a real location must never carry a NULL source",
                        )

    def test_a_taxonomy_filled_location_is_still_labelled_matched(self) -> None:
        """Guards the pre-existing behaviour this change must not disturb."""
        clear_role_taxonomy_cache()
        with Session(self.engine) as db:
            self._approve(db, "Jersey City, NJ", LOCATION_ENTITY_TYPE)
            db.commit()
            filled = fill_entity_gaps(
                {"company": None, "end_client": None},
                db=db,
                owner_id=OWNER,
                subject="Java Developer in Jersey City, NJ",
                body="",
                location="unknown",
            )

        self.assertEqual(filled["location"], "Jersey City, NJ")
        self.assertEqual(filled["location_source"], RoleSource.TAXONOMY_MATCHED)


if __name__ == "__main__":
    unittest.main()
