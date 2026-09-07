import unittest
from unittest import mock

from app.mcp_server.tools import taxonomy_review
from app.mcp_server.tools.taxonomy_review import (
    MAX_PROPOSAL_KEYS,
    propose_taxonomy_bulk_review,
)
from app.services.taxonomy_bulk_review_service import Recommendation


def rec(key: str, *, bucket: str, count: int = 3, locked: bool = False) -> Recommendation:
    return Recommendation(
        key=key,
        display_name=key.title(),
        occurrence_count=count,
        bucket=bucket,
        reason="test",
        locked=locked,
    )


class ProposeTaxonomyBulkReviewTests(unittest.TestCase):
    def _run(self, recommendations, **kwargs) -> dict:
        # The tool opens its own session to read pending records; the classification
        # itself is what these tests are about, so it is stubbed directly.
        #
        # refine_with_model is stubbed too. use_model defaults to True, so without
        # this every one of these tests would make real DeepSeek calls - slow, paid
        # and non-deterministic. The default itself is pinned separately below.
        with mock.patch.object(taxonomy_review, "SessionLocal"), mock.patch.object(
            taxonomy_review, "classify_entities", return_value=recommendations
        ), mock.patch.object(
            taxonomy_review, "classify_skills", return_value=recommendations
        ), mock.patch.object(
            taxonomy_review, "refine_with_model", side_effect=lambda recs, **_kw: (list(recs), None)
        ):
            return propose_taxonomy_bulk_review(**kwargs)

    def test_rejects_an_unknown_scope(self) -> None:
        result = self._run([], scope="planet", action="approve")
        self.assertIn("error", result)
        self.assertIn("skill", result["scopes"])

    def test_rejects_an_unknown_action(self) -> None:
        result = self._run([], scope="skill", action="delete")
        self.assertIn("error", result)
        self.assertEqual(sorted(result["actions"]), ["approve", "dismiss"])

    def test_proposes_only_the_matching_bucket(self) -> None:
        result = self._run(
            [
                rec("good one", bucket="approve"),
                rec("junk one", bucket="dismiss", locked=True),
                rec("unsure one", bucket="review"),
            ],
            scope="skill",
            action="approve",
        )
        self.assertEqual(result["action"], "propose_taxonomy_bulk_review")
        self.assertEqual(result["keys"], ["good one"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["taxonomy_action"], "approve")

    def test_never_proposes_the_review_bucket(self) -> None:
        for action in ("approve", "dismiss"):
            result = self._run([rec("unsure one", bucket="review")], scope="skill", action=action)
            self.assertEqual(result["status"], "nothing_to_do")
            self.assertEqual(result["counts"]["review"], 1)

    def test_reports_undecided_records_as_work_left_for_the_human(self) -> None:
        result = self._run(
            [rec("good one", bucket="approve"), rec("unsure one", bucket="review")],
            scope="skill",
            action="approve",
        )
        self.assertEqual(result["needs_human_count"], 1)

    def test_a_locked_record_is_never_in_an_approve_proposal(self) -> None:
        # Locked records only ever land in dismiss today, so force the contradiction
        # to prove the guard holds if the bucketing rules ever change.
        result = self._run([rec("junk one", bucket="approve", locked=True)], scope="skill", action="approve")
        self.assertEqual(result["status"], "nothing_to_do")

    def test_a_locked_record_can_be_dismissed(self) -> None:
        result = self._run([rec("junk one", bucket="dismiss", locked=True)], scope="skill", action="dismiss")
        self.assertEqual(result["keys"], ["junk one"])

    def test_batches_are_capped_and_report_what_is_left(self) -> None:
        many = [rec(f"skill {index}", bucket="approve", count=index) for index in range(MAX_PROPOSAL_KEYS + 25)]
        result = self._run(many, scope="skill", action="approve")
        self.assertEqual(result["count"], MAX_PROPOSAL_KEYS)
        self.assertEqual(len(result["keys"]), MAX_PROPOSAL_KEYS)
        self.assertEqual(result["total_in_bucket"], MAX_PROPOSAL_KEYS + 25)
        self.assertEqual(result["remaining_after_batch"], 25)

    def test_the_batch_takes_the_most_frequent_records_first(self) -> None:
        result = self._run(
            [rec("rare", bucket="approve", count=1), rec("common", bucket="approve", count=99)],
            scope="skill",
            action="approve",
        )
        self.assertEqual(result["keys"], ["common", "rare"])

    def test_use_model_without_a_key_reports_the_error_and_still_proposes(self) -> None:
        with mock.patch.object(taxonomy_review.settings, "deepseek_api_key", ""):
            result = self._run([rec("good one", bucket="approve")], scope="skill", action="approve", use_model=True)
        self.assertIn("DeepSeek API key is missing", result["model_error"])
        self.assertEqual(result["keys"], ["good one"])

    def test_the_model_refines_by_default(self) -> None:
        # The default is use_model=True, so a caller that says nothing still gets the
        # undecided middle re-sorted. Pinned because flipping it back is a one-word
        # change that nothing else would catch.
        calls: list[str] = []

        def fake_refine(recs, **kwargs):
            calls.append(str(kwargs.get("scope")))
            return list(recs), None

        with mock.patch.object(taxonomy_review, "SessionLocal"), mock.patch.object(
            taxonomy_review, "classify_skills", return_value=[rec("good one", bucket="approve")]
        ), mock.patch.object(taxonomy_review, "refine_with_model", side_effect=fake_refine), mock.patch.object(
            taxonomy_review.settings, "deepseek_api_key", "test-key"
        ):
            propose_taxonomy_bulk_review(scope="skill", action="approve")
        self.assertEqual(calls, ["skill"])

    def test_use_model_false_skips_the_model_entirely(self) -> None:
        calls: list[str] = []

        with mock.patch.object(taxonomy_review, "SessionLocal"), mock.patch.object(
            taxonomy_review, "classify_skills", return_value=[rec("good one", bucket="approve")]
        ), mock.patch.object(
            taxonomy_review, "refine_with_model", side_effect=lambda recs, **kw: (calls.append("called"), (list(recs), None))[1]
        ):
            result = propose_taxonomy_bulk_review(scope="skill", action="approve", use_model=False)
        self.assertEqual(calls, [])
        self.assertIsNone(result["model_error"])

    def test_the_card_states_reversibility(self) -> None:
        result = self._run([rec("good one", bucket="approve")], scope="location", action="approve")
        self.assertTrue(result["reversible"])
        self.assertIn("dismissed later", result["reversible_detail"])


if __name__ == "__main__":
    unittest.main()
