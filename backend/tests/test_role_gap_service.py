import json

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
