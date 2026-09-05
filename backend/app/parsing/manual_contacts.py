"""Pull the recruiter's contact details out of a pasted requirement.

The only genuinely new logic in manual intake. Everything else the feature does
is already built for Gmail and Nvoids and is simply called.

It is new because neither existing source has to do it. Gmail reads the address
off the message's From header (`gmail_client.py`); Nvoids reads it off
structured feed fields. A paste has neither - just text that a human forwarded
from WhatsApp, and whose only contact details sit in a signature block:

    Thanks & regards
    T Mahesh royal
    US It Recruiter
    Fusion Global Technologies and Solutions
    Email: mahesh@fusiongts.com / Contact: +1 (210) 485-6386

**This module reads the raw text, footer included, and that is the whole
point.** `phase0.strip_recruiter_footer` cuts the message at exactly that
sign-off, which is correct for job-content parsing - a signature is not a
requirement - and would throw away every contact detail there is. The caller
keeps two copies: this function gets the raw one, the JD parser gets the
stripped one.

Nothing here is ever guessed. An address is returned only if it appears
verbatim in the text: no domain constructed from a company name, no value
borrowed from a similarly-named record. The failure mode of a wrong address is
a real person's resume reaching a stranger, so absent is always better than
plausible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.phase0 import (
    EMAIL_RE,
    FOOTER_SIGNOFF_RE,
    FOOTER_TITLE_RE,
    PHONE_RE,
    email_domain,
)
from app.premium_numbers.phone_normalization import best_display_phone, canonicalize_phone

# Lines that are structure rather than identity. A signature block is mostly
# labels, and the name is whichever line is not one of these.
_LABEL_RE = re.compile(
    r"^\s*(email|e-mail|mail|contact|phone|cell|mobile|tel|telephone|direct|desk|"
    r"linkedin|website|web|address|fax|gtalk|skype|hangout)\b\s*[:\-]?",
    re.IGNORECASE,
)
# Company suffixes, used only to prefer one candidate line over another - never
# to invent a company that is not written down.
_COMPANY_HINT_RE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|technologies|technology|solutions|"
    r"systems|services|consulting|consultancy|group|global|software|staffing|"
    r"labs|partners|associates|enterprises)\b",
    re.IGNORECASE,
)
_DISCLAIMER_RE = re.compile(
    r"\b(confidential|unsubscribe|disclaimer|intended recipient|privileged)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ManualContacts:
    """What was found in the text. Every field is "" when it was not there."""

    recruiter_email: str = ""
    sender_name: str = ""
    phone: str = ""
    phone_display: str = ""
    company: str = ""

    @property
    def sender_identity(self) -> str:
        """The `sender` a RecruiterEmail row wants: "Name <addr>", or the best half."""
        if self.sender_name and self.recruiter_email:
            return f"{self.sender_name} <{self.recruiter_email}>"
        return self.recruiter_email or self.sender_name


def _footer_start(lines: list[str]) -> int:
    """Index of the sign-off line, or -1.

    Matched from the end: a requirement body can contain the word "thanks" in
    passing, and the signature is the last one, not the first.
    """
    for index in range(len(lines) - 1, -1, -1):
        if FOOTER_SIGNOFF_RE.match(lines[index].strip()):
            return index
    return -1


def _addresses(text: str) -> list[str]:
    return [match.group(0).lower() for match in EMAIL_RE.finditer(text)]


def _pick_address(text: str, footer_index: int, lines: list[str], employer_domains: frozenset[str]) -> str:
    """Prefer an address in the signature, then anywhere, excluding our own domains.

    An address on an employer domain is the user's own side of the conversation
    - a CC target - not the recruiter being written to.
    """
    footer_text = "\n".join(lines[footer_index:]) if footer_index >= 0 else ""
    for candidate_text in (footer_text, text):
        for address in _addresses(candidate_text):
            if email_domain(address) not in employer_domains:
                return address
    return ""


def _pick_phone(text: str, footer_index: int, lines: list[str]) -> tuple[str, str]:
    footer_text = "\n".join(lines[footer_index:]) if footer_index >= 0 else ""
    for candidate_text in (footer_text, text):
        for match in PHONE_RE.finditer(candidate_text):
            canonical = canonicalize_phone(match.group(0))
            if canonical:
                return canonical, best_display_phone(match.group(0))
    return "", ""


def _signature_lines(lines: list[str], footer_index: int) -> list[str]:
    """The identity lines of the signature: no labels, no URLs, no disclaimers."""
    if footer_index < 0:
        return []
    kept: list[str] = []
    for raw_line in lines[footer_index + 1 :]:
        line = raw_line.strip()
        if not line or _LABEL_RE.match(line) or _DISCLAIMER_RE.search(line):
            continue
        if EMAIL_RE.search(line) or PHONE_RE.search(line):
            continue
        kept.append(line)
    return kept


def _pick_name_and_company(lines: list[str], footer_index: int) -> tuple[str, str]:
    """The name sits above the job title, the company below it.

    That is the shape of a recruiter signature, and when the title line is
    missing the first identity line is the name and nothing is the company. A
    company is never inferred from the email domain: "fusiongts.com" is not a
    company name, and writing one down that the sender did not is the same class
    of mistake as inventing an address.
    """
    candidates = _signature_lines(lines, footer_index)
    if not candidates:
        return "", ""

    title_index = next(
        (index for index, line in enumerate(candidates) if FOOTER_TITLE_RE.search(line)), -1
    )
    if title_index > 0:
        name = candidates[title_index - 1]
        below = candidates[title_index + 1 :]
        company = next((line for line in below if _COMPANY_HINT_RE.search(line)), "")
        if not company and below:
            company = below[0]
        return name, company

    name = candidates[0]
    company = next(
        (line for line in candidates[1:] if _COMPANY_HINT_RE.search(line)),
        "",
    )
    return name, company


def extract_manual_contacts(
    text: str,
    *,
    employer_domains: frozenset[str] | set[str] = frozenset(),
) -> ManualContacts:
    """Read contact details out of the raw pasted text, signature included.

    `employer_domains` are the owner's own domains; an address on one of them is
    treated as a CC target rather than the recruiter. Pass
    `employer_domains_for_owner(db, owner_id)`.

    Pure: no database, no settings, no network. Returns "" for anything the text
    does not literally contain.
    """
    if not text or not text.strip():
        return ManualContacts()

    lines = text.splitlines()
    footer_index = _footer_start(lines)
    domains = frozenset(employer_domains)

    recruiter_email = _pick_address(text, footer_index, lines, domains)
    phone, phone_display = _pick_phone(text, footer_index, lines)
    sender_name, company = _pick_name_and_company(lines, footer_index)

    return ManualContacts(
        recruiter_email=recruiter_email,
        sender_name=sender_name,
        phone=phone,
        phone_display=phone_display,
        company=company,
    )
