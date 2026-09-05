"""The assistant's answer to "who am I".

Without a profile the model drafts a vendor pitch about the user in the third
person, padded with `[Insert Visa Status]` placeholders, because nothing in the
prompt tells it who is asking. These tests hold the two halves of the fix: the
profile reaches the system prompt on every turn, and the settings round-trip
neither loses it nor lets an unaware client blank it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ai.chat.system_prompt import build_system_prompt
from app.models import Base, UserSettings
from app.services.chat_service import ChatService

PROFILE = """# Chaithanya Dheeraj
- Work Authorization: H1B
- Passport: X1234567
- Notice period: 2 weeks
"""


# --- the prompt ----------------------------------------------------------


def test_the_profile_is_carried_into_the_prompt_verbatim() -> None:
    prompt = build_system_prompt(PROFILE)
    assert "Passport: X1234567" in prompt
    assert "<user_profile>" in prompt


def test_the_profile_is_marked_trusted_and_first_person() -> None:
    prompt = build_system_prompt(PROFILE)
    # It must not be mistaken for the untrusted email/resume/web content that
    # every other delimited block in this prompt carries.
    assert "trusted" in prompt
    assert "first person" in prompt


def test_the_prompt_names_the_failure_it_exists_to_prevent() -> None:
    prompt = build_system_prompt(PROFILE)
    assert "[Insert Location]" in prompt
    assert "third person" in prompt


def test_no_profile_gets_ask_dont_invent_guidance_not_silence() -> None:
    prompt = build_system_prompt("")
    assert "<user_profile>" not in prompt
    # A model with no profile must ask rather than emit a placeholder or guess.
    assert "has not written a profile" in prompt
    assert "Profile Settings" in prompt


def test_whitespace_only_profile_counts_as_no_profile() -> None:
    assert "has not written a profile" in build_system_prompt("   \n\t  ")


def test_braces_in_the_profile_do_not_break_prompt_assembly() -> None:
    # Markdown holding `{...}` - a JSON snippet, a template token - reaches
    # `.format()`'s field parser if the profile is interpolated carelessly.
    prompt = build_system_prompt('Rate: {"usd": 58} and {unclosed')
    assert '{"usd": 58}' in prompt
    assert "{unclosed" in prompt


def test_the_default_prompt_still_builds_with_no_argument() -> None:
    assert build_system_prompt()


# --- the round-trip ------------------------------------------------------


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_chat_reads_the_stored_profile(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chat_service as chat_service_module

    monkeypatch.setattr(chat_service_module.settings, "owner_id", "owner-profile")
    db.add(UserSettings(owner_id="owner-profile", candidate_profile_markdown=PROFILE))
    db.commit()
    assert ChatService._candidate_profile(db) == PROFILE


def test_chat_survives_a_missing_settings_row(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chat_service as chat_service_module

    monkeypatch.setattr(chat_service_module.settings, "owner_id", "owner-absent")
    # Chat must degrade to the no-profile prompt, not raise on a fresh install.
    assert ChatService._candidate_profile(db) == ""


def test_the_column_defaults_to_empty_not_null(db: Session) -> None:
    db.add(UserSettings(owner_id="owner-default"))
    db.commit()
    row = db.query(UserSettings).filter(UserSettings.owner_id == "owner-default").one()
    assert row.candidate_profile_markdown == ""


def test_a_settings_save_cannot_write_the_profile() -> None:
    """One write path, not two.

    The profile is uploaded as a file. If SettingsRequest still carried the
    field, a settings save could blank a profile the user never touched.
    """
    from app.schemas import SettingsRequest, SettingsResponse

    assert "candidate_profile_markdown" not in SettingsRequest.model_fields
    # Still readable, or the panel could not say which file is loaded.
    for field in (
        "candidate_profile_markdown",
        "candidate_profile_filename",
        "candidate_profile_uploaded_at",
    ):
        assert field in SettingsResponse.model_fields
