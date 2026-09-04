"""Controlled vocabularies for the fields a filter actually needs (W10).

Four small sets, each derived from every distinct value in production rather
than from what the concepts ought to be. They are deliberately not a general
normalization framework: `work_mode`, `location`, `domain` and `interview_type`
are the columns W8's search and W11's charts read, and each is small enough to
curate by hand - 32 distinct domains over 70 rows, 5 work modes over 741.

**Reading, not writing.** Nothing here modifies a stored value. The extractor is
not at fault for any of this (`temp157.md` §7.5.1), and rewriting history to suit
a filter would destroy the evidence of what a recruiter actually sent. A derived
value is returned with the fact that it was derived, so §3's tiers survive:
what the record asserts and what this module worked out are never confused.

**One value can carry several concepts.** `Financial services/payments` and
`Government/Regulatory` are single stored strings naming two domains each. A
one-to-one canonical map would have to pick one and silently lose the other, so
`normalize_domain` returns a tuple. A filter for payments finds that row; so does
a filter for banking.

**The location column is half work mode.** 544 of 1,105 populated values are
`Remote, Remote, USA`, `onsite`, `unknown` or similar - the field says how, not
where. `normalize_location` returns None for those rather than inventing a
place, and `derive_work_mode` reads the same strings the other way: 133 rows
carry a work mode in `location` while `work_mode` itself is empty, and only 4
rows disagree when both are populated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.field_coverage import is_placeholder

# --- Work mode ------------------------------------------------------------
# 741 populated, 5 distinct: Remote 383, Onsite 218, Hybrid 134, plus three
# stragglers that are Hybrid spelled long ("Hybrid - 3 Days onsite") and one
# shouted ONSITE.
REMOTE = "Remote"
ONSITE = "Onsite"
HYBRID = "Hybrid"
WORK_MODES = (REMOTE, ONSITE, HYBRID)

_WORK_MODE_PREFIX = ((HYBRID, "hybrid"), (REMOTE, "remote"), (ONSITE, "onsite"))


def normalize_work_mode(value: str | None) -> str | None:
    """Canonical work mode, or None when the string does not name one.

    Hybrid is tested first: "Hybrid - 3 Days onsite" contains both words, and
    the leading one is the answer.
    """
    text = (value or "").strip().lower()
    if not text or is_placeholder(text):
        return None
    for canonical, prefix in _WORK_MODE_PREFIX:
        if text.startswith(prefix):
            return canonical
    if "work from home" in text or text in {"wfh", "fully remote", "100% remote"}:
        return REMOTE
    return None


@dataclass(frozen=True)
class DerivedWorkMode:
    value: str | None
    source: str  # "recorded" | "location" | "none"

    @property
    def is_derived(self) -> bool:
        return self.source == "location"


def derive_work_mode(work_mode: str | None, location: str | None) -> DerivedWorkMode:
    """Work mode from its own column, else read out of `location`.

    `location` holds a work mode on 484 rows, and on 133 of them `work_mode` is
    empty. Recovering those lifts coverage from 66.3% to 78.2% without writing
    anything. The source travels with the value because a derived answer is a
    tier-2 resolution, not something the record asserted.
    """
    recorded = normalize_work_mode(work_mode)
    if recorded:
        return DerivedWorkMode(recorded, "recorded")
    from_location = normalize_work_mode(location)
    if from_location:
        return DerivedWorkMode(from_location, "location")
    return DerivedWorkMode(None, "none")


# --- Location -------------------------------------------------------------
# Two stored formats for one place: "Dallas, Texas, USA" (19 rows) and
# "Dallas, TX" (14). Normalizing joins 33 rows that a filter previously split.
_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
_STATE_CODES = frozenset(_STATES.values())

# City spellings that are one place. Kept tiny and literal on purpose: this is
# not the entity resolver, and a fuzzy city matcher here would become a second
# opinion on identity - the failure `entity_resolution_service` exists to stop.
_CITY_ALIASES = {
    "nyc": "New York",
    "new york city": "New York",
    "sf": "San Francisco",
    "dfw": "Dallas",
    "washington dc": "Washington",
}

_NON_PLACES = frozenset({"remote", "onsite", "hybrid", "unknown", "any", "various", "usa", "us"})


@dataclass(frozen=True)
class Place:
    city: str
    state: str | None

    def as_text(self) -> str:
        return f"{self.city}, {self.state}" if self.state else self.city


def normalize_location(value: str | None) -> Place | None:
    """A place, or None when the string names a work mode, nothing, or a country.

    Returning None for `Remote, Remote, USA` is the point: it is not a location
    that happens to be missing, it is the wrong kind of answer in this column.
    """
    text = (value or "").strip()
    if not text or is_placeholder(text):
        return None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    # Trailing country, then anything that is purely a work mode or filler.
    while parts and parts[-1].lower() in {"usa", "us", "united states"}:
        parts.pop()
    parts = [part for part in parts if part.lower() not in _NON_PLACES]
    if not parts:
        return None

    city_raw = parts[0]
    city = _CITY_ALIASES.get(city_raw.lower(), city_raw)
    state: str | None = None
    if len(parts) > 1:
        tail = parts[1]
        if tail.upper() in _STATE_CODES:
            state = tail.upper()
        else:
            state = _STATES.get(tail.lower())
    if not re.search(r"[A-Za-z]", city):
        return None
    return Place(city=city.title() if city.islower() else city, state=state)


# --- Domain ---------------------------------------------------------------
# 32 distinct values over 70 rows, collapsing to ten concepts. Matched on
# substrings because the stored values are phrases, not labels: "Banking or
# Financial Services" and "Trading and Banking" are both banking.
DOMAIN_VOCABULARY: dict[str, tuple[str, ...]] = {
    "banking_finance": (
        "banking", "financial", "finance", "capital market", "wealth management",
        "trading", "mortgage", "asset",
    ),
    "payments": ("payment", "cards"),
    "healthcare": ("healthcare", "health care", "pharma", "life science"),
    "insurance": ("insurance",),
    "telecom": ("telecom", "telecommunication", "wireless", "network provider"),
    "airline": ("airline", "aviation"),
    "government": ("government", "regulatory", "public sector", "state of"),
    "retail": ("retail", "ecommerce", "e-commerce"),
    "transportation": ("transportation", "logistics", "freight"),
    "energy": ("energy", "utilities", "oil and gas"),
}


def normalize_domain(value: str | None) -> tuple[str, ...]:
    """Every canonical domain a stored value names, in vocabulary order.

    A tuple rather than one value: `Cards & Payments / Asset & Wealth Management`
    is payments *and* banking, and picking one would lose a row from whichever
    filter did not win.
    """
    text = (value or "").strip().lower()
    if not text or is_placeholder(text):
        return ()
    return tuple(
        canonical
        for canonical, needles in DOMAIN_VOCABULARY.items()
        if any(needle in text for needle in needles)
    )


def domain_matches(stored: str | None, wanted: str) -> bool:
    """True when a stored domain names the wanted concept.

    `wanted` may be a canonical key ("banking_finance") or anything the
    vocabulary recognises ("banking", "finance"), so a user's own word works
    without them learning the vocabulary's spelling.
    """
    canonical = normalize_domain(stored)
    target = wanted.strip().lower().replace(" ", "_")
    if target in canonical:
        return True
    return bool(set(normalize_domain(wanted)) & set(canonical))


# --- Interview type -------------------------------------------------------
# 614 populated, 217 distinct, collapsing to four: "F2F Interview", "F2F",
# "F2F interview", "In Person Interview", "In-person interview" and "Face to
# Face Interview" are one concept spelled six ways.
IN_PERSON = "in_person"
VIDEO = "video"
PHONE = "phone"
INTERVIEW_TYPES = (IN_PERSON, VIDEO, PHONE)

_INTERVIEW_VOCABULARY: dict[str, tuple[str, ...]] = {
    IN_PERSON: ("f2f", "face to face", "face-to-face", "in person", "in-person", "onsite"),
    VIDEO: ("video", "virtual", "zoom", "teams", "webex", "skype"),
    PHONE: ("phone", "telephonic", "call"),
}


def normalize_interview_type(value: str | None) -> tuple[str, ...]:
    """Every interview format a stored value names.

    A tuple because "2 Video + F2F" is a real stored value describing a process
    with both, and a chart of face-to-face interviews should count it.
    """
    text = (value or "").strip().lower()
    if not text or is_placeholder(text):
        return ()
    return tuple(
        canonical
        for canonical, needles in _INTERVIEW_VOCABULARY.items()
        if any(needle in text for needle in needles)
    )


# --- Role -----------------------------------------------------------------
# 645 distinct job titles over 1,117 rows - far too many to chart, and mostly
# one job spelled differently: "Java Developer" 74, "Java Full Stack Developer"
# 35, "Senior Java Developer" 16, "Sr Java Developer" 13.
#
# Seniority is deliberately not a family. "Senior Java Developer" and "Java
# Developer" are the same role at different levels, and a chart of what
# recruiters are looking for should count them together; splitting them would
# answer a question nobody asked.
ROLE_VOCABULARY: dict[str, tuple[str, ...]] = {
    "java": ("java", "j2ee", "spring boot"),
    "full_stack": ("full stack", "fullstack", "full-stack"),
    "backend": ("backend", "back end", "back-end"),
    "frontend": ("frontend", "front end", "front-end", "react", "angular", "ui developer"),
    "architect": ("architect",),
    "data": ("data engineer", "data scientist", "etl", "big data", "spark", "databricks"),
    "devops": ("devops", "sre", "platform engineer", "cloud engineer", "kubernetes"),
    "qa": ("qa ", "quality assurance", "test engineer", "sdet", "automation test"),
    "business_analyst": ("business analyst", "ba ", "business systems analyst"),
    "project_manager": ("project manager", "program manager", "scrum master", "delivery manager"),
    "dotnet": (".net", "dotnet", "c#"),
    "python": ("python", "django"),
    "mobile": ("ios developer", "android developer", "mobile developer", "react native"),
    "salesforce": ("salesforce", "sfdc"),
    "security": ("security", "cyber", "infosec"),
}


def normalize_role(value: str | None) -> tuple[str, ...]:
    """Every role family a job title names.

    Multi-label for the same reason `normalize_domain` is: "Java Full Stack
    Developer" is 35 rows that belong in both the Java count and the full-stack
    count, and picking one would understate whichever lost.
    """
    text = f" {(value or '').strip().lower()} "
    if not text.strip() or is_placeholder(text.strip()):
        return ()
    return tuple(
        canonical
        for canonical, needles in ROLE_VOCABULARY.items()
        if any(needle in text for needle in needles)
    )
