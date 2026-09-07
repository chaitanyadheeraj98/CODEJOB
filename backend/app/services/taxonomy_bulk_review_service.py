"""Bucket pending taxonomy records into approve / dismiss / needs-review.

Deliberately pure: every function here takes already-fetched rows and returns
plain values. No `Session`, and nothing imported from `main.py` - which is where
both writers live, so importing it would be circular. The endpoints fetch, call
in here, then write with the helpers they already have.

The rules pass is not new judgement. It reuses the predicates the manual UI
already enforces one card at a time, so a bulk decision and a hand decision
cannot disagree about the same record.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Any

from app.ai.deepseek_client import deepseek_json_completion
from app.parsing.skill_audit import (
    custom_skill_requires_review,
    is_safe_for_bulk_skill_approval,
)
from app.schemas import PendingSkillResponse
from app.services.taxonomy_learning_service import (
    BULK_APPROVAL_MIN_OCCURRENCES,
    is_safe_for_bulk_entity_approval,
)
from app.skill_taxonomy import normalize_taxonomy_text


BUCKET_APPROVE = "approve"
BUCKET_DISMISS = "dismiss"
BUCKET_REVIEW = "review"
BUCKETS = (BUCKET_APPROVE, BUCKET_DISMISS, BUCKET_REVIEW)

SOURCE_RULES = "rules"
SOURCE_MODEL = "model"

SKILL_SCOPE = "skill"
ENTITY_SCOPES = ("company", "location", "role")
SCOPES = (SKILL_SCOPE, *ENTITY_SCOPES)

# One classify call sends at most MODEL_MAX_ITEMS names, MODEL_BATCH_SIZE per
# request. The cap is a spend bound, not a correctness one: whatever the model
# does not see keeps its rules verdict, which is always `review` here.
#
# 25 is measured, not assumed. Against the live queue, 200 names at batch=10 (20
# requests) took 59.9s while batch=25 (8 requests) took 34.7s: DeepSeek's
# per-request overhead and tail latency dominate, so more, smaller calls is the
# slower shape even though each individual call is quicker.
MODEL_BATCH_SIZE = 25
# 200, not 400. At 400 a proposal cost 35-78s inside a chat turn - long enough
# that the user cannot tell a working assistant from a hung one. Halving it makes
# the first answer land quickly, and the queue drains by asking again: a second
# batch is cheap, an opaque wait is not.
MODEL_MAX_ITEMS = 200
# Batches are independent, so they run concurrently. Sequentially, 400 names took
# 443s against the live queue - unusable inside a chat turn or an open overlay.
# Wall time is now the slowest wave rather than the sum.
#
# Sized so MODEL_MAX_ITEMS / MODEL_BATCH_SIZE fits in a single wave: at 8 workers
# the 16 batches took two waves and 65s, which overran the MCP client's timeout
# and broke the chat path outright. A failed batch is tolerated per-batch, so if
# this ever draws rate limiting the cost is names keeping their rules verdict.
# Enough that MODEL_MAX_ITEMS / MODEL_BATCH_SIZE is a single wave, so wall time is
# one call's latency rather than a multiple of it. Measured per-call latency under
# concurrency ranges 9.5-47s, and wall time tracks the slowest call in the wave -
# which is why lowering MODEL_MAX_ITEMS further buys almost nothing.
MODEL_MAX_WORKERS = 16
# Raised from the client's 20s default, which timed out 2 of 16 batches. With the
# calls in parallel a longer per-call budget costs patience only on the slow ones,
# not on the total.
MODEL_TIMEOUT_SECONDS = 60.0
# Measured twice against the live queue, not estimated. 40 names at 2,000 tokens
# truncated every batch; 25 names at 3,500 still truncated 6 of 16. A cap only
# bounds a runaway reply - output is billed on what is actually produced - so it
# is set well clear of the worst batch rather than tuned close to the average.
MODEL_MAX_TOKENS = 8000
MAX_REASON_CHARS = 80

MAX_APPLY_KEYS = 2000

_SCOPE_NOUN = {
    SKILL_SCOPE: "technical skill",
    "company": "company name",
    "location": "location",
    "role": "job title",
}

# Auxiliary and copula verbs. A closed class, and none of them appears inside a
# real skill name - their presence means the text is a clause someone cut out of
# a sentence ("Work is about 70% backend"), not a name.
_CLAUSE_WORDS = frozenset({
    "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "must", "should", "will", "about",
})

# A dangling continuation only reads as one when the word leads. "Java and Spring"
# is a compound worth a look; "and innovation initiatives" is the tail of a bullet.
_LEADING_FRAGMENT_WORDS = frozenset({
    "and", "or", "with", "for", "including", "plus", "the", "a", "on",
})


def looks_like_a_sentence_fragment(value: str) -> bool:
    """True when a name reads as a piece of prose rather than a skill.

    `custom_skill_requires_review` covers the lowercase-leading case, unbalanced
    brackets and long blobs, but it gates its fragment test on `text[:1].islower()`
    - so a fragment that happens to start with a capital passes it. Live data made
    that concrete: "Work is about 70% backend" and "Version 1 experience is plus."
    both reached the approve bucket and were written into the taxonomy.

    Kept here rather than folded into `custom_skill_requires_review`, which
    `skill_taxonomy_cleanup` also depends on and whose current answers are pinned
    by `test_skill_audit`.
    """
    text = (value or "").strip()
    if not text:
        return False
    # A trailing period is a sentence remnant. Mid-string periods are ordinary in
    # names (".NET", "Node.js", "asp.net core"), so only the final one counts.
    if text.endswith("."):
        return True
    words = normalize_taxonomy_text(text).split()
    if len(words) < 2:
        return False
    if words[0] in _LEADING_FRAGMENT_WORDS:
        return True
    return any(word in _CLAUSE_WORDS for word in words)


class BulkReviewCountMismatch(ValueError):
    """The confirmed count disagrees with the keys actually submitted."""

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(f"Confirmed {expected} records but received {actual} unique keys")
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class Recommendation:
    key: str
    display_name: str
    occurrence_count: int
    candidate_ids: tuple[int, ...] = ()
    bucket: str = BUCKET_REVIEW
    reason: str = ""
    source: str = SOURCE_RULES
    # Rules dismissed this as malformed. `SkillUpgradeSection` already greys out
    # Approve for exactly these records, so neither the model nor a stale browser
    # tab may route one to approve - the bulk path cannot be weaker than the
    # manual path it replaces.
    locked: bool = False


def classify_skills(rows: Sequence[PendingSkillResponse]) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for row in rows:
        if row.suspicious or row.recoverable_skills:
            bucket, reason, locked = BUCKET_DISMISS, "malformed_or_recoverable", True
        # Checked before the approve branch, not after. `is_safe_for_bulk_skill_approval`
        # only asks whether the text is one clean token; a lowercase fragment like
        # "and innovation initiatives" passes it and, seen 8 times, would auto-approve -
        # which is precisely the weakness of the existing Approve all button. A name
        # this predicate flags goes to the human no matter how often it appears.
        elif custom_skill_requires_review(row.skill_name) or looks_like_a_sentence_fragment(row.skill_name):
            bucket, reason, locked = BUCKET_REVIEW, "needs_review", False
        elif (
            is_safe_for_bulk_skill_approval(row.skill_name)
            and row.occurrence_count >= BULK_APPROVAL_MIN_OCCURRENCES
        ):
            bucket, reason, locked = BUCKET_APPROVE, "safe_repeated", False
        else:
            bucket, reason, locked = BUCKET_REVIEW, "safe_singleton", False
        recommendations.append(
            Recommendation(
                key=row.normalized_name,
                display_name=row.skill_name,
                occurrence_count=row.occurrence_count,
                candidate_ids=tuple(row.candidate_ids),
                bucket=bucket,
                reason=reason,
                locked=locked,
            )
        )
    return recommendations


def classify_entities(rows: Sequence[dict[str, object]]) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for row in rows:
        display_name = str(row.get("display_name") or "")
        occurrence_count = int(row.get("occurrence_count") or 0)
        if not is_safe_for_bulk_entity_approval(display_name):
            bucket, reason, locked = BUCKET_DISMISS, "unsafe_name", True
        elif occurrence_count >= BULK_APPROVAL_MIN_OCCURRENCES:
            bucket, reason, locked = BUCKET_APPROVE, "safe_repeated", False
        else:
            bucket, reason, locked = BUCKET_REVIEW, "safe_singleton", False
        raw_ids = row.get("candidate_ids")
        candidate_ids = tuple(int(value) for value in raw_ids) if isinstance(raw_ids, list) else ()
        recommendations.append(
            Recommendation(
                key=str(row.get("normalized_name") or normalize_taxonomy_text(display_name)),
                display_name=display_name,
                occurrence_count=occurrence_count,
                candidate_ids=candidate_ids,
                bucket=bucket,
                reason=reason,
                locked=locked,
            )
        )
    return recommendations


def bucket_counts(recommendations: Sequence[Recommendation]) -> dict[str, int]:
    counts = {bucket: 0 for bucket in BUCKETS}
    for item in recommendations:
        if item.bucket in counts:
            counts[item.bucket] += 1
    return counts


def _system_prompt(scope: str) -> str:
    noun = _SCOPE_NOUN.get(scope, "value")
    return (
        f"You triage {noun} values that an email parser extracted from recruiter emails. "
        f"For each value, decide whether it belongs in the owner's canonical {noun} vocabulary.\n"
        f'"approve" - a real, well-formed {noun} worth storing.\n'
        f'"dismiss" - junk: a sentence fragment, boilerplate, a placeholder, or not a {noun} at all.\n'
        '"review" - genuinely ambiguous; leave it for the human.\n'
        "Return exactly one JSON object shaped as "
        '{"decisions": [{"name": "<the value copied verbatim>", '
        '"bucket": "approve|dismiss|review", "reason": "<why, at most 40 characters>"}]}. '
        "Include one entry per value you were given, and never invent a value."
    )


def _user_prompt(chunk: Sequence[Recommendation]) -> str:
    lines = [
        f'{index}. "{item.display_name}" (seen {item.occurrence_count}x)'
        for index, item in enumerate(chunk, start=1)
    ]
    return "Classify each value:\n" + "\n".join(lines)


def _parse_decisions(
    payload: object,
    chunk: Sequence[Recommendation],
) -> dict[str, tuple[str, str]]:
    """Keep only decisions about names we actually sent, in buckets we accept."""
    # Matched on the normalized key so the model echoing back different casing or
    # spacing still lands on the right record, and nothing else does.
    by_name = {normalize_taxonomy_text(item.display_name): item.key for item in chunk}
    decisions: dict[str, tuple[str, str]] = {}
    raw = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return decisions
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = by_name.get(normalize_taxonomy_text(str(entry.get("name") or "")))
        bucket = str(entry.get("bucket") or "").strip().lower()
        if key is None or bucket not in BUCKETS:
            continue
        reason = " ".join(str(entry.get("reason") or "").split())[:MAX_REASON_CHARS]
        decisions[key] = (bucket, reason or "model_decision")
    return decisions


def refine_with_model(
    recommendations: Sequence[Recommendation],
    *,
    scope: str,
    completion: Callable[..., dict[str, Any]] = deepseek_json_completion,
) -> tuple[list[Recommendation], str | None]:
    """Let the model re-sort the ambiguous middle. Returns (result, model_error).

    Never raises: a provider failure degrades to the rules answer rather than
    failing the whole classification, because the rules answer is already
    complete and useful on its own.
    """
    candidates = sorted(
        (item for item in recommendations if item.bucket == BUCKET_REVIEW and not item.locked),
        key=lambda item: (-item.occurrence_count, item.display_name.casefold()),
    )[:MODEL_MAX_ITEMS]
    if not candidates:
        return list(recommendations), None

    # Per batch, not all-or-nothing. An earlier version discarded every decision
    # when any one call failed, which against the live queue meant one truncated
    # response threw away the whole pass. A batch that fails simply leaves its
    # names on their rules verdict - and that verdict is `review`, the bucket that
    # already means "a human decides" - so partial refinement is strictly better
    # than none rather than a half-trusted state.
    chunks = [
        candidates[start : start + MODEL_BATCH_SIZE]
        for start in range(0, len(candidates), MODEL_BATCH_SIZE)
    ]

    def run(chunk: list[Recommendation]) -> dict[str, tuple[str, str]]:
        payload = completion(
            _system_prompt(scope),
            _user_prompt(chunk),
            max_tokens=MODEL_MAX_TOKENS,
            timeout_seconds=MODEL_TIMEOUT_SECONDS,
        )
        return _parse_decisions(payload, chunk)

    decisions: dict[str, tuple[str, str]] = {}
    failures: list[str] = []
    batches = len(chunks)
    # Chunks are disjoint, so the merge is order-independent and the result does
    # not depend on which batch finishes first.
    with ThreadPoolExecutor(max_workers=min(MODEL_MAX_WORKERS, batches)) as pool:
        for future in as_completed([pool.submit(run, chunk) for chunk in chunks]):
            try:
                decisions.update(future.result())
            except Exception as exc:  # noqa: BLE001 - any provider failure is recoverable here
                failures.append(f"{type(exc).__name__}: {exc}")

    model_error: str | None = None
    if failures:
        model_error = (
            f"{len(failures)} of {batches} batches failed; "
            f"those names kept their rules verdict. First error: {failures[0]}"
        )[:300]
    # Every batch failing is the same outcome as the old all-or-nothing path, so
    # say so plainly rather than reporting a refinement that did not happen.
    if len(failures) == batches:
        return list(recommendations), model_error

    refined: list[Recommendation] = []
    for item in recommendations:
        decision = decisions.get(item.key)
        if decision is None or item.locked or item.bucket != BUCKET_REVIEW:
            refined.append(item)
            continue
        bucket, reason = decision
        refined.append(replace(item, bucket=bucket, reason=reason, source=SOURCE_MODEL))
    return refined, model_error


def select_applicable(
    recommendations: Sequence[Recommendation],
    *,
    keys: Sequence[str],
    action: str,
    expected_count: int,
) -> tuple[list[Recommendation], list[dict[str, str]]]:
    """Resolve confirmed keys against freshly-derived recommendations.

    `expected_count` is the count the user saw on the confirmation screen. It is
    checked against the keys actually submitted before anything is resolved, so
    a request whose count and payload disagree writes nothing at all.
    """
    unique = list(dict.fromkeys(str(key) for key in keys))
    if len(unique) != expected_count:
        raise BulkReviewCountMismatch(expected=expected_count, actual=len(unique))

    by_key = {item.key: item for item in recommendations}
    applicable: list[Recommendation] = []
    skipped: list[dict[str, str]] = []
    for key in unique:
        item = by_key.get(key)
        if item is None:
            # Approved, dismissed or re-parsed since the browser classified.
            skipped.append({"key": key, "reason": "no_longer_pending"})
        elif action == BUCKET_APPROVE and item.locked:
            skipped.append({"key": key, "reason": "unsafe_for_approval"})
        else:
            applicable.append(item)
    return applicable, skipped
