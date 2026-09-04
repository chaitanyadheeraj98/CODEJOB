import os
import unittest

os.environ["DEBUG"] = "false"

from app.mcp_server.tools.provenance import (
    CONFIDENCE_LEVELS,
    INFERENCE_ASSUMPTION,
    EvidenceEntry,
    block,
    inference_block,
)


def entry(**overrides) -> EvidenceEntry:
    values = {
        "signal": "job_title",
        "left_value": "Senior Java Developer",
        "right_value": "Java Developer",
        "normalized_to": "java developer",
        "match": "exact",
        "weight": 0.2,
        "sub_score": 1.0,
        "source": "canonical_entity_taxonomy",
    }
    values.update(overrides)
    return EvidenceEntry(**values)


class BlockIsUnchangedTests(unittest.TestCase):
    # v2's block() feeds every shipped analysis renderer. W1 adds beside it; a
    # changed key here would silently drop a v2 chart.
    def test_block_still_returns_exactly_its_seven_keys(self) -> None:
        """`coverage` was added for W12 - deliberately, which is what this pin is for.

        An aggregate declares how populated the columns it reads are, so a trend
        line over a sparse column carries that fact instead of leaving it to be
        recalled. Six keys became seven; the lock stays so the next addition is
        also a decision rather than a drift.
        """
        keys = set(block(metric="m", source="s", row_count=1))

        self.assertEqual(
            keys,
            {"metric", "source", "row_count", "date_range", "filters", "assumptions", "coverage"},
        )

    def test_block_without_coverage_returns_an_empty_list_not_a_missing_key(self) -> None:
        """The frontend projects provenance key by key; a missing key would read
        as absent rather than empty. Charts with no declared source columns still
        carry the key."""
        self.assertEqual(block(metric="m", source="s", row_count=1)["coverage"], [])

    def test_block_does_not_gain_the_inference_assumption(self) -> None:
        self.assertEqual(block(metric="m", source="s", row_count=1)["assumptions"], [])


class InferenceBlockTests(unittest.TestCase):
    def build(self, **overrides) -> dict:
        values = {
            "metric": "relationship",
            "source": "relationship_scoring",
            "row_count": 2,
            "confidence": "likely",
            "score": 0.72,
            "evidence": [entry()],
            "semantic_available": True,
        }
        values.update(overrides)
        return inference_block(**values)

    def test_it_carries_everything_block_carries(self) -> None:
        payload = self.build()

        for key in ("metric", "source", "row_count", "date_range", "filters", "assumptions"):
            self.assertIn(key, payload)

    def test_the_inference_assumption_is_always_first(self) -> None:
        payload = self.build(assumptions=["Capped at 20,000 pairs."])

        self.assertEqual(payload["assumptions"][0], INFERENCE_ASSUMPTION)
        self.assertEqual(payload["assumptions"][1], "Capped at 20,000 pairs.")

    def test_it_is_present_even_with_no_other_assumptions(self) -> None:
        self.assertEqual(self.build()["assumptions"], [INFERENCE_ASSUMPTION])

    def test_evidence_is_serialized_to_plain_dicts(self) -> None:
        evidence = self.build()["evidence"]

        self.assertIsInstance(evidence[0], dict)
        self.assertEqual(evidence[0]["signal"], "job_title")
        self.assertEqual(evidence[0]["match"], "exact")

    def test_confidence_score_and_population_are_carried(self) -> None:
        payload = self.build(confidence="confirmed", score=1.0, semantic_available=False)

        self.assertEqual(payload["confidence"], "confirmed")
        self.assertEqual(payload["score"], 1.0)
        self.assertIs(payload["semantic_available"], False)

    def test_every_declared_level_is_constructible(self) -> None:
        for level in CONFIDENCE_LEVELS:
            self.assertEqual(self.build(confidence=level)["confidence"], level)


class InferenceBlockRefusesTests(unittest.TestCase):
    def build(self, **overrides) -> dict:
        values = {
            "metric": "relationship",
            "source": "relationship_scoring",
            "row_count": 2,
            "confidence": "likely",
            "score": 0.72,
            "evidence": [entry()],
            "semantic_available": True,
        }
        values.update(overrides)
        return inference_block(**values)

    def test_an_unknown_confidence_level_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build(confidence="probable")

    def test_none_is_not_a_displayable_confidence(self) -> None:
        # "none" is a real scorer verdict, but it means "do not surface this" -
        # constructing a payload for it would defeat that.
        with self.assertRaises(ValueError):
            self.build(confidence="none")

    # An empty-evidence inference is the exact failure this phase exists to
    # prevent: a claim with nothing behind it, presented as a finding.
    def test_empty_evidence_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build(evidence=[])

    def test_a_score_above_one_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build(score=1.5)

    def test_a_negative_score_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build(score=-0.1)


if __name__ == "__main__":
    unittest.main()
