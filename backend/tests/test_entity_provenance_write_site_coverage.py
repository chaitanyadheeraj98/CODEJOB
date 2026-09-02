"""Every place that writes `company` or `location` must say where it came from.

`company_source` and `location_source` shipped with only one writer between them:
`fill_entity_gaps` labelled the values *it* filled and nothing labelled the values
the parser extracted. NULL therefore meant two opposite things - "the parser found
this" and "there is nothing here" - and the column inverted in practice. Measured
on 39 rows ingested after provenance shipped: 30 carried a real company and none
were labelled, and the one labelled location was the two-letter fragment "IN" that
the taxonomy had filled. Filtering for verified values returned the worst row and
discarded the other 38.

Most write sites spread `**fill_entity_gaps(...)`, so the choke point covers them -
`test_entity_taxonomy_gap_fill.py` proves that path behaviourally. This guards the
other kind: a site that passes `company=` or `location=` directly, bypassing the
choke point entirely. Two already existed (the nvoids failed-mapping row and the
requirement-expansion child) and both were invisible to every behavioural test,
because a test that does not know a write site exists cannot assert against it.

If this fails, label the value at that site - or route it through
`fill_entity_gaps`. Do not extend an allow-list.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

APP = Path(__file__).parents[1] / "app"

# Constructors that persist a candidate row.
ROW_CONSTRUCTORS = {"RecruiterEmail"}

# Each value keyword and the provenance keyword that must accompany it.
PAIRS = (("company", "company_source"), ("location", "location_source"))


def _python_files() -> list[Path]:
    return [path for path in APP.rglob("*.py") if "__pycache__" not in str(path)]


def _row_constructions(tree: ast.AST):
    """Every RecruiterEmail(...) call.

    Via AST rather than regex so unrelated `company=`/`location=` keywords - contact
    builders, filter arguments, draft context - cannot produce false hits.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in ROW_CONSTRUCTORS:
            yield node


class EntityProvenanceWriteSiteCoverageTests(unittest.TestCase):
    def test_every_explicit_company_or_location_is_labelled(self) -> None:
        offenders: list[str] = []
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in _row_constructions(tree):
                keywords = {kw.arg for kw in call.keywords if kw.arg}
                for value, source in PAIRS:
                    # A site spreading **fill_entity_gaps(...) passes neither
                    # explicitly; the choke point supplies both, so it is not an
                    # offender. Only a hand-written value without its label is.
                    if value in keywords and source not in keywords:
                        offenders.append(
                            f"{path.relative_to(APP.parent)}:{call.lineno} ({value})"
                        )
        self.assertEqual(
            offenders,
            [],
            "These sites persist a company or location with no provenance, so NULL "
            "there is ambiguous forever: " + ", ".join(offenders),
        )

    def test_the_choke_point_is_the_only_taxonomy_labeller(self) -> None:
        """`taxonomy_matched` must not be assertable from outside the matcher.

        A site that hand-writes `taxonomy_matched` would claim the vocabulary
        confirmed a value it never saw. Only role_taxonomy.py earns that label.
        """
        offenders: list[str] = []
        for path in _python_files():
            if path.name == "role_taxonomy.py":
                continue
            source = path.read_text(encoding="utf-8")
            for number, line in enumerate(source.splitlines(), start=1):
                if "TAXONOMY_MATCHED" in line and "company_source" in line:
                    offenders.append(f"{path.relative_to(APP.parent)}:{number}")
                elif "TAXONOMY_MATCHED" in line and "location_source" in line:
                    offenders.append(f"{path.relative_to(APP.parent)}:{number}")
        self.assertEqual(
            offenders,
            [],
            "These sites claim a taxonomy match without running the matcher: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
