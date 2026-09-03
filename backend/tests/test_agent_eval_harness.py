"""Deterministic regression gate for chatbot-facing tool contracts.

Add one test here per real user-reported bug: seed known ground-truth values
and assert on them directly - never an LLM judge or answer-text similarity.
This is the release gate that should have caught the ats_score/score mix-up
(a bulk-search tool silently exposing the wrong numeric field) before it
reached a user; extend it the same way for the next data-shape gap.
"""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools.candidates import get_candidate, search_candidates
from app.mcp_server.tools.support import propose_create_github_issue
from app.models import RecruiterEmail


class ToolEvalHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patches = [patch("app.mcp_server.tools.candidates.SessionLocal", self.SessionLocal)]
        for active_patch in self.patches:
            active_patch.start()

        with self.SessionLocal() as db:
            # score (AI-match x100) is deliberately far from ats_score so a
            # tool that silently reads the wrong field fails loudly here.
            high_ai_low_ats = RecruiterEmail(
                owner_id=settings.owner_id,
                sender="a@example.com",
                subject="Java Engineer",
                body="body",
                role="Java Engineer",
                state="needs_review",
                score=95,
                ats_score=61.0,
            )
            low_ai_high_ats = RecruiterEmail(
                owner_id=settings.owner_id,
                sender="b@example.com",
                subject="Java Engineer",
                body="body",
                role="Java Engineer",
                state="needs_review",
                score=40,
                ats_score=76.56,
            )
            unscored = RecruiterEmail(
                owner_id=settings.owner_id,
                sender="c@example.com",
                subject="Java Engineer",
                body="body",
                role="Java Engineer",
                state="needs_review",
                score=0,
                ats_score=None,
            )
            db.add_all([high_ai_low_ats, low_ai_high_ats, unscored])
            db.commit()
            self.high_ai_low_ats_id = high_ai_low_ats.id
            self.low_ai_high_ats_id = low_ai_high_ats.id
            self.unscored_id = unscored.id

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_search_candidates_exposes_real_ats_score_not_ai_match_score(self) -> None:
        result = search_candidates("Java", "needs_review", 10)
        by_id = {row["id"]: row for row in result["candidates"]}

        self.assertEqual(by_id[self.high_ai_low_ats_id]["ats_score"], 61.0)
        self.assertEqual(by_id[self.low_ai_high_ats_id]["ats_score"], 76.56)
        # The candidate that only wins on the AI-match "score" must not also
        # win (or tie) on ats_score - otherwise this fixture wouldn't catch
        # a regression back to reading the wrong field.
        self.assertGreater(by_id[self.low_ai_high_ats_id]["ats_score"], by_id[self.high_ai_low_ats_id]["ats_score"])
        self.assertGreater(by_id[self.high_ai_low_ats_id]["score"], by_id[self.low_ai_high_ats_id]["score"])

    def test_search_candidates_reports_missing_ats_score_as_none_not_invented(self) -> None:
        result = search_candidates("Java", "needs_review", 10)
        by_id = {row["id"]: row for row in result["candidates"]}
        self.assertIsNone(by_id[self.unscored_id]["ats_score"])

    def test_get_candidate_ats_score_matches_search_candidates(self) -> None:
        listed = search_candidates("Java", "needs_review", 10)
        listed_row = next(row for row in listed["candidates"] if row["id"] == self.low_ai_high_ats_id)
        single = get_candidate(self.low_ai_high_ats_id)
        self.assertEqual(single["ats_score"], listed_row["ats_score"])


class GithubIssueProposalEvalTests(unittest.TestCase):
    def test_missing_fields_reported_without_filing(self) -> None:
        self.assertEqual(
            propose_create_github_issue("", "summary")["missing"],
            ["user_report"],
        )
        self.assertEqual(
            propose_create_github_issue("report", "")["missing"],
            ["ai_summary"],
        )

    def test_happy_path_preserves_user_report_and_ai_summary_verbatim(self) -> None:
        result = propose_create_github_issue(
            user_report="the ats scores in chat are wrong",
            ai_summary="Bulk search returns score instead of ats_score",
            context="Email 7323 expected 76.56, got 61",
        )
        self.assertEqual(result["action"], "create_github_issue")
        self.assertEqual(result["user_report"], "the ats scores in chat are wrong")
        self.assertEqual(result["ai_summary"], "Bulk search returns score instead of ats_score")
        self.assertIn("Email 7323", result["context"])


if __name__ == "__main__":
    unittest.main()
