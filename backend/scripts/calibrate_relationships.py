"""Measure the relationship scorer against the labeled set, and choose thresholds.

Run deliberately, not on a schedule and not from a route:

    PYTHONPATH=. uv run python -m scripts.calibrate_relationships --split test

Three things this reports that a single accuracy number would hide.

**The full confusion matrix per band.** The interesting failure is a "Likely"
that is wrong: it suppresses outreach the user would otherwise have made, and
they never find out. Precision at the Likely band is the number that decides
whether anything may be surfaced at all.

**The two populations separately.** Only 48% of production opportunities reach
an embedded source email. `blend_scores` returns a keyword-only score for the
rest, and those are different distributions. A threshold calibrated across both
pooled is calibrated for neither, so the two are never pooled here.

**Per-sampler figures.** A blocking key that contributes mostly false positives
is invisible in a pooled number, and the location+skill block is capped - so the
metrics it distorts are exactly the ones that hide the distortion.

Nothing here writes to the database or edits the thresholds. Choosing them is a
human decision, recorded by editing `relationship_scoring.py` and flipping
THRESHOLDS_CALIBRATED in the same commit that writes the measured figures into
temp157.md §7.8.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import RecruiterOpportunity, RelationshipLabel
from app.services import relationship_labeling_service as labeling
from app.services import relationship_scoring as scoring

# A label of same_program is the positive class. related_distinct is
# deliberately negative: "these are similar roles" is what the scorer must NOT
# be allowed to call "the same hiring program", and it is the distinction the
# whole phase turns on.
POSITIVE_VERDICTS = frozenset({"same_program"})
NEGATIVE_VERDICTS = frozenset({"related_distinct", "unrelated"})
# Excluded from precision and reported separately: a labeler forced to guess
# produces a set that measures guessing.
ABSTAIN_VERDICTS = frozenset({"unsure"})

BANDS = (scoring.CONFIDENCE_CONFIRMED, scoring.CONFIDENCE_LIKELY, scoring.CONFIDENCE_POSSIBLE)


@dataclass
class Matrix:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    abstained: int = 0

    @property
    def precision(self) -> float | None:
        predicted = self.true_positive + self.false_positive
        return round(self.true_positive / predicted, 3) if predicted else None

    @property
    def recall(self) -> float | None:
        actual = self.true_positive + self.false_negative
        return round(self.true_positive / actual, 3) if actual else None

    def as_dict(self) -> dict[str, object]:
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "true_negative": self.true_negative,
            "abstained": self.abstained,
            "precision": self.precision,
            "recall": self.recall,
        }


@dataclass
class Scored:
    label: RelationshipLabel
    score: float
    confidence: str
    semantic_available: bool
    sampler: str = ""


@dataclass
class Report:
    total: int = 0
    abstained: int = 0
    by_band: dict[str, dict[str, object]] = field(default_factory=dict)
    by_population: dict[str, dict[str, object]] = field(default_factory=dict)
    by_sampler: dict[str, dict[str, object]] = field(default_factory=dict)
    threshold_sweep: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "abstained": self.abstained,
            "by_band": self.by_band,
            "by_population": self.by_population,
            "by_sampler": self.by_sampler,
            "threshold_sweep": self.threshold_sweep,
            "notes": self.notes,
        }


def score_labeled_set(db: Session, *, owner_id: str, split: str | None) -> list[Scored]:
    labels = labeling.labeled_pairs(db, owner_id=owner_id, split=split)
    if not labels:
        return []
    needed = {int(row.left_opportunity_id) for row in labels} | {int(row.right_opportunity_id) for row in labels}
    rows = {
        int(row.id): row
        for row in db.query(RecruiterOpportunity)
        .filter(RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.id.in_(needed))
        .all()
    }
    context = scoring.build_context(db, owner_id=owner_id, opportunities=list(rows.values()))

    scored: list[Scored] = []
    for label in labels:
        left = rows.get(int(label.left_opportunity_id))
        right = rows.get(int(label.right_opportunity_id))
        if left is None or right is None:
            continue
        pair = scoring.score_pair(db, owner_id=owner_id, left=left, right=right, context=context)
        scored.append(Scored(
            label=label,
            score=pair.score,
            confidence=pair.confidence,
            semantic_available=pair.semantic_available,
            sampler=label.sampler or "unknown",
        ))
    return scored


def _matrix(rows: list[Scored], *, predicted: set[str]) -> Matrix:
    matrix = Matrix()
    for row in rows:
        if row.label.verdict in ABSTAIN_VERDICTS:
            matrix.abstained += 1
            continue
        positive_truth = row.label.verdict in POSITIVE_VERDICTS
        positive_call = row.confidence in predicted
        if positive_call and positive_truth:
            matrix.true_positive += 1
        elif positive_call:
            matrix.false_positive += 1
        elif positive_truth:
            matrix.false_negative += 1
        else:
            matrix.true_negative += 1
    return matrix


def _sweep(rows: list[Scored]) -> list[dict[str, object]]:
    """Precision/recall across candidate LIKELY_MIN values.

    The choice is made from the curve rather than by argument. The shipped
    banner thresholds put 89% of stored pairs at same-or-related, so a value
    below 0.60 is not a starting point - it is the thing being ruled out.
    """
    sweep: list[dict[str, object]] = []
    for step in range(50, 100, 5):
        threshold = step / 100
        matrix = Matrix()
        for row in rows:
            if row.label.verdict in ABSTAIN_VERDICTS:
                matrix.abstained += 1
                continue
            positive_truth = row.label.verdict in POSITIVE_VERDICTS
            positive_call = row.score >= threshold
            if positive_call and positive_truth:
                matrix.true_positive += 1
            elif positive_call:
                matrix.false_positive += 1
            elif positive_truth:
                matrix.false_negative += 1
            else:
                matrix.true_negative += 1
        sweep.append({"threshold": threshold, **matrix.as_dict()})
    return sweep


def build_report(rows: list[Scored]) -> Report:
    report = Report(total=len(rows))
    report.abstained = sum(1 for row in rows if row.label.verdict in ABSTAIN_VERDICTS)

    report.by_band = {
        scoring.CONFIDENCE_CONFIRMED: _matrix(rows, predicted={scoring.CONFIDENCE_CONFIRMED}).as_dict(),
        # The band that decides whether anything may be surfaced.
        scoring.CONFIDENCE_LIKELY: _matrix(
            rows, predicted={scoring.CONFIDENCE_CONFIRMED, scoring.CONFIDENCE_LIKELY}
        ).as_dict(),
        scoring.CONFIDENCE_POSSIBLE: _matrix(rows, predicted=set(BANDS)).as_dict(),
    }

    # Never pooled. A threshold calibrated across both is calibrated for
    # neither.
    for name, subset in (
        ("semantic_available", [row for row in rows if row.semantic_available]),
        ("keyword_only", [row for row in rows if not row.semantic_available]),
    ):
        report.by_population[name] = {
            "count": len(subset),
            **_matrix(subset, predicted={scoring.CONFIDENCE_CONFIRMED, scoring.CONFIDENCE_LIKELY}).as_dict(),
        }
        if subset and len(subset) < 30:
            report.notes.append(
                f"The {name} population has only {len(subset)} labeled pairs; "
                "a precision figure over that few is not a measurement."
            )

    for sampler in sorted({row.sampler for row in rows}):
        subset = [row for row in rows if row.sampler == sampler]
        report.by_sampler[sampler] = {
            "count": len(subset),
            **_matrix(subset, predicted={scoring.CONFIDENCE_CONFIRMED, scoring.CONFIDENCE_LIKELY}).as_dict(),
        }

    report.threshold_sweep = _sweep(rows)

    if report.total < 150:
        report.notes.append(
            f"Only {report.total} labeled pairs. The target is 150-300 with at least 40% "
            "hard negatives; below that, every figure here is provisional."
        )
    hard = sum(1 for row in rows if row.sampler == scoring.BLOCK_LOCATION_SKILL)
    if report.total and hard / report.total < 0.4:
        report.notes.append(
            f"Hard negatives are {round(hard / report.total * 100)}% of the set, below the 40% target. "
            "A set of easy pairs produces thresholds that look excellent and fail in production."
        )
    if not scoring.THRESHOLDS_CALIBRATED:
        report.notes.append(
            "THRESHOLDS_CALIBRATED is still False in relationship_scoring.py, so nothing can be "
            "surfaced. Flip it in the same commit that records these figures in temp157.md §7.8."
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", default=settings.owner_id)
    parser.add_argument(
        "--split",
        default="test",
        choices=["test", "train", "all"],
        help="Held-out by default. Tuning against the full set produces numbers that mean nothing.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = score_labeled_set(
            db, owner_id=args.owner_id, split=None if args.split == "all" else args.split
        )
    finally:
        db.close()

    if not rows:
        print(
            f"No labeled pairs in split '{args.split}' for owner '{args.owner_id}'.\n"
            "Label pairs first: the scorer cannot be calibrated against nothing, and a threshold "
            "chosen without a labeled set is a guess with a decimal point."
        )
        return 1

    report = build_report(rows)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return 0

    print(f"Labeled pairs scored: {report.total} (split={args.split}, abstained={report.abstained})")
    print("\nBy confidence band (positive class = same_program):")
    for band, matrix in report.by_band.items():
        print(f"  {band:<10} precision={matrix['precision']} recall={matrix['recall']} "
              f"tp={matrix['true_positive']} fp={matrix['false_positive']} "
              f"fn={matrix['false_negative']} tn={matrix['true_negative']}")

    print("\nBy population (never pooled):")
    for name, matrix in report.by_population.items():
        print(f"  {name:<20} n={matrix['count']:<4} precision={matrix['precision']} recall={matrix['recall']}")

    print("\nBy sampler:")
    for name, matrix in report.by_sampler.items():
        print(f"  {name:<24} n={matrix['count']:<4} precision={matrix['precision']} recall={matrix['recall']}")

    print("\nThreshold sweep for LIKELY_MIN:")
    for point in report.threshold_sweep:
        print(f"  >= {point['threshold']:<5} precision={point['precision']} recall={point['recall']}")

    if report.notes:
        print("\nNotes:")
        for note in report.notes:
            print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
