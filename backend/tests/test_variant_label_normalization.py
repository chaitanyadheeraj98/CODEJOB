import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import ResumeAsset
from app.services.resume_enrichment_service import normalize_variant_label


class NormalizeVariantLabelTests(unittest.TestCase):
    def test_the_extractors_domain_list_gets_one_consistent_casing(self):
        # Both of these were real labels in the live library, on resumes that were
        # otherwise the same. Only the casing differed, because the label is
        # whatever string the model happened to return that run.
        self.assertEqual(
            normalize_variant_label("banking, healthcare, telecom, EdTech"),
            "Banking, Healthcare, Telecom, EdTech",
        )
        self.assertEqual(
            normalize_variant_label("Banking, Healthcare, Telecom, EdTech"),
            "Banking, Healthcare, Telecom, EdTech",
        )

    def test_a_word_with_an_inner_capital_is_left_alone(self):
        # Not in the canonical map, so these survive on the inner-capital rule
        # alone: title-casing them would be a downgrade, not a fix.
        self.assertEqual(
            normalize_variant_label("J2EE, PostgreSQL, macOS"),
            "J2EE, PostgreSQL, macOS",
        )

    def test_a_domain_word_gets_its_accepted_spelling(self):
        # "edtech" title-cased to "Edtech" would sit beside a resume already
        # labelled "EdTech" - the exact inconsistency this is meant to remove.
        self.assertEqual(normalize_variant_label("banking, edtech, saas, iot"), "Banking, EdTech, SaaS, IoT")

    def test_it_capitalises_across_separators_inside_a_segment(self):
        self.assertEqual(normalize_variant_label("healthcare/insurance claims"), "Healthcare/Insurance Claims")
        self.assertEqual(normalize_variant_label("banking and financial services"), "Banking and Financial Services")

    def test_spacing_is_normalised_and_empty_segments_dropped(self):
        self.assertEqual(normalize_variant_label("  banking ,, telecom  "), "Banking, Telecom")

    def test_it_is_safe_on_nothing_at_all(self):
        self.assertEqual(normalize_variant_label(""), "")
        self.assertEqual(normalize_variant_label(None), "")

    def test_the_result_still_fits_the_column(self):
        # ResumeAsset.variant_label is String(120); a long model answer must not
        # blow past it now that the value is rewritten on the way in.
        self.assertLessEqual(len(normalize_variant_label("domain " * 200)), 120)

    def test_it_is_idempotent(self):
        once = normalize_variant_label("banking, healthcare, EdTech")
        self.assertEqual(normalize_variant_label(once), once)


class VariantLabelWritePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        with self.SessionLocal() as db:
            db.add(
                ResumeAsset(
                    owner_id=main.settings.owner_id,
                    file_path="/tmp/resume.pdf",
                    file_name="resume.pdf",
                    mime_type="application/pdf",
                    sha256="0" * 64,
                    version=1,
                    skills_text="Java",
                    variant_label="banking, healthcare, telecom, EdTech",
                    is_enabled=True,
                    is_current=True,
                )
            )
            db.commit()
            self.resume_id = db.query(ResumeAsset.id).scalar()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()

    def test_patch_stores_the_normalised_label(self):
        response = self.client.patch(
            f"/settings/resumes/{self.resume_id}",
            json={"variant_label": "banking, financial services, EdTech"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["variant_label"], "Banking, Financial Services, EdTech")
        # Stored, not merely rendered - the next reader sees the clean value too.
        with self.SessionLocal() as db:
            stored = db.get(ResumeAsset, self.resume_id)
            self.assertEqual(stored.variant_label, "Banking, Financial Services, EdTech")

    def test_a_label_can_still_be_cleared(self):
        response = self.client.patch(f"/settings/resumes/{self.resume_id}", json={"variant_label": "  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["variant_label"], "")


if __name__ == "__main__":
    unittest.main()
