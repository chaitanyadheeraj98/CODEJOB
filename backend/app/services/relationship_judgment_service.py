"""Confirm, reject, or correct an inferred relationship.

This is the only write path v3 exposes, and it is reached the way v2 established
the boundary: a user's click on a rendered control. v3 registers no `propose_*`
tool and no model-callable write, so there is no path from a model's output to a
change in the database.

Kept deliberately separate from `relationship_labeling_service`: labels are
training data authored in a deliberate session, judgments are production
feedback on claims the scorer already made. Calibrating against judgments would
measure the scorer's agreement with itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import (
    OpportunityCluster,
    OpportunityClusterMember,
    RecruiterOpportunity,
    RelationshipJudgment,
    utc_now,
)
from app.services import relationship_clustering_service as clustering
from app.services import relationship_scoring as scoring

VERDICT_CONFIRMED = "confirmed"
VERDICT_REJECTED = "rejected"
VERDICT_CORRECTED = "corrected"
VERDICTS = (VERDICT_CONFIRMED, VERDICT_REJECTED, VERDICT_CORRECTED)

SUBJECT_CLUSTER = "cluster"


@dataclass(frozen=True)
class JudgmentResult:
    cluster_id: str
    verdict: str
    status: str
    confidence: str
    member_ids: list[int]
    suppression_key: str

    def as_dict(self) -> dict[str, object]:
        return {
            "cluster_id": self.cluster_id,
            "verdict": self.verdict,
            "status": self.status,
            "confidence": self.confidence,
            "member_ids": list(self.member_ids),
            "suppression_key": self.suppression_key,
        }


def _cluster_for(db: Session, *, owner_id: str, cluster_id: str) -> OpportunityCluster:
    cluster = (
        db.query(OpportunityCluster)
        .filter(OpportunityCluster.owner_id == owner_id, OpportunityCluster.id == cluster_id)
        .one_or_none()
    )
    if cluster is None:
        # Out of scope and nonexistent return the same answer, so the response
        # never confirms that another owner's cluster exists.
        raise LookupError("Relationship not found")
    return cluster


def _member_ids(db: Session, cluster_id: str) -> list[int]:
    return sorted(
        int(row.opportunity_id)
        for row in db.query(OpportunityClusterMember).filter(
            OpportunityClusterMember.cluster_id == cluster_id
        )
    )


def record_judgment(
    db: Session,
    *,
    owner_id: str,
    cluster_id: str,
    verdict: str,
    note: str = "",
    correct_member_ids: list[int] | None = None,
) -> JudgmentResult:
    """Apply one user decision to a cluster and persist it.

    `confirmed` is the only path to the Confirmed band that a user can take, and
    today it is the only one that exists at all beyond a shared email thread -
    `end_client_confirmed` is false on every production row.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    cluster = _cluster_for(db, owner_id=owner_id, cluster_id=cluster_id)
    members = _member_ids(db, cluster.id)

    if verdict == VERDICT_CORRECTED:
        requested = sorted({int(item) for item in (correct_member_ids or [])})
        if len(requested) < 2:
            raise ValueError("a corrected relationship needs at least two records")
        owned = {
            int(row.id)
            for row in db.query(RecruiterOpportunity)
            .filter(RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.id.in_(requested))
            .all()
        }
        if owned != set(requested):
            raise LookupError("Relationship not found")
        members = requested

    suppression_key = clustering.member_key(members) if verdict == VERDICT_REJECTED else ""
    db.add(RelationshipJudgment(
        owner_id=owner_id,
        subject_type=SUBJECT_CLUSTER,
        subject_id=cluster.id,
        verdict=verdict,
        correction_json=json.dumps({"member_ids": members}) if verdict == VERDICT_CORRECTED else None,
        note=note,
        suppression_key=suppression_key,
    ))

    if verdict == VERDICT_REJECTED:
        cluster.status = clustering.STATUS_REJECTED
        # The key lives on the cluster too, so a later pass reproducing this
        # exact member set matches it without reading correction_json.
        cluster.member_key = clustering.member_key(members)
    else:
        cluster.status = clustering.STATUS_CONFIRMED
        # A person asserting a relationship is a recorded fact, not an
        # inference - the one legitimate promotion into the Confirmed band.
        cluster.confidence = scoring.CONFIDENCE_CONFIRMED

    if verdict == VERDICT_CORRECTED:
        existing = {
            int(row.opportunity_id): row
            for row in db.query(OpportunityClusterMember).filter(
                OpportunityClusterMember.cluster_id == cluster.id
            )
        }
        for opportunity_id, row in existing.items():
            if opportunity_id not in members:
                db.delete(row)
        for opportunity_id in members:
            row = existing.get(opportunity_id)
            if row is None:
                db.add(OpportunityClusterMember(
                    owner_id=owner_id, cluster_id=cluster.id, opportunity_id=opportunity_id,
                    confidence=scoring.CONFIDENCE_CONFIRMED, score=1.0, evidence_json="[]",
                ))
            else:
                row.confidence = scoring.CONFIDENCE_CONFIRMED
        cluster.member_key = clustering.member_key(members)

    if verdict == VERDICT_CONFIRMED:
        db.query(OpportunityClusterMember).filter(
            OpportunityClusterMember.cluster_id == cluster.id
        ).update({OpportunityClusterMember.confidence: scoring.CONFIDENCE_CONFIRMED})

    cluster.updated_at = utc_now()
    db.commit()
    return JudgmentResult(
        cluster_id=cluster.id,
        verdict=verdict,
        status=cluster.status,
        confidence=cluster.confidence,
        member_ids=members,
        suppression_key=suppression_key,
    )


def cluster_detail(db: Session, *, owner_id: str, cluster_id: str) -> dict[str, object]:
    """One cluster, its members and their evidence."""
    cluster = _cluster_for(db, owner_id=owner_id, cluster_id=cluster_id)
    members = (
        db.query(OpportunityClusterMember)
        .filter(OpportunityClusterMember.cluster_id == cluster.id)
        .order_by(OpportunityClusterMember.score.desc(), OpportunityClusterMember.opportunity_id.asc())
        .all()
    )
    rows = {
        int(row.id): row
        for row in db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.id.in_([int(member.opportunity_id) for member in members] or [0]),
        )
        .all()
    }
    return {
        "cluster_id": cluster.id,
        "label": cluster.label,
        "status": cluster.status,
        "confidence": cluster.confidence,
        "method": cluster.method,
        "semantic_available": bool(cluster.semantic_available),
        "inferred": {
            "end_client": cluster.inferred_end_client,
            "partner": cluster.inferred_partner,
            "domain": cluster.inferred_domain,
        },
        "members": [
            {
                "opportunity_id": int(member.opportunity_id),
                "confidence": member.confidence,
                "score": float(member.score),
                "job_title": (rows[int(member.opportunity_id)].job_title or "") if int(member.opportunity_id) in rows else "",
                "location": (rows[int(member.opportunity_id)].location or "") if int(member.opportunity_id) in rows else "",
                "evidence": json.loads(member.evidence_json or "[]"),
            }
            for member in members
        ],
    }


def clusters_for_opportunity(db: Session, *, owner_id: str, opportunity_id: int) -> list[dict[str, object]]:
    """Every cluster containing this record, weakest claims last."""
    member_rows = (
        db.query(OpportunityClusterMember)
        .filter(
            OpportunityClusterMember.owner_id == owner_id,
            OpportunityClusterMember.opportunity_id == int(opportunity_id),
        )
        .all()
    )
    cluster_ids = [str(row.cluster_id) for row in member_rows]
    if not cluster_ids:
        return []
    clusters = (
        db.query(OpportunityCluster)
        .filter(OpportunityCluster.owner_id == owner_id, OpportunityCluster.id.in_(cluster_ids))
        .all()
    )
    ordered = sorted(
        clusters,
        key=lambda cluster: (
            -clustering.BAND_ORDER.index(cluster.confidence)
            if cluster.confidence in clustering.BAND_ORDER
            else 0,
            cluster.id,
        ),
    )
    return [cluster_detail(db, owner_id=owner_id, cluster_id=cluster.id) for cluster in ordered]
