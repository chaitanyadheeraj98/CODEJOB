"""Find a company in job-description text, and say what the finding is worth.

§16 of `temp162.md`. Two separate jobs, deliberately not merged:

**Finding the name** is word-bounded, never a substring. `Citi` matches 455
bodies as a substring and **12** with word boundaries - the other 443 are the
word *citizenship*, which appears in almost every job description. Most companies
show no false positives at all, and nothing in a result tells you which kind you
are looking at, so bounding is not optional. This is the third appearance of one
defect: `client-facing` → `facing` in the extractor, `morgan` → *Morgan, Utah* in
nvoids' own results, and now this.

`taxonomy_matcher.AliasMatcher` already does word-bounded matching, is cached and
tested, and is what `role_taxonomy` uses. Nothing new is written here.

**Saying what it means** is the harder half. A company named in a description can
occupy four roles - end client, implementation partner, prime vendor, or the firm
that posted the job - and four companies in this corpus demonstrably occupy more
than one (Infosys, Deloitte, Mphasis, TCS). Nineteen of thirty-seven
implementation partners are also posting companies.

So a mention is never reported as a relationship. `classify_mention` returns the
strongest evidence the record actually carries, and `MENTION_DESCRIBED` - the
weakest - means only *this text names this company*.

The Deloitte case is why the roles are checked in parallel rather than one at a
time. Of 25 checkable body mentions, 7 have Deloitte as the end client and 18
have it as the implementation partner, with Edward Jones as the client on all 18.
Checking `end_client` alone reported 72% of them as errors; they were correct
rows about a different column (§16.11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.skill_taxonomy import normalize_taxonomy_text
from app.taxonomy_matcher import AliasMatcher

# Strongest to weakest. The label travels with every result so an answer can say
# what it rests on, and so "named in a description" can never be rendered as
# "is the end client".
MENTION_END_CLIENT = "end_client_field"       # the end-client column says so
MENTION_PARTNER_FIELD = "partner_field"       # the partner column says so
MENTION_ROLE_LABELLED = "role_labelled"       # the body states the role in words
MENTION_ALIAS = "alias_resolved"              # an approved alias maps to it
MENTION_DESCRIBED = "described"               # named in the text, role unknown
MENTION_STRENGTH = (
    MENTION_END_CLIENT,
    MENTION_PARTNER_FIELD,
    MENTION_ROLE_LABELLED,
    MENTION_ALIAS,
    MENTION_DESCRIBED,
)

# What an answer may say for each label. Kept here rather than in the prompt so a
# tool result carries its own wording and cannot be paraphrased into a claim.
MENTION_PHRASING = {
    MENTION_END_CLIENT: "recorded as the end client",
    MENTION_PARTNER_FIELD: "recorded as the implementation partner",
    MENTION_ROLE_LABELLED: "named in the description with its role stated",
    MENTION_ALIAS: "matched through an approved alias",
    MENTION_DESCRIBED: "named in the description",
}

# Roles a labelled mention can carry. `prime_vendor` is here because a body may
# state it even though the column never has - 19 labelled mentions across 12,654
# documents, and zero structured rows (§16.11).
ROLE_END_CLIENT = "end_client"
ROLE_IMPLEMENTATION_PARTNER = "implementation_partner"
ROLE_PRIME_VENDOR = "prime_vendor"

# Matched against normalized text, so punctuation is already spaces.
_ROLE_LABEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (ROLE_IMPLEMENTATION_PARTNER, re.compile(r"\bimplementation partner\b")),
    (ROLE_PRIME_VENDOR, re.compile(r"\bprime vendor\b")),
    (ROLE_END_CLIENT, re.compile(r"\bend client\b")),
)

# How far after a role label to look for the company. Long enough for
# "Implementation Partner: Deloitte", short enough that the next paragraph's
# company does not get captured.
ROLE_LABEL_WINDOW = 60


@dataclass(frozen=True)
class CompanyMention:
    """One company found in one document, with what the finding is worth."""

    company: str
    matched_alias: str
    label: str
    role: str | None = None

    @property
    def is_relationship_claim(self) -> bool:
        """False for `described` - the only label that asserts nothing."""
        return self.label != MENTION_DESCRIBED

    def as_dict(self) -> dict[str, object]:
        return {
            "company": self.company,
            "matched_alias": self.matched_alias,
            "evidence": self.label,
            "phrasing": MENTION_PHRASING[self.label],
            "role": self.role,
            "is_relationship_claim": self.is_relationship_claim,
        }


def company_aliases(company: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Normalized surface forms to search for.

    A multi-word name is kept whole. `Morgan Stanley` never degrades to `morgan`,
    which is what returned a posting located in *Morgan, Utah*.
    """
    forms = [company, *extra]
    normalized = [normalize_taxonomy_text(form) for form in forms]
    return tuple(dict.fromkeys(form for form in normalized if form))


def find_mentions(text: str | None, company: str, *, extra_aliases: tuple[str, ...] = ()) -> list[str]:
    """Word-bounded aliases of `company` present in `text`.

    Returns the matched surface forms, empty when the company is absent. Never a
    substring: `citizenship` does not contain the company `Citi`.
    """
    normalized = normalize_taxonomy_text(text)
    if not normalized:
        return []
    aliases = company_aliases(company, extra_aliases)
    if not aliases:
        return []
    matcher = AliasMatcher(list(aliases))
    # Padded so a match at either end still sees a space boundary.
    found = matcher.find(f" {normalized} ")
    return list(dict.fromkeys(match.alias for match in found))


def labelled_roles(text: str | None, company: str, *, extra_aliases: tuple[str, ...] = ()) -> set[str]:
    """Roles the text explicitly assigns to `company`.

    A company is only credited with a role when the label and the name are close
    together. Without that, `prime vendor` appearing anywhere in a long
    description would decorate every company named in it.
    """
    normalized = normalize_taxonomy_text(text)
    if not normalized:
        return set()
    aliases = company_aliases(company, extra_aliases)
    positions = [
        match.start
        for match in AliasMatcher(list(aliases)).find(f" {normalized} ")
    ]
    if not positions:
        return set()
    roles: set[str] = set()
    for role, pattern in _ROLE_LABEL_PATTERNS:
        for label in pattern.finditer(f" {normalized} "):
            if any(0 <= start - label.end() <= ROLE_LABEL_WINDOW for start in positions):
                roles.add(role)
    return roles


def classify_mention(
    company: str,
    *,
    end_client_field: str | None = None,
    implementation_partner_field: str | None = None,
    body: str | None = None,
    extra_aliases: tuple[str, ...] = (),
) -> CompanyMention | None:
    """The strongest evidence this record carries for `company`, or None.

    Every source is checked, not just the one being asked about. Checking
    `end_client` alone reported 18 correct Deloitte rows as errors, because
    Deloitte was the implementation partner on all of them (§16.11).
    """
    aliases = company_aliases(company, extra_aliases)
    if not aliases:
        return None

    def _field_names_company(value: str | None) -> str | None:
        found = find_mentions(value, company, extra_aliases=extra_aliases)
        return found[0] if found else None

    matched = _field_names_company(end_client_field)
    if matched:
        return CompanyMention(company, matched, MENTION_END_CLIENT, ROLE_END_CLIENT)

    matched = _field_names_company(implementation_partner_field)
    if matched:
        # Its own label, not the end-client one. A row that says "Deloitte is
        # the partner" must never be phrased as "recorded as the end client" -
        # that is the §16.11 confusion re-introduced in the output format.
        return CompanyMention(
            company, matched, MENTION_PARTNER_FIELD, ROLE_IMPLEMENTATION_PARTNER
        )

    in_body = find_mentions(body, company, extra_aliases=extra_aliases)
    if not in_body:
        return None

    roles = labelled_roles(body, company, extra_aliases=extra_aliases)
    if roles:
        # Strongest first, so a body naming two roles reports the one that
        # matters most rather than whichever the set happened to yield.
        for role in (ROLE_END_CLIENT, ROLE_IMPLEMENTATION_PARTNER, ROLE_PRIME_VENDOR):
            if role in roles:
                return CompanyMention(company, in_body[0], MENTION_ROLE_LABELLED, role)
    return CompanyMention(company, in_body[0], MENTION_DESCRIBED, None)
