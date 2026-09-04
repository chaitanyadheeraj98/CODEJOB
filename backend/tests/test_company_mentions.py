"""§16.9 acceptance tests — matching and provenance.

Every string here came out of production on 2026-09-04.
"""

from __future__ import annotations

import pytest

from app.services import company_mentions as CM


# --- Matching (§16.9 #1-#4) -----------------------------------------------


def test_citi_does_not_match_citizenship() -> None:
    """§16.9 #1. `%citi%` matches 455 bodies; word-bounded, 12. The other 443
    are the word *citizenship*, which appears in almost every description."""
    assert CM.find_mentions("Must have US citizenship or green card", "Citi") == []
    assert CM.find_mentions("Citizens Bank is the client", "Citi") == []
    assert CM.find_mentions("End client: Citi", "Citi") == ["citi"]


def test_citi_still_matches_its_real_aliases() -> None:
    found = CM.find_mentions(
        "The client is Citigroup", "Citi", extra_aliases=("Citigroup", "Citibank")
    )
    assert found == ["citigroup"]


def test_morgan_stanley_does_not_match_morgan_utah() -> None:
    """§16.9 #2. nvoids' own search returned a posting located in Morgan, Utah
    for the query `morgan`. A two-word company requires both words."""
    assert CM.find_mentions("Onsite role in Morgan, Utah, USA", "Morgan Stanley") == []
    assert CM.find_mentions("Need EX- Morgan Stanley candidates", "Morgan Stanley") == [
        "morgan stanley"
    ]


def test_a_single_token_company_still_matches(  ) -> None:
    """§16.9 #3. Bounding must not break the common case."""
    for company, text in (("Adobe", "Adobe Experience Manager"), ("Verizon", "Client: Verizon")):
        assert CM.find_mentions(text, company)


@pytest.mark.parametrize("punctuation", ["Morgan Stanley.", "(Morgan Stanley)", "EX-Morgan Stanley,"])
def test_punctuation_around_a_name_does_not_hide_it(punctuation: str) -> None:
    assert CM.find_mentions(f"Client is {punctuation} today", "Morgan Stanley")


def test_the_shipped_word_bounded_matcher_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """§16.9 #4. No second matcher. Two components disagreeing about whether two
    names are the same entity is the failure the entity layer exists to prevent."""
    import app.services.company_mentions as module

    assert module.AliasMatcher.__module__ == "app.taxonomy_matcher"


# --- Provenance (§16.9 #5-#8, #20-#22) ------------------------------------


def test_the_end_client_column_is_the_strongest_evidence() -> None:
    """§16.9 #5."""
    mention = CM.classify_mention("Capgemini", end_client_field="Capgemini", body="…")

    assert mention.label == CM.MENTION_END_CLIENT
    assert mention.role == CM.ROLE_END_CLIENT
    assert mention.is_relationship_claim


def test_a_body_only_mention_asserts_nothing() -> None:
    """§16.9 #6. The summary must say "named in the description", never "end
    client" - a company in a body can be any of four things."""
    mention = CM.classify_mention("Cognizant", body="Experience with Cognizant a plus")

    assert mention.label == CM.MENTION_DESCRIBED
    assert mention.role is None
    assert mention.is_relationship_claim is False
    assert mention.as_dict()["phrasing"] == "named in the description"


def test_deloitte_is_the_partner_and_edward_jones_the_client() -> None:
    """§16.9 #20, and the case that corrected §16.2.

    All 18 production rows where Deloitte "was not the end client" are rows
    where it is the implementation partner and Edward Jones is the client. A
    check that reads only `end_client` calls those errors.
    """
    mention = CM.classify_mention(
        "Deloitte",
        end_client_field="ED Jones",
        implementation_partner_field="Deloitte",
        body="Long description mentioning Deloitte.",
    )

    assert mention.label == CM.MENTION_PARTNER_FIELD
    assert mention.role == CM.ROLE_IMPLEMENTATION_PARTNER
    # And never phrased as the end client, which is the §16.2 confusion
    # re-appearing in the output format.
    assert mention.as_dict()["phrasing"] == "recorded as the implementation partner"


def test_a_bare_mention_is_never_promoted_to_a_role() -> None:
    """§16.9 #21. Under 1% of bodies state a role. A name alone never implies
    one - not partner, not prime vendor."""
    mention = CM.classify_mention("Infosys", body="Similar to Infosys and Wipro engagements")

    assert mention.label == CM.MENTION_DESCRIBED
    assert mention.role is None


def test_a_stated_role_is_read_from_the_body() -> None:
    mention = CM.classify_mention(
        "Deloitte", body="Implementation Partner: Deloitte. The client is Edward Jones."
    )

    assert mention.label == CM.MENTION_ROLE_LABELLED
    assert mention.role == CM.ROLE_IMPLEMENTATION_PARTNER


def test_a_role_label_far_from_the_company_does_not_attach() -> None:
    """`prime vendor` somewhere in a long description must not decorate every
    company named anywhere else in it."""
    body = "Prime vendor arrangements vary. " + ("filler text " * 40) + "Deloitte was mentioned."

    mention = CM.classify_mention("Deloitte", body=body)

    assert mention.label == CM.MENTION_DESCRIBED
    assert mention.role is None


def test_prime_vendor_can_be_read_from_a_body_though_the_column_is_empty() -> None:
    """0 structured rows, 19 labelled mentions across 12,654 documents. Rare, but
    when the text states it the text is the evidence."""
    mention = CM.classify_mention("Vdart Inc", body="Prime Vendor: Vdart Inc")

    assert mention.role == CM.ROLE_PRIME_VENDOR
    assert mention.label == CM.MENTION_ROLE_LABELLED


def test_absence_returns_none_rather_than_a_weak_match() -> None:
    assert CM.classify_mention("Capgemini", body="A Java role in Dallas") is None
    assert CM.classify_mention("", body="anything") is None


def test_the_end_client_column_outranks_a_body_label() -> None:
    """Both present: the column the record was written for wins."""
    mention = CM.classify_mention(
        "Capgemini",
        end_client_field="Capgemini",
        body="Implementation Partner: Capgemini",
    )

    assert mention.label == CM.MENTION_END_CLIENT


def test_every_label_has_wording_the_answer_can_use() -> None:
    """The phrasing ships with the result so a tool response cannot be
    paraphrased into a stronger claim than it supports."""
    assert set(CM.MENTION_PHRASING) == set(CM.MENTION_STRENGTH)
    for label, phrase in CM.MENTION_PHRASING.items():
        assert phrase and phrase[0].islower()
