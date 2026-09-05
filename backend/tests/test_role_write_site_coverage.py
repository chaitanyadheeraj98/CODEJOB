"""Every place that builds a RecruiterEmail must label where `role` came from.

This exists because an audit missed five write sites. The original sweep grepped
for attribute assignment (`email.role = ...`) and found six, but the primary
ingest paths build the row in a constructor instead - `RecruiterEmail(role=...)`
in run_orchestrator._email_row, orchestration_service.sync_gmail (x2) and
main.ingest_email (x2, since removed with that endpoint). Those five wrote
`role=str(parsed["role"])` with no normalisation, no provenance and no taxonomy
lookup, so newly ingested mail bypassed the entire mechanism while every
behavioural test still passed.

A source-level check is crude, but it catches the failure a behavioural test
cannot: a *new* write site added later, in a file nobody thought to look at. If
this fails, route that site through `assign_role()` - do not extend the
allow-list.
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).parents[1] / "app"

# Role values that are already ladder output.
ALLOWED_ROLE_VALUES = {
    "assigned.role",
    "nvoids_role.role",
    "feed_role.role",
    "child_role.role",
}


def _python_files() -> list[Path]:
    return [path for path in APP.rglob("*.py") if "__pycache__" not in str(path)]


def _recruiter_email_calls(tree: ast.AST):
    """Every RecruiterEmail(...) construction.

    Via AST rather than regex so unrelated `role=` keywords - contact roles,
    draft-builder arguments, routing evidence - cannot produce false hits.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "RecruiterEmail":
            yield node


class RoleWriteSiteCoverageTests(unittest.TestCase):
    def test_every_recruiter_email_construction_labels_provenance(self) -> None:
        offenders: list[str] = []
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in _recruiter_email_calls(tree):
                keywords = {kw.arg for kw in call.keywords if kw.arg}
                if "role" in keywords and "role_source" not in keywords:
                    offenders.append(f"{path.relative_to(APP.parent)}:{call.lineno}")
        self.assertEqual(
            offenders,
            [],
            "Build a RecruiterEmail with a role but no role_source and the value is "
            "unlabelled forever. Route through assign_role(): " + ", ".join(offenders),
        )

    def test_no_unprocessed_role_reaches_a_constructor(self) -> None:
        """`role=str(parsed["role"])` is the exact shape that slipped past the audit."""
        offenders: list[str] = []
        for path in _python_files():
            source = path.read_text(encoding="utf-8")
            for call in _recruiter_email_calls(ast.parse(source)):
                for keyword in call.keywords:
                    if keyword.arg != "role":
                        continue
                    value = ast.get_source_segment(source, keyword.value) or ""
                    if value not in ALLOWED_ROLE_VALUES:
                        offenders.append(
                            f"{path.relative_to(APP.parent)}:{call.lineno} role={value}"
                        )
        self.assertEqual(
            offenders,
            [],
            "Unprocessed role passed to a constructor; compute it with assign_role() "
            "first: " + " | ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
