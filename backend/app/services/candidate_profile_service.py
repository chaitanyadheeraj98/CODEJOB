"""The vocabulary, the validator and the composer for the Candidate Profile.

Four responsibilities, no I/O except what it is handed. It exists because the
profile can now be written from three places - a proposal card the assistant
raised, a proposal card the user asked for, and a control in Settings - and
three copies of "what a valid profile is" would be three things to keep in
agreement.

The validator here is the one that used to live inline in
`upload_candidate_profile`. Every check runs in the same order and raises with
the same message, because the multipart upload route still calls it and its
rejections are what the user reads.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import HTTPException

CANDIDATE_PROFILE_MAX_CHARS = 20000
CANDIDATE_PROFILE_MAX_BYTES = 1_000_000
CANDIDATE_PROFILE_SUFFIXES = (".md", ".markdown", ".txt")

SAVED_HEADING = "## Saved from chat"

# The only labels an appended entry may carry. A model-chosen label is a model
# writing its own prompt: <user_profile> is the one block the model is told to
# believe, so the vocabulary in it is fixed here and nowhere else.
#
# The aliases are what the *assistant* might have called the field when it asked
# ("what is your visa status?"), so R2's ask-first check can recognise the
# question, and what the user might type into the Settings control.
PROFILE_FIELDS: dict[str, tuple[str, ...]] = {
    "Work Authorization": ("work authorization", "work authorisation", "visa", "visa status"),
    "Notice period": ("notice period", "notice"),
    "Current location": ("current location", "location", "where are you based"),
    "Willing to relocate": ("relocate", "relocation"),
    "Rate": ("rate", "hourly rate", "expected rate", "compensation", "pay"),
    "Phone": ("phone", "phone number", "contact number", "mobile"),
    "Email": ("email", "email address"),
    "Passport": ("passport", "passport number"),
    "Total experience": ("total experience", "years of experience", "experience"),
    "Availability": ("availability", "available", "start date", "when can you start"),
    "Employment type": ("employment type", "c2c", "w2", "1099", "corp to corp"),
    "LinkedIn": ("linkedin",),
}

MAX_ENTRY_CHARS = 2000


def fingerprint(profile_text: str) -> str:
    """SHA-256 hex of the stored profile - the concurrency token (R7).

    A card is computed against one version of the document. The write carries
    this back so a profile edited in another tab between the two is a 409 rather
    than an overwrite of something the user never saw.
    """
    return hashlib.sha256((profile_text or "").encode("utf-8")).hexdigest()


def validate_profile_text(raw: bytes | str, filename: str) -> str:
    """Every check that used to be inline in the upload route, in the same order.

    Callers: the multipart upload route, whose behaviour must not change, and
    the from-attachment route, which needs the identical rules applied to text
    that arrived by a different door.
    """
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
    else:
        payload = raw
    if not payload:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(payload) > CANDIDATE_PROFILE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="That file is too large to be a profile.")
    try:
        text = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail=f"{filename} is not UTF-8 text. Save it as a plain Markdown file and try again.",
        ) from None
    if not text:
        raise HTTPException(status_code=400, detail="That file has no text in it.")
    if len(text) > CANDIDATE_PROFILE_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That profile is {len(text):,} characters; the limit is "
                f"{CANDIDATE_PROFILE_MAX_CHARS:,}. It is sent to the chat model on every message."
            ),
        )
    return text


def validate_profile_filename(filename: str) -> str:
    """The name checks the upload route runs before it reads a byte."""
    name = (filename or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="File name required")
    if Path(name).suffix.lower() not in CANDIDATE_PROFILE_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"{name} is not a Markdown file. Upload a {', '.join(CANDIDATE_PROFILE_SUFFIXES)} file.",
        )
    return name


def canonical_field(name: str) -> str | None:
    """Registry lookup, case-insensitive, aliases included.

    None means the field is not one this feature will write - which is a refusal
    naming the list, never a new label invented on the spot.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for label, aliases in PROFILE_FIELDS.items():
        if needle == label.lower() or needle in aliases:
            return label
    return None


def compose_entry(field: str, value: str, *, verbatim: bool) -> str:
    """One line, in one of exactly two shapes.

    Free-form composition by the model is not a third shape: the label comes
    from the registry and the value comes from the user, so nothing the model
    wrote itself reaches the document.
    """
    cleaned = (value or "").strip()
    if verbatim:
        return f'- {field} (in the user\'s words): "{cleaned}"'
    return f"- {field}: {cleaned}"


def entry_field(entry: str) -> str | None:
    """Which registry field a composed line carries, read back off the line.

    Deliberately derived from the entry rather than passed alongside it. The
    append route recomposes server-side from `payload.entry` and nothing else,
    so a caller cannot name one field and supply a line for another - which is
    what a `field` parameter would have allowed, and what makes the difference
    between "replace my Work Authorization" and "delete my Passport".
    """
    line = (entry or "").strip()
    if line.startswith("- "):
        line = line[2:]
    head = line.split(":", 1)[0].strip()
    # `- Work Authorization (in the user's words): "..."` - the two shapes
    # compose_entry produces, and no others.
    if "(" in head:
        head = head.split("(", 1)[0].strip()
    return canonical_field(head)


def _saved_section(lines: list[str]) -> tuple[int, int] | None:
    """Half-open bounds of the Saved-from-chat entries, heading excluded.

    None when the section does not exist yet. Everything outside these bounds is
    the user's own document and is never rewritten by this module.
    """
    try:
        heading_at = next(
            index for index, row in enumerate(lines) if row.strip() == SAVED_HEADING
        )
    except StopIteration:
        return None
    end = len(lines)
    for index in range(heading_at + 1, len(lines)):
        if lines[index].startswith("#"):
            end = index
            break
    while end > heading_at + 1 and not lines[end - 1].strip():
        end -= 1
    return heading_at + 1, end


def conflicting_statements(existing: str, field: str | None) -> list[str]:
    """Lines *outside* the Saved-from-chat block that already state this field.

    Not something to fix - the user's uploaded text is theirs, and rewriting it
    is precisely what the trust boundary forbids. It is something to *show*: a
    profile that says `Location : Dallas` above and `- Current location: Austin`
    below is contradictory, and the user is the only one who can resolve it.
    """
    if not field:
        return []
    labels = [field.lower(), *PROFILE_FIELDS.get(field, ())]
    lines = (existing or "").strip().split("\n")
    bounds = _saved_section(lines)
    found: list[str] = []
    for index, row in enumerate(lines):
        if bounds and bounds[0] <= index < bounds[1]:
            continue
        text = row.strip()
        if not text or text.startswith("#") or ":" not in text:
            continue
        head = text.split(":", 1)[0].strip().lower().lstrip("-* ").strip()
        if head in labels:
            found.append(text)
    return found


def plan_append(existing: str, entry: str) -> tuple[str, list[str]]:
    """The resulting document, and the entries it replaces.

    One field, one line. A second `- Work Authorization: ...` beside the first
    would put a contradiction inside the one block the system prompt tells the
    model to believe, and the model has no way to tell which half is current -
    so a repeat of a field it already holds is an update, not an addition.

    No timestamp goes into an entry. `- Notice period: 2 weeks (saved
    2026-09-05)` is a value the model reads as authoritative and can leak into a
    drafted email. Staleness is carried by candidate_profile_uploaded_at.
    """
    current = (existing or "").strip()
    if not current:
        # R8 is enforced by the tool and by the route; the composer refuses too,
        # so it cannot become a second way to create a profile.
        raise HTTPException(
            status_code=400,
            detail="There is no profile to add to yet. Upload one in Settings › Profile Settings › Candidate Profile first.",
        )
    line = (entry or "").strip()
    if not line:
        raise HTTPException(status_code=400, detail="There is nothing to add.")

    lines = current.split("\n")
    bounds = _saved_section(lines)
    replaced: list[str] = []
    if bounds is None:
        combined = f"{current}\n\n{SAVED_HEADING}\n{line}\n"
    else:
        start, end = bounds
        field = entry_field(line)
        body = lines[start:end]
        matches = [i for i, row in enumerate(body) if field and entry_field(row) == field]
        if matches:
            replaced = [body[i] for i in matches]
            # Rewrite the first in place and drop any others. A profile written
            # before this rule could already hold several lines for one field;
            # leaving the extras would be leaving the contradiction.
            keep = set(matches[1:])
            body = [row for i, row in enumerate(body) if i not in keep]
            body[matches[0]] = line
        else:
            body = [*body, line]
        combined = "\n".join([*lines[:start], *body, *lines[end:]])
        if not combined.endswith("\n"):
            combined += "\n"

    if len(combined) > CANDIDATE_PROFILE_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That would make the profile {len(combined):,} characters; the limit is "
                f"{CANDIDATE_PROFILE_MAX_CHARS:,}. It is sent to the chat model on every message."
            ),
        )
    return combined, replaced


def compose_append(existing: str, entry: str) -> str:
    """The appended document, complete - what the card shows and the route stores.

    The card's preview and the route's write are the same call, so they cannot
    disagree about what a confirmation means.
    """
    return plan_append(existing, entry)[0]
