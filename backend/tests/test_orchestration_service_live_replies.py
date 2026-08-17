import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import EmailConversation, RecruiterEmail
from app.services.orchestration_service import OrchestrationService


class OrchestrationServiceLiveRepliesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_count_live_unread_replies_only_counts_sent_threads(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecruiterEmail(
                    owner_id="default-owner",
                    sender="recruiter@example.com",
                    subject="Role",
                    body="body",
                    sent_status="sent",
                    external_thread_id="t-sent",
                )
            )
            db.add(
                EmailConversation(
                    owner_id="default-owner",
                    root_recruiter_email_id=0,
                    external_thread_id="t-conv",
                )
            )
            db.commit()

            deps = SimpleNamespace(
                owner_id="default-owner",
                list_unread_thread_ids=lambda: {"t-sent", "t-conv", "t-noise-newsletter"},
            )
            count = OrchestrationService(deps).count_live_unread_replies(db)

        self.assertEqual(count, 2)

    def test_count_live_unread_replies_zero_when_unread_mail_is_not_a_sent_thread(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecruiterEmail(
                    owner_id="default-owner",
                    sender="recruiter@example.com",
                    subject="Role",
                    body="body",
                    sent_status="sent",
                    external_thread_id="t-sent",
                )
            )
            db.commit()

            deps = SimpleNamespace(
                owner_id="default-owner",
                list_unread_thread_ids=lambda: {"t-noise-newsletter", "t-promo"},
            )
            count = OrchestrationService(deps).count_live_unread_replies(db)

        self.assertEqual(count, 0)

    def test_count_live_unread_replies_returns_zero_when_dep_missing(self) -> None:
        with Session(self.engine) as db:
            deps = SimpleNamespace(owner_id="default-owner", list_unread_thread_ids=None)
            count = OrchestrationService(deps).count_live_unread_replies(db)

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
