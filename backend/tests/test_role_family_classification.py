"""classify_role_family reports the evidence, and reports it honestly.

The point of this module is not that the families are right - they are the same
families detect_role_family has always produced, and the parity test below pins
that. The point is the confidence: an aggregate that groups by role family needs
to know which of its groups it is entitled to trust.
"""

import pytest

from app.services.role_provenance import role_family_fields
from app.skill_taxonomy import (
    ROLE_FAMILY_TAXONOMY_VERSION,
    classify_role_family,
    detect_role_family,
)

# role, skills_text - spanning every branch of the ladder, because the branches are
# what the wrapper could have reordered.
PARITY_CASES = [
    ('Java Backend Developer', 'Java, Spring Boot'),
    ('Java Full Stack Developer', 'Java, React, Spring Boot'),
    ('Java Full Stack Developer', 'LangChain, LLMs, RAG, Embeddings'),
    ('Machine Learning Engineer', 'Python'),
    ('Frontend Engineer', 'React, TypeScript'),
    ('DevOps Engineer', 'Kubernetes, Terraform'),
    ('Data Engineer', 'SQL, Oracle'),
    ('IT Security Auditor', 'Oracle, Java, Spring Boot'),
    ('IT Security Auditor', ''),
    ('', 'LangChain, LLMs, RAG, Embeddings, Vector databases, Pinecone'),
    ('', 'React, TypeScript, Angular'),
    ('', 'none_detected'),
    ('', ''),
    (None, None),
]


@pytest.mark.parametrize('role,skills', PARITY_CASES)
def test_the_wrapper_returns_exactly_what_the_classifier_decided(role, skills):
    """detect_role_family is now a one-liner over classify_role_family, and ~10
    scoring call sites still depend on it answering as it always has."""
    assert detect_role_family(role, skills) == classify_role_family(role, skills).family


def test_a_named_role_is_the_strongest_signal_this_classifier_has():
    """The recruiter said "Java Backend Developer", so the skill weights - which
    are a dead heat here - do not get a vote."""
    result = classify_role_family('Java Backend Developer', 'Java, Spring Boot')
    assert result.family == 'java_backend'
    assert result.method == 'title_regex'
    assert result.confidence == 0.9


def test_a_decisive_skill_win_reads_near_certain():
    result = classify_role_family('', 'LangChain, LLMs, RAG, Embeddings, Vector databases, Pinecone')
    assert (result.family, result.method) == ('ai', 'skill_weights')
    assert result.confidence == 1.0


def test_a_photo_finish_reads_as_a_coin_flip():
    """java_backend and java_fullstack score identically on this skill set; the
    winner is decided by dict insertion order alone. Recording 0.5 is what stops
    an aggregate from treating that as a finding.
    """
    result = classify_role_family('IT Security Auditor', 'Oracle, Java, Spring Boot')
    assert (result.family, result.method) == ('java_fullstack', 'skill_weights')
    assert result.confidence == 0.5


def test_falling_through_to_general_is_recorded_as_no_answer():
    """The motivating case: there is no security family, so this lands in general.
    It has always landed there - what is new is that it now says so with 0.0
    instead of being indistinguishable from a confident classification.
    """
    result = classify_role_family('IT Security Auditor', 'none_detected')
    assert result.family == 'general'
    assert result.method == 'unclassified'
    assert result.confidence == 0.0


def test_every_classification_carries_the_vocabulary_that_produced_it():
    for role, skills in PARITY_CASES:
        assert classify_role_family(role, skills).taxonomy_version == ROLE_FAMILY_TAXONOMY_VERSION


def test_role_family_fields_is_shaped_for_a_recruiter_email_constructor():
    """The write sites spread this with **, so the keys are the column names."""
    fields = role_family_fields(role='IT Security Auditor', skills_text='Oracle, Java, Spring Boot')
    assert fields == {
        'role_family': 'java_fullstack',
        'role_family_confidence': 0.5,
        'role_family_taxonomy_version': ROLE_FAMILY_TAXONOMY_VERSION,
    }


def test_role_family_fields_never_returns_null_for_a_row_it_touched():
    """NULL means "never classified". A row this helper wrote is classified, even
    when the answer is general - conflating the two would make the backfill
    re-process rows forever.
    """
    fields = role_family_fields(role=None, skills_text=None)
    assert fields['role_family'] == 'general'
    assert fields['role_family_confidence'] == 0.0
    assert all(value is not None for value in fields.values())
