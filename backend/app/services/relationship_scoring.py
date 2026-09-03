"""Decomposed pair scoring over opportunities. The heart of v3.

Three design rules, each forced by what the production data actually looks like.

**Absent is not a mismatch.** End client is populated on 6.7% of opportunities,
implementation partner on 1.6%, domain on 6.2%. A scorer that treats a missing
value as a disagreement would rank pairs by how completely they were extracted
rather than by how related they are. Base weights are renormalized over the
signals actually available for a pair, and every absent signal still emits an
evidence entry so the user can see what could *not* be compared.

**The sparse fields are bonuses, never keys.** They raise a score when present
on both sides and cost nothing when absent - and they are written so they start
mattering for free if extraction coverage ever improves.

**The decomposition must reconstruct the total.** Every evidence entry carries
the weight actually used, so `sum(weight * sub_score) == score`. A test pins it.
An explanation that does not add up to the number it explains is a fiction, and
this phase is entirely about claims a user can check.

On its relationship to the three duplicate detectors that already ship
(temp160.md F8), because the user must never see two parts of the app
confidently contradict each other:

- `role_similarity_service.compute_role_similarity` powers the Needs Review
  warning. This module measures the same two things with the same primitives -
  `extract_taxonomy_skills` for the skill Jaccard, `cosine_similarity` for the
  embedding - so the underlying measurements agree by construction. Only the
  banding differs, deliberately: 0.60/0.25 are tuned for a warning banner where
  a false positive costs a glance, and 89% of 7,056 stored pairs clear them.
  A clustering claim needs a materially stricter bar, so v3 bands with its own
  constants and leaves those thresholds untouched.
- This module deliberately does **not** call `compute_role_similarity`, because
  that function writes a `RoleSimilarityCheck` row per call. A pass over even
  the same-recruiter block would add ~1,489 rows to a shipped table on every
  run. Calling the same primitives directly gives identical sub-scores with no
  side effect. A test asserts the pass writes no such row.
- `application_service.find_duplicate_candidates` requires an exact `end_client`
  match, which 93% of records cannot supply. It will legitimately disagree with
  a v3 verdict; that is a coverage difference, not a contradiction, and it is
  stated in the provenance assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.mcp_server.tools.provenance import EvidenceEntry
from app.models import RecruiterEmail, RecruiterOpportunity
from app.semantic.embeddings_service import embedding_from_json
from app.semantic.similarity import cosine_similarity
from app.services import entity_resolution_service as resolution
from app.services.role_taxonomy import LOCATION_ENTITY_TYPE, ROLE_ENTITY_TYPE
from app.skill_taxonomy import extract_taxonomy_skills, normalize_taxonomy_text

METHOD = "v3_weighted_v1"

# Base signals. Every one of these is populated on 98-100% of production rows
# except requirement_text, which reaches 48% - which is why renormalization is
# load-bearing rather than a nicety. Weights sum to 1.0 before renormalization.
BASE_WEIGHTS: dict[str, float] = {
    "recruiter": 0.20,
    "sender_domain": 0.10,
    "skills": 0.25,
    "job_title": 0.20,
    "location": 0.10,
    "requirement_text": 0.15,
}

# Bonus signals: 1.6-6.7% populated. They raise a score when present on both
# sides and cost nothing when absent. Demoting them from keys to bonuses is the
# single change that makes this phase buildable on today's data.
BONUS_WEIGHTS: dict[str, float] = {
    "end_client": 0.10,
    "implementation_partner": 0.10,
    "domain": 0.05,
}

# Correlated: the same recruiter almost always implies the same sender domain.
# Counting both would manufacture independence, and independence is exactly what
# the Likely band is asking about.
CORRELATED_SIGNALS = frozenset({"recruiter", "sender_domain"})

STRONG_SUB_SCORE = 0.8
MIN_STRONG_SIGNALS_FOR_LIKELY = 2

# The only Confirmed source that exists today. `end_client_confirmed` is false
# on all 1,099 production rows, so the band the spec designed around it cannot
# fire; see `_confirmed_by_field` below, which is written and unreachable on
# purpose so it activates for free if extraction ever populates that column.
CONFIRMED_RULE = "same email thread"

# PROVISIONAL. These are W6's output, and W6 needs a labeled set that does not
# exist yet. They are set materially above the shipped 0.60/0.25 banner
# thresholds because 89% of stored pairs clear those, and a band admitting a
# majority of candidate pairs is not a Likely band. Nothing may be surfaced to
# a user while THRESHOLDS_CALIBRATED is False.
LIKELY_MIN = 0.75
POSSIBLE_MIN = 0.50

# Flipped to True only by a human, in the same commit that records the measured
# precision. `relationship_clustering_service` refuses to leave shadow mode
# while it is False - the fourth independent point of that control, and the
# only one that cannot be defeated by an environment variable.
THRESHOLDS_CALIBRATED = False

CONFIDENCE_CONFIRMED = "confirmed"
CONFIDENCE_LIKELY = "likely"
CONFIDENCE_POSSIBLE = "possible"
CONFIDENCE_NONE = "none"


@dataclass(frozen=True)
class PairScore:
    left_id: int
    right_id: int
    score: float
    confidence: str
    evidence: list[EvidenceEntry]
    semantic_available: bool
    method: str = METHOD

    @property
    def surfaceable(self) -> bool:
        return self.confidence != CONFIDENCE_NONE


@dataclass
class ScoringContext:
    """Per-pass batch state. Built once, threaded through every pair.

    603,801 possible pairs makes a per-pair query impossible, so everything a
    comparison needs is resolved up front: entity indexes, skill sets, source
    emails, embeddings.
    """

    owner_id: str
    skills: dict[int, frozenset[str]] = field(default_factory=dict)
    thread_ids: dict[int, str] = field(default_factory=dict)
    embeddings: dict[int, list[float]] = field(default_factory=dict)
    roles: dict[str, resolution.ResolvedEntity] = field(default_factory=dict)
    locations: dict[str, resolution.ResolvedEntity] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def sender_domain(value: str | None) -> str:
    """The domain of an email sender, from either a bare address or "Name <a@b>"."""
    text = str(value or "").strip().lower()
    if ">" in text:
        text = text.rsplit("<", 1)[-1].rstrip(">")
    _, _, domain = text.partition("@")
    return domain.strip().strip(">").strip()


def _skill_ids(opportunity: RecruiterOpportunity) -> frozenset[str]:
    # The same text construction compute_role_similarity uses, so the two
    # detectors measure the same thing.
    text = f"{opportunity.job_title or ''} {opportunity.extracted_skills or ''}"
    return frozenset(skill.id for skill in extract_taxonomy_skills(text))


def build_context(
    db: Session, *, owner_id: str, opportunities: list[RecruiterOpportunity]
) -> ScoringContext:
    """Resolve everything a pass needs, with a fixed number of queries."""
    context = ScoringContext(owner_id=owner_id)
    if not opportunities:
        return context

    for opportunity in opportunities:
        context.skills[int(opportunity.id)] = _skill_ids(opportunity)

    email_ids = {int(row.source_email_id) for row in opportunities if row.source_email_id}
    emails: dict[int, RecruiterEmail] = {}
    if email_ids:
        emails = {
            int(row.id): row
            for row in db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.id.in_(email_ids))
            .all()
        }
    for opportunity in opportunities:
        email = emails.get(int(opportunity.source_email_id or 0))
        if email is None:
            continue
        thread = str(email.external_thread_id or "").strip()
        if thread:
            context.thread_ids[int(opportunity.id)] = thread
        vector = embedding_from_json(email.semantic_embedding)
        if vector:
            context.embeddings[int(opportunity.id)] = vector

    context.roles = resolution.resolve_many(
        db,
        owner_id=owner_id,
        entity_type=ROLE_ENTITY_TYPE,
        surfaces=[str(row.job_title or "") for row in opportunities],
    )
    context.locations = resolution.resolve_many(
        db,
        owner_id=owner_id,
        entity_type=LOCATION_ENTITY_TYPE,
        surfaces=[str(row.location or "") for row in opportunities],
    )
    return context


def _entry(
    signal: str,
    left: str,
    right: str,
    *,
    match: str,
    weight: float,
    sub_score: float,
    source: str,
    normalized_to: str = "",
) -> EvidenceEntry:
    return EvidenceEntry(
        signal=signal,
        left_value=left,
        right_value=right,
        normalized_to=normalized_to,
        match=match,
        weight=weight,
        sub_score=sub_score,
        source=source,
    )


def _absent(signal: str, left: str, right: str, source: str) -> EvidenceEntry:
    return _entry(signal, left, right, match="absent", weight=0.0, sub_score=0.0, source=source)


def _canonical_signal(
    signal: str,
    left_surface: str,
    right_surface: str,
    resolved: dict[str, resolution.ResolvedEntity],
    source: str,
) -> tuple[EvidenceEntry, float]:
    """Compare two surfaces through the canonical vocabulary.

    Returns (entry, sub_score), or (absent-entry, 0.0) when either side is
    blank. An unresolved surface still compares - on its own normalized text -
    because a vocabulary gap is not a reason to stop comparing two records.
    """
    left, right = str(left_surface or "").strip(), str(right_surface or "").strip()
    if not left or not right:
        return _absent(signal, left, right, source), 0.0

    left_resolved = resolved.get(left)
    right_resolved = resolved.get(right)
    left_canonical = left_resolved.canonical if left_resolved else left
    right_canonical = right_resolved.canonical if right_resolved else right
    left_norm = normalize_taxonomy_text(left_canonical)
    right_norm = normalize_taxonomy_text(right_canonical)

    if left_norm and left_norm == right_norm:
        both_resolved = bool(left_resolved and left_resolved.resolved and right_resolved and right_resolved.resolved)
        match = "alias" if both_resolved and left_canonical not in (left, right) else "exact"
        return _entry(
            signal, left, right, match=match, weight=0.0, sub_score=1.0, source=source,
            normalized_to=left_canonical,
        ), 1.0

    # Containment is a partial signal, not a match: "Java Developer" inside
    # "Senior Java Developer - Backend" is real evidence and weaker than equality.
    contained = bool(left_norm and right_norm) and (left_norm in right_norm or right_norm in left_norm)
    sub_score = 0.6 if contained else 0.0
    return _entry(
        signal, left, right, match="overlap", weight=0.0, sub_score=sub_score, source=source,
    ), sub_score


def _confirmed_by_field(left: RecruiterOpportunity, right: RecruiterOpportunity) -> bool:
    """Both records assert the same confirmed end client.

    Unreachable today: `end_client_confirmed` is False on all 1,099 production
    rows, so this returns False for every real pair. It is written rather than
    omitted so the Confirmed band widens for free the moment extraction starts
    populating that column - see temp160.md §22.4 item 2.
    """
    if not (bool(left.end_client_confirmed) and bool(right.end_client_confirmed)):
        return False
    left_client = normalize_taxonomy_text(left.end_client or "")
    return bool(left_client) and left_client == normalize_taxonomy_text(right.end_client or "")


def _thread_entry(
    left: RecruiterOpportunity, right: RecruiterOpportunity, context: ScoringContext
) -> tuple[EvidenceEntry, bool]:
    left_thread = context.thread_ids.get(int(left.id), "")
    right_thread = context.thread_ids.get(int(right.id), "")
    left_message = str(left.gmail_message_id or "").strip()
    right_message = str(right.gmail_message_id or "").strip()

    same_message = bool(left_message) and left_message == right_message
    same_thread = bool(left_thread) and left_thread == right_thread
    if same_message or same_thread:
        value = left_message if same_message else left_thread
        return _entry(
            "thread", value, value, match="exact", weight=1.0, sub_score=1.0,
            source="recruiter_opportunities",
        ), True
    if not (left_message or left_thread) or not (right_message or right_thread):
        return _absent("thread", left_message or left_thread, right_message or right_thread, "recruiter_opportunities"), False
    return _entry(
        "thread", left_message or left_thread, right_message or right_thread,
        match="overlap", weight=0.0, sub_score=0.0, source="recruiter_opportunities",
    ), False


def _band(score: float, evidence: list[EvidenceEntry]) -> str:
    """Which band a scored pair falls in.

    The independence rule matters as much as the threshold: two correlated
    signals agreeing is one fact, not two, and a Likely claim rests on two
    independent ones.
    """
    if score < POSSIBLE_MIN:
        return CONFIDENCE_NONE
    strong: set[str] = set()
    for entry in evidence:
        if entry.match != "absent" and entry.weight > 0 and entry.sub_score >= STRONG_SUB_SCORE:
            strong.add("recruiter_identity" if entry.signal in CORRELATED_SIGNALS else entry.signal)
    if score >= LIKELY_MIN and len(strong) >= MIN_STRONG_SIGNALS_FOR_LIKELY:
        return CONFIDENCE_LIKELY
    return CONFIDENCE_POSSIBLE


def score_pair(
    db: Session,
    *,
    owner_id: str,
    left: RecruiterOpportunity,
    right: RecruiterOpportunity,
    context: ScoringContext,
) -> PairScore:
    """Score one pair and explain it. No database access; `context` holds it all."""
    thread_entry, confirmed_by_thread = _thread_entry(left, right, context)
    left_embedding = context.embeddings.get(int(left.id))
    right_embedding = context.embeddings.get(int(right.id))
    semantic_available = bool(left_embedding and right_embedding)

    raw: dict[str, tuple[EvidenceEntry, float]] = {}

    left_recruiter = int(left.recruiter_number_id or 0)
    right_recruiter = int(right.recruiter_number_id or 0)
    if left_recruiter and right_recruiter:
        same = left_recruiter == right_recruiter
        raw["recruiter"] = (
            _entry(
                "recruiter", str(left_recruiter), str(right_recruiter),
                match="exact" if same else "overlap", weight=0.0,
                sub_score=1.0 if same else 0.0, source="premium_number_contacts",
            ),
            1.0 if same else 0.0,
        )
    else:
        raw["recruiter"] = (_absent("recruiter", str(left_recruiter or ""), str(right_recruiter or ""), "premium_number_contacts"), 0.0)

    left_domain, right_domain = sender_domain(left.email_sender), sender_domain(right.email_sender)
    if left_domain and right_domain:
        same = left_domain == right_domain
        raw["sender_domain"] = (
            _entry(
                "sender_domain", left_domain, right_domain,
                match="exact" if same else "overlap", weight=0.0,
                sub_score=1.0 if same else 0.0, source="recruiter_opportunities",
            ),
            1.0 if same else 0.0,
        )
    else:
        raw["sender_domain"] = (_absent("sender_domain", left_domain, right_domain, "recruiter_opportunities"), 0.0)

    left_skills = context.skills.get(int(left.id), frozenset())
    right_skills = context.skills.get(int(right.id), frozenset())
    if left_skills and right_skills:
        jaccard = len(left_skills & right_skills) / len(left_skills | right_skills)
        raw["skills"] = (
            _entry(
                "skills", ", ".join(sorted(left_skills)), ", ".join(sorted(right_skills)),
                match="overlap", weight=0.0, sub_score=jaccard, source="role_similarity_service",
            ),
            jaccard,
        )
    else:
        raw["skills"] = (_absent("skills", left.job_title or "", right.job_title or "", "role_similarity_service"), 0.0)

    raw["job_title"] = _canonical_signal(
        "job_title", left.job_title or "", right.job_title or "", context.roles, "canonical_entity_taxonomy"
    )
    raw["location"] = _canonical_signal(
        "location", left.location or "", right.location or "", context.locations, "canonical_entity_taxonomy"
    )

    if semantic_available:
        # blend_scores maps cosine into [0,1] the same way; matching it keeps
        # this sub-score on the same scale as the shipped detector's.
        similarity = (cosine_similarity(left_embedding or [], right_embedding or []) + 1.0) / 2.0
        raw["requirement_text"] = (
            _entry(
                "requirement_text", left.email_subject or "", right.email_subject or "",
                match="semantic", weight=0.0, sub_score=similarity, source="role_similarity_service",
            ),
            similarity,
        )
    else:
        raw["requirement_text"] = (
            _absent("requirement_text", left.email_subject or "", right.email_subject or "", "role_similarity_service"),
            0.0,
        )

    bonus_raw: dict[str, tuple[EvidenceEntry, float]] = {}
    for signal in BONUS_WEIGHTS:
        left_value = str(getattr(left, signal, "") or "").strip()
        right_value = str(getattr(right, signal, "") or "").strip()
        if not left_value or not right_value:
            bonus_raw[signal] = (_absent(signal, left_value, right_value, "recruiter_opportunities"), 0.0)
            continue
        same = normalize_taxonomy_text(left_value) == normalize_taxonomy_text(right_value)
        bonus_raw[signal] = (
            _entry(
                signal, left_value, right_value, match="exact" if same else "overlap",
                weight=0.0, sub_score=1.0 if same else 0.0, source="recruiter_opportunities",
            ),
            1.0 if same else 0.0,
        )

    if confirmed_by_thread or _confirmed_by_field(left, right):
        # Asserted, never scored. Every other signal is still reported, at zero
        # weight: the user should see what agrees and what does not even when
        # the thread has already settled the question.
        evidence = [thread_entry]
        for signal in [*BASE_WEIGHTS, *BONUS_WEIGHTS]:
            entry = raw.get(signal, bonus_raw.get(signal))
            if entry:
                evidence.append(entry[0])
        return PairScore(
            left_id=int(left.id), right_id=int(right.id), score=1.0,
            confidence=CONFIDENCE_CONFIRMED, evidence=evidence, semantic_available=semantic_available,
        )

    available = {signal: weight for signal, weight in BASE_WEIGHTS.items() if raw[signal][0].match != "absent"}
    available_weight = sum(available.values())
    evidence: list[EvidenceEntry] = [thread_entry]
    base_score = 0.0
    for signal, weight in BASE_WEIGHTS.items():
        entry, sub_score = raw[signal]
        # Renormalized over what could actually be compared: a pair missing the
        # requirement embedding is scored on the rest summed to 1.0, not
        # penalized for the gap.
        effective = (weight / available_weight) if (available_weight and signal in available) else 0.0
        base_score += effective * sub_score
        evidence.append(_entry(
            signal, entry.left_value, entry.right_value, match=entry.match,
            weight=effective, sub_score=sub_score, source=entry.source,
            normalized_to=entry.normalized_to,
        ))

    bonus_total = sum(BONUS_WEIGHTS[signal] * sub for signal, (_ignored, sub) in bonus_raw.items())
    headroom = max(0.0, 1.0 - base_score)
    # Bonuses are added after renormalization and can never push a score past
    # 1.0. Scaling them rather than clipping the total keeps the decomposition
    # reconstructing the score exactly.
    scale = 1.0 if bonus_total <= headroom else (headroom / bonus_total if bonus_total else 0.0)
    for signal, (entry, sub_score) in bonus_raw.items():
        effective = BONUS_WEIGHTS[signal] * scale if entry.match != "absent" else 0.0
        evidence.append(_entry(
            signal, entry.left_value, entry.right_value, match=entry.match,
            weight=effective, sub_score=sub_score, source=entry.source,
        ))

    score = round(base_score + min(bonus_total, headroom), 6)
    return PairScore(
        left_id=int(left.id),
        right_id=int(right.id),
        score=score,
        confidence=_band(score, evidence),
        evidence=evidence,
        semantic_available=semantic_available,
    )


BLOCK_RECRUITER = "same_recruiter"
BLOCK_SENDER_DOMAIN = "same_sender_domain"
BLOCK_LOCATION_SKILL = "same_location_and_skill"
BLOCK_END_CLIENT = "same_end_client"

BLOCKING_KEYS = (BLOCK_RECRUITER, BLOCK_SENDER_DOMAIN, BLOCK_LOCATION_SKILL, BLOCK_END_CLIENT)


@dataclass(frozen=True)
class CandidatePairs:
    pairs: list[tuple[int, int]]
    by_block: dict[str, int]
    capped_blocks: tuple[str, ...]

    @property
    def assumptions(self) -> list[str]:
        """Every exclusion the blocking made, in the user's words.

        A cap that is not stated is an undocumented exclusion, and the whole
        point of the provenance contract is that the user gets to see those.
        """
        stated = [
            "Only pairs sharing a recruiter, a sender domain, a location and skill, "
            "or an end client were compared - an exhaustive comparison of every pair is not run."
        ]
        if self.capped_blocks:
            stated.append(
                "The pair limit was reached, so some candidate pairs were not compared: "
                + ", ".join(self.capped_blocks)
            )
        return stated


def candidate_pairs(
    db: Session,
    *,
    owner_id: str,
    since: datetime | None = None,
    max_pairs: int = 20_000,
) -> CandidatePairs:
    """Blocked candidate generation. Exhaustive pairing is not viable.

    1,099 production opportunities is 603,801 unordered pairs. The four blocks
    below reduce that to something scorable while keeping the cross-recruiter
    case the product actually needs. Only the location+skill block can explode,
    so it runs last and is the one that hits the cap.
    """
    query = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.owner_id == owner_id)
    if since is not None:
        query = query.filter(
            or_(RecruiterOpportunity.created_at >= since, RecruiterOpportunity.updated_at >= since)
        )
    rows = query.order_by(RecruiterOpportunity.id.asc()).all()

    seen: set[tuple[int, int]] = set()
    ordered: list[tuple[int, int]] = []
    by_block: dict[str, int] = {key: 0 for key in BLOCKING_KEYS}
    capped: list[str] = []

    def add(block: str, left: int, right: int) -> bool:
        key = (left, right) if left < right else (right, left)
        if key[0] == key[1]:
            return True
        if key in seen:
            return True
        if len(ordered) >= max_pairs:
            return False
        seen.add(key)
        ordered.append(key)
        by_block[block] += 1
        return True

    def pair_within(block: str, groups: dict[str, list[int]]) -> None:
        for members in groups.values():
            if len(members) < 2:
                continue
            for index, left in enumerate(members):
                for right in members[index + 1 :]:
                    if not add(block, left, right):
                        if block not in capped:
                            capped.append(block)
                        return

    recruiters: dict[str, list[int]] = {}
    domains: dict[str, list[int]] = {}
    clients: dict[str, list[int]] = {}
    for row in rows:
        if row.recruiter_number_id:
            recruiters.setdefault(str(row.recruiter_number_id), []).append(int(row.id))
        domain = sender_domain(row.email_sender)
        if domain:
            domains.setdefault(domain, []).append(int(row.id))
        client = normalize_taxonomy_text(row.end_client or "")
        if client:
            clients.setdefault(client, []).append(int(row.id))

    pair_within(BLOCK_RECRUITER, recruiters)
    pair_within(BLOCK_END_CLIENT, clients)
    pair_within(BLOCK_SENDER_DOMAIN, domains)

    # Last, because it is the only block that can explode: it is the one that
    # should lose pairs to the cap, and losing them must be visible.
    by_location: dict[str, list[int]] = {}
    skills_by_id: dict[int, frozenset[str]] = {}
    for row in rows:
        location = normalize_taxonomy_text(row.location or "")
        if not location:
            continue
        skills_by_id[int(row.id)] = _skill_ids(row)
        by_location.setdefault(location, []).append(int(row.id))
    for members in by_location.values():
        if len(members) < 2:
            continue
        stop = False
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if not (skills_by_id.get(left, frozenset()) & skills_by_id.get(right, frozenset())):
                    continue
                if not add(BLOCK_LOCATION_SKILL, left, right):
                    if BLOCK_LOCATION_SKILL not in capped:
                        capped.append(BLOCK_LOCATION_SKILL)
                    stop = True
                    break
            if stop:
                break
        if stop:
            break

    return CandidatePairs(pairs=ordered, by_block=by_block, capped_blocks=tuple(capped))
