"""The measurement R13 was added to justify, kept as a test so it cannot rot.

§5.4 claimed section addressing would make prompt tokens on a tailoring session
"measurably fall" and left the number owed. This is that number, computed from
the tool payloads themselves rather than from a live model: the payload is what
enters the model's context for the rest of the turn, so its size is what the
prompt-token count is a function of.

Sizes are reported in characters, which are measured exactly. The token figure
in the printed summary is characters/4 - the usual English rule of thumb, and an
estimate; the *ratio* is the same in either unit, and the ratio is the claim.

Run it on its own to see the table:

    pytest tests/test_resume_section_tokens.py -s
"""

import json
import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools import resumes as resume_tools
from app.models import ResumeAsset


# A resume of ordinary length for this product - four roles, a skills block and
# education. Deliberately not a toy: the saving is a ratio between one section
# and the whole document, so a three-line fixture would overstate it.
RESUME = """# Chaithanya Dheeraj
Austin, TX | chaithanya@example.com | (512) 555-0142 | linkedin.com/in/example

## Summary
Senior full stack engineer with nine years building payment and identity
systems at scale. Led the migration of a monolithic billing service to a set of
event-driven services handling 40 million transactions a month. Comfortable
owning a feature from schema design through rollout, on-call, and the follow-up
that keeps it from paging anyone again.

## Skills
Languages: Python, TypeScript, Go, SQL, Java
Frameworks: FastAPI, Django, React, Node.js, Spring Boot
Data: PostgreSQL, Redis, Kafka, Snowflake, DynamoDB
Cloud: AWS (ECS, Lambda, RDS, S3), Terraform, Docker, Kubernetes
Practice: distributed tracing, load testing, incident review, schema migration

## Experience

### Staff Engineer, Northwind Payments (2022 - present)
- Split a 400k-line billing monolith into seven services without a maintenance
  window, using a dual-write and backfill strategy verified by a nightly parity
  job that compared both stores row for row.
- Cut p99 checkout latency from 1.9s to 310ms by moving fee calculation off the
  request path and caching the rate table with an explicit invalidation hook.
- Designed the idempotency layer now used by every write endpoint; duplicate
  charge incidents went from roughly one a month to none in eighteen months.
- Mentored four engineers, two of whom now own services outright.

### Senior Engineer, Aldercore Systems (2019 - 2022)
- Built the identity service backing single sign-on for 2.1 million users,
  including SAML and OIDC support and a migration path off the legacy session
  store that ran for six months with no user-visible cutover.
- Introduced contract testing between the identity service and its eleven
  consumers, which caught 23 breaking changes before they reached staging.
- Ran the on-call rotation redesign that halved after-hours pages.

### Engineer, Brightfield Health (2017 - 2019)
- Implemented HL7 and FHIR ingestion for clinical records from 40 hospital
  systems, normalising wildly inconsistent source data into one schema.
- Wrote the deduplication logic that reconciled patient identity across sources
  with a measured false-merge rate below 0.02%.

### Junior Engineer, Cobalt Analytics (2016 - 2017)
- Maintained the ETL pipeline feeding the reporting warehouse.
- Added the first integration test suite the team had.

## Education
B.Tech, Computer Science - Jawaharlal Nehru Technological University, 2016

## Certifications
AWS Certified Solutions Architect - Professional (2023)
"""

# The run the system prompt actually describes: "Summary, then Skills, then a
# role's bullets". The third entry is one role rather than the whole Experience
# block on purpose - `find_section` returns a section *with* its subsections, so
# asking for "Experience" would return four roles and understate what addressing
# buys on the step where it is used most.
TAILORING_SECTIONS = (
    "Summary",
    "Skills",
    "Staff Engineer, Northwind Payments (2022 - present)",
)


class ResumeSectionTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.session_patch = patch.object(resume_tools, "SessionLocal", self.SessionLocal)
        self.session_patch.start()
        with self.SessionLocal() as db:
            row = ResumeAsset(
                owner_id=settings.owner_id,
                file_name="resume.docx",
                file_path="/tmp/resume.docx",
                sha256="0" * 64,
                content_markdown=RESUME,
                version=1,
                is_current=True,
            )
            db.add(row)
            db.commit()
            self.resume_id = row.id

    def tearDown(self) -> None:
        self.session_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _payload_chars(payload: dict[str, object]) -> int:
        """What the whole tool result costs, not just its resume text.

        The model receives the serialised payload, so the keys and the
        `sections` list a section read adds are part of its price and are
        counted here rather than quietly left out to flatter the result.
        """
        return len(json.dumps(payload, separators=(",", ":")))

    def test_a_tailoring_run_reads_far_less_when_it_addresses_sections(self) -> None:
        whole = resume_tools.get_resume(resume_id=self.resume_id)
        self.assertIn("untrusted_resume_data", whole)
        whole_chars = self._payload_chars(whole)

        per_section = {}
        for heading in TAILORING_SECTIONS:
            payload = resume_tools.get_resume(resume_id=self.resume_id, section=heading)
            self.assertEqual(payload.get("section"), heading, payload)
            per_section[heading] = self._payload_chars(payload)

        # The run as the assistant actually performs it: one read per section it
        # is about to rewrite. Today that is one whole-document read each time.
        before = whole_chars * len(TAILORING_SECTIONS)
        after = sum(per_section.values())
        saved = before - after

        print(f"\n  Whole-document read: {whole_chars:,} chars (~{whole_chars // 4:,} tokens)")
        for heading, chars in per_section.items():
            print(f"  Section '{heading}': {chars:,} chars (~{chars // 4:,} tokens)")
        print(
            f"  {len(TAILORING_SECTIONS)}-section tailoring run: "
            f"{before:,} -> {after:,} chars "
            f"(~{before // 4:,} -> ~{after // 4:,} tokens), "
            f"{saved * 100 // before}% less"
        )

        # The floor the claim has to clear. Left well under the measured figure
        # on purpose: this asserts that section addressing still pays, not that
        # the fixture keeps its exact proportions, so editing the resume above
        # does not turn a documentation change into a failing build.
        self.assertLess(after, before)
        self.assertGreaterEqual(saved * 100 // before, 40)

        # Every section read is smaller than reading the whole thing, including
        # Experience - the largest section and the one most likely to erase the
        # saving if section splitting ever regressed to near-whole-document.
        for heading, chars in per_section.items():
            self.assertLess(chars, whole_chars, heading)

    def test_a_section_read_still_names_every_heading_so_nothing_is_hidden(self) -> None:
        """The saving must not cost the model its map of the document.

        A section read returns that one body plus the full heading list, which is
        what lets the next call address the next section without a whole-document
        read to discover it exists.
        """
        payload = resume_tools.get_resume(resume_id=self.resume_id, section="Summary")
        self.assertEqual(
            payload["sections"],
            ["Chaithanya Dheeraj", "Summary", "Skills", "Experience",
             "Staff Engineer, Northwind Payments (2022 - present)",
             "Senior Engineer, Aldercore Systems (2019 - 2022)",
             "Engineer, Brightfield Health (2017 - 2019)",
             "Junior Engineer, Cobalt Analytics (2016 - 2017)",
             "Education", "Certifications"],
        )
        self.assertNotIn("Northwind", payload["untrusted_resume_data"])


if __name__ == "__main__":
    unittest.main()
