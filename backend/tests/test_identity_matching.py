from app.models import PremiumNumberContact
from app.premium_numbers.extraction import ExtractedContactGroup
from app.premium_numbers.identity_matching import classify_identity_match


def _candidate(**overrides) -> ExtractedContactGroup:
    values = {
        "phone_number_display": "(214) 555-1212",
        "phone_number_normalized": "12145551212",
        "owner_name": "Ada Lovelace",
        "contact_email": "ada@example.com",
        "company": "Example",
        "designation": "Recruiter",
        "purpose": "Direct contact",
        "confidence": "high",
        "contact_type": "recruiter_direct",
        "recruiter_relevance_score": 90,
        "is_recruiter_relevant": True,
        "relevance_reason": "external_domain",
        "source_fragment": "Ada +1 214 555 1212",
        "role": "recruiter",
        "colocation_verified": True,
    }
    values.update(overrides)
    return ExtractedContactGroup(**values)


def _contact(**overrides) -> PremiumNumberContact:
    values = {
        "owner_id": "owner",
        "normalized_phone_number": "12145551212",
        "display_phone_number": "(214) 555-1212",
        "recruiter_name": "Ada Lovelace",
        "recruiter_email": "ada@example.com",
        "company": "Example",
    }
    values.update(overrides)
    return PremiumNumberContact(**values)


def test_email_match_confirms_even_when_other_fields_differ() -> None:
    result = classify_identity_match(_contact(), _candidate(owner_name="Different", company="Other"), "recruiter")
    assert result.outcome == "confirmed"


def test_name_match_with_blank_company_confirms() -> None:
    result = classify_identity_match(
        _contact(recruiter_email="", company="Unknown"),
        _candidate(owner_name="  Ada\nLovelace  ", contact_email="", company="Example"),
        "recruiter",
    )
    assert result.outcome == "confirmed"


def test_two_agreeing_fields_confirm() -> None:
    result = classify_identity_match(
        _contact(recruiter_email="other@example.com"),
        _candidate(contact_email="ada@example.com"),
        "recruiter",
    )
    assert result.outcome == "confirmed"


def test_matching_extension_plus_one_field_confirms() -> None:
    result = classify_identity_match(
        _contact(phone_extension="368", recruiter_email="other@example.com"),
        _candidate(phone_extension="368"),
        "recruiter",
    )
    assert result.outcome == "confirmed"


def test_different_extensions_conflict_even_when_name_and_company_agree() -> None:
    result = classify_identity_match(
        _contact(phone_extension="334"),
        _candidate(phone_extension="368"),
        "recruiter",
    )
    assert result.outcome == "conflicting"


def test_blank_incoming_extension_does_not_force_a_conflict_on_its_own() -> None:
    result = classify_identity_match(
        _contact(recruiter_name="Ada Lovelace", recruiter_email="", company="Unknown", phone_extension="368"),
        _candidate(owner_name="Unknown", contact_email="", company="Unknown", phone_extension=""),
        "recruiter",
    )
    assert result.outcome == "insufficient"


def test_conflict_and_insufficient_are_distinct() -> None:
    conflict = classify_identity_match(
        _contact(),
        _candidate(owner_name="Grace Hopper", contact_email="", company=""),
        "recruiter",
    )
    insufficient = classify_identity_match(
        _contact(recruiter_name="Unknown", recruiter_email="", company="Unknown"),
        _candidate(owner_name="Unknown", contact_email="", company="Unknown"),
        "recruiter",
    )
    assert conflict.outcome == "conflicting"
    assert insufficient.outcome == "insufficient"
