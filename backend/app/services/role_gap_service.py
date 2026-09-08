"""Forward-looking resume gap analysis.

The resume tracking feature originally tried to answer "which resume variant gets
callbacks?". That question is unanswerable: a recruiter callback says nothing about
whether the resume reached an end client, and the acceptance metric only counts
milestones the user has to advance by hand.

This service answers the question the data can actually support - "which roles do I
keep losing, and what would a winning resume need?" - by aggregating the resume
picker breakdown the scoring pipeline already writes onto every processed email.
Nothing here is user-entered; it is all a by-product of scoring that was previously
computed and discarded.

Read-only. No new tables, and deliberately no new index: the query is bounded by
owner_id + created_at, both already indexed, and the JSON payloads are parsed in
Python rather than in the database.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import RecruiterEmail, ResumeAsset, utc_now
from app.schemas import resume_variant_code

# A skill has to show up this many times inside a role family before it is worth
# putting on a build list. Below that it is one recruiter's idiosyncratic wording.
MIN_SKILL_OCCURRENCES = 4

# Fragments the JD extractor emits alongside real skills. These survive the
# substring filter (nothing else in the list contains them) but carry no signal:
# "add development to your resume" is not an action anyone can take.
_STOP_SKILLS = frozenset(
    {
        'a', 'analysis', 'api', 'apis', 'application', 'applications', 'architecture',
        'as', 'automation', 'build', 'ci', 'cd', 'cloud', 'code', 'communication',
        'data', 'database', 'databases', 'design', 'development', 'engineering',
        'framework', 'frameworks', 'integration', 'language', 'languages',
        'management', 'methodologies', 'model', 'models', 'modules', 'monitoring',
        'operations', 'performance', 'pipeline', 'pipelines', 'platform', 'process',
        'production', 'programming', 'relational', 'security', 'server', 'service',
        'services', 'software', 'solution', 'solutions', 'stack', 'support',
        'system', 'systems', 'technologies', 'technology', 'test', 'testing',
        'tool', 'tools', 'tuning', 'web',
        # Parser artifacts - these are not skills the parser found, they are what it
        # writes when it found nothing.
        'none_detected', 'not_specified', 'role', 'unknown', 'n/a', 'none',
        # Naming the field is not a build target: "add DevOps to your resume" for a
        # DevOps role is tautology, not advice.
        'big data', 'data engineering', 'debugging', 'devops', 'oop',
        'ci/cd tools', 'project management', 'testing automation',
    }
)


def _json_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or '').strip()]


def clean_missing_skills(
    raw_skills: Iterable[str],
    *,
    whole_word_fragments: bool = False,
) -> list[str]:
    """Strip the extractor's fragments out of one JD's missing-skill list.

    The parser emits a phrase and its pieces together - "GitHub Actions", "GitHub"
    and "actions" all land in the same list. Keeping the pieces inflates counts and
    fills the build list with words like "actions". A token that is a strict
    substring of another token in the same list is always the fragment, so drop it
    and keep the longer phrase.

    `whole_word_fragments` requires that substring to fall on word boundaries, which
    is what the paragraph above actually describes. Without it, short acronyms are
    deleted by unrelated longer ones - "IAM" disappears from any JD that also says
    "CIAM" - and the skill silently never reaches a build list. It defaults off
    because the family report's output is pinned; role_target_service passes it.
    """
    unique: dict[str, str] = {}
    for skill in raw_skills:
        key = ' '.join(skill.split()).casefold()
        if key and key not in unique:
            unique[key] = ' '.join(skill.split())

    def _is_fragment_of(key: str, other: str) -> bool:
        if whole_word_fragments:
            return f' {key} ' in f' {other} '
        return key in other

    kept: list[str] = []
    for key, label in unique.items():
        if key in _STOP_SKILLS:
            continue
        if any(other != key and _is_fragment_of(key, other) for other in unique):
            continue
        kept.append(label)
    return kept


class _FamilyBucket:
    __slots__ = (
        'jd_count', 'flagged_count', 'role_fits', 'scores', 'variants', 'skills',
        'samples', 'titles', 'confidences',
    )

    def __init__(self) -> None:
        self.jd_count = 0
        self.flagged_count = 0
        self.role_fits: list[float] = []
        self.scores: list[float] = []
        self.variants: Counter[int] = Counter()
        self.skills: Counter[str] = Counter()
        self.samples: list[dict[str, Any]] = []
        self.titles: Counter[str] = Counter()
        self.confidences: list[float] = []


def role_gap_report(
    db: Session,
    *,
    owner_id: str,
    window_days: int = 90,
    min_jds: int = 5,
    skills_per_role: int = 12,
    limit: int = 12,
) -> dict[str, Any]:
    """Rank role families by how often the resume library fails them, and say why."""
    since = utc_now() - timedelta(days=max(1, window_days))

    rows = (
        db.query(
            RecruiterEmail.id,
            RecruiterEmail.role,
            RecruiterEmail.subject,
            RecruiterEmail.created_at,
            RecruiterEmail.resume_asset_id,
            RecruiterEmail.resume_picker_breakdown_json,
            RecruiterEmail.role_family,
            RecruiterEmail.role_family_confidence,
        )
        .filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.created_at >= since,
            RecruiterEmail.resume_picker_breakdown_json.isnot(None),
            RecruiterEmail.resume_picker_breakdown_json != '',
            RecruiterEmail.resume_picker_breakdown_json != '{}',
        )
        .all()
    )

    buckets: dict[str, _FamilyBucket] = defaultdict(_FamilyBucket)
    # How widely each skill is demanded across every family. A skill concentrated in
    # one family is a build target; one demanded everywhere is just background noise.
    skill_totals: Counter[str] = Counter()
    analysed = 0

    for row in rows:
        breakdown = _json_obj(row.resume_picker_breakdown_json)
        if not breakdown:
            continue
        analysed += 1

        # The column is the source of truth; the JSON is the fallback for rows the
        # backfill has not reached yet. Both hold the same answer for any row written
        # since the column existed, so this switch is a no-op on a backfilled table -
        # what it buys is a confidence to group on, which the JSON never carried.
        family = str(row.role_family or breakdown.get('jd_role_family') or 'general')
        bucket = buckets[family]
        bucket.jd_count += 1
        if row.role_family_confidence is not None:
            bucket.confidences.append(float(row.role_family_confidence))

        flagged = (
            breakdown.get('selection_status') == 'needs_review'
            or bool(breakdown.get('selection_warning'))
        )
        if flagged:
            bucket.flagged_count += 1

        role_fit = breakdown.get('role_family_fit_score')
        if isinstance(role_fit, (int, float)):
            bucket.role_fits.append(float(role_fit))
        final_score = breakdown.get('final_resume_score')
        if isinstance(final_score, (int, float)):
            bucket.scores.append(float(final_score))
        if row.resume_asset_id is not None:
            bucket.variants[int(row.resume_asset_id)] += 1

        title = ' '.join(str(row.role or '').split())
        if title:
            bucket.titles[title] += 1

        # Only flagged JDs contribute to the build list. A JD the library already
        # satisfies has nothing to teach us about what to write next.
        if flagged:
            missing = clean_missing_skills(
                _str_list(breakdown.get('mandatory_missing_skills'))
                + _str_list(breakdown.get('missing_priority_skills'))
            )
            bucket.skills.update(missing)
            skill_totals.update(set(missing))

            if len(bucket.samples) < 5:
                bucket.samples.append(
                    {
                        'email_id': int(row.id),
                        'role': title or ' '.join(str(row.subject or '').split())[:120],
                        'missing_skills': missing[:8],
                        'created_at': row.created_at,
                    }
                )

    variant_labels = _variant_labels(db, owner_id)

    groups: list[dict[str, Any]] = []
    for family, bucket in buckets.items():
        if bucket.jd_count < min_jds:
            continue
        best_variant_id, best_variant_uses = (
            bucket.variants.most_common(1)[0] if bucket.variants else (None, 0)
        )
        groups.append(
            {
                'role_family': family,
                'jd_count': bucket.jd_count,
                'flagged_count': bucket.flagged_count,
                'flagged_share': round(bucket.flagged_count / bucket.jd_count, 4),
                'median_role_fit': round(median(bucket.role_fits), 4) if bucket.role_fits else None,
                # How trustworthy the grouping itself is, as opposed to how well the
                # resumes scored. None means no row in the group is classified yet.
                'median_confidence': (
                    round(median(bucket.confidences), 4) if bucket.confidences else None
                ),
                'median_resume_score': round(median(bucket.scores), 4) if bucket.scores else None,
                'closest_variant_code': resume_variant_code(best_variant_id),
                'closest_variant_label': variant_labels.get(best_variant_id or -1, ''),
                'closest_variant_uses': best_variant_uses,
                'top_titles': [title for title, _ in bucket.titles.most_common(5)],
                'missing_skills': _rank_skills(
                    bucket.skills, skill_totals, limit=skills_per_role
                ),
                'sample_jds': bucket.samples,
            }
        )

    groups.sort(key=lambda group: group['flagged_count'], reverse=True)
    return {
        'window_days': window_days,
        'analysed_jds': analysed,
        'groups': groups[:limit],
    }


def _rank_skills(
    family_counts: Counter[str],
    skill_totals: Counter[str],
    *,
    limit: int,
    min_occurrences: int = MIN_SKILL_OCCURRENCES,
) -> list[dict[str, Any]]:
    """Rank a family's missing skills by demand *concentrated in that family*.

    Ranking on raw frequency puts generic filler at the top, because filler appears
    in every JD of every family. Weighting each count by the share of its demand
    that sits inside this family promotes the skills that actually distinguish the
    role - the JMeter/Dynatrace/Grafana cluster for performance work, the
    RAG/LangChain/embeddings cluster for GenAI - and demotes the filler.

    `min_occurrences` defaults to the family floor and is only lowered by callers
    grouping something smaller than a family, where four mentions is a share of the
    group the family floor never meant to demand. See role_target_service.
    """
    ranked: list[dict[str, Any]] = []
    for skill, count in family_counts.items():
        if count < min_occurrences:
            continue
        total = skill_totals.get(skill, count) or count
        concentration = count / total
        ranked.append(
            {
                'skill': skill,
                'jd_count': count,
                'concentration': round(concentration, 4),
                'score': round(count * concentration, 2),
            }
        )
    ranked.sort(key=lambda item: (item['score'], item['jd_count']), reverse=True)
    return ranked[:limit]


def _variant_labels(db: Session, owner_id: str) -> dict[int, str]:
    return {
        resume.id: (resume.variant_label or resume.file_name or '')
        for resume in db.query(ResumeAsset).filter(ResumeAsset.owner_id == owner_id).all()
    }
