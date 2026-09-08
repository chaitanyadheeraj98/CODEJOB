"""Where a candidate's `role` came from, and how it is cleaned before storage.

Two defects motivated this module, both found in production:

1. `role` could carry a whole HTML job description - one row reached 6,632
   characters - because a parsed value was stored without cleaning. `role` feeds
   chatbot prompt context (mcp_server/tools/premium_numbers.py), role-similarity
   scoring (main.py) and application snapshots (appts_service.py), so a value
   like that corrupts three consumers at once.

2. When extraction failed, the subject was silently stored in its place through
   an `or` chain. The stored value was then indistinguishable from a real
   extraction - a plausible wrong answer with no marker. In the `invalid`
   manifest bucket this happened to 93% of rows against a ~33% baseline, so
   roughly 235 rows carry an unverified title that nothing flags.

`assign_role` replaces those `or` chains with an explicit ladder, and every
outcome records a `RoleSource`. The value is only trustworthy if you can tell
where it came from.

Deliberately pure: no database, no network. The taxonomy matcher is injected so
this stays unit-testable and so the ladder degrades safely to a labelled subject
fallback when no matcher is wired up.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.parsing.document_extraction import clean_html_if_present
from app.skill_taxonomy import classify_role_family

# Job titles are short. The longest genuinely-clean role measured across 8,508
# production rows was 67 characters; the cap leaves generous headroom while still
# refusing a pasted job description.
ROLE_MAX_CHARS = 200

MARKUP_PATTERN = re.compile(r"<[a-zA-Z/]")
_WHITESPACE = re.compile(r"\s+")
_TRAILING_SEPARATORS = re.compile(r"[\s\-:;,|/\\.]+$")
_LEADING_SEPARATORS = re.compile(r"^[\s\-:;,|/\\.]+")
# Anchored, no nested quantifiers - this runs on user-supplied subject lines.
_MULTI_ROLE_PREFIX = re.compile(r"^\s*\d{1,3}\s*[-.)]?\s*(requirements?|positions?|openings?)\b", re.IGNORECASE)
_SEGMENT_SPLIT = re.compile(r"::+|;")
# Bounded body so an unclosed "<" in free text cannot cause runaway matching.
_RESIDUAL_TAG = re.compile(r"<[^<>]{0,200}>")


class RoleSource:
    """Provenance vocabulary. NULL means 'unknown' - never assume 'extracted'.

    Rows written before this module existed carry NULL and are unverified by
    definition; consumers must treat NULL as untrusted rather than backfilling an
    assumption onto history.
    """

    EXTRACTED = "extracted"
    TAXONOMY_MATCHED = "taxonomy_matched"
    SUBJECT_FALLBACK = "subject_fallback"
    SOURCE_PARENT = "source_parent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RoleAssignment:
    role: str
    role_canonical: str | None
    role_source: str


@dataclass(frozen=True)
class TaxonomyMatch:
    canonical_name: str


# Injected by the caller once the taxonomy matcher exists. Returning None must
# always be safe: the ladder falls through to a labelled subject fallback.
TaxonomyMatcher = Callable[[str, str], TaxonomyMatch | None]


def normalize_role(raw: str | None) -> str:
    """Strip markup, keep the first line, cap the length. Idempotent.

    Idempotence matters because the same function runs on the write path and in
    tests that re-feed their own output; a non-idempotent clean would keep
    clipping a value on every pass.
    """
    if not raw:
        return ""
    text = str(raw)
    if not text.strip():
        return ""

    cleaned = clean_html_if_present(text)
    # clean_html_if_present only fires on an allow-list of structural tags
    # (br/div/table/...), so inline markup like <b> or <em> survives it. That is
    # correct for body text but not here: this function's contract is that the
    # result contains no markup, so it enforces that itself rather than depending
    # on a helper tuned for a different job. Entities are unescaped first (so
    # &lt;b&gt; cannot smuggle a tag past the strip), looping until stable to keep
    # the whole function idempotent.
    for _ in range(3):
        unescaped = html.unescape(cleaned)
        if unescaped == cleaned:
            break
        cleaned = unescaped
    cleaned = _RESIDUAL_TAG.sub(" ", cleaned)

    # A job description's title sits on the first line; everything after the first
    # break is body that leaked in (the `<br />` cases).
    first_line = next((line for line in cleaned.splitlines() if line.strip()), "")
    collapsed = _WHITESPACE.sub(" ", first_line).strip()
    collapsed = _LEADING_SEPARATORS.sub("", collapsed)
    collapsed = _TRAILING_SEPARATORS.sub("", collapsed)

    if len(collapsed) <= ROLE_MAX_CHARS:
        return collapsed
    clipped = collapsed[:ROLE_MAX_CHARS]
    # Prefer a word boundary, but only if one exists reasonably near the end -
    # otherwise a long unbroken token would collapse the value to almost nothing.
    boundary = clipped.rfind(" ")
    if boundary >= ROLE_MAX_CHARS // 2:
        clipped = clipped[:boundary]
    return _TRAILING_SEPARATORS.sub("", clipped)


def is_multi_role_subject(text: str | None) -> bool:
    """True when a subject advertises several jobs at once.

    Such an email has no single role, so inferring one - by taxonomy match or
    otherwise - would fabricate a selection the email does not support. The
    manifest pipeline expands these into children; the container itself must not
    claim a role.
    """
    if not text or not text.strip():
        return False
    if _MULTI_ROLE_PREFIX.search(text):
        return True
    segments = [part.strip() for part in _SEGMENT_SPLIT.split(text) if part.strip()]
    # Three or more, so an ordinary "Java Developer :: Remote" title is not caught.
    return len(segments) >= 3


def assign_role(
    *,
    extracted: str | None,
    subject: str | None,
    body: str | None = None,
    matcher: TaxonomyMatcher | None = None,
) -> RoleAssignment:
    """The single assignment ladder shared by every write site.

    Ordering is deliberate: a real extraction always wins; a multi-role container
    is refused a title before any matcher runs; and a confident taxonomy match is
    preferred over a raw subject because a bounded title reads better in the
    draft copy that interpolates `role`.
    """
    normalized = normalize_role(extracted)
    if normalized:
        canonical = None
        if matcher is not None:
            match = matcher(normalized, body or "")
            canonical = match.canonical_name if match else None
        return RoleAssignment(normalized, canonical, RoleSource.EXTRACTED)

    if is_multi_role_subject(subject):
        return RoleAssignment("", None, RoleSource.SOURCE_PARENT)

    if matcher is not None:
        match = matcher(subject or "", body or "")
        if match:
            canonical = normalize_role(match.canonical_name)
            if canonical:
                return RoleAssignment(canonical, canonical, RoleSource.TAXONOMY_MATCHED)

    fallback = normalize_role(subject)
    if fallback:
        return RoleAssignment(fallback, None, RoleSource.SUBJECT_FALLBACK)
    return RoleAssignment("", None, RoleSource.UNKNOWN)


def apply_role_assignment(
    email: object,
    *,
    extracted: str | None,
    subject: str | None,
    body: str | None = None,
    matcher: TaxonomyMatcher | None = None,
) -> RoleAssignment | None:
    """Set role/role_source/role_canonical on a candidate row.

    Preserves the precedence the write sites had before this existed - a freshly
    parsed role wins, then whatever the row already holds, then the ladder - so
    swapping this in changes labelling, not which value survives.

    When the row's existing role is reused, both the value and its provenance are
    left untouched and None is returned. That is deliberate: this work is
    forward-only, so a legacy value is never rewritten and never has a provenance
    asserted onto it. A NULL role_source keeps meaning "unverified" rather than
    being upgraded to a guess on the next re-process.
    """
    parsed_role = normalize_role(extracted)
    if parsed_role:
        assignment = assign_role(extracted=parsed_role, subject=subject, body=body, matcher=matcher)
    elif str(getattr(email, "role", "") or "").strip():
        return None
    else:
        assignment = assign_role(extracted=None, subject=subject, body=body, matcher=matcher)

    email.role = assignment.role
    email.role_source = assignment.role_source
    if assignment.role_canonical is not None:
        email.role_canonical = assignment.role_canonical
    return assignment


def role_family_fields(*, role: str | None, skills_text: str | None) -> dict[str, object]:
    """The three role-family columns, shaped to be spread into RecruiterEmail(...).

    Returned as a dict rather than set on the row so the write sites can keep using
    `**` the way they already do for `jd_entity_fields_from_parsed` and
    `fill_entity_gaps` - one line each, and impossible to add a column at one site
    and forget it at another.

    Classified here, at row construction, rather than off the resume picker
    breakdown: the breakdown only exists when a resume was actually selected, so
    reading the family from there left every unmatched JD with no family at all -
    exactly the rows a gap report most needs to see.
    """
    classification = classify_role_family(role, skills_text)
    return {
        "role_family": classification.family,
        "role_family_confidence": classification.confidence,
        "role_family_taxonomy_version": classification.taxonomy_version,
    }


def apply_role_family(email: object, *, role: str | None, skills_text: str | None) -> None:
    """Set the role-family columns on an existing row.

    For the re-process path, where the row is updated rather than constructed.
    Unlike `apply_role_assignment` this always overwrites: a re-classification is
    the point, and the taxonomy version records which vocabulary produced it, so
    nothing is lost by refreshing a stale answer.
    """
    for column, value in role_family_fields(role=role, skills_text=skills_text).items():
        setattr(email, column, value)
