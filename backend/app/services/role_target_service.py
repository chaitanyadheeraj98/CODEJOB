"""Answer "what would it take to apply for <role>?" for a role the taxonomy cannot name.

`role_gap_service` aggregates the seven role families the scoring pipeline knows about.
That question is retrospective and closed: a role only shows up once it is already a
pattern, and a role outside the seven does not land in `general` - it gets scattered
across them. Security work is the live example: 125 security-titled JDs in the corpus
split across six families, none in `general`.

The scatter is also why the existing build list cannot surface those skills. Ranking by
demand *concentrated in one family* is the right call for a family aggregate, but a role
smeared across six families has low concentration everywhere - `IAM` ranks last inside
`devops_cloud` at 0.21 and `API Security` never surfaces at all.

So this service does not group by family. It takes the role as a string and assembles a
cohort out of JD evidence - stored `role` and `skills_text`, populated on 98% of the
corpus, against the 56% that carries a scoring breakdown. Because the cohort is built
around one role instead of one family, the same concentration weighting that buried
those skills now promotes them.

Read-only, and deliberately inert: nothing here feeds `final_resume_score`, so no JD
gets a different resume because this module exists.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import RecruiterEmail, ResumeAsset, utc_now
from app.schemas import resume_variant_code
# Reused rather than reimplemented: the extractor emits the same fragments here as it
# does there, and the whole point of the cohort is that it feeds the ranking the build
# list already uses.
from app.services.role_gap_service import (
    MIN_SKILL_OCCURRENCES,
    _rank_skills,
    clean_missing_skills,
)
from app.services.role_similarity_service import _normalized_title
from app.skill_taxonomy import (
    compute_intent_weighted_match,
    entries_from_skills_text,
    normalize_taxonomy_text,
)

# Words that appear in every third job title. Scoring a title match against these makes
# "Java Developer" look like a hit for "IT Security Auditor", so they are stripped
# before the overlap is measured and only added back when a title is nothing but them.
_GENERIC_TITLE_WORDS = frozenset(
    {
        'it', 'developer', 'specialist', 'architect', 'manager', 'consultant',
        'administrator', 'admin', 'associate', 'professional', 'staff', 'expert',
        'technical', 'technology', 'software', 'systems', 'system', 'application',
        'applications', 'services', 'service', 'solutions', 'support', 'team',
        'full', 'stack', 'remote', 'onsite', 'hybrid', 'contract', 'position',
        'role', 'opening', 'urgent', 'immediate', 'need', 'req', 'hiring', 'w2',
        'c2c', 'usc', 'gc', 'ii', 'iii', 'iv', 'level', 'years', 'exp',
    }
)

# A JD joins the cohort on its title when it shares this much of the target's
# distinguishing vocabulary. Half of one token is not a match, so a single-token target
# ("Security Engineer" -> "security") admits only titles that actually carry the word.
TITLE_SEED_THRESHOLD = 0.5

# ...and on its skills when it demands this much of what the seed demands. Set low
# enough that a differently-titled posting for the same work ("BISO", "GRC Lead") is
# admitted, high enough that a Java JD sharing "SQL" and "REST" is not.
SKILL_RECALL_THRESHOLD = 0.35

# How many seed skills count as "the role's vocabulary" for the recall pass. Past this
# the tail is one recruiter's wording and every JD looks equally distant from it.
CORE_VOCABULARY_SIZE = 25

# Cohort sizes at which the answer stops being a finding and starts being a guess.
CORPUS_EVIDENCE_MIN = 25
THIN_EVIDENCE_MIN = 3

_SKILL_SPLIT_RE = re.compile(r'[,;|\n\r]+')

# The extractor occasionally emits a clause instead of a skill. A keyword nobody would
# paste onto a resume is not a build target.
_MAX_SKILL_WORDS = 5
_MAX_SKILL_CHARS = 48


def _short_title(text: str | None) -> str:
    """The `role` column sometimes holds an entire posting rather than a title, so a
    sample rendered straight out of it can be several thousand characters of HTML."""
    return ' '.join(str(text or '').split())[:120]


def _title_tokens(text: str | None) -> set[str]:
    """Normalised title words, with seniority and engineer/developer already collapsed."""
    return set(_normalized_title(str(text or '')).split())


def _distinguishing_tokens(tokens: set[str]) -> set[str]:
    """The words that make this title *this* role rather than a job posting."""
    distinguishing = tokens - _GENERIC_TITLE_WORDS
    return distinguishing or tokens


def _skill_tokens(skills_text: str | None) -> list[str]:
    """One JD's skills_text as build-list candidates.

    `clean_missing_skills` does the real work - dropping the extractor's fragments and
    the filler words - and is the same filter the family build list runs, so a skill
    that is not worth suggesting there is not worth suggesting here either.
    """
    parts: list[str] = []
    for raw in _SKILL_SPLIT_RE.split(str(skills_text or '')):
        token = _trim_orphan_brackets(' '.join(raw.split()).strip(' .:<>-'))
        if not token or len(token) > _MAX_SKILL_CHARS or len(token.split()) > _MAX_SKILL_WORDS:
            continue
        # A bare number is what is left when the split lands inside "Java 17, 8" - no
        # letter, no skill. "SOC 2" and "IPv6" still qualify.
        if not any(character.isalpha() for character in token):
            continue
        parts.append(token)
    return clean_missing_skills(parts, whole_word_fragments=True)


def _trim_orphan_brackets(token: str) -> str:
    """Drop a bracket the comma split cut in half, and only that one.

    The extractor writes "Lightning Web Components (LWC)" and the split leaves "WSDL)"
    behind from elsewhere. Stripping brackets unconditionally fixes the second and
    mangles the first, so only an unbalanced one goes.
    """
    for opener, closer in (('(', ')'), ('[', ']'), ('{', '}')):
        while token.endswith(closer) and token.count(closer) > token.count(opener):
            token = token[:-1].rstrip()
        while token.startswith(opener) and token.count(opener) > token.count(closer):
            token = token[1:].lstrip()
    return token


def _contains_skill(normalized_resume_text: str, skill: str) -> bool:
    """Is this keyword literally on the resume?

    Deliberately a text check rather than a taxonomy lookup: for a role the taxonomy has
    no family for, most of the demanded vocabulary is not in the taxonomy at all, and a
    lookup would report every one of those as absent from every resume.
    """
    needle = normalize_taxonomy_text(skill)
    return bool(needle) and f' {needle} ' in normalized_resume_text


def _evidence_tier(cohort_size: int) -> str:
    if cohort_size >= CORPUS_EVIDENCE_MIN:
        return 'corpus'
    if cohort_size >= THIN_EVIDENCE_MIN:
        return 'thin'
    return 'none'


def _empty_report(target_role: str, window_days: int, cohort_size: int = 0) -> dict[str, Any]:
    """What the corpus has nothing to say looks like.

    An empty skill list with a `none` tier is the honest answer. Falling back to the
    library's generic strengths here would read as advice about the target role while
    being about something else entirely.
    """
    return {
        'target_role': target_role,
        'window_days': window_days,
        'cohort_size': cohort_size,
        'evidence_tier': 'none',
        'demanded_skills': [],
        'variants': [],
        'closest_variant_code': '',
        'closest_variant_label': '',
        'verdict_tone': 'ok',
        'verdict': (
            f'No job description in the last {window_days} days looks like this role, so there '
            'is nothing in your inbox to compare your resumes against. Widen the window, or '
            'check the spelling of the title.'
        ),
        'sample_jds': [],
        'narrative': None,
    }


def analyse_role_target(
    db: Session,
    *,
    owner_id: str,
    target_role: str,
    window_days: int = 365,
    cohort_limit: int = 400,
    skills_limit: int = 20,
) -> dict[str, Any]:
    """How far every resume variant is from one named role, and what would close the gap."""
    target_role = ' '.join(str(target_role or '').split())
    if not target_role:
        return _empty_report(target_role, window_days)

    since = utc_now() - timedelta(days=max(1, window_days))
    rows = (
        db.query(
            RecruiterEmail.id,
            RecruiterEmail.role,
            RecruiterEmail.skills_text,
            RecruiterEmail.role_family,
        )
        .filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.created_at >= since,
            RecruiterEmail.skills_text.isnot(None),
            RecruiterEmail.skills_text != '',
        )
        .all()
    )
    if not rows:
        return _empty_report(target_role, window_days)

    # Every skill's demand across the whole window. This is the denominator that turns a
    # raw count into "concentrated in this role" rather than "common in every posting".
    window_totals: Counter[str] = Counter()
    row_skills: dict[int, list[str]] = {}
    for row in rows:
        skills = _skill_tokens(row.skills_text)
        row_skills[int(row.id)] = skills
        window_totals.update(set(skills))

    cohort = _build_cohort(
        rows,
        row_skills,
        window_totals,
        target_role=target_role,
        cohort_limit=cohort_limit,
    )
    evidence_tier = _evidence_tier(len(cohort))
    if evidence_tier == 'none':
        return _empty_report(target_role, window_days, cohort_size=len(cohort))

    cohort_counts: Counter[str] = Counter()
    for member in cohort:
        cohort_counts.update(set(row_skills[member['email_id']]))

    # The family build list needs four mentions before it will suggest a skill, because a
    # family spans hundreds of JDs. A cohort of twelve does not get four of anything, so
    # the floor scales with the cohort and only reaches the family floor once the cohort
    # is large enough for it to mean the same thing.
    min_occurrences = max(2, min(MIN_SKILL_OCCURRENCES, round(len(cohort) * 0.1)))
    ranked = _rank_skills(
        cohort_counts, window_totals, limit=60, min_occurrences=min_occurrences
    )
    if not ranked:
        return _empty_report(target_role, window_days, cohort_size=len(cohort))

    demanded = [dict(item) for item in ranked[:skills_limit]]
    # The match runs against the wider list: the tail still tells the taxonomy which
    # clusters this role sits in, even though it is too thin to put on a build list.
    cohort_skills_text = ', '.join(item['skill'] for item in ranked)

    variants = _rank_variants(
        db,
        owner_id=owner_id,
        target_role=target_role,
        cohort_skills_text=cohort_skills_text,
        demanded=demanded,
    )

    closest = variants[0] if variants else None
    if closest is not None:
        closest_text = closest.pop('_normalized_skills')
        for item in demanded:
            item['covered_by_closest'] = _contains_skill(closest_text, item['skill'])
    else:
        for item in demanded:
            item['covered_by_closest'] = False
    for variant in variants:
        variant.pop('_normalized_skills', None)

    tone, verdict = _verdict(closest, evidence_tier=evidence_tier, cohort_size=len(cohort))

    report: dict[str, Any] = {
        'target_role': target_role,
        'window_days': window_days,
        'cohort_size': len(cohort),
        'evidence_tier': evidence_tier,
        'demanded_skills': demanded,
        'variants': variants,
        'closest_variant_code': closest['variant_code'] if closest else '',
        'closest_variant_label': closest['variant_label'] if closest else '',
        'verdict_tone': tone,
        'verdict': verdict,
        'sample_jds': [
            {
                'email_id': member['email_id'],
                'role': member['role'],
                'skills': ', '.join(row_skills[member['email_id']][:8]),
                'match_reason': member['match_reason'],
                'match_score': member['match_score'],
            }
            for member in cohort[:5]
        ],
        'narrative': None,
    }
    report['narrative'] = _narrative(report)
    return report


def _build_cohort(
    rows: Iterable[Any],
    row_skills: dict[int, list[str]],
    window_totals: Counter[str],
    *,
    target_role: str,
    cohort_limit: int,
) -> list[dict[str, Any]]:
    """Assemble the JDs that are about this role, in three passes.

    Title first, because a recruiter naming the role is the strongest signal available.
    Then the seed's own vocabulary, then everything else that demands enough of it - which
    is what admits the postings that describe the work without using the user's words for
    it. Every member records which pass admitted it and how strongly, so the cohort can be
    audited instead of taken on faith.
    """
    target_tokens = _distinguishing_tokens(_title_tokens(target_role))
    if not target_tokens:
        return []

    seed: list[dict[str, Any]] = []
    remainder: list[Any] = []
    for row in rows:
        tokens = _title_tokens(row.role)
        overlap = len(target_tokens & tokens) / len(target_tokens) if tokens else 0.0
        if overlap >= TITLE_SEED_THRESHOLD:
            seed.append(
                {
                    'email_id': int(row.id),
                    'role': _short_title(row.role),
                    'role_family': str(row.role_family or ''),
                    'match_reason': 'title',
                    'match_score': round(overlap, 4),
                }
            )
        else:
            remainder.append(row)

    if not seed:
        return []

    vocabulary: Counter[str] = Counter()
    for member in seed:
        vocabulary.update(set(row_skills[member['email_id']]))

    # Recall keys on what the seed demands *and nobody else does*, not on what it demands
    # most often. Ranking the seed by raw frequency returns the corpus's most common
    # skills, because they are common everywhere - a security seed's top skills come back
    # Spring Boot, Docker, AWS, and recall then admits every Java posting in the window.
    # Weighting by concentration is the same correction the build list already applies.
    core = dict(
        sorted(
            (
                (skill, count * (count / (window_totals.get(skill, count) or count)))
                for skill, count in vocabulary.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:CORE_VOCABULARY_SIZE]
    )
    core_mass = sum(core.values())

    recalled: list[dict[str, Any]] = []
    if core_mass:
        for row in remainder:
            skills = set(row_skills[int(row.id)])
            covered = sum(count for skill, count in core.items() if skill in skills)
            share = covered / core_mass
            if share >= SKILL_RECALL_THRESHOLD:
                recalled.append(
                    {
                        'email_id': int(row.id),
                        'role': _short_title(row.role),
                        'role_family': str(row.role_family or ''),
                        'match_reason': 'skills',
                        'match_score': round(share, 4),
                    }
                )

    # Title matches outrank skill matches at equal score: the recruiter said the word.
    cohort = sorted(
        seed + recalled,
        key=lambda member: (member['match_score'], member['match_reason'] == 'title'),
        reverse=True,
    )
    return cohort[: max(1, cohort_limit)]


def _rank_variants(
    db: Session,
    *,
    owner_id: str,
    target_role: str,
    cohort_skills_text: str,
    demanded: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score every enabled resume against the cohort, worst news first.

    `compute_intent_weighted_match` is the same function the picker uses to decide which
    resume goes out, so "why is this variant not close" is answered in the picker's own
    terms rather than in a second opinion invented here.
    """
    resumes = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == owner_id, ResumeAsset.is_enabled.is_(True))
        .all()
    )
    demanded_mass = sum(float(item['score']) for item in demanded) or 1.0

    variants: list[dict[str, Any]] = []
    for resume in resumes:
        normalized_skills = f' {normalize_taxonomy_text(resume.skills_text)} '
        matched = [item for item in demanded if _contains_skill(normalized_skills, item['skill'])]
        matched_names = {item['skill'] for item in matched}
        missing = [item for item in demanded if item['skill'] not in matched_names]
        breakdown = compute_intent_weighted_match(
            jd_role=target_role,
            jd_skills_text=cohort_skills_text,
            resume_skills_text=resume.skills_text,
        )
        variants.append(
            {
                'variant_code': resume_variant_code(resume.id),
                'variant_label': resume.variant_label or resume.primary_role or resume.file_name or '',
                'coverage': round(sum(float(item['score']) for item in matched) / demanded_mass, 4),
                'matched_skills': [item['skill'] for item in matched][:12],
                'missing_skills': [item['skill'] for item in missing][:12],
                'role_alignment_score': round(float(breakdown.role_alignment_score), 4),
                'foundation_score': round(float(breakdown.foundation_score), 4),
                '_normalized_skills': normalized_skills,
            }
        )

    # variant_code breaks the tie. A library aimed at another role scores 0.0 across the
    # board, and without a deterministic last key the "closest" variant is whichever row
    # the database happened to return first - a different answer to the same question on
    # every refresh.
    variants.sort(key=lambda variant: (-variant['coverage'], -variant['foundation_score'], variant['variant_code']))
    return variants


def _verdict(
    closest: dict[str, Any] | None,
    *,
    evidence_tier: str,
    cohort_size: int,
) -> tuple[str, str]:
    """The one sentence the user came for, in the tones the gap cards already use."""
    if closest is None:
        return 'ok', 'No enabled resume to compare against. Upload or enable a variant first.'

    label = closest['variant_label'] or closest['variant_code']
    coverage = int(round(float(closest['coverage']) * 100))
    hedge = '' if evidence_tier == 'corpus' else (
        f' Only {cohort_size} matching postings, so treat this as a hint rather than a pattern.'
    )

    if closest['coverage'] >= 0.6 and closest['role_alignment_score'] >= 0.75:
        return 'ok', (
            f'{label} already carries {coverage}% of what these postings ask for. '
            f'Apply with it and fill the few remaining gaps in the cover letter.{hedge}'
        )
    if closest['coverage'] >= 0.35:
        return 'close', (
            f'{label} is your closest resume at {coverage}% of the demanded vocabulary, but the '
            f'named tools below are missing. A focused variant off this one should compete.{hedge}'
        )
    return 'wrong', (
        f'No resume in your library is the right shape for this role - the closest, {label}, '
        f'carries {coverage}% of what these postings ask for. This needs a new resume built '
        f'around the skills below, not an edit to an existing one.{hedge}'
    )


_NARRATIVE_PROMPT = """You are explaining a resume gap analysis to the person who owns the resumes.

Target role: {target_role}
Job descriptions in the corpus matching this role: {cohort_size}
Closest resume variant: {closest} covering {coverage}% of the demanded vocabulary
Skills these postings demand that the closest resume already has: {matched}
Skills these postings demand that it does not: {missing}

Write one paragraph, at most 90 words, explaining why that variant is or is not close and
what the user would have to add. Use only the skill names listed above - do not name any
technology that does not appear in those two lists, and do not invent counts. Address the
user as "you". No preamble, no bullet points, no heading."""


def _narrative(report: dict[str, Any]) -> str | None:
    """One paragraph of phrasing over numbers that were already computed.

    The model is given the finding and asked to say it in prose. It never contributes a
    skill: every name it is allowed to use came out of the corpus with a `jd_count`
    behind it. If it is slow, down, or absent the deterministic payload stands on its
    own - the endpoint must not depend on a local model being up.
    """
    from app.config import settings

    if not settings.feature_role_target_narrative_enabled:
        return None
    closest = report['variants'][0] if report['variants'] else None
    if closest is None:
        return None

    try:
        from app.ai.chat.llm import build_chat_llm

        response = build_chat_llm().invoke(
            _NARRATIVE_PROMPT.format(
                target_role=report['target_role'],
                cohort_size=report['cohort_size'],
                closest=closest['variant_label'] or closest['variant_code'],
                coverage=int(round(float(closest['coverage']) * 100)),
                matched=', '.join(closest['matched_skills']) or 'none',
                missing=', '.join(closest['missing_skills']) or 'none',
            )
        )
        text = ' '.join(str(getattr(response, 'content', '') or '').split())
    except Exception:
        return None
    return text[:1200] or None
