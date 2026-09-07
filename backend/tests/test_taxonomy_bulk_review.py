import unittest

from app.schemas import PendingSkillResponse
from app.services.taxonomy_bulk_review_service import (
    MODEL_BATCH_SIZE,
    MODEL_TIMEOUT_SECONDS,
    BUCKET_APPROVE,
    BUCKET_DISMISS,
    BUCKET_REVIEW,
    SOURCE_MODEL,
    SOURCE_RULES,
    BulkReviewCountMismatch,
    Recommendation,
    bucket_counts,
    classify_entities,
    classify_skills,
    looks_like_a_sentence_fragment,
    refine_with_model,
    select_applicable,
)


def skill_row(
    name: str,
    *,
    occurrence_count: int = 1,
    suspicious: bool = False,
    recoverable: list[str] | None = None,
) -> PendingSkillResponse:
    return PendingSkillResponse(
        skill_name=name,
        normalized_name=name.casefold(),
        occurrence_count=occurrence_count,
        candidate_ids=[7, 3],
        suspicious=suspicious,
        recoverable_skills=recoverable or [],
        source_tags=["ai"],
    )


def entity_row(name: str, *, occurrence_count: int = 1) -> dict[str, object]:
    return {
        "entity_type": "location",
        "display_name": name,
        "normalized_name": name.casefold(),
        "occurrence_count": occurrence_count,
        "candidate_ids": [11],
    }


def bucket_of(recommendations: list[Recommendation], key: str) -> Recommendation:
    return next(item for item in recommendations if item.key == key)


class ClassifySkillsTests(unittest.TestCase):
    def test_suspicious_blob_is_dismissed_and_locked(self) -> None:
        result = classify_skills([skill_row("and innovation initiatives", occurrence_count=8, suspicious=True)])
        self.assertEqual(result[0].bucket, BUCKET_DISMISS)
        self.assertEqual(result[0].reason, "malformed_or_recoverable")
        self.assertTrue(result[0].locked)

    def test_recoverable_skill_is_dismissed_and_locked(self) -> None:
        result = classify_skills([skill_row("Java and Spring", occurrence_count=5, recoverable=["Java"])])
        self.assertEqual(result[0].bucket, BUCKET_DISMISS)
        self.assertTrue(result[0].locked)

    def test_safe_repeated_skill_is_approved(self) -> None:
        result = classify_skills([skill_row("Temporal Workflow", occurrence_count=6)])
        self.assertEqual(result[0].bucket, BUCKET_APPROVE)
        self.assertEqual(result[0].reason, "safe_repeated")
        self.assertFalse(result[0].locked)
        self.assertEqual(result[0].source, SOURCE_RULES)

    def test_safe_singleton_goes_to_review_not_approve(self) -> None:
        result = classify_skills([skill_row("Temporal Workflow", occurrence_count=1)])
        self.assertEqual(result[0].bucket, BUCKET_REVIEW)
        self.assertEqual(result[0].reason, "safe_singleton")

    def test_a_repeated_lowercase_fragment_is_never_auto_approved(self) -> None:
        # Regression: this exact value sat in the live queue with 8 hits. It is one
        # clean token, so is_safe_for_bulk_skill_approval accepts it and the approve
        # branch used to win. A fragment must reach the human however often it recurs.
        result = classify_skills([skill_row("and innovation initiatives", occurrence_count=8)])
        self.assertEqual(result[0].bucket, BUCKET_REVIEW)
        self.assertEqual(result[0].reason, "needs_review")

    def test_a_lowercase_name_without_a_fragment_word_still_approves(self) -> None:
        # The review predicate needs both a lowercase start and a fragment word, so
        # ordinary lowercase tool names must not get swept into the review bucket.
        result = classify_skills([skill_row("webhook", occurrence_count=4)])
        self.assertEqual(result[0].bucket, BUCKET_APPROVE)

    def test_name_requiring_review_goes_to_review(self) -> None:
        # Lowercase leading fragment word: custom_skill_requires_review catches it,
        # is_safe_for_bulk_skill_approval does not reject it outright.
        result = classify_skills([skill_row("with kubernetes", occurrence_count=4)])
        self.assertEqual(result[0].bucket, BUCKET_REVIEW)

    def test_capitalised_sentence_fragments_never_reach_approve(self) -> None:
        # Regression: both of these were written into the live taxonomy as approved
        # skills. They start with a capital, so custom_skill_requires_review's
        # islower() gate let them through to the approve branch.
        for name in ("Work is about 70% backend", "Version 1 experience is plus."):
            with self.subTest(name=name):
                result = classify_skills([skill_row(name, occurrence_count=9)])
                self.assertEqual(result[0].bucket, BUCKET_REVIEW, name)
                self.assertEqual(result[0].reason, "needs_review")

    def test_real_skill_names_are_not_mistaken_for_fragments(self) -> None:
        # The fragment test must not eat ordinary names. "Human-in-the-Loop" contains
        # "the", and ".NET"/"Node.js" contain periods - only a trailing one counts.
        for name in (
            "Human-in-the-Loop",
            "Node.js",
            ".NET",
            "High-Scale Transactional Systems",
            "Spring Boot",
            "Wells Fargo",
        ):
            with self.subTest(name=name):
                self.assertFalse(looks_like_a_sentence_fragment(name), name)

    def test_a_clean_unknown_name_still_reaches_approve(self) -> None:
        # Names already in the base taxonomy (Node.js, Spring Boot) are rejected by
        # is_safe_for_bulk_skill_approval and never appear in the unknown queue, so
        # the approve path has to be checked with a genuinely unknown name.
        result = classify_skills([skill_row("High-Scale Transactional Systems", occurrence_count=4)])
        self.assertEqual(result[0].bucket, BUCKET_APPROVE)

    def test_candidate_ids_and_counts_survive(self) -> None:
        result = classify_skills([skill_row("Temporal Workflow", occurrence_count=6)])
        self.assertEqual(result[0].candidate_ids, (7, 3))
        self.assertEqual(result[0].occurrence_count, 6)


class ClassifyEntitiesTests(unittest.TestCase):
    def test_unbalanced_bracket_name_is_dismissed_and_locked(self) -> None:
        result = classify_entities([entity_row("Omaha, NE (remote", occurrence_count=9)])
        self.assertEqual(result[0].bucket, BUCKET_DISMISS)
        self.assertEqual(result[0].reason, "unsafe_name")
        self.assertTrue(result[0].locked)

    def test_repeated_clean_name_is_approved(self) -> None:
        result = classify_entities([entity_row("Omaha, NE", occurrence_count=3)])
        self.assertEqual(result[0].bucket, BUCKET_APPROVE)
        self.assertFalse(result[0].locked)

    def test_single_hit_clean_name_goes_to_review(self) -> None:
        result = classify_entities([entity_row("Trenton, NJ", occurrence_count=1)])
        self.assertEqual(result[0].bucket, BUCKET_REVIEW)
        self.assertEqual(result[0].reason, "safe_singleton")


class BucketCountsTests(unittest.TestCase):
    def test_counts_cover_every_bucket(self) -> None:
        recommendations = classify_skills(
            [
                skill_row("Temporal Workflow", occurrence_count=6),
                skill_row("Trenton fragment", occurrence_count=2, suspicious=True),
                skill_row("Temporal Workflow", occurrence_count=1),
            ]
        )
        self.assertEqual(
            bucket_counts(recommendations),
            {BUCKET_APPROVE: 1, BUCKET_DISMISS: 1, BUCKET_REVIEW: 1},
        )


class RefineWithModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recommendations = [
            Recommendation(key="clean", display_name="Clean Skill", occurrence_count=1, bucket=BUCKET_REVIEW, reason="safe_singleton"),
            Recommendation(key="junk", display_name="Junk Blob", occurrence_count=8, bucket=BUCKET_DISMISS, reason="malformed_or_recoverable", locked=True),
            Recommendation(key="good", display_name="Good Skill", occurrence_count=6, bucket=BUCKET_APPROVE, reason="safe_repeated"),
        ]

    def test_model_moves_a_review_row_and_marks_the_source(self) -> None:
        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            return {"decisions": [{"name": "Clean Skill", "bucket": "approve", "reason": "real tool"}]}

        refined, error = refine_with_model(self.recommendations, scope="skill", completion=completion)
        self.assertIsNone(error)
        self.assertEqual(bucket_of(refined, "clean").bucket, BUCKET_APPROVE)
        self.assertEqual(bucket_of(refined, "clean").source, SOURCE_MODEL)
        self.assertEqual(bucket_of(refined, "clean").reason, "real tool")

    def test_model_cannot_promote_a_locked_dismissal(self) -> None:
        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            return {"decisions": [{"name": "Junk Blob", "bucket": "approve", "reason": "looks fine to me"}]}

        refined, error = refine_with_model(self.recommendations, scope="skill", completion=completion)
        self.assertIsNone(error)
        self.assertEqual(bucket_of(refined, "junk").bucket, BUCKET_DISMISS)
        self.assertTrue(bucket_of(refined, "junk").locked)
        self.assertEqual(bucket_of(refined, "junk").source, SOURCE_RULES)

    def test_model_cannot_demote_a_rules_approval(self) -> None:
        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            return {"decisions": [{"name": "Good Skill", "bucket": "dismiss", "reason": "nope"}]}

        refined, _error = refine_with_model(self.recommendations, scope="skill", completion=completion)
        self.assertEqual(bucket_of(refined, "good").bucket, BUCKET_APPROVE)

    def test_unknown_name_from_the_model_is_dropped(self) -> None:
        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            return {"decisions": [{"name": "Never Sent", "bucket": "approve", "reason": "invented"}]}

        refined, error = refine_with_model(self.recommendations, scope="skill", completion=completion)
        self.assertIsNone(error)
        self.assertEqual([item.bucket for item in refined], [item.bucket for item in self.recommendations])

    def test_invalid_bucket_is_ignored(self) -> None:
        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            return {"decisions": [{"name": "Clean Skill", "bucket": "delete", "reason": "x"}]}

        refined, _error = refine_with_model(self.recommendations, scope="skill", completion=completion)
        self.assertEqual(bucket_of(refined, "clean").bucket, BUCKET_REVIEW)

    def test_provider_failure_keeps_rules_verdicts_and_reports_the_error(self) -> None:
        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("DeepSeek API key is missing")

        refined, error = refine_with_model(self.recommendations, scope="skill", completion=completion)
        self.assertIsNotNone(error)
        self.assertIn("DeepSeek API key is missing", str(error))
        self.assertEqual([item.bucket for item in refined], [item.bucket for item in self.recommendations])
        self.assertTrue(all(item.source == SOURCE_RULES for item in refined))

    def test_no_review_rows_means_no_model_call(self) -> None:
        calls: list[str] = []

        def completion(system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            calls.append(system)
            return {"decisions": []}

        only_decided = [item for item in self.recommendations if item.bucket != BUCKET_REVIEW]
        refined, error = refine_with_model(only_decided, scope="skill", completion=completion)
        self.assertEqual(calls, [])
        self.assertIsNone(error)
        self.assertEqual(len(refined), 2)


class BatchFailureToleranceTests(unittest.TestCase):
    def _many(self, count: int) -> list[Recommendation]:
        return [
            Recommendation(key=f"k{i}", display_name=f"Name {i}", occurrence_count=1, bucket=BUCKET_REVIEW)
            for i in range(count)
        ]

    def test_one_failed_batch_does_not_discard_the_others(self) -> None:
        rows = self._many(MODEL_BATCH_SIZE * 3)
        calls = {"n": 0}

        def completion(_system: str, user: str, **_kwargs: object) -> dict[str, object]:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("truncated JSON content")
            names = [line.split('"')[1] for line in user.splitlines() if '"' in line]
            return {"decisions": [{"name": name, "bucket": "approve", "reason": "ok"} for name in names]}

        refined, error = refine_with_model(rows, scope="skill", completion=completion)
        self.assertEqual(calls["n"], 3)
        approved = [item for item in refined if item.bucket == BUCKET_APPROVE]
        self.assertEqual(len(approved), MODEL_BATCH_SIZE * 2)
        # The failed batch's names keep the rules verdict, which is the safe bucket.
        still_review = [item for item in refined if item.bucket == BUCKET_REVIEW]
        self.assertEqual(len(still_review), MODEL_BATCH_SIZE)
        self.assertTrue(all(item.source == SOURCE_RULES for item in still_review))
        self.assertIsNotNone(error)
        self.assertIn("1 of 3 batches failed", str(error))

    def test_every_batch_failing_leaves_all_rules_verdicts(self) -> None:
        rows = self._many(MODEL_BATCH_SIZE * 2)

        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("truncated JSON content")

        refined, error = refine_with_model(rows, scope="skill", completion=completion)
        self.assertTrue(all(item.bucket == BUCKET_REVIEW for item in refined))
        self.assertTrue(all(item.source == SOURCE_RULES for item in refined))
        self.assertIn("2 of 2 batches failed", str(error))

    def test_batches_run_concurrently(self) -> None:
        # Sequentially this took 443s against the live queue. The guard is wall time
        # against a stub that sleeps: 4 batches x 0.3s is ~0.3s in parallel and ~1.2s
        # if this ever silently reverts to a serial loop.
        import time

        def completion(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
            time.sleep(0.3)
            return {"decisions": []}

        rows = self._many(MODEL_BATCH_SIZE * 4)
        started = time.perf_counter()
        refine_with_model(rows, scope="skill", completion=completion)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.9, f"batches appear to be running serially ({elapsed:.2f}s)")

    def test_a_per_call_timeout_is_passed_to_the_provider(self) -> None:
        seen: list[object] = []

        def completion(_system: str, _user: str, **kwargs: object) -> dict[str, object]:
            seen.append(kwargs.get("timeout_seconds"))
            return {"decisions": []}

        refine_with_model(self._many(MODEL_BATCH_SIZE), scope="skill", completion=completion)
        self.assertEqual(seen, [MODEL_TIMEOUT_SECONDS])

    def test_the_batch_size_is_what_gets_sent(self) -> None:
        sizes: list[int] = []

        def completion(_system: str, user: str, **_kwargs: object) -> dict[str, object]:
            sizes.append(len([line for line in user.splitlines() if '"' in line]))
            return {"decisions": []}

        refine_with_model(self._many(MODEL_BATCH_SIZE + 3), scope="skill", completion=completion)
        self.assertEqual(sizes, [MODEL_BATCH_SIZE, 3])


class SelectApplicableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recommendations = [
            Recommendation(key="good", display_name="Good Skill", occurrence_count=6, bucket=BUCKET_APPROVE),
            Recommendation(key="junk", display_name="Junk Blob", occurrence_count=8, bucket=BUCKET_DISMISS, locked=True),
        ]

    def test_count_mismatch_raises_before_anything_resolves(self) -> None:
        with self.assertRaises(BulkReviewCountMismatch) as caught:
            select_applicable(self.recommendations, keys=["good"], action=BUCKET_APPROVE, expected_count=2)
        self.assertEqual(caught.exception.expected, 2)
        self.assertEqual(caught.exception.actual, 1)

    def test_duplicate_keys_are_deduped_before_the_count_check(self) -> None:
        applicable, skipped = select_applicable(
            self.recommendations, keys=["good", "good"], action=BUCKET_APPROVE, expected_count=1
        )
        self.assertEqual([item.key for item in applicable], ["good"])
        self.assertEqual(skipped, [])

    def test_key_no_longer_pending_is_skipped(self) -> None:
        applicable, skipped = select_applicable(
            self.recommendations, keys=["gone"], action=BUCKET_APPROVE, expected_count=1
        )
        self.assertEqual(applicable, [])
        self.assertEqual(skipped, [{"key": "gone", "reason": "no_longer_pending"}])

    def test_approving_a_locked_key_is_refused(self) -> None:
        applicable, skipped = select_applicable(
            self.recommendations, keys=["junk"], action=BUCKET_APPROVE, expected_count=1
        )
        self.assertEqual(applicable, [])
        self.assertEqual(skipped, [{"key": "junk", "reason": "unsafe_for_approval"}])

    def test_dismissing_a_locked_key_is_allowed(self) -> None:
        applicable, skipped = select_applicable(
            self.recommendations, keys=["junk"], action=BUCKET_DISMISS, expected_count=1
        )
        self.assertEqual([item.key for item in applicable], ["junk"])
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
