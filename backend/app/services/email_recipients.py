"""Recipient overrides for an assistant-drafted reply.

`propose_send_email` could change only the words. To and CC were read off the
`RecruiterEmail` row and drawn on the confirmation card as facts, so "send this
one to the employer instead, and drop the CC" was a request the assistant would
agree to, rewrite the body for, and then not carry out - the envelope never
moved. The card still showed the original addresses, which is the one place a
user would have caught it.

Addresses are normalised in two places on purpose:

- in the tool, so a model that writes a malformed address is told immediately
  rather than at the click;
- in the route, because the card's values reach the server as a request body,
  and a value that passed through a model is not a value the server may trust.

An empty CC is a real answer, not a missing one. `None` means "keep whatever
the thread already has" and `""` means "send this with no CC at all", and the
two have to stay distinguishable all the way to `send_reply_with_attachment`,
which treats a falsy CC as "no Cc header".
"""

from __future__ import annotations

import re

# Deliberately stricter than the scanner's pattern in external_feeds/parser.py.
# That one finds addresses inside prose and can afford to be generous; this one
# decides what goes in a To header, where being generous means delivering mail
# somewhere nobody named.
_ADDRESS_RE = re.compile(r"^[^@\s,;:<>\"]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")

# Gmail accepts far more. This is a sanity bound on what an assistant may put
# on one message, not a protocol limit: a reply that needs eleven people copied
# is a reply a human should be addressing.
MAX_CC_ADDRESSES = 10


class InvalidRecipient(ValueError):
    """A supplied To or CC value cannot be used as an address."""


def normalize_address(value: str) -> str:
    """One address, validated. Raises `InvalidRecipient` for anything else."""
    cleaned = (value or "").strip().strip("<>").strip()
    if not cleaned:
        raise InvalidRecipient("Recipient email is missing.")
    if not _ADDRESS_RE.match(cleaned):
        raise InvalidRecipient(f"'{cleaned}' is not a valid email address.")
    return cleaned


def normalize_cc(value: str) -> str:
    """A comma-separated CC list, validated, de-duplicated, order preserved.

    Returns `""` for an empty input, which is a legitimate result: it means the
    message goes out with no Cc header.
    """
    raw = (value or "").replace(";", ",")
    parts = [part.strip() for part in raw.split(",")]
    seen: set[str] = set()
    addresses: list[str] = []
    for part in parts:
        if not part:
            continue
        address = normalize_address(part)
        # Case-insensitive, because a duplicate that differs only in case is
        # still the same person receiving the same mail twice.
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        addresses.append(address)
    if len(addresses) > MAX_CC_ADDRESSES:
        raise InvalidRecipient(
            f"Too many CC addresses: {len(addresses)}. The limit is {MAX_CC_ADDRESSES}."
        )
    return ", ".join(addresses)
