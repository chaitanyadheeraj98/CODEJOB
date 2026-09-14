"""G2 / §13: give someone their data before they leave.

*"Offer a JSON download of their applications, conversations and taxonomy
overlay before deletion. It is a read of tables you already query, and omitting
it is the thing people complain about."*

Three decisions worth stating, because each has a plausible alternative.

**An allowlist of tables, not "everything minus a few".** 69 of this schema's
71 tables carry `owner_id`. A sweep would be exhaustive and wrong: it would
hand back `gmail_credentials` and `user_sessions`, which are keys to the
account rather than contents of it. G3's deletion must be generated from
metadata precisely so nothing is missed; an export must be curated precisely so
nothing is *added* by accident. Opposite problems, opposite mechanisms.

**A column denylist on top, spelled out per table.** Two kinds of column are
dropped. `tracking_token` is the open-tracking capability for mail already
sent - a value someone else could use to forge an open event. The embeddings
and the resume-picker blobs are **machinery rather than correspondence**: they
were 1.53 GB of a 1.93 GB export measured against one real account, next to
31 MB of actual message bodies. Nobody asking for their data means "the float
vector you derived from it".

Note that a substring rule over column names would be worse than useless here:
it would strip `chat_turn.prompt_tokens`, which is a count, while telling you
nothing about a capability spelled some other way. Names, not patterns - and
`MUST_KEEP` exists so that trimming this list further has to argue with a test
rather than quietly drop the part people actually wanted.

**Streamed, not assembled.** One real account holds ~19,000 rows across these
tables, most of them `recruiter_emails` carrying message bodies. The
alternative - a job that writes a file and hands back a link - would create an
artefact holding a full copy of someone's mailbox, living outside the database,
which G3's metadata-driven sweep would never find. Streaming keeps the peak
memory bounded and leaves nothing behind to delete later.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Application,
    AppTSApplication,
    AppTSApplicationEvent,
    AppTSApplicationInterview,
    AppTSApplicationOutreachMessage,
    AppTSApplicationRTR,
    AppTSApplicationSkillGapSnapshot,
    AppTSApplicationSuggestion,
    CanonicalEntityTaxonomyEntry,
    ChatMessage,
    ChatSession,
    EmailConversation,
    JobIntentTaxonomyEntry,
    RecruiterEmail,
)

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1

#: Rows per round trip. Bounds peak memory on the 9,500-row tables without
#: making 9,500 round trips.
BATCH = 500

#: Columns removed from an otherwise whole row. Keyed by table so the rule is
#: readable as "this column of this table", never as a pattern - see the module
#: docstring for why a substring rule is actively harmful here.
DENIED_COLUMNS: dict[str, frozenset[str]] = {
    "recruiter_emails": frozenset({
        "tracking_token",
        # Machinery, not correspondence. Measured against one real account:
        # these four columns are 1.53 GB of a 1.93 GB export, against 31 MB of
        # actual message bodies. A float vector is not a conversation, and the
        # scratch record of which resumes were considered for an email is this
        # application thinking aloud, not something its user wrote or received.
        "semantic_embedding",
        "resume_picker_candidates_json",
        "resume_picker_breakdown_json",
        "parser_details_json",
    }),
    "appts_applications": frozenset({"embedding"}),
    "canonical_entity_taxonomy_entries": frozenset({"embedding_json"}),
}

#: Kept deliberately, so a later "trim the export" change has to argue with a
#: test rather than quietly drop the part that matters.
MUST_KEEP: dict[str, frozenset[str]] = {
    "recruiter_emails": frozenset({"subject", "body", "sender", "created_at"}),
    "chat_messages": frozenset({"role", "content"}),
}

#: The export, in order. Everything here is content the account owns; nothing
#: here is a key to it. `gmail_credentials`, `provider_credentials` and
#: `user_sessions` are absent on purpose and a test asserts they stay absent.
SECTIONS: tuple[tuple[str, type], ...] = (
    ("applications", Application),
    ("application_tracking", AppTSApplication),
    ("application_tracking_events", AppTSApplicationEvent),
    ("application_tracking_interviews", AppTSApplicationInterview),
    ("application_tracking_outreach", AppTSApplicationOutreachMessage),
    ("application_tracking_rtrs", AppTSApplicationRTR),
    ("application_tracking_skill_gaps", AppTSApplicationSkillGapSnapshot),
    ("application_tracking_suggestions", AppTSApplicationSuggestion),
    ("recruiter_emails", RecruiterEmail),
    ("email_conversations", EmailConversation),
    ("chat_sessions", ChatSession),
    ("chat_messages", ChatMessage),
    ("entity_taxonomy", CanonicalEntityTaxonomyEntry),
    ("job_intent_taxonomy", JobIntentTaxonomyEntry),
)

#: Tables that must never appear in an export, asserted by a test rather than
#: remembered. They hold credentials and session material: keys to the account,
#: not contents of it.
NEVER_EXPORTED = frozenset({"gmail_credentials", "provider_credentials", "user_sessions"})


def _json_safe(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        # Nothing in the exported tables is binary today; if something becomes
        # binary, hand back a length rather than silently base64-ing a blob.
        return f"<{len(value)} bytes>"
    return value


def _row_to_dict(row, table_name: str) -> dict[str, object]:
    denied = DENIED_COLUMNS.get(table_name, frozenset())
    return {
        column.name: _json_safe(getattr(row, column.name))
        for column in row.__table__.columns
        if column.name not in denied
    }


def _rows(db: Session, model: type, owner_id: str) -> Iterator[object]:
    """Every row of one table belonging to one owner.

    `ChatMessage` is the one table here without an `owner_id`; it reaches its
    account through `session_id`, exactly as `ChatTurn` did before F1. Joining
    rather than skipping it, because a conversation export without the messages
    is the complaint this feature exists to answer.
    """
    query = db.query(model)
    if hasattr(model, "owner_id"):
        query = query.filter(model.owner_id == owner_id)
    elif model is ChatMessage:
        query = query.join(ChatSession, ChatMessage.session_id == ChatSession.id).filter(
            ChatSession.owner_id == owner_id
        )
    else:  # pragma: no cover - guarded by test_every_section_is_owner_scoped
        raise RuntimeError(f"{model.__name__} has no owner scope; refusing to export it")
    return query.order_by(model.id).yield_per(BATCH)


def stream_export(db: Session, owner_id: str) -> Iterator[str]:
    """Yield the export as JSON text, a chunk at a time.

    The whole document is never held in memory at once, so one account's
    mailbox cannot decide how much the API process allocates.
    """
    header = {
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "owner_id": owner_id,
    }
    yield "{" + json.dumps(header, separators=(",", ":"))[1:-1] + ',"sections":{'

    for section_index, (name, model) in enumerate(SECTIONS):
        yield ("," if section_index else "") + json.dumps(name) + ":["
        count = 0
        try:
            for row in _rows(db, model, owner_id):
                yield ("," if count else "") + json.dumps(
                    _row_to_dict(row, model.__table__.name), separators=(",", ":"), default=str
                )
                count += 1
        except Exception:
            # A half-written section is still the user's data. Log it, close the
            # array, and carry on rather than aborting a download that may
            # already have delivered thousands of rows.
            logger.exception("account_export_section_failed section=%s owner=%s", name, owner_id)
        yield "]"

    yield "}}"


def filename_for(owner_id: str) -> str:
    return f"codejob-export-{datetime.now(UTC).date().isoformat()}.json"
