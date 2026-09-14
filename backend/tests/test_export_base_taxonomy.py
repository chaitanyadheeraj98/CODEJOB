import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import CanonicalEntityTaxonomyEntry, JobIntentTaxonomyEntry
from scripts.export_base_taxonomy import export_base_taxonomy


def test_export_is_approved_owner_scoped_reviewable_and_versioned(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            CanonicalEntityTaxonomyEntry(
                owner_id="source",
                entity_type="role",
                canonical_name="Data Engineer",
                aliases_json=json.dumps(["Data Engineering", " data engineering ", "Data Engineer"]),
                embedding_json="[0.1, 0.2]",
                status="approved",
            ),
            CanonicalEntityTaxonomyEntry(
                owner_id="source",
                entity_type="role",
                canonical_name="Rejected Role",
                status="dismissed",
            ),
            CanonicalEntityTaxonomyEntry(
                owner_id="other",
                entity_type="company",
                canonical_name="Private Company",
                status="approved",
            ),
            JobIntentTaxonomyEntry(
                owner_id="source",
                phrase="  Direct hire role!  ",
                normalized_phrase="stale value",
                polarity="positive_recruiter_jd",
                confidence_aggregate=1.2,
                sample_evidence_json='["private@example.com"]',
                status="approved",
            ),
            JobIntentTaxonomyEntry(
                owner_id="source",
                phrase="Dismissed signal",
                normalized_phrase="dismissed signal",
                polarity="negative_newsletter",
                status="dismissed",
            ),
            JobIntentTaxonomyEntry(
                owner_id="source",
                phrase="Share resumes at recruiter@example.com",
                normalized_phrase="share resumes at recruiter example com",
                polarity="negative_candidate_hotlist",
                status="approved",
            ),
        ])
        db.commit()
        counts = export_base_taxonomy(db, owner_id="source", output_dir=tmp_path)
        export_base_taxonomy(db, owner_id="source", output_dir=tmp_path)

    roles = json.loads((tmp_path / "roles.json").read_text(encoding="utf-8"))
    intents = json.loads((tmp_path / "job_intent.json").read_text(encoding="utf-8"))
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))

    assert counts == {"role": 1, "company": 0, "location": 0, "job_intent": 1}
    assert roles["entries"] == [{
        "aliases": ["Data Engineering"],
        "canonical_name": "Data Engineer",
        "entity_type": "role",
    }]
    assert intents["entries"] == [{
        "confidence": 1.0,
        "normalized_phrase": "direct hire role",
        "phrase": "Direct hire role!",
        "polarity": "positive_recruiter_jd",
    }]
    assert (tmp_path / "VERSION").read_text(encoding="utf-8") == "2\n"
    for secret in (
        "source",
        "other",
        "private@example.com",
        "recruiter@example.com",
        "[0.1, 0.2]",
        "Rejected Role",
    ):
        assert secret not in serialized
    engine.dispose()
