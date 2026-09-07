from app.gmail_client import _append_variant_token
from app.schemas import parse_variant_token, resume_variant_code, resume_variant_token


def test_variant_code_is_zero_padded_and_stable():
    assert resume_variant_code(7) == 'R07'
    assert resume_variant_code(14) == 'R14'
    assert resume_variant_code(210) == 'R210'
    assert resume_variant_code(None) == ''


def test_token_carries_both_the_variant_and_the_send():
    assert resume_variant_token(14, 8842) == 'CJ-R14-8842'


def test_token_is_empty_when_either_half_is_unknown():
    assert resume_variant_token(None, 8842) == ''
    assert resume_variant_token(14, None) == ''


def test_token_round_trips():
    assert parse_variant_token(resume_variant_token(14, 8842)) == ('R14', 8842)


def test_parse_tolerates_the_mess_of_a_real_paste():
    """The user pastes out of a mail client, so the marker arrives surrounded by junk."""
    assert parse_variant_token('  CJ-R14-8842  ') == ('R14', 8842)
    assert parse_variant_token('"CJ-R14-8842"') == ('R14', 8842)
    assert parse_variant_token('re: your note CJ-R14-8842 thanks') == ('R14', 8842)
    assert parse_variant_token('cj-r14-8842') == ('R14', 8842)


def test_parse_rejects_anything_that_is_not_a_marker():
    assert parse_variant_token(None) is None
    assert parse_variant_token('') is None
    assert parse_variant_token('R14') is None
    assert parse_variant_token('CJ-14-8842') is None
    assert parse_variant_token('just some words') is None


def test_appended_token_is_hidden_and_present_in_the_html():
    html = _append_variant_token('<p>Hello</p>', 'CJ-R14-8842')

    assert 'CJ-R14-8842' in html
    # Invisible to the recruiter: no box, no text, no space taken.
    assert 'display:none' in html
    assert 'font-size:0' in html
    assert 'aria-hidden="true"' in html
    # The body it was given must survive untouched ahead of the marker.
    assert html.startswith('<p>Hello</p>')


def test_append_is_a_no_op_without_a_token():
    assert _append_variant_token('<p>Hello</p>', None) == '<p>Hello</p>'
    assert _append_variant_token('<p>Hello</p>', '') == '<p>Hello</p>'


def test_appended_token_is_escaped():
    html = _append_variant_token('<p>Hi</p>', 'CJ-R14-1<script>')
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_the_flag_defaults_on_so_the_migration_does_not_silently_disable_it():
    """The marker shipped before the switch existed; upgrading must not turn it off."""
    from app.models import UserSettings

    assert UserSettings.__table__.c.feature_resume_variant_marker_enabled.default.arg is True
