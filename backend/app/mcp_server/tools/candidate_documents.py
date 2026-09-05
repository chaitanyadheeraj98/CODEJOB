from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import CandidateDocument


# Words that carry no identifying information in a request for a document, and
# would otherwise match a file that merely contains them. Without this, the whole
# phrase is one substring and "my passport" misses a document labelled
# "passport" - which is the phrasing the user actually types.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "attach", "attached", "copy", "doc", "docs", "document",
        "documents", "file", "files", "for", "my", "of", "or", "please", "scan",
        "send", "the", "to", "with",
    }
)


def _tokens(query: str) -> list[str]:
    words = [word.strip(".,;:!?\"'()").lower() for word in query.split()]
    kept = [word for word in words if word and word not in _STOPWORDS]
    # An all-stopword query ("my documents") is a request to see everything.
    return kept


def list_candidate_documents(query: str = "") -> dict[str, object]:
    """List the user's stored documents - passport, degree, W2 - with their ids.

    Call it whenever the user asks for a document to be attached to a mail, so
    "attach my passport and the W2" becomes ids. Pass the user's own words as
    `query`: it matches each meaningful word against `label` (what the user
    calls the file) and `file_name`, so one call can find several documents.
    Leave it empty to see everything.

    A word that matches nothing simply returns fewer documents than the user
    named - check the list against what they asked for rather than assuming
    every request was found.

    These are the user's own files. Never describe their contents - nothing here
    reads them - and never guess an id that this tool did not return.
    """
    tokens = _tokens(query)
    db = SessionLocal()
    try:
        rows = (
            db.query(CandidateDocument)
            .filter(CandidateDocument.owner_id == settings.owner_id)
            .order_by(CandidateDocument.created_at.desc(), CandidateDocument.id.desc())
            .all()
        )
        matched = [
            row
            for row in rows
            if not tokens
            or any(
                token in row.label.lower() or token in row.file_name.lower() for token in tokens
            )
        ]
        return {
            "count": len(matched),
            "total_on_file": len(rows),
            # The words actually searched, so a miss caused by a dropped filler
            # word is visible rather than looking like an empty locker.
            "matched_on": tokens,
            "documents": [
                {
                    "id": row.id,
                    "label": row.label,
                    "file_name": row.file_name,
                    "mime_type": row.mime_type,
                    "file_size": row.file_size,
                }
                for row in matched
            ],
            "usage": (
                "Pass the ids as document_ids to propose_send_email. The user still"
                " confirms the send, and the card lists every file by name."
            ),
        }
    finally:
        db.close()
