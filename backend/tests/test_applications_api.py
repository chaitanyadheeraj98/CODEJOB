import os
import unittest
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import Application, PremiumNumberContact, RecruiterOpportunity, ResumeAsset
from app.services import application_service


class ApplicationsApiTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    @staticmethod
    def _sources(
        db: Session,
        *,
        owner_id: str,
        suffix: str,
    ) -> tuple[ResumeAsset, RecruiterOpportunity]:
        resume = ResumeAsset(
            owner_id=owner_id,
            file_path=f"missing-{suffix}.pdf",
            file_name=f"{suffix}-resume.pdf",
            sha256=(suffix[0] if suffix else "a") * 64,
            version=2,
        )
        recruiter = PremiumNumberContact(
            owner_id=owner_id,
            normalized_phone_number=f"1214555{suffix[-4:].zfill(4)}",
            display_phone_number=f"+1 214 555 {suffix[-4:].zfill(4)}",
            is_recruiter=True,
            recruiter_name=f"Recruiter {suffix}",
            company=f"Agency {suffix}",
        )
        db.add_all([resume, recruiter])
        db.flush()
        opportunity = RecruiterOpportunity(
            owner_id=owner_id,
            recruiter_number_id=recruiter.id,
            gmail_message_id=f"application-api-{suffix}",
            job_title=f"Java Developer {suffix}",
            end_client=f"Client {suffix}",
        )
        db.add(opportunity)
        db.commit()
        db.refresh(resume)
        db.refresh(opportunity)
        return resume, opportunity

    def test_full_crud_status_timeline_and_summary(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="1001",
            )

        created = self.client.post(
            "/applications",
            json={"resume_asset_id": resume.id, "recruiter_opportunity_id": opportunity.id},
        )
        self.assertEqual(created.status_code, 201, created.text)
        application_id = created.json()["id"]
        self.assertEqual(created.json()["resume_file_name_snapshot"], "1001-resume.pdf")
        self.assertEqual(created.json()["current_job_title"], "Java Developer 1001")
        self.assertEqual(created.json()["events"], [])

        listing = self.client.get("/applications", params={"q": "java", "resume_asset_id": resume.id})
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual([item["id"] for item in listing.json()["items"]], [application_id])
        self.assertEqual(listing.json()["items"][0]["events"], [])

        detail = self.client.get(f"/applications/{application_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual([event["event_type"] for event in detail.json()["events"]], ["created"])

        invalid = self.client.patch(f"/applications/{application_id}", json={"status": "not-a-status"})
        self.assertEqual(invalid.status_code, 422, invalid.text)

        next_action_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        updated = self.client.patch(
            f"/applications/{application_id}",
            json={
                "status": "contacted",
                "next_action_type": "Follow up",
                "next_action_at": next_action_at,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["status"], "contacted")
        self.assertEqual(updated.json()["next_action_type"], "Follow up")

        note = self.client.post(
            f"/applications/{application_id}/events",
            json={"event_type": "note", "note": "Recruiter requested an updated summary."},
        )
        self.assertEqual(note.status_code, 201, note.text)

        summary = self.client.get("/applications/dashboard-summary")
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["due_today"], 1)
        self.assertEqual(summary.json()["waiting_on_recruiter"], 1)

        detail = self.client.get(f"/applications/{application_id}")
        self.assertEqual(
            [event["event_type"] for event in detail.json()["events"]],
            ["created", "status_changed", "next_action_set", "note"],
        )

        deleted = self.client.delete(f"/applications/{application_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertEqual(self.client.get(f"/applications/{application_id}").status_code, 404)
        self.assertEqual(self.client.get("/applications").json()["items"], [])

    def test_duplicate_and_cross_owner_sources_are_rejected(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="2001",
            )
            other_resume, other_opportunity = self._sources(
                db,
                owner_id="other-owner",
                suffix="2002",
            )
            resume_id = resume.id
            opportunity_id = opportunity.id
            other_opportunity_id = other_opportunity.id
            other_application = application_service.create_application(
                db,
                owner_id="other-owner",
                resume_asset_id=other_resume.id,
                recruiter_opportunity_id=other_opportunity.id,
            )
            db.commit()
            other_application_id = other_application.id

        payload = {"resume_asset_id": resume_id, "recruiter_opportunity_id": opportunity_id}
        self.assertEqual(self.client.post("/applications", json=payload).status_code, 201)
        duplicate = self.client.post("/applications", json=payload)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        cross_owner = self.client.post(
            "/applications",
            json={"resume_asset_id": resume_id, "recruiter_opportunity_id": other_opportunity_id},
        )
        self.assertEqual(cross_owner.status_code, 404, cross_owner.text)
        self.assertEqual(self.client.get(f"/applications/{other_application_id}").status_code, 404)

    def test_active_application_guards_source_deletion_and_snapshots_survive_after_close(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="3001",
            )

        created = self.client.post(
            "/applications",
            json={"resume_asset_id": resume.id, "recruiter_opportunity_id": opportunity.id},
        )
        self.assertEqual(created.status_code, 201, created.text)
        application_id = created.json()["id"]

        resume_delete = self.client.delete(f"/settings/resumes/{resume.id}")
        self.assertEqual(resume_delete.status_code, 409, resume_delete.text)
        opportunity_delete = self.client.delete(f"/recruiter-opportunities/{opportunity.id}")
        self.assertEqual(opportunity_delete.status_code, 409, opportunity_delete.text)

        closed = self.client.patch(f"/applications/{application_id}", json={"status": "hired"})
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertEqual(self.client.delete(f"/settings/resumes/{resume.id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/recruiter-opportunities/{opportunity.id}").status_code, 200)

        preserved = self.client.get(f"/applications/{application_id}")
        self.assertEqual(preserved.status_code, 200, preserved.text)
        self.assertEqual(preserved.json()["resume_file_name_snapshot"], "3001-resume.pdf")
        self.assertEqual(preserved.json()["job_title_snapshot"], "Java Developer 3001")
        self.assertEqual(preserved.json()["current_job_title"], "")


if __name__ == "__main__":
    unittest.main()
