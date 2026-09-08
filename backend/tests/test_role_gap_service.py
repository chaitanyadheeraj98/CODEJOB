import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RecruiterEmail
from app.services import role_gap_service


def test_clean_missing_skills_drops_fragments_of_longer_phrases():
    """The JD parser emits a phrase and its pieces together; only the phrase is real."""
    cleaned = role_gap_service.clean_missing_skills(
        ['GitHub Actions', 'GitHub', 'actions', 'Agile methodologies', 'methodologies', 'JMeter']
    )
    assert set(cleaned) == {'GitHub Actions', 'Agile methodologies', 'JMeter'}


def test_clean_missing_skills_drops_generic_filler_and_parser_artifacts():
    cleaned = role_gap_service.clean_missing_skills(
        ['development', 'management', 'none_detected', 'Role', 'Dynatrace']
    )
    assert cleaned == ['Dynatrace']


def test_clean_missing_skills_is_case_and_whitespace_insensitive():
    cleaned = role_gap_service.clean_missing_skills(['JMeter', 'jmeter', '  JMeter  '])
    assert cleaned == ['JMeter']


def test_rank_skills_prefers_concentration_over_raw_frequency():
    """A skill demanded everywhere is noise; one concentrated in this family is signal.

    "Azure" is asked for twice as often as "JMeter" inside this family, but almost
    all of that demand comes from elsewhere. Ranking on raw counts would bury the
    skill that actually distinguishes the role.
    """
    family = role_gap_service.Counter({'Azure': 100, 'JMeter': 50})
    totals = role_gap_service.Counter({'Azure': 1000, 'JMeter': 55})

    ranked = role_gap_service._rank_skills(family, totals, limit=5)

    assert [item['skill'] for item in ranked] == ['JMeter', 'Azure']


def test_rank_skills_ignores_skills_below_the_occurrence_floor():
    family = role_gap_service.Counter({'Rare': role_gap_service.MIN_SKILL_OCCURRENCES - 1})
    ranked = role_gap_service._rank_skills(family, role_gap_service.Counter({'Rare': 1}), limit=5)
    assert ranked == []


def test_json_obj_survives_malformed_payloads():
    assert role_gap_service._json_obj(None) == {}
    assert role_gap_service._json_obj('') == {}
    assert role_gap_service._json_obj('not json') == {}
    assert role_gap_service._json_obj('[1, 2]') == {}
    assert role_gap_service._json_obj(json.dumps({'a': 1})) == {'a': 1}


class RoleGapReportGroupingTests(unittest.TestCase):
    """Which family a JD is grouped under, now that the answer lives in a column."""

    def setUp(self) -> None:
        self.engine = create_engine(
            'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(Base.metadata.drop_all, self.engine)

    @staticmethod
    def _seed(
        db: Session,
        *,
        count: int,
        json_family: str,
        role_family: str | None = None,
        confidence: float | None = None,
    ) -> None:
        """`count` flagged JDs, so every one of them reaches the build list."""
        for index in range(count):
            db.add(
                RecruiterEmail(
                    owner_id='owner',
                    sender='rec@example.com',
                    subject=f'JD {index}',
                    body='jd',
                    role='IT Security Auditor',
                    role_family=role_family,
                    role_family_confidence=confidence,
                    resume_picker_breakdown_json=json.dumps(
                        {
                            'jd_role_family': json_family,
                            'selection_status': 'needs_review',
                            'role_family_fit_score': 0.4,
                            'final_resume_score': 55.0,
                            'mandatory_missing_skills': ['API Security'],
                        }
                    ),
                )
            )
        db.commit()

    def _report(self, db: Session) -> dict:
        return role_gap_service.role_gap_report(db, owner_id='owner', min_jds=1)

    def test_the_column_wins_over_the_stale_json_copy(self):
        """Both carry a family; only the column survives a reclassification, so a
        row that has been backfilled under a newer taxonomy must group by it."""
        with Session(self.engine) as db:
            self._seed(db, count=2, json_family='general', role_family='devops_cloud', confidence=0.9)
            groups = self._report(db)['groups']

        self.assertEqual([group['role_family'] for group in groups], ['devops_cloud'])

    def test_rows_the_backfill_has_not_reached_still_group_by_the_json(self):
        """NULL means never classified, not `general` - the JSON is all we have."""
        with Session(self.engine) as db:
            self._seed(db, count=2, json_family='java_backend', role_family=None)
            groups = self._report(db)['groups']

        self.assertEqual([group['role_family'] for group in groups], ['java_backend'])
        self.assertIsNone(
            groups[0]['median_confidence'],
            'an unclassified group must not report a confidence it does not have',
        )

    def test_median_confidence_summarises_how_trustworthy_the_grouping_is(self):
        with Session(self.engine) as db:
            self._seed(db, count=2, json_family='general', role_family='java_fullstack', confidence=0.5)
            self._seed(db, count=1, json_family='general', role_family='java_fullstack', confidence=0.9)
            groups = self._report(db)['groups']

        self.assertEqual(groups[0]['jd_count'], 3)
        self.assertEqual(groups[0]['median_confidence'], 0.5)

    def test_an_unclassifiable_group_is_reported_with_zero_confidence(self):
        """The motivating case: security work has no family, so it lands in
        `general` - but now it says so, instead of looking like a real grouping.
        """
        with Session(self.engine) as db:
            self._seed(db, count=2, json_family='general', role_family='general', confidence=0.0)
            groups = self._report(db)['groups']

        self.assertEqual(groups[0]['role_family'], 'general')
        self.assertEqual(groups[0]['median_confidence'], 0.0)
