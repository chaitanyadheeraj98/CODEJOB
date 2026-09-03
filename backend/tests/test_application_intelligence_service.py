import json
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationInterview,
    ApplicationSuggestion,
    EmailConversation,
    EmailReplyMessage,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    UserSettings,
)
from app.services import application_intelligence_service as service
from app.skill_taxonomy import role_family_fit_score


class ApplicationIntelligenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _application(
        application_id: int,
        *,
        recruiter_id: int = 1,
        status: str = "contacted",
        status_changed_at: datetime | None = None,
        submitted: bool = False,
        owner_id: str = "owner",
    ) -> Application:
        now = status_changed_at or datetime.now(UTC)
        return Application(
            id=application_id,
            owner_id=owner_id,
            resume_asset_id=application_id,
            resume_version_snapshot=1,
            resume_file_name_snapshot=f"resume-{application_id}.pdf",
            resume_sha256_snapshot=str(application_id % 10) * 64,
            recruiter_opportunity_id=application_id,
            recruiter_contact_id=recruiter_id,
            job_title_snapshot="Java Developer",
            end_client_snapshot="Bank X",
            status=status,
            status_changed_at=now,
            submitted_to_client_at=now if submitted else None,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _status_event(application_id: int, target: str, occurred_at: datetime) -> ApplicationEvent:
        return ApplicationEvent(
            owner_id="owner",
            application_id=application_id,
            event_type="status_changed",
            metadata_json=json.dumps({"to": target}),
            occurred_at=occurred_at,
            created_at=occurred_at,
        )

    def test_role_family_fit_score_matches_extracted_runtime_logic(self) -> None:
        self.assertEqual(
            role_family_fit_score(
                jd_role_family="java_backend",
                resume_role_family="ai",
                role_alignment_score=0.4,
                foundation_score=0.7,
                jd_priority_score=0.6,
            ),
            (0.72, "ai_enabled_fullstack_override"),
        )

    def test_reputation_uses_history_and_labels_small_samples(self) -> None:
        monday = datetime(2026, 8, 3, 9, tzinfo=UTC)
        with Session(self.engine) as db:
            applications = [
                self._application(1, status="rejected", submitted=True),
                self._application(2, status="client_reviewing", submitted=True),
                self._application(3, status="contacted"),
            ]
            db.add_all(applications)
            db.add_all(
                [
                    self._status_event(1, "contacted", monday),
                    self._status_event(1, "recruiter_responded", monday + timedelta(days=2)),
                    self._status_event(1, "offer", monday + timedelta(days=3)),
                    self._status_event(2, "contacted", monday),
                    ApplicationEvent(
                        owner_id="owner",
                        application_id=2,
                        event_type="recruiter_replied",
                        metadata_json="{}",
                        occurred_at=monday + timedelta(days=1),
                        created_at=monday + timedelta(days=1),
                    ),
                ]
            )
            db.add(ApplicationInterview(owner_id="owner", application_id=1, result="completed"))
            db.commit()

            reputation = service.compute_recruiter_reputation(
                db,
                owner_id="owner",
                recruiter_contact_id=1,
            )
            self.assertEqual(reputation.history_label, "established")
            self.assertEqual(reputation.outreach_count, 3)
            self.assertEqual(reputation.replies_count, 2)
            self.assertEqual(reputation.median_first_reply_business_days, 1.5)
            self.assertEqual(reputation.submissions_count, 2)
            self.assertEqual(reputation.interviews_after_submission_count, 1)
            self.assertEqual(reputation.offers_count, 1)

            applications[2].status = "matched"
            db.commit()
            limited = service.compute_recruiter_reputation(db, owner_id="owner", recruiter_contact_id=1)
            self.assertEqual(limited.history_label, "limited_history")

    def test_ranking_filters_risk_flags_applied_rows_and_explains_scores(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            db.add(
                UserSettings(
                    owner_id="owner",
                    remote_preference="remote",
                    candidate_work_authorizations_json='["USC"]',
                    preferred_employment_types_json='["W2"]',
                    preferred_minimum_rate=80,
                )
            )
            resume = ResumeAsset(
                owner_id="owner",
                file_path="resume.pdf",
                file_name="resume.pdf",
                sha256="a" * 64,
                skills_text="Java, Spring Boot, SQL",
            )
            good = PremiumNumberContact(
                owner_id="owner",
                normalized_phone_number="12145550001",
                display_phone_number="+1 214 555 0001",
                is_recruiter=True,
            )
            unknown = PremiumNumberContact(
                owner_id="owner",
                normalized_phone_number="12145550002",
                display_phone_number="+1 214 555 0002",
                is_recruiter=True,
            )
            blocked = PremiumNumberContact(
                owner_id="owner",
                normalized_phone_number="12145550003",
                display_phone_number="+1 214 555 0003",
                is_recruiter=True,
                do_not_work_again=True,
            )
            db.add_all([resume, good, unknown, blocked])
            db.flush()
            opportunities = [
                RecruiterOpportunity(
                    owner_id="owner",
                    recruiter_number_id=good.id,
                    gmail_message_id="good",
                    job_title="Java Developer",
                    extracted_skills="Java, Spring Boot, SQL",
                    work_mode="Remote",
                    visa_restrictions="USC only",
                    employment_type="W2",
                    rate_amount=100,
                    job_confidence="high",
                    end_client_confirmed=True,
                    received_at=now,
                ),
                RecruiterOpportunity(
                    owner_id="owner",
                    recruiter_number_id=unknown.id,
                    gmail_message_id="unknown",
                    job_title="Backend Engineer",
                    extracted_skills="Java",
                    work_mode="",
                    visa_restrictions="",
                    job_confidence="unknown",
                    received_at=None,
                ),
                RecruiterOpportunity(
                    owner_id="owner",
                    recruiter_number_id=blocked.id,
                    gmail_message_id="blocked",
                    job_title="Java Developer",
                    extracted_skills="Java, Spring Boot, SQL",
                    received_at=now,
                ),
            ]
            db.add_all(opportunities)
            db.flush()
            db.add(
                Application(
                    owner_id="owner",
                    resume_asset_id=resume.id,
                    resume_version_snapshot=1,
                    resume_file_name_snapshot=resume.file_name,
                    resume_sha256_snapshot=resume.sha256,
                    recruiter_opportunity_id=opportunities[1].id,
                    recruiter_contact_id=unknown.id,
                    status="matched",
                )
            )
            db.commit()

            default_matches = service.rank_opportunities_for_resume(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
            )
            self.assertEqual([item.opportunity_id for item in default_matches], [opportunities[0].id])
            all_matches = service.rank_opportunities_for_resume(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
                exclude_already_applied=False,
            )
            self.assertEqual(all_matches[0].opportunity_id, opportunities[0].id)
            self.assertNotIn(opportunities[2].id, [item.opportunity_id for item in all_matches])
            self.assertTrue(all(item.score > 0 and item.reasons for item in all_matches))
            self.assertEqual([item.score for item in all_matches], sorted((item.score for item in all_matches), reverse=True))

    def test_status_signal_correlation_is_owner_scoped_and_idempotent(self) -> None:
        self.assertEqual(service.detect_status_change_signal("Let's schedule a call"), ("interview_1", "schedule a call"))
        self.assertEqual(
            service.detect_status_change_signal("We can schedule a call, but the position has been filled"),
            ("rejected", "position has been filled"),
        )
        self.assertIsNone(service.detect_status_change_signal("Thanks for the update"))

        with Session(self.engine) as db:
            root = RecruiterEmail(
                owner_id="owner",
                sender="recruiter@example.com",
                subject="Role",
                body="JD",
                external_message_id="root",
                sent_status="sent",
            )
            recruiter = PremiumNumberContact(
                owner_id="owner",
                normalized_phone_number="12145550004",
                display_phone_number="+1 214 555 0004",
                is_recruiter=True,
            )
            db.add_all([root, recruiter])
            db.flush()
            opportunity = RecruiterOpportunity(
                owner_id="owner",
                recruiter_number_id=recruiter.id,
                source_email_id=root.id,
                gmail_message_id="correlation",
                job_title="Java Developer",
            )
            db.add(opportunity)
            db.flush()
            application = self._application(10, recruiter_id=recruiter.id)
            application.recruiter_opportunity_id = opportunity.id
            conversation = EmailConversation(
                owner_id="owner",
                root_recruiter_email_id=root.id,
                external_thread_id="thread",
            )
            db.add_all([application, conversation])
            db.flush()
            reply = EmailReplyMessage(
                owner_id="owner",
                conversation_id=conversation.id,
                direction="inbound",
                external_message_id="reply",
                body="We would like to set up an interview",
                snippet="Set up an interview",
            )
            db.add(reply)
            db.commit()

            suggestion = service.correlate_reply_to_application(db, owner_id="owner", reply_message_id=reply.id)
            db.flush()
            self.assertIsNotNone(suggestion)
            self.assertEqual(suggestion.suggested_status, "interview_1")
            self.assertEqual(
                db.query(ApplicationEvent).filter_by(application_id=application.id, event_type="recruiter_replied").count(),
                1,
            )
            self.assertIsNone(service.correlate_reply_to_application(db, owner_id="owner", reply_message_id=reply.id))
            db.flush()
            self.assertEqual(db.query(ApplicationSuggestion).filter_by(application_id=application.id).count(), 1)
            self.assertEqual(
                db.query(ApplicationEvent).filter_by(application_id=application.id, event_type="recruiter_replied").count(),
                1,
            )
            self.assertIsNone(service.correlate_reply_to_application(db, owner_id="other", reply_message_id=reply.id))

    def test_reminder_sweep_covers_rules_without_mutating_applications(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            contacted = self._application(21, status="contacted", status_changed_at=now - timedelta(days=7))
            submitted = self._application(22, status="submitted_to_client", status_changed_at=now - timedelta(days=10))
            interview = self._application(23, status="interview_1", status_changed_at=now - timedelta(days=2))
            stale = self._application(24, status="matched", status_changed_at=now - timedelta(days=22))
            closed = self._application(25, status="rejected", status_changed_at=now - timedelta(days=30))
            db.add_all([contacted, submitted, interview, stale, closed])
            db.add(
                ApplicationInterview(
                    owner_id="owner",
                    application_id=interview.id,
                    result="completed",
                    updated_at=now - timedelta(days=3),
                )
            )
            db.commit()

            created = service.generate_reminder_sweep_suggestions(db, owner_id="owner")
            db.flush()
            self.assertEqual([row.suggestion_type for row in created].count("next_action"), 3)
            self.assertEqual([row.suggestion_type for row in created].count("stale_prompt"), 1)
            self.assertEqual({row.application_id for row in created}, {21, 22, 23, 24})
            self.assertEqual(contacted.next_action_type, None)
            self.assertEqual(contacted.status, "contacted")
            self.assertEqual(service.generate_reminder_sweep_suggestions(db, owner_id="owner"), [])


if __name__ == "__main__":
    unittest.main()
