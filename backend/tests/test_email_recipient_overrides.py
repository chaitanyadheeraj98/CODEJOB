"""To and CC became things the assistant can change, so they became things
that can be changed wrongly.

The tool used to accept only a body. "Send this to the employer instead" was a
request it would agree to and then not carry out: it rewrote the words and left
the envelope pointing at the recruiter, with the card still showing the
original addresses. These tests hold the new behaviour to the two properties
that make the confirmation card worth reading - the card shows the envelope
that will actually be used, and the server sends nothing the card could not
have honestly shown.
"""

import unittest

from app.services.email_recipients import (
    MAX_CC_ADDRESSES,
    InvalidRecipient,
    normalize_address,
    normalize_cc,
)


class NormalizeAddressTests(unittest.TestCase):
    def test_accepts_and_trims_a_plain_address(self) -> None:
        self.assertEqual(normalize_address("  kartheek@horizonsoftech.net "), "kartheek@horizonsoftech.net")
        self.assertEqual(normalize_address("<hr@horizonsoftech.net>"), "hr@horizonsoftech.net")

    def test_empty_is_a_missing_recipient_not_a_malformed_one(self) -> None:
        # Different causes, different sentences: one is "you forgot", the other
        # is "you typed something that is not an address".
        with self.assertRaises(InvalidRecipient) as missing:
            normalize_address("   ")
        self.assertIn("missing", str(missing.exception).lower())

    def test_rejects_what_would_silently_misdeliver(self) -> None:
        for value in (
            "kartheek",                      # no domain at all
            "kartheek@horizonsoftech",       # no TLD
            "a@b.c",                         # single-character TLD
            "one@example.com, two@example.com",  # a list where one was asked for
            "Kartheek <kartheek@x.com>",     # display name, which Gmail would send as-is
            "kartheek@@horizonsoftech.net",
            "kar theek@horizonsoftech.net",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRecipient):
                    normalize_address(value)


class NormalizeCcTests(unittest.TestCase):
    def test_empty_cc_is_a_result_not_an_error(self) -> None:
        # The distinction the whole change rests on: "no CC" has to be
        # expressible, because dropping the CC is a thing users ask for.
        self.assertEqual(normalize_cc(""), "")
        self.assertEqual(normalize_cc("   "), "")

    def test_splits_normalises_and_preserves_order(self) -> None:
        self.assertEqual(
            normalize_cc(" hr@horizonsoftech.net ,kartheek@horizonsoftech.net"),
            "hr@horizonsoftech.net, kartheek@horizonsoftech.net",
        )
        self.assertEqual(
            normalize_cc("a@example.com; b@example.com"),
            "a@example.com, b@example.com",
        )

    def test_drops_case_insensitive_duplicates(self) -> None:
        # The same person copied twice receives the same mail twice, whatever
        # case the model wrote them in.
        self.assertEqual(normalize_cc("HR@Example.com, hr@example.com"), "HR@Example.com")

    def test_one_bad_address_fails_the_whole_list(self) -> None:
        # Not "send to the good ones": a CC list that silently loses a name is
        # worse than one that refuses.
        with self.assertRaises(InvalidRecipient):
            normalize_cc("good@example.com, nonsense")

    def test_caps_the_number_of_addresses(self) -> None:
        many = ", ".join(f"user{index}@example.com" for index in range(MAX_CC_ADDRESSES + 1))
        with self.assertRaises(InvalidRecipient) as exc:
            normalize_cc(many)
        self.assertIn(str(MAX_CC_ADDRESSES), str(exc.exception))


if __name__ == "__main__":
    unittest.main()
