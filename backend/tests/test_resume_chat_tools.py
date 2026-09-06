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


class ResumeChatToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.session_patch = patch.object(resume_tools, "SessionLocal", self.SessionLocal)
        self.session_patch.start()

    def tearDown(self) -> None:
        self.session_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _resume(
        self,
        *,
        owner: str = settings.owner_id,
        file_name: str = "resume.pdf",
        variant_label: str = "",
        primary_role: str = "",
        version: int = 1,
        content: str | None = "Resume content",
        current: bool = True,
    ) -> int:
        with self.SessionLocal() as db:
            row = ResumeAsset(
                owner_id=owner,
                file_path=f"/tmp/{owner}-{version}-{file_name}",
                file_name=file_name,
                sha256=f"{version:064x}"[-64:],
                version=version,
                variant_label=variant_label,
                primary_role=primary_role,
                content_markdown=content,
                content_summary="summary",
                is_current=current,
            )
            db.add(row)
            db.commit()
            return row.id

    def test_variant_label_is_the_first_name_matched(self) -> None:
        expected = self._resume(variant_label="Cloud Platform", primary_role="Backend Engineer")
        self._resume(file_name="cloud_filename.pdf", variant_label="Java")
        self.assertEqual(resume_tools.get_resume(variant="cloud")["id"], expected)

    def test_primary_role_is_used_when_no_label_matches(self) -> None:
        expected = self._resume(variant_label="Backend", primary_role="Data Engineer")
        self.assertEqual(resume_tools.get_resume(variant="data")["id"], expected)

    def test_file_name_is_used_when_no_label_or_role_matches(self) -> None:
        expected = self._resume(
            file_name="java_resume.pdf", variant_label="Backend", primary_role="Software Engineer"
        )
        self.assertEqual(resume_tools.get_resume(variant="java")["id"], expected)

    def test_same_name_returns_the_highest_version(self) -> None:
        self._resume(file_name="cloud.pdf", variant_label="Cloud", version=1, current=False)
        expected = self._resume(
            file_name="cloud.pdf", variant_label="Updated", version=2, content="new content"
        )
        payload = resume_tools.get_resume(variant="cloud")
        self.assertEqual(payload["id"], expected)
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["other_versions"], 1)
        self.assertIn("new content", payload["untrusted_resume_data"])

    def test_different_matching_names_are_reported_as_ambiguous(self) -> None:
        self._resume(file_name="aws.pdf", variant_label="Cloud AWS")
        self._resume(file_name="azure.pdf", variant_label="Cloud Azure")
        payload = resume_tools.get_resume(variant="cloud")
        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(len(payload["matches"]), 2)
        self.assertNotIn("untrusted_resume_data", payload)

    def test_no_match_lists_known_names(self) -> None:
        self._resume(
            file_name="java.pdf", variant_label="Backend", primary_role="Java Engineer"
        )
        payload = resume_tools.get_resume(variant="cloud")
        self.assertEqual(payload["status"], "not_found")
        self.assertIn("Backend", payload["known"])
        self.assertIn("Java Engineer", payload["known"])
        self.assertIn("java.pdf", payload["known"])

    def test_failed_extraction_is_not_presented_as_an_empty_resume(self) -> None:
        resume_id = self._resume(variant_label="Unreadable", content=None)
        payload = resume_tools.get_resume(resume_id)
        self.assertIn("error", payload)
        self.assertNotIn("untrusted_resume_data", payload)

    def test_readable_content_stays_inside_the_untrusted_delimiters(self) -> None:
        resume_id = self._resume(content="Built Kubernetes platforms")
        payload = resume_tools.get_resume(resume_id)
        self.assertIn("<untrusted_resume_data>", payload["untrusted_resume_data"])
        self.assertIn("Built Kubernetes platforms", payload["untrusted_resume_data"])
        self.assertIn("</untrusted_resume_data>", payload["untrusted_resume_data"])

    def test_another_owners_resume_is_invisible_by_id_and_name(self) -> None:
        resume_id = self._resume(owner="other-owner", variant_label="Cloud Secret")
        self.assertEqual(resume_tools.get_resume(resume_id), {"error": "Resume not found"})
        self.assertEqual(resume_tools.get_resume(variant="cloud")["status"], "not_found")

    def test_id_lookup_remains_backwards_compatible(self) -> None:
        resume_id = self._resume(file_name="original.pdf", content="Original content")
        payload = resume_tools.get_resume(resume_id)
        self.assertEqual(payload["id"], resume_id)
        self.assertEqual(payload["file_name"], "original.pdf")
        self.assertIn("Original content", payload["untrusted_resume_data"])

    def test_list_resumes_includes_variant_names_and_readability(self) -> None:
        self._resume(variant_label="Cloud", primary_role="Cloud Engineer", content="readable")
        row = resume_tools.list_resumes()["resumes"][0]
        self.assertEqual(row["variant_label"], "Cloud")
        self.assertEqual(row["primary_role"], "Cloud Engineer")
        self.assertTrue(row["readable"])


if __name__ == "__main__":
    unittest.main()
