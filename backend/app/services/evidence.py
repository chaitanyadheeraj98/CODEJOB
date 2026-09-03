"""The shape of one contributing signal behind an inferred claim.

Lives beside the services rather than in `mcp_server/tools/provenance.py`, where
the phase plan put it, for a concrete reason: `app/mcp_server/__init__.py`
eagerly imports the server, which builds the whole tool registry. A service
importing the tool layer therefore pulls the registry back through itself, and
`relationship_scoring` -> `provenance` -> ... -> `relationship_scoring` is a
circular import. The dependency direction is wrong anyway - an evidence entry is
a domain type that tools serialize, not a tool concern.

`provenance.py` re-exports everything here, so a tool still reads
`from app.mcp_server.tools.provenance import EvidenceEntry`.
"""

from __future__ import annotations

from dataclasses import dataclass

CONFIDENCE_LEVELS = ("confirmed", "likely", "possible")

INFERENCE_ASSUMPTION = (
    "This relationship was inferred from the records listed, not recorded by anyone."
)

MATCH_KINDS = ("exact", "alias", "semantic", "overlap", "absent")


@dataclass(frozen=True)
class EvidenceEntry:
    """One contributing signal, decomposed.

    The UI explanation is generated from a list of these; the model reads them,
    it never composes them. `match="absent"` is a first-class value and means
    the signal could not be compared - it is not a mismatch, and it must never
    be scored as one (see relationship_scoring's renormalization).
    """

    signal: str
    left_value: str
    right_value: str
    normalized_to: str
    match: str
    weight: float
    sub_score: float
    source: str

    def as_dict(self) -> dict[str, object]:
        return {
            "signal": self.signal,
            "left_value": self.left_value,
            "right_value": self.right_value,
            "normalized_to": self.normalized_to,
            "match": self.match,
            "weight": round(float(self.weight), 4),
            "sub_score": round(float(self.sub_score), 4),
            "source": self.source,
        }


