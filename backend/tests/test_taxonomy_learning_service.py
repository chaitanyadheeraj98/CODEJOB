import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import CustomSkillTaxonomyEntry, RecruiterEmail
from app.services.taxonomy_import_service import analyze_import, load_import_candidates
from app.services.taxonomy_learning_service import embed_pending_skills, list_pending_entities, upsert_entity


class TaxonomyLearningServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _email(self, *, sender: str, company: str, location: str) -> RecruiterEmail:
        return RecruiterEmail(
            owner_id="default-owner",
            sender=sender,
            subject="Role",
            body="Body",
            role="Engineer",
            location=location,
            salary_text="",
            skills_text="Java",
            score=0,
            decision="qualified",
            state="needs_review",
            source="gmail",
            parser_details_json=json.dumps(
                {
                    "ai_extractor_result": {
                        "company": company,
                        "primary_location": location,
                        "mentioned_locations": [location],
                    }
                }
            ),
        )

    def test_company_and_location_candidates_aggregate_and_suppress_aliases(self) -> None:
        with self.SessionLocal() as db:
            db.add_all(
                [
                    self._email(sender="one@example.com", company="Acme Corp", location="New York, NY"),
                    self._email(sender="two@example.com", company="Acme Corp", location="New York, NY"),
                ]
            )
            db.commit()

            companies = list_pending_entities(db, owner_id="default-owner", entity_type="company")
            locations = list_pending_entities(db, owner_id="default-owner", entity_type="location")
            self.assertEqual(companies[0]["occurrence_count"], 2)
            self.assertEqual(locations[0]["occurrence_count"], 2)

            upsert_entity(
                db,
                owner_id="default-owner",
                entity_type="company",
                display_name="Acme Corp",
                canonical_name="Acme Corporation",
                aliases=["Acme Corp"],
                occurrence_count=2,
                status="approved",
            )
            self.assertEqual(list_pending_entities(db, owner_id="default-owner", entity_type="company"), [])

    @patch("app.services.taxonomy_learning_service.generate_embeddings")
    @patch("app.services.taxonomy_learning_service.load_skill_taxonomy")
    def test_embedding_batch_classifies_and_marks_custom_skill_done(self, mock_taxonomy, mock_embeddings) -> None:
        reference = type(
            "Entry",
            (),
            {
                "id": "python",
                "canonical_name": "Python",
                "category": "programming_language",
                "cluster_id": "backend_languages",
                "match_tier": "role_defining",
                "weight": 1.4,
            },
        )()
        mock_taxonomy.return_value.entries = (reference,)
        mock_embeddings.return_value = ([[1.0, 0.0], [0.9, 0.1]], "hash")
        with self.SessionLocal() as db:
            row = CustomSkillTaxonomyEntry(
                owner_id="default-owner",
                canonical_name="New Python Tool",
                category="custom",
                occurrence_count=3,
                embedding_status="pending",
                status="approved",
            )
            db.add(row)
            db.commit()

            result = embed_pending_skills(db, owner_id="default-owner")
            db.refresh(row)

            self.assertEqual(result["embedded_count"], 1)
            self.assertEqual(row.embedding_status, "done")
            self.assertEqual(row.category, "programming_language")
            self.assertEqual(row.cluster_hint, "backend_languages")

    def test_import_analysis_routes_alias_collisions_to_review(self) -> None:
        payload = [
            {"name": "New Workflow", "aliases": ["Java"]},
            {"name": "Unique Workflow", "aliases": ["Unique Flow"]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "taxonomy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            accepted, collisions = analyze_import(load_import_candidates(path))

        self.assertEqual([item.canonical_name for item in accepted], ["Unique Workflow"])
        self.assertEqual(collisions[0].alias, "Java")


if __name__ == "__main__":
    unittest.main()
