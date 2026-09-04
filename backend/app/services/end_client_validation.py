"""Decide whether a string is usable as an end-client name.

Blank is a first-class answer here. `clean_end_client` returns `""` for anything
it cannot vouch for, and `""` means **not identified** - never "this job has no
end client", and never a licence to substitute something else. Every caller that
used to fall back to the posting company, the recruiter company, or the literal
"Unknown" now records the blank instead, because a wrong client name is worse
than a missing one: it is indistinguishable from a real answer downstream.

The rules are derived from production, not invented. On 2026-09-03 the audit of
1,117 `recruiter_opportunities` found 24 of 76 populated values invalid, all
reaching the column the same way - `phone_intelligence_workflow_service` mapped
`ExternalOpportunity.company` into `end_client` when the AI extractor returned
nothing, and that company was itself scraped by a `client|company` label regex
that matches the "client" inside "client-facing" and "client-side" and then runs
on through `<br />` markup because its capture stops only at a newline, comma,
or semicolon. Hence `facing skills<br /><br />Preferred:...`, `side Technologies
HTML`, and a 305-character job description stored as a company name.

Two things this module deliberately does not do.

It does not repair a value. `facing skills<br />` is not a damaged rendering of a
real client name, it is a fragment of a sentence about a different subject, and
there is nothing in it to recover. Trimming it to `facing` would produce a value
that passes every length and markup check and is still wrong.

It does not consult a vocabulary. `role_taxonomy.load_entity_taxonomy` resolves
approved company names, but at 6.7% coverage an end-client column has nothing to
resolve, and a matcher here would become a second opinion on entity identity -
the failure `entity_resolution_service` exists to prevent. This module answers
only "is this shaped like a company name", which is a syntactic question.
"""

from __future__ import annotations

import re

# The longest legitimate value in production is 85 characters and 13 words:
# "State of OR (OHA/ODHS - Oregon Health Authority/ Oregon Department of Human
# Services)". A tighter bound destroys it, so length and word count are only
# backstops here - `_PROSE_MARKERS` does the real work of telling a name from a
# sentence, and it does not get looser as names get longer.
MAX_END_CLIENT_LENGTH = 100
MAX_END_CLIENT_WORDS = 14

# Words that appear in sentences and never in the name of a company or agency.
# "of" and "and" are deliberately absent - "State of NC" and "U.S. Securities
# and Exchange Commission" are real clients. Compared case-sensitively against
# lowercase tokens only, so the "ITS" in "State of NY ITS" (the New York Office
# of Information Technology Services) is not mistaken for the pronoun.
_PROSE_MARKERS = frozenset(
    {
        "are", "is", "was", "were", "be", "been", "being", "am",
        "will", "would", "should", "can", "could", "may", "might", "must",
        "you", "your", "we", "our", "us", "they", "their", "them", "it", "its",
        "this", "that", "these", "those", "who", "which", "what",
        "please", "need", "needs", "needed", "required", "require", "requires",
        "looking", "seeking", "interested", "pursuing", "apply", "send",
        "have", "has", "had", "do", "does", "did", "not", "any", "all",
    }
)

# The tails of "client-facing", "client-side", "client-based". The upstream
# regex captures what follows the word "client", so these arrive as the *start*
# of the value. Matched case-insensitively at the front only.
_COMPOUND_TAILS = ("facing", "side", "based", "oriented", "specific", "driven")

_HTML_MARKERS = ("<", ">", "&amp", "&nbsp", "&lt", "&gt", "&#")

# "Local to NC Only - NO" and "Local to Idaho Only - No" are eligibility
# constraints that the label scraper picked up as client names.
_CONSTRAINT_RE = re.compile(r"(?i)^\s*local\s+to\b.*\bonly\b")

_COMPOUND_TAIL_RE = re.compile(
    r"(?i)^(?:" + "|".join(_COMPOUND_TAILS) + r")(?:\W|$)"
)

# A value ending on one of these is a truncated clause, not a name:
# "Server applications using".
_TRAILING_STOPWORDS = frozenset(
    {
        "using", "with", "and", "or", "for", "the", "a", "an", "of", "to",
        "in", "on", "at", "from", "by", "is", "are", "was", "were", "will",
        "that", "this", "its", "their", "our", "your", "as", "but", "if",
    }
)

_LETTER_RE = re.compile(r"[A-Za-z]")


def is_valid_end_client(value: str | None) -> bool:
    """True when `value` is shaped like a company or agency name."""
    return bool(clean_end_client(value))


def rejection_reason(value: str | None) -> str:
    """Why `clean_end_client` would blank this value, or "" if it would keep it.

    Used by the remediation script so an audit trail records the rule that fired
    rather than only the fact that something changed.
    """
    text = str(value or "").strip()
    if not text:
        return "empty"
    if any(marker in text for marker in _HTML_MARKERS):
        return "html_markup"
    if "@" in text:
        return "email_address"
    if any(ch in text for ch in ("\n", "\r", "\t")):
        return "embedded_newline"
    if ":" in text:
        return "label_or_clause"
    if len(text) > MAX_END_CLIENT_LENGTH:
        return f"too_long_{len(text)}_chars"
    if _COMPOUND_TAIL_RE.match(text):
        return "compound_tail_fragment"
    if _CONSTRAINT_RE.match(text):
        return "eligibility_constraint"
    if not _LETTER_RE.search(text):
        return "no_letters"

    words = text.split()

    # A sentence gives itself away by its function words, whatever its length.
    # This is the rule that separates a long agency name from a long clause.
    for word in words:
        token = word.strip(".,;:!?()[]").lower()
        if word.strip(".,;:!?()[]").islower() and token in _PROSE_MARKERS:
            return "prose_marker"

    # Real names are capitalised. "a leader in its industry." is not. Allow the
    # eBay/iRobot shape, where a lowercase first letter is followed by a capital.
    first = next((ch for ch in text if ch.isalpha()), "")
    if first.islower():
        index = text.index(first)
        following = text[index + 1 : index + 2]
        if not following.isupper():
            return "uncapitalised"

    if len(words) > MAX_END_CLIENT_WORDS:
        return f"too_many_words_{len(words)}"

    # Only a genuinely lowercase final word signals a truncated clause. "State
    # of NY ITS" ends on an agency acronym that happens to spell a stopword, so
    # the comparison is case-sensitive: "using" rejects, "ITS" does not.
    last_word = words[-1].strip(".,;:!?")
    if last_word.islower() and last_word in _TRAILING_STOPWORDS:
        return "trailing_stopword"
    return ""


def clean_end_client(value: str | None) -> str:
    """Return a usable end-client name, or "" meaning *not identified*.

    Never guesses, never substitutes, never partially repairs. A caller with no
    trustworthy end client stores the blank.
    """
    text = str(value or "").strip()
    if rejection_reason(text):
        return ""
    return text
