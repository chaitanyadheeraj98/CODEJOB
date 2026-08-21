import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import (
    Application,
    ApplicationSuggestion,
    AttachmentAsset,
    PremiumNumberContact,
    RecruiterOpportunity,
    ResumeAsset,
    UserSettings,
)
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

    def test_rtr_and_interview_crud_with_proof_and_validation(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="4001",
            )
            attachment = AttachmentAsset(
                owner_id=main.settings.owner_id,
                file_path="rtr-proof.pdf",
                file_name="rtr-proof.pdf",
                sha256="e" * 64,
                file_size=12,
            )
            db.add(attachment)
            db.commit()
            attachment_id = attachment.id
            resume_id = resume.id
            opportunity_id = opportunity.id

        created = self.client.post(
            "/applications",
            json={"resume_asset_id": resume_id, "recruiter_opportunity_id": opportunity_id},
        )
        application_id = created.json()["id"]
        requested = self.client.post(
            f"/applications/{application_id}/rtr",
            json={
                "role_scope": "Senior Java Developer",
                "end_client_scope": "Client 4001",
            },
        )
        self.assertEqual(requested.status_code, 201, requested.text)
        self.assertEqual(requested.json()["status"], "rtr_requested")
        rtr_id = requested.json()["rtr_history"][0]["id"]

        no_proof = self.client.patch(
            f"/applications/{application_id}/rtr/{rtr_id}",
            json={"status": "confirmed"},
        )
        self.assertEqual(no_proof.status_code, 422, no_proof.text)
        invalid_rtr = self.client.patch(
            f"/applications/{application_id}/rtr/{rtr_id}",
            json={"status": "not-valid"},
        )
        self.assertEqual(invalid_rtr.status_code, 422, invalid_rtr.text)
        confirmed = self.client.patch(
            f"/applications/{application_id}/rtr/{rtr_id}",
            json={"status": "confirmed", "proof_attachment_id": attachment_id},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["status"], "rtr_confirmed")
        self.assertEqual(confirmed.json()["rtr_history"][0]["proof_attachment_id"], attachment_id)

        invalid_round = self.client.post(
            f"/applications/{application_id}/interviews",
            json={"round_type": "interview_9"},
        )
        self.assertEqual(invalid_round.status_code, 422, invalid_round.text)
        added = self.client.post(
            f"/applications/{application_id}/interviews",
            json={
                "round_type": "interview_2",
                "format": "video",
                "interviewer_names": "Alex",
            },
        )
        self.assertEqual(added.status_code, 201, added.text)
        self.assertEqual(added.json()["status"], "interview_2")
        interview_id = added.json()["interviews"][0]["id"]
        invalid_result = self.client.patch(
            f"/applications/{application_id}/interviews/{interview_id}",
            json={"result": "unknown"},
        )
        self.assertEqual(invalid_result.status_code, 422, invalid_result.text)
        updated = self.client.patch(
            f"/applications/{application_id}/interviews/{interview_id}",
            json={"result": "passed", "feedback": "Strong technical round"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["interviews"][0]["result"], "passed")
        self.assertEqual(updated.json()["interviews"][0]["feedback"], "Strong technical round")
        deleted = self.client.delete(f"/applications/{application_id}/interviews/{interview_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["interviews"], [])

        invalid_reason = self.client.patch(
            f"/applications/{application_id}",
            json={"status": "rejected", "closed_reason_code": "not-valid"},
        )
        self.assertEqual(invalid_reason.status_code, 422, invalid_reason.text)
        closed = self.client.patch(
            f"/applications/{application_id}",
            json={"status": "rejected", "closed_reason_code": "skills_gap"},
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertEqual(closed.json()["closed_reason_code"], "skills_gap")

    def test_submit_duplicate_warning_override_and_risk_field_patches(self) -> None:
        with Session(self.engine) as db:
            first_resume, first_opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="5001",
            )
            second_resume, second_opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="5002",
            )
            for opportunity in (first_opportunity, second_opportunity):
                opportunity.job_title = "Senior Java Developer"
                opportunity.end_client = "Bank X"
            recruiter_id = first_opportunity.recruiter_number_id
            db.commit()
            first_resume_id = first_resume.id
            first_opportunity_id = first_opportunity.id
            second_resume_id = second_resume.id
            second_opportunity_id = second_opportunity.id

        first = self.client.post(
            "/applications",
            json={
                "resume_asset_id": first_resume_id,
                "recruiter_opportunity_id": first_opportunity_id,
            },
        ).json()
        second = self.client.post(
            "/applications",
            json={
                "resume_asset_id": second_resume_id,
                "recruiter_opportunity_id": second_opportunity_id,
            },
        ).json()

        bypass = self.client.patch(
            f"/applications/{second['id']}",
            json={"status": "submitted_to_client"},
        )
        self.assertEqual(bypass.status_code, 422, bypass.text)
        warned = self.client.post(
            f"/applications/{second['id']}/submit-to-client",
            json={"override_duplicate_warning": False},
        )
        self.assertEqual(warned.status_code, 409, warned.text)
        self.assertEqual([row["id"] for row in warned.json()["detail"]["duplicates"]], [first["id"]])
        overridden = self.client.post(
            f"/applications/{second['id']}/submit-to-client",
            json={"override_duplicate_warning": True},
        )
        self.assertEqual(overridden.status_code, 200, overridden.text)
        self.assertEqual(overridden.json()["status"], "submitted_to_client")
        self.assertIn(
            "duplicate_override",
            [event["event_type"] for event in overridden.json()["events"]],
        )

        opportunity_patch = {
            "employment_type": "contract",
            "rate_amount": 85.5,
            "rate_currency": "USD",
            "rate_unit": "hour",
            "contract_duration": "12 months",
            "relocation_required": False,
            "extension_likely": "yes",
            "end_client_confirmed": True,
            "job_confidence": "high",
        }
        opportunity_response = self.client.patch(
            f"/recruiter-opportunities/{first_opportunity_id}",
            json=opportunity_patch,
        )
        self.assertEqual(opportunity_response.status_code, 200, opportunity_response.text)
        for key, value in opportunity_patch.items():
            self.assertEqual(opportunity_response.json()[key], value)

        recruiter_response = self.client.patch(
            f"/recruiter-numbers/{recruiter_id}",
            json={
                "recruiter_verification_level": "trusted",
                "do_not_work_again": True,
                "do_not_work_again_reason": "Repeated duplicate submissions",
            },
        )
        self.assertEqual(recruiter_response.status_code, 200, recruiter_response.text)
        self.assertEqual(recruiter_response.json()["recruiter_verification_level"], "trusted")
        self.assertTrue(recruiter_response.json()["do_not_work_again"])

    def test_phase_three_match_reputation_suggestions_and_reminder_routes(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="6001",
            )
            other_resume, _ = self._sources(db, owner_id="other-owner", suffix="6002")
            resume.skills_text = "Java, Spring Boot, SQL"
            opportunity.extracted_skills = "Java, Spring Boot, SQL"
            opportunity.work_mode = "Remote"
            opportunity.job_confidence = "high"
            opportunity.end_client_confirmed = True
            settings_row = UserSettings(
                owner_id=main.settings.owner_id,
                remote_preference="remote",
                feature_applications_enabled=True,
                feature_application_automation_enabled=True,
            )
            db.add(settings_row)
            db.commit()
            resume_id = resume.id
            opportunity_id = opportunity.id
            recruiter_id = opportunity.recruiter_number_id
            other_resume_id = other_resume.id

        matched = self.client.get(
            "/applications/match",
            params={"resume_asset_id": resume_id, "exclude_already_applied": False},
        )
        self.assertEqual(matched.status_code, 200, matched.text)
        self.assertEqual(matched.json()["items"][0]["opportunity"]["id"], opportunity_id)
        self.assertTrue(matched.json()["items"][0]["reasons"])
        self.assertEqual(
            self.client.get("/applications/match", params={"resume_asset_id": other_resume_id}).status_code,
            404,
        )

        created = self.client.post(
            "/applications",
            json={"resume_asset_id": resume_id, "recruiter_opportunity_id": opportunity_id},
        )
        self.assertEqual(created.status_code, 201, created.text)
        application_id = created.json()["id"]
        with Session(self.engine) as db:
            suggestion = ApplicationSuggestion(
                owner_id=main.settings.owner_id,
                application_id=application_id,
                suggestion_type="status_change",
                suggested_status="recruiter_responded",
                reason="Matched recruiter reply",
            )
            other_suggestion = ApplicationSuggestion(
                owner_id="other-owner",
                application_id=application_id,
                suggestion_type="status_change",
                suggested_status="rejected",
                reason="Other owner",
            )
            db.add_all([suggestion, other_suggestion])
            db.commit()
            suggestion_id = suggestion.id
            other_suggestion_id = other_suggestion.id

        suggestions = self.client.get("/applications/suggestions")
        self.assertEqual(suggestions.status_code, 200, suggestions.text)
        self.assertEqual([row["id"] for row in suggestions.json()["items"]], [suggestion_id])
        self.assertEqual(self.client.get("/applications/dashboard-summary").json()["pending_suggestions"], 1)
        accepted = self.client.post(f"/applications/suggestions/{suggestion_id}/accept", json={})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["status"], "recruiter_responded")
        self.assertEqual(self.client.post(f"/applications/suggestions/{suggestion_id}/accept", json={}).status_code, 404)
        self.assertEqual(self.client.post(f"/applications/suggestions/{other_suggestion_id}/dismiss").status_code, 404)

        with Session(self.engine) as db:
            application = db.get(Application, application_id)
            application.status = "contacted"
            application.status_changed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=7)
            application.next_action_at = None
            db.commit()
        reminders = self.client.post("/applications/reminders/run")
        self.assertEqual(reminders.status_code, 200, reminders.text)
        self.assertEqual(reminders.json()["items"][0]["suggestion_type"], "next_action")

        reputation = self.client.get(f"/recruiter-numbers/{recruiter_id}/reputation")
        self.assertEqual(reputation.status_code, 200, reputation.text)
        self.assertEqual(reputation.json()["recruiter_contact_id"], recruiter_id)
        self.assertEqual(reputation.json()["history_label"], "limited_history")

    def test_phase_four_draft_and_send_routes_require_explicit_send(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="7001",
            )
            application = application_service.create_application(
                db,
                owner_id=main.settings.owner_id,
                resume_asset_id=resume.id,
                recruiter_opportunity_id=opportunity.id,
            )
            db.add(UserSettings(owner_id=main.settings.owner_id))
            db.commit()
            application_id = application.id

        saved_settings = self.client.put(
            "/settings",
            json={
                "feature_applications_enabled": True,
                "feature_application_outreach_drafts_enabled": True,
            },
        )
        self.assertEqual(saved_settings.status_code, 200, saved_settings.text)
        self.assertTrue(saved_settings.json()["feature_application_outreach_drafts_enabled"])
        self.assertTrue(self.client.get("/settings").json()["feature_application_outreach_drafts_enabled"])

        draft_result = SimpleNamespace(
            to="recruiter@example.com",
            cc="employer@example.com",
            thread_id="thread-7001",
            subject="Following up - Java Developer 7001",
            body="Hi Recruiter,\n\nAny update?",
            source="ai_disabled",
            ai_model=None,
            ai_error=None,
            resume_context_status="injected",
            resume_file_name="7001-resume.pdf",
            message_kind="followup",
        )
        with patch(
            "app.main.application_outreach_service.build_application_draft",
            return_value=draft_result,
        ) as build:
            drafted = self.client.post(
                f"/applications/{application_id}/draft-message",
                json={"message_kind": "followup"},
            )
        self.assertEqual(drafted.status_code, 200, drafted.text)
        self.assertEqual(drafted.json()["source"], "ai_disabled")
        self.assertEqual(drafted.json()["to"], "recruiter@example.com")
        build.assert_called_once()
        self.assertEqual(
            self.client.post(
                f"/applications/{application_id}/draft-message",
                json={"message_kind": "invalid"},
            ).status_code,
            422,
        )

        def send_stub(_db, application, **_kwargs):
            return application, "gmail-message-7001"

        payload = {
            "to": "recruiter@example.com",
            "cc": "employer@example.com",
            "subject": "Following up",
            "body": "Any update?",
            "thread_id": "thread-7001",
            "message_kind": "followup",
            "include_resume": True,
            "attachment_asset_ids": [],
        }
        with patch(
            "app.main.application_outreach_service.send_application_message",
            side_effect=send_stub,
        ) as send:
            sent = self.client.post(
                f"/applications/{application_id}/send-message",
                json=payload,
            )
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertTrue(sent.json()["sent"])
        self.assertEqual(sent.json()["gmail_message_id"], "gmail-message-7001")
        self.assertEqual(sent.json()["application"]["status"], "matched")
        self.assertEqual(send.call_args.kwargs["thread_id"], "thread-7001")
        self.assertEqual(
            self.client.post(
                f"/applications/{application_id}/send-message",
                json={**payload, "to": "   "},
            ).status_code,
            422,
        )

    def test_phase_four_send_route_surfaces_gmail_failure(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(
                db,
                owner_id=main.settings.owner_id,
                suffix="7002",
            )
            application = application_service.create_application(
                db,
                owner_id=main.settings.owner_id,
                resume_asset_id=resume.id,
                recruiter_opportunity_id=opportunity.id,
            )
            db.commit()
            application_id = application.id

        with patch(
            "app.main.application_outreach_service.send_application_message",
            side_effect=RuntimeError("Gmail unavailable"),
        ):
            response = self.client.post(
                f"/applications/{application_id}/send-message",
                json={
                    "to": "recruiter@example.com",
                    "subject": "Following up",
                    "body": "Any update?",
                },
            )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("Gmail unavailable", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
