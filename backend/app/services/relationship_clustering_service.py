"""Group scored pairs into clusters, and keep them out of sight until measured.

Two rules do most of the work here.

**Closure runs over Confirmed and Likely edges only.** Chaining weak edges is
how clustering produces one giant component containing everything, and with a
score distribution where 89% of stored pairs already clear the shipped
thresholds that risk is not theoretical. A Possible edge can attach a record to
a cluster that strong edges already built; it can never merge two clusters, and
it can never seed one.

**A cluster is as strong as its weakest edge.** Confidence is the minimum over
the bands that built it, never the maximum, because the weakest link is exactly
what a user would dispute.

Shadow mode is enforced at four independent points, which is deliberate
redundancy on the control that matters most:

1. `status` defaults to "shadow" in the model.
2. This pass writes "shadow" unless surfacing is explicitly enabled.
3. The `get_relationships` tool filters shadow clusters out of every query.
4. `relationship_scoring.THRESHOLDS_CALIBRATED` is False until a human records
   a measured precision figure, and this pass refuses to leave shadow mode
   while it is - the one point an environment variable cannot defeat.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    OpportunityCluster,
    OpportunityClusterMember,
    RecruiterOpportunity,
    RelationshipJudgment,
    utc_now,
)
from app.services import relationship_scoring as scoring

STATUS_SHADOW = "shadow"
STATUS_PROPOSED = "proposed"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

# Weakest first: a cluster takes the minimum of its edges.
BAND_ORDER = (scoring.CONFIDENCE_POSSIBLE, scoring.CONFIDENCE_LIKELY, scoring.CONFIDENCE_CONFIRMED)
STRONG_BANDS = frozenset({scoring.CONFIDENCE_LIKELY, scoring.CONFIDENCE_CONFIRMED})


@dataclass
class ClusteringPassResult:
    scored_pairs: int = 0
    surfaceable_pairs: int = 0
    clusters_written: int = 0
    clusters_suppressed: int = 0
    members_written: int = 0
    pairs_by_block: dict[str, int] = field(default_factory=dict)
    capped_blocks: tuple[str, ...] = ()
    band_counts: dict[str, int] = field(default_factory=dict)
    largest_cluster: int = 0
    surfaced: bool = False
    assumptions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "scored_pairs": self.scored_pairs,
            "surfaceable_pairs": self.surfaceable_pairs,
            "clusters_written": self.clusters_written,
            "clusters_suppressed": self.clusters_suppressed,
            "members_written": self.members_written,
            "pairs_by_block": dict(self.pairs_by_block),
            "capped_blocks": list(self.capped_blocks),
            "band_counts": dict(self.band_counts),
            "largest_cluster": self.largest_cluster,
            "surfaced": self.surfaced,
            "assumptions": list(self.assumptions),
        }


def member_key(opportunity_ids: list[int]) -> str:
    """A stable fingerprint of a member set.

    Fixed width and indexable, so suppressing a rejected set never needs a query
    over an unindexable Text column.
    """
    joined = ",".join(str(item) for item in sorted(set(opportunity_ids)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def surfacing_allowed() -> bool:
    """Whether a cluster may be written in any state other than shadow.

    Both halves are required. The flag says an operator intended to surface;
    THRESHOLDS_CALIBRATED says somebody actually measured the precision the
    surfacing claim rests on. An operator cannot supply the second by setting
    an environment variable.
    """
    return bool(settings.feature_relationship_surfacing_enabled) and scoring.THRESHOLDS_CALIBRATED


class _Components:
    """Union-find over strong edges only."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def add(self, node: int) -> None:
        self._parent.setdefault(node, node)

    def find(self, node: int) -> int:
        self.add(node)
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[max(left_root, right_root)] = min(left_root, right_root)

    def groups(self) -> dict[int, list[int]]:
        grouped: dict[int, list[int]] = {}
        for node in self._parent:
            grouped.setdefault(self.find(node), []).append(node)
        return {root: sorted(members) for root, members in grouped.items()}


def _weakest(bands: list[str]) -> str:
    return min(bands, key=BAND_ORDER.index) if bands else scoring.CONFIDENCE_POSSIBLE


def _agreed_value(rows: list[RecruiterOpportunity], attribute: str) -> str:
    """The value every member carrying one agrees on, else blank.

    Never majority-voted. These columns are 1.6-6.7% populated in production, so
    a majority is routinely one row out of eight, and an inferred attribute
    presented from a single unconfirmed record is a fabrication with a label on
    it.
    """
    values = {
        str(getattr(row, attribute, "") or "").strip()
        for row in rows
        if str(getattr(row, attribute, "") or "").strip()
    }
    return values.pop() if len(values) == 1 else ""


def _suppressed_keys(db: Session, *, owner_id: str) -> set[str]:
    return {
        str(row.suppression_key)
        for row in db.query(RelationshipJudgment)
        .filter(
            RelationshipJudgment.owner_id == owner_id,
            RelationshipJudgment.verdict == "rejected",
            RelationshipJudgment.suppression_key != "",
        )
        .all()
    }


def run_clustering_pass(
    db: Session,
    *,
    owner_id: str,
    since: datetime | None = None,
    dry_run: bool = False,
    max_pairs: int = 20_000,
) -> ClusteringPassResult:
    """Score the candidate pairs, group them, and persist the result.

    Idempotent: re-running over the same inputs reproduces the same member sets,
    keeps their cluster ids (so a judgment stays attached to the claim it was
    made about), and never double-adds a member.
    """
    result = ClusteringPassResult()

    candidates = scoring.candidate_pairs(db, owner_id=owner_id, since=since, max_pairs=max_pairs)
    result.pairs_by_block = dict(candidates.by_block)
    result.capped_blocks = candidates.capped_blocks
    result.assumptions = list(candidates.assumptions)
    result.surfaced = surfacing_allowed()
    if not result.surfaced:
        result.assumptions.append(
            "Clustering is running in shadow mode: results are recorded but not shown anywhere."
        )
    if not candidates.pairs:
        return result

    needed = {item for pair in candidates.pairs for item in pair}
    rows = {
        int(row.id): row
        for row in db.query(RecruiterOpportunity)
        .filter(RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.id.in_(needed))
        .all()
    }
    context = scoring.build_context(db, owner_id=owner_id, opportunities=list(rows.values()))

    edges: list[scoring.PairScore] = []
    for left_id, right_id in candidates.pairs:
        left, right = rows.get(left_id), rows.get(right_id)
        if left is None or right is None:
            continue
        pair = scoring.score_pair(db, owner_id=owner_id, left=left, right=right, context=context)
        result.scored_pairs += 1
        result.band_counts[pair.confidence] = result.band_counts.get(pair.confidence, 0) + 1
        if pair.surfaceable:
            edges.append(pair)
    result.surfaceable_pairs = len(edges)
    if not edges:
        return result

    components = _Components()
    strong = [edge for edge in edges if edge.confidence in STRONG_BANDS]
    for edge in strong:
        components.union(edge.left_id, edge.right_id)
    groups = {root: members for root, members in components.groups().items() if len(members) > 1}

    # Best edge between any two members, used for a member's own confidence,
    # score and evidence.
    best_edge: dict[int, scoring.PairScore] = {}
    edge_bands: dict[int, list[str]] = {}
    root_of: dict[int, int] = {member: root for root, members in groups.items() for member in members}

    def record(node: int, root: int, edge: scoring.PairScore) -> None:
        current = best_edge.get(node)
        if current is None or edge.score > current.score:
            best_edge[node] = edge
        edge_bands.setdefault(root, []).append(edge.confidence)

    for edge in strong:
        root = root_of.get(edge.left_id)
        if root is None:
            continue
        record(edge.left_id, root, edge)
        record(edge.right_id, root, edge)

    # Possible edges attach a record to a cluster strong edges already built.
    # They never merge two clusters and never seed one: chaining weak evidence
    # is how a clustering pass ends up with a single component containing
    # everything.
    attachments: dict[int, tuple[int, scoring.PairScore]] = {}
    for edge in edges:
        if edge.confidence != scoring.CONFIDENCE_POSSIBLE:
            continue
        left_root, right_root = root_of.get(edge.left_id), root_of.get(edge.right_id)
        if (left_root is None) == (right_root is None):
            continue
        node = edge.right_id if left_root is not None else edge.left_id
        root = left_root if left_root is not None else right_root
        assert root is not None
        # A record is attached to at most one cluster - the strongest offer -
        # so membership never becomes ambiguous.
        existing = attachments.get(node)
        if existing is None or edge.score > existing[1].score:
            attachments[node] = (root, edge)

    for node, (root, edge) in attachments.items():
        groups[root].append(node)
        best_edge[node] = edge
        edge_bands.setdefault(root, []).append(edge.confidence)

    suppressed = _suppressed_keys(db, owner_id=owner_id)
    existing_clusters = {
        str(row.member_key): row
        for row in db.query(OpportunityCluster)
        .filter(OpportunityCluster.owner_id == owner_id, OpportunityCluster.method == scoring.METHOD)
        .all()
    }
    produced_keys: set[str] = set()

    for root, members in groups.items():
        members = sorted(set(members))
        if len(members) < 2:
            continue
        key = member_key(members)
        result.largest_cluster = max(result.largest_cluster, len(members))
        if key in suppressed:
            # "Never re-proposed identically" means exactly this: the same
            # member set stays suppressed. A different set overlapping it is a
            # new claim and may be proposed.
            result.clusters_suppressed += 1
            continue
        produced_keys.add(key)
        if dry_run:
            result.clusters_written += 1
            result.members_written += len(members)
            continue

        member_rows = [rows[item] for item in members if item in rows]
        band = _weakest(edge_bands.get(root, []))
        cluster = existing_clusters.get(key)
        if cluster is None:
            cluster = OpportunityCluster(id=str(uuid.uuid4()), owner_id=owner_id, method=scoring.METHOD)
            db.add(cluster)
        cluster.label = _agreed_value(member_rows, "end_client") or (member_rows[0].job_title or "")[:255]
        cluster.inferred_end_client = _agreed_value(member_rows, "end_client")[:255]
        cluster.inferred_partner = _agreed_value(member_rows, "implementation_partner")[:255]
        cluster.inferred_domain = _agreed_value(member_rows, "domain")[:255]
        cluster.confidence = band
        # A band is only comparable within its population, so a cluster claims
        # the richer one only when every member is in it.
        cluster.semantic_available = all(
            best_edge[item].semantic_available for item in members if item in best_edge
        )
        cluster.member_key = key
        cluster.updated_at = utc_now()
        if cluster.status not in (STATUS_CONFIRMED, STATUS_REJECTED):
            cluster.status = STATUS_PROPOSED if result.surfaced else STATUS_SHADOW
        db.flush()

        present = {
            int(row.opportunity_id): row
            for row in db.query(OpportunityClusterMember)
            .filter(OpportunityClusterMember.cluster_id == cluster.id)
            .all()
        }
        for item in members:
            edge = best_edge.get(item)
            confidence = edge.confidence if edge else scoring.CONFIDENCE_POSSIBLE
            score = edge.score if edge else 0.0
            evidence = json.dumps([entry.as_dict() for entry in edge.evidence]) if edge else "[]"
            row = present.get(item)
            if row is None:
                db.add(OpportunityClusterMember(
                    owner_id=owner_id, cluster_id=cluster.id, opportunity_id=item,
                    confidence=confidence, score=score, evidence_json=evidence,
                ))
                result.members_written += 1
            else:
                row.confidence, row.score, row.evidence_json = confidence, score, evidence
        for opportunity_id, row in present.items():
            if opportunity_id not in members:
                db.delete(row)
        result.clusters_written += 1

    if not dry_run:
        # A cluster nobody has judged and nobody has seen is not a record worth
        # keeping once the scorer stops producing it. A judged one is.
        for key, cluster in existing_clusters.items():
            if key not in produced_keys and cluster.status == STATUS_SHADOW:
                db.delete(cluster)
        db.commit()
    return result
