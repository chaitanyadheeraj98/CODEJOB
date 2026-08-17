import os
import unittest
from types import SimpleNamespace

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import ResumeAsset, UserSettings


class ResumeSelectionWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.original_scoring_runtime_service = main.scoring_runtime_service

    def tearDown(self) -> None:
        main.scoring_runtime_service = self.original_scoring_runtime_service
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _seed_user_settings(self, db: Session) -> UserSettings:
        user_settings = UserSettings(
            owner_id=main.settings.owner_id,
            enabled=True,
            gmail_query="is:unread",
            default_gmail_query="is:unread",
            default_date_mode="off",
            qualification_threshold=0.6,
            feature_ai_enabled=False,
            feature_semantic_enabled=False,
            fallback_draft_template="Hi",
            signature_name="Tester",
            signature_phone="+1",
            signature_email="tester@example.com",
            policy_json="",
        )
        db.add(user_settings)
        db.commit()
        db.refresh(user_settings)
        return user_settings

    def _seed_resume(
        self,
        db: Session,
        *,
        file_name: str,
        version: int,
        is_enabled: bool,
        is_current: bool,
    ) -> ResumeAsset:
        resume = ResumeAsset(
            owner_id=main.settings.owner_id,
            file_path=file_name,
            file_name=file_name,
            mime_type="application/pdf",
            sha256=f"sha-{file_name}",
            version=version,
            skills_text="java, spring",
            is_enabled=is_enabled,
            is_current=is_current,
            semantic_embedding=None,
        )
        db.add(resume)
        db.commit()
        db.refresh(resume)
        return resume

    def test_wrapper_forwards_explicit_resumes_and_fallback_resume(self) -> None:
        captured: dict[str, object] = {}

        class FakeScoringRuntime:
            def select_best_resume_match(self, **kwargs: object) -> object:
                captured.update(kwargs)
                return SimpleNamespace(resume=kwargs.get("fallback_resume"))

        main.scoring_runtime_service = FakeScoringRuntime()  # type: ignore[assignment]

        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db)
            current_resume = self._seed_resume(db, file_name="current.pdf", version=1, is_enabled=True, is_current=True)
            alt_resume = self._seed_resume(db, file_name="alt.pdf", version=2, is_enabled=True, is_current=False)

            result = main._select_best_resume_match(
                subject="Java role",
                body="Need Java and Spring",
                parsed={"role": "Java Developer", "skills_text": "Java, Spring"},
                user_settings=user_settings,
                email_row=None,
                resumes=[alt_resume],
                fallback_resume=current_resume,
                db=db,
                owner_id=main.settings.owner_id,
                external_thread_id="thread-1",
            )

            self.assertIs(result.resume, current_resume)
            self.assertEqual(captured["resumes"], [alt_resume])
            self.assertIs(captured["fallback_resume"], current_resume)

    def test_wrapper_falls_back_to_db_enabled_and_current_resumes(self) -> None:
        captured: dict[str, object] = {}

        class FakeScoringRuntime:
            def select_best_resume_match(self, **kwargs: object) -> object:
                captured.update(kwargs)
                return SimpleNamespace(resume=kwargs.get("fallback_resume"))

        main.scoring_runtime_service = FakeScoringRuntime()  # type: ignore[assignment]

        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db)
            current_resume = self._seed_resume(db, file_name="current.pdf", version=1, is_enabled=True, is_current=True)
            alt_resume = self._seed_resume(db, file_name="alt.pdf", version=2, is_enabled=True, is_current=False)
            self._seed_resume(db, file_name="disabled.pdf", version=3, is_enabled=False, is_current=False)

            result = main._select_best_resume_match(
                subject="Java role",
                body="Need Java and Spring",
                parsed={"role": "Java Developer", "skills_text": "Java, Spring"},
                user_settings=user_settings,
                email_row=None,
                db=db,
                owner_id=main.settings.owner_id,
                external_thread_id="thread-2",
            )

            self.assertIs(result.resume, current_resume)
            resumes = captured["resumes"]
            assert isinstance(resumes, list)
            self.assertEqual([resume.file_name for resume in resumes], ["current.pdf", "alt.pdf"])
            self.assertIs(captured["fallback_resume"], current_resume)


if __name__ == "__main__":
    unittest.main()
