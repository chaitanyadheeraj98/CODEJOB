"""Every place that records `gate_provider` must record `gate_error` beside it.

`EmailIntentDecision.error` was populated on every failure and then discarded: the
write sites persisted the provider and nothing else. The cost of that was invisible
until the historical record was read back - roughly half of all gate calls had
degraded to the rules taxonomy, and not one row said whether the cause was a
timeout, a rate limit, or a response that failed schema validation. Those want three
different fixes.

A behavioural test cannot catch the failure this guards, because the failure is a
*new* write site added later in a file nobody thought to look at - and every
existing behavioural test still passes when that happens. So this is an AST check,
in the same spirit as `test_role_write_site_coverage.py`, which exists because that
exact mistake has already been made once in this codebase.

If this fails, add `gate_error` at the offending site. Do not extend an allow-list.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

APP = Path(__file__).parents[1] / "app"

# Constructors that carry the gate's verdict onto a persisted row.
GATE_ROW_CONSTRUCTORS = {"SkippedItemRecord", "RecruiterEmail", "RecentRunSkippedItem"}


def _python_files() -> list[Path]:
    return [path for path in APP.rglob("*.py") if "__pycache__" not in str(path)]


class GateErrorWriteSiteCoverageTests(unittest.TestCase):
    def test_every_gate_provider_keyword_has_a_gate_error_beside_it(self) -> None:
        offenders: list[str] = []
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name not in GATE_ROW_CONSTRUCTORS:
                    continue
                keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
                if "gate_provider" in keywords and "gate_error" not in keywords:
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "These sites persist a gate provider with no error, so a fallback there is "
            "undiagnosable forever: " + ", ".join(offenders),
        )

    def test_every_gate_provider_assignment_has_a_gate_error_beside_it(self) -> None:
        """The ingest paths assign onto an existing row rather than constructing one."""
        offenders: list[str] = []
        for path in _python_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                targets = [
                    target.attr
                    for target in node.targets
                    if isinstance(target, ast.Attribute)
                ]
                if "gate_provider" not in targets:
                    continue
                # The paired write must be within a few lines - same block, same row.
                window = source.splitlines()[node.lineno - 1 : node.lineno + 4]
                if not any("gate_error" in line for line in window):
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "These assignments set gate_provider without setting gate_error next to it: "
            + ", ".join(offenders),
        )


class GateErrorPersistenceTests(unittest.TestCase):
    """The AST checks above prove the *call* passes gate_error. This proves it lands."""

    def setUp(self) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session, sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.db import Base

        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, class_=Session)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(Base.metadata.drop_all, bind=self.engine)

    def test_skipped_item_round_trips_the_gate_error(self) -> None:
        from app.models import RecentRunSkippedItem
        from app.recent_runs import RUN_SOURCE_GMAIL_SYNC, SkippedItemRecord, record_skipped_item

        with self.Session() as db:
            record_skipped_item(
                db,
                SkippedItemRecord(
                    owner_id="owner",
                    run_source=RUN_SOURCE_GMAIL_SYNC,
                    run_key="run-1",
                    source_type="gmail",
                    reason_code="general_newsletter",
                    reason_detail="Newsletter.",
                    gate_action="skip",
                    gate_provider="deepseek_fallback_taxonomy",
                    gate_error="deepseek_invalid_shape",
                ),
            )
            db.commit()
            row = db.query(RecentRunSkippedItem).one()

        self.assertEqual(row.gate_provider, "deepseek_fallback_taxonomy")
        self.assertEqual(row.gate_error, "deepseek_invalid_shape")

    def test_a_successful_gate_call_records_no_error(self) -> None:
        """NULL means "the provider answered", and must not be confused with a code."""
        from app.models import RecentRunSkippedItem
        from app.recent_runs import RUN_SOURCE_GMAIL_SYNC, SkippedItemRecord, record_skipped_item

        with self.Session() as db:
            record_skipped_item(
                db,
                SkippedItemRecord(
                    owner_id="owner",
                    run_source=RUN_SOURCE_GMAIL_SYNC,
                    run_key="run-2",
                    source_type="gmail",
                    reason_code="general_newsletter",
                    reason_detail="Newsletter.",
                    gate_action="skip",
                    gate_provider="deepseek",
                    gate_error=None,
                ),
            )
            db.commit()
            row = db.query(RecentRunSkippedItem).one()

        self.assertIsNone(row.gate_error)


if __name__ == "__main__":
    unittest.main()
