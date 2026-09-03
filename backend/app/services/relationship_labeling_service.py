"""Sampling and recording ground truth for the relationship scorer.

Two decisions here are the difference between a labeled set that measures
something and one that does not.

**Hard negatives are sampled deliberately.** Pairs that share a canonical
location and at least one skill but come from different recruiters and
different sender domains are exactly the pairs that separate "similar role" from
"same hiring program". A set of easy pairs produces thresholds that look
excellent in the report and fail in production, and nothing about the report
would reveal it.

**The split is assigned at insert, not at evaluation.** Assigning it later lets
the held-out set drift as labeling continues, which is the easiest mistake to
make here and the hardest to detect afterwards.

The labeler is shown exactly the signals the scorer uses and no others. The
previous plan listed eight fields to display, five of which are blank on
essentially every production pair; a labeler shown five blank fields learns to
judge on the other three, and the set silently becomes ground truth for a
different scorer than the one being built.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import RecruiterOpportunity, RelationshipLabel
from app.services import relationship_scoring as scoring

VERDICTS = ("same_program", "related_distinct", "unrelated", "unsure")
SPLITS = ("train", "test")

# One in three, assigned at insert. Small enough to leave a usable training set
# at 150-300 pairs, large enough that a precision figure over it is not a single
# lucky handful.
TEST_SPLIT_EVERY = 3

# The fields shown side by side. Exactly the signals score_pair reads.
DISPLAY_FIELDS = (
    ("job_title", "Job title", "100%"),
    ("extracted_skills", "Skills", "100%"),
    ("location", "Location", "98.9%"),
    ("email_sender", "Sender", "100%"),
    ("email_subject", "Subject", "100%"),
    ("end_client", "End client", "6.7%"),
    ("implementation_partner", "Implementation partner", "1.6%"),
    ("domain", "Domain", "6.2%"),
)


@dataclass(frozen=True)
class LabelCandidate:
    left_id: int
    right_id: int
    sampler: str
    left: dict[str, str] = field(default_factory=dict)
    right: dict[str, str] = field(default_factory=dict)
    # Carried, but the UI must not reveal it before the verdict is recorded: a
    # labeler who sees the score first calibrates to the scorer instead of
    # labeling the truth, and the resulting set measures agreement with itself.
    score: float = 0.0
    confidence: str = scoring.CONFIDENCE_NONE

    def as_dict(self) -> dict[str, object]:
        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "sampler": self.sampler,
            "left": dict(self.left),
            "right": dict(self.right),
            "score": self.score,
            "confidence": self.confidence,
            "fields": [
                {"key": key, "label": label, "coverage": coverage}
                for key, label, coverage in DISPLAY_FIELDS
            ],
        }


def canonical_pair(left_id: int, right_id: int) -> tuple[int, int]:
    """Always (lower, higher).

    Without this the unique constraint permits both orderings and one pair can
    be labeled twice, with opposite verdicts, by the same person.
    """
    left, right = int(left_id), int(right_id)
    if left == right:
        raise ValueError("a pair must name two different opportunities")
    return (left, right) if left < right else (right, left)


def _display(row: RecruiterOpportunity) -> dict[str, str]:
    return {key: str(getattr(row, key, "") or "") for key, _label, _coverage in DISPLAY_FIELDS}


def _split_for(left_id: int, right_id: int) -> str:
    """Deterministic from the pair, so a re-sampled pair lands in the same split.

    A pair that moved between train and test on a re-run would leak training
    data into the held-out set without anybody noticing.
    """
    digest = hashlib.sha256(f"{left_id}:{right_id}".encode("utf-8")).hexdigest()
    return SPLITS[1] if int(digest[:8], 16) % TEST_SPLIT_EVERY == 0 else SPLITS[0]


def _is_hard_negative(left: RecruiterOpportunity, right: RecruiterOpportunity) -> bool:
    return (
        int(left.recruiter_number_id or 0) != int(right.recruiter_number_id or 0)
        and scoring.sender_domain(left.email_sender) != scoring.sender_domain(right.email_sender)
    )


def sample_pairs_for_labeling(
    db: Session,
    *,
    owner_id: str,
    limit: int = 20,
    hard_negative_ratio: float = 0.4,
) -> list[LabelCandidate]:
    """Unlabeled pairs to judge, stratified across blocks and biased to hard cases."""
    candidates = scoring.candidate_pairs(db, owner_id=owner_id)
    if not candidates.pairs:
        return []

    labeled = {
        (int(row.left_opportunity_id), int(row.right_opportunity_id))
        for row in db.query(RelationshipLabel).filter(RelationshipLabel.owner_id == owner_id).all()
    }
    unlabeled = [pair for pair in candidates.pairs if pair not in labeled]
    if not unlabeled:
        return []

    needed = {item for pair in unlabeled for item in pair}
    rows = {
        int(row.id): row
        for row in db.query(RecruiterOpportunity)
        .filter(RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.id.in_(needed))
        .all()
    }
    context = scoring.build_context(db, owner_id=owner_id, opportunities=list(rows.values()))

    hard: list[LabelCandidate] = []
    easy: list[LabelCandidate] = []
    for left_id, right_id in unlabeled:
        left, right = rows.get(left_id), rows.get(right_id)
        if left is None or right is None:
            continue
        scored = scoring.score_pair(db, owner_id=owner_id, left=left, right=right, context=context)
        candidate = LabelCandidate(
            left_id=left_id,
            right_id=right_id,
            # Which block produced the pair, so precision can later be
            # attributed to one.
            sampler=candidates.block_of.get((left_id, right_id), ""),
            left=_display(left),
            right=_display(right),
            score=scored.score,
            confidence=scored.confidence,
        )
        (hard if _is_hard_negative(left, right) else easy).append(candidate)

    target_hard = min(len(hard), int(round(max(0.0, min(hard_negative_ratio, 1.0)) * limit)))
    picked = hard[:target_hard] + easy[: limit - target_hard]
    if len(picked) < limit:
        remaining = [item for item in hard[target_hard:] if item not in picked]
        picked += remaining[: limit - len(picked)]
    return picked[:limit]


def record_label(
    db: Session,
    *,
    owner_id: str,
    left_opportunity_id: int,
    right_opportunity_id: int,
    verdict: str,
    reason: str = "",
    labeler: str = "",
    sampler: str = "",
) -> RelationshipLabel:
    """Record one verdict. Re-labeling a pair updates it rather than duplicating."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    left_id, right_id = canonical_pair(left_opportunity_id, right_opportunity_id)

    existing = (
        db.query(RelationshipLabel)
        .filter(
            RelationshipLabel.owner_id == owner_id,
            RelationshipLabel.left_opportunity_id == left_id,
            RelationshipLabel.right_opportunity_id == right_id,
        )
        .one_or_none()
    )
    if existing is not None:
        existing.verdict = verdict
        existing.reason = reason
        existing.labeler = labeler or existing.labeler
        db.commit()
        return existing

    row = RelationshipLabel(
        owner_id=owner_id,
        left_opportunity_id=left_id,
        right_opportunity_id=right_id,
        verdict=verdict,
        reason=reason,
        split=_split_for(left_id, right_id),
        labeler=labeler,
        sampler=sampler,
    )
    db.add(row)
    db.commit()
    return row


def label_summary(db: Session, *, owner_id: str) -> dict[str, object]:
    """Counts by verdict, split and sampler, plus the hard-negative share.

    The hard-negative share is reported because a set that drifts below it
    stops measuring the boundary it exists to measure, and the drift is
    invisible in a total count.
    """
    rows = db.query(RelationshipLabel).filter(RelationshipLabel.owner_id == owner_id).all()
    by_verdict: dict[str, int] = {}
    by_split: dict[str, int] = {}
    by_sampler: dict[str, int] = {}
    for row in rows:
        by_verdict[row.verdict] = by_verdict.get(row.verdict, 0) + 1
        by_split[row.split] = by_split.get(row.split, 0) + 1
        by_sampler[row.sampler or "unknown"] = by_sampler.get(row.sampler or "unknown", 0) + 1

    hard = sum(count for sampler, count in by_sampler.items() if sampler == scoring.BLOCK_LOCATION_SKILL)
    return {
        "total": len(rows),
        "by_verdict": by_verdict,
        "by_split": by_split,
        "by_sampler": by_sampler,
        "hard_negative_share": round(hard / len(rows), 3) if rows else 0.0,
        # Both are targets, not gates - the harness reports against them.
        "target_total": 150,
        "target_hard_negative_share": 0.4,
    }


def labeled_pairs(db: Session, *, owner_id: str, split: str | None = None) -> list[RelationshipLabel]:
    """The labeled set, optionally restricted to one split."""
    query = db.query(RelationshipLabel).filter(RelationshipLabel.owner_id == owner_id)
    if split:
        query = query.filter(RelationshipLabel.split == split)
    return query.order_by(RelationshipLabel.id.asc()).all()


def unlabeled_count(db: Session, *, owner_id: str) -> int:
    candidates = scoring.candidate_pairs(db, owner_id=owner_id)
    labeled = {
        (int(row.left_opportunity_id), int(row.right_opportunity_id))
        for row in db.query(RelationshipLabel).filter(RelationshipLabel.owner_id == owner_id).all()
    }
    return sum(1 for pair in candidates.pairs if pair not in labeled)
