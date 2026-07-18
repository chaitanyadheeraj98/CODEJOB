import unittest
import json

from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService


class ScoringRuntimeServiceTests(unittest.TestCase):
    def test_semantic_text_for_email_compacts_skills(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))
        text = service.semantic_text_for_email(
            "",
            "Need AI Engineer with strong RAG and tool calling experience.",
            "AI Engineer",
            "RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability, Python, Java, TypeScript, REST APIs",
        )
        self.assertIn("RoleFamily:ai", text)
        self.assertIn("Clusters:", text)
        self.assertIn("RAG", text)

    def test_semantic_text_for_resume_prefers_manual_skills(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))

        class Resume:
            skills_text = "Java, Spring Boot, AWS"
            file_path = "missing.docx"
            file_name = "missing.docx"

        text = service.semantic_text_for_resume(Resume())
        self.assertIn("Skills:", text)
        self.assertIn("Java", text)
        self.assertIn("Spring Boot", text)
        self.assertIn("AWS", text)

    def test_semantic_text_for_resume_can_skip_file_fallback_for_matching(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))

        class Resume:
            skills_text = ""
            file_path = "missing.docx"
            file_name = "missing.docx"

        text = service.semantic_text_for_resume(Resume(), allow_file_fallback=False)
        self.assertEqual(text, "")

    def test_extract_latest_message_block_prefers_newest_segment(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))
        body = (
            "Latest update: Java + Spring Boot role in TX.\n"
            "Please review.\n\n"
            "On Thu, May 28, 2026 at 4:11 PM Recruiter wrote:\n"
            "> older thread details"
        )
        latest, source = service._extract_latest_message_block(body)
        self.assertIn("Latest update", latest)
        self.assertEqual(source, "latest_block")

    def test_safe_embed_chunking_for_long_input(self) -> None:
        calls: list[int] = []

        def embed(text: str) -> tuple[list[float], str]:
            calls.append(len(text))
            return [1.0, 2.0], "openrouter"

        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=embed))
        vector, payload, provider, chunks = service._safe_embed_with_chunking(None, "x" * 2500)
        self.assertEqual(provider, "openrouter")
        self.assertEqual(chunks, 3)
        self.assertEqual(vector, [1.0, 2.0])
        self.assertTrue(payload is not None)
        self.assertGreaterEqual(len(calls), 2)

    def test_safe_embed_chunking_reuses_cached_payload_for_long_input(self) -> None:
        calls: list[int] = []

        def embed(text: str) -> tuple[list[float], str]:
            calls.append(len(text))
            return [1.0, 2.0], "openrouter"

        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=embed))
        vector, payload, provider, chunks = service._safe_embed_with_chunking("[0.25,0.75]", "x" * 2500)
        self.assertEqual(vector, [0.25, 0.75])
        self.assertEqual(payload, "[0.25,0.75]")
        self.assertEqual(provider, "cache")
        self.assertEqual(chunks, 1)
        self.assertEqual(calls, [])

    def test_missing_resume_skills_skips_semantic_instead_of_file_extraction(self) -> None:
        def failing_embed(_text: str) -> tuple[list[float], str]:
            raise ValueError("No embedding data received")

        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=failing_embed))
        parsed = {
            "role": "Java Developer",
            "skills_text": "java, spring, spring boot, react",
            "salary_text": "",
            "location": "hybrid",
        }

        class Settings:
            feature_semantic_enabled = True
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            semantic_embedding = None
            file_path = "missing.docx"
            file_name = "missing.docx"

        score, summary, source, _email_embedding, _resume_embedding, diag = service.compute_blended_ai_score(
            subject="Java Developer",
            body="Body " + ("x" * 2000),
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resume=Resume(),
        )
        self.assertEqual(source, "v2_rules_plus_semantic")
        self.assertIn("semantic skipped (resume text unavailable)", summary)
        self.assertLessEqual(score, 0.18)
        self.assertEqual(diag.fallback_reason, "resume_text_unavailable")

    def test_compute_ats_score_rewards_exact_overlap(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java, Spring Boot, AWS, Microservices",
            "salary_text": "",
            "location": "Remote",
        }

        class Settings:
            feature_semantic_enabled = False

        class Resume:
            def __init__(self, file_name: str, skills_text: str):
                self.file_name = file_name
                self.skills_text = skills_text

        weak_resume = Resume("weak.docx", "Java")
        strong_resume = Resume("strong.docx", "Java, Spring Boot, AWS, Microservices")
        weak_score, weak_source, weak_summary, weak_breakdown = service.compute_ats_score(
            subject="Java Developer",
            body="Need Java, Spring Boot, AWS, and Microservices",
            parsed=parsed,
            user_settings=Settings(),
            resume=weak_resume,
        )
        strong_score, strong_source, strong_summary, strong_breakdown = service.compute_ats_score(
            subject="Java Developer",
            body="Need Java, Spring Boot, AWS, and Microservices",
            parsed=parsed,
            user_settings=Settings(),
            resume=strong_resume,
        )

        self.assertIsNotNone(weak_score)
        self.assertIsNotNone(strong_score)
        assert weak_score is not None and strong_score is not None
        self.assertLess(weak_score, strong_score)
        self.assertEqual(weak_source, "hybrid_structured_only")
        self.assertEqual(strong_source, "hybrid_structured_only")
        self.assertIn("ATS hybrid score", strong_summary or "")
        self.assertEqual(json.loads(strong_breakdown or "{}")["selected_resume_file_name"], "strong.docx")

    def test_compute_ats_score_penalizes_vague_ai_wording(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "AI Engineer",
            "skills_text": "RAG, Tool Calling, Embeddings, Observability",
            "salary_text": "",
            "location": "Remote",
        }

        class Settings:
            feature_semantic_enabled = False

        class Resume:
            file_name = "ai_resume.docx"
            skills_text = "Generative AI, AI exposure, AI-assisted engineering, Observability"

        score, _source, summary, breakdown = service.compute_ats_score(
            subject="AI Engineer",
            body="Need RAG, tool calling, embeddings, and observability",
            parsed=parsed,
            user_settings=Settings(),
            resume=Resume(),
        )
        self.assertIsNotNone(score)
        assert score is not None
        self.assertLess(score, 45.0)
        self.assertIn("weak_signals=", summary or "")
        self.assertIn("weak_signal_hits", json.loads(breakdown or "{}"))

    def test_weak_skills_uses_rich_fallback_keyword_source(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "java",
            "salary_text": "",
            "location": "hybrid",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        score, summary, _source, _e, _r, diag = service.compute_blended_ai_score(
            subject="Java Developer",
            body="Need Java + Spring Boot + React developers",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resume=None,
        )
        self.assertGreaterEqual(score, 0.54)
        self.assertEqual(diag.keyword_source, "rich_fallback")
        self.assertIn("AI fit score computed", summary)

    def test_thread_snapshot_carry_forward_when_current_is_weaker(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))

        class FakeRow:
            def __init__(self, rid: int, skills: str):
                self.id = rid
                self.skills_text = skills

        class FakeQuery:
            def __init__(self, rows):
                self.rows = rows
            def filter(self, *_args, **_kwargs):
                return self
            def order_by(self, *_args, **_kwargs):
                return self
            def limit(self, *_args, **_kwargs):
                return self
            def all(self):
                return self.rows

        class FakeDb:
            def __init__(self, rows):
                self.rows = rows
            def query(self, _model):
                return FakeQuery(self.rows)

        parsed = {"role": "Java Developer", "skills_text": "java", "salary_text": "", "location": "hybrid"}

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        fake_db = FakeDb([FakeRow(11, "java, spring, spring boot, react")])
        _score, _summary, _src, _e, _r, diag = service.compute_blended_ai_score(
            subject="Java Developer",
            body="Need Java",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resume=None,
            db=fake_db,
            owner_id="default-owner",
            external_thread_id="thread-1",
        )
        self.assertEqual(diag.keyword_source, "thread_carry_forward")
        self.assertTrue(diag.thread_snapshot_used)
        self.assertEqual(diag.thread_snapshot_email_id, 11)

    def test_select_best_resume_match_prefers_hands_on_ai_resume_for_ai_jd(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "AI Engineer",
            "skills_text": "RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability, Python, Java, TypeScript, REST APIs, Secure SDLC",
            "salary_text": "",
            "location": "Alpharetta, GA",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str, current: bool = False):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = current

        resumes = [
            Resume(
                1,
                "Chaithanya_Dheeraj_Full_Stack_Engineer_Java_Python_GenAI.docx",
                "Java, Python, Prompt Engineering, RAG, Semantic Retrieval, Embeddings, Agentic Workflows, Human-in-the-Loop, AI Evaluations, Observability, Secure SDLC",
                True,
            ),
            Resume(
                2,
                "Chaithanya_Dheeraj_Full_Stack_Java_Engineer_React_AI_Dallas.docx",
                "Java, TypeScript, REST APIs, Generative AI, AI exposure, Agentic AI Concepts, AI-Assisted Engineering, Observability",
            ),
        ]

        selection = service.select_best_resume_match(
            subject="",
            body="Role: AI Engineer. Need RAG, tool calling, embeddings, HITL and observability.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=resumes,
            fallback_resume=resumes[0],
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "Chaithanya_Dheeraj_Full_Stack_Engineer_Java_Python_GenAI.docx")
        self.assertIn("matched_ai_core", selection.ai_summary)
        self.assertGreater(selection.final_resume_score, 0.0)
        self.assertIsNotNone(selection.picker_breakdown_json)
        self.assertIsNotNone(selection.candidate_rankings_json)

    def test_select_best_resume_match_reuses_precomputed_email_embedding_across_resumes(self) -> None:
        calls: list[int] = []

        def embed(text: str) -> tuple[list[float], str]:
            calls.append(len(text))
            return [0.5, 0.5], "sbert"

        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=embed))
        parsed = {
            "role": "AI Engineer",
            "skills_text": "RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability, Python, Java, TypeScript, REST APIs",
            "salary_text": "",
            "location": "Remote",
        }
        body = "Need an AI engineer with strong retrieval, agentic workflows, observability, and safety. " * 40

        class Settings:
            feature_semantic_enabled = True
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        resume_a = Resume(1, "a.docx", "Python, RAG")
        resume_b = Resume(2, "b.docx", "Java, Observability")
        email_chunks = service._chunk_text(
            service.semantic_text_for_email("", body, parsed["role"], parsed["skills_text"]),
            chunk_size=900,
        )
        expected_email_chunks = len(email_chunks)

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[resume_a, resume_b],
            fallback_resume=resume_a,
        )

        self.assertIsNotNone(selection.resume)
        self.assertEqual(len(calls), expected_email_chunks + 2)
        self.assertEqual(calls[:expected_email_chunks], [len(chunk) for chunk in email_chunks])
        self.assertTrue(all(count < 900 for count in calls[-2:]))

    def test_non_ai_jd_keeps_foundation_bias_stable(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java, Spring Boot, Microservices, REST APIs, AWS, CI/CD",
            "salary_text": "",
            "location": "Dallas, TX",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        full_stack = Resume(1, "java_full_stack.docx", "Java, Spring Boot, Microservices, REST APIs, AWS, CI/CD, SQL")
        ai_specialist = Resume(2, "ai_specialist.docx", "RAG, Tool Calling, Embeddings, Prompt Engineering, Python")

        selection = service.select_best_resume_match(
            subject="",
            body="Java Developer role with Spring Boot and AWS.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[full_stack, ai_specialist],
            fallback_resume=full_stack,
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "java_full_stack.docx")

    def test_resume_picker_prefers_priority_coverage_for_email_3988_style_jd(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Full Stack Developer",
            "skills_text": "Java, Spring Boot, Oracle, PL/SQL, Artificial Intelligence tools, Cloud-native development, OpenShift, Testing Automation, GitHub Enterprise, Microservices",
            "salary_text": "",
            "location": "Irving, TX",
        }
        parser_details = {
            "skills_audit": {
                "known": ["Java", "Spring Boot", "Oracle", "PL/SQL", "OpenShift", "GitHub Enterprise", "Microservices"],
                "unknown": ["Artificial Intelligence tools", "Cloud-native development", "Testing Automation"],
            }
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        role_clean = Resume(1, "RCR.docx", "Java, Spring Boot, React, SQL, REST APIs, Microservices, AWS, Jenkins")
        priority_rich = Resume(2, "ARP.docx", "Java, Spring Boot, Oracle Concepts, PL/SQL Concepts, GitHub Enterprise, OpenShift, AI tools, Cloud-native development, Microservices")

        selection = service.select_best_resume_match(
            subject="",
            body="Need Oracle, PL/SQL, AI tools, OpenShift and cloud-native Java full stack experience.",
            parsed=parsed,
            parser_details=parser_details,
            user_settings=Settings(),
            email_row=None,
            resumes=[role_clean, priority_rich],
            fallback_resume=role_clean,
        )

        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "ARP.docx")
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertIn("Oracle", breakdown.get("matched_priority_skills", []))
        self.assertGreater(float(breakdown.get("partial_credit_score", 0.0)), 0.0)
        self.assertEqual(breakdown.get("mandatory_gate_status"), "needs_review")

    def test_mandatory_pass_beats_slightly_higher_broad_fit(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Full Stack Developer",
            "skills_text": "Java, Spring Boot, Oracle, PL/SQL",
            "salary_text": "",
            "location": "Remote",
        }
        body = "Required Qualifications:\nJava\nSpring Boot\nOracle\nPL/SQL"

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        broad_fit = Resume(1, "broad_fit.docx", "Java, Spring Boot, React, AWS, CI/CD, Microservices")
        mandatory_fit = Resume(2, "mandatory_fit.docx", "Java, Spring Boot, Oracle, PL/SQL")

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[broad_fit, mandatory_fit],
            fallback_resume=broad_fit,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertEqual(selection.resume.file_name, "mandatory_fit.docx")
        self.assertEqual(breakdown.get("mandatory_gate_status"), "pass")
        self.assertEqual(breakdown.get("mandatory_missing_skills"), [])

    def test_alias_based_mandatory_gate_passes_reactive_and_procedural_sql(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Spring Reactive, Procedural SQL, Java",
            "salary_text": "",
            "location": "Remote",
        }
        body = "Required Skills:\nSpring Reactive\nProcedural SQL\nJava"

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        reactive_resume = Resume(1, "reactive_resume.docx", "Java, Spring WebFlux, PL/SQL, Stored Procedures")

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[reactive_resume],
            fallback_resume=reactive_resume,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        evidence = breakdown.get("mandatory_evidence", {})
        self.assertEqual(breakdown.get("mandatory_gate_status"), "pass")
        self.assertEqual(breakdown.get("mandatory_missing_skills"), [])
        self.assertEqual(evidence["Spring Reactive"]["matched_alias"], "spring webflux")
        self.assertEqual(evidence["Procedural SQL"]["matched_alias"], "pl/sql")

    def test_oracle_db_does_not_satisfy_oci_requirement(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Cloud Security Engineer",
            "skills_text": "Oracle Cloud Infrastructure (OCI), Terraform, PowerShell",
            "salary_text": "",
            "location": "Remote",
        }
        body = "Required Skills:\nOracle Cloud Infrastructure (OCI)\nTerraform\nPowerShell"

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        oracle_db_resume = Resume(1, "oracle_db.docx", "Oracle Database, PL/SQL, Terraform, PowerShell")

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[oracle_db_resume],
            fallback_resume=oracle_db_resume,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertIn("Oracle Cloud Infrastructure (OCI)", breakdown.get("mandatory_missing_skills", []))
        self.assertEqual(breakdown.get("mandatory_gate_status"), "fail")

    def test_sspm_security_jd_prefers_security_resume(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Cloud Security Engineer",
            "skills_text": "SaaS Security Posture Management, AppOmni, CASB, Terraform, PowerShell",
            "salary_text": "",
            "location": "Remote",
        }
        body = "Must Have:\nSaaS Security Posture Management\nAppOmni\nCASB\nTerraform\nPowerShell"

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        unrelated_resume = Resume(1, "oracle_java.docx", "Oracle, Java, Spring Boot")
        security_resume = Resume(2, "security_resume.docx", "SSPM, AppOmni, CASB, Terraform, PowerShell")

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[unrelated_resume, security_resume],
            fallback_resume=unrelated_resume,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertEqual(selection.resume.file_name, "security_resume.docx")
        self.assertEqual(breakdown.get("mandatory_gate_status"), "pass")

    def test_all_fail_fallback_and_candidate_rankings_include_gate_details(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Cloud Security Engineer",
            "skills_text": "SaaS Security Posture Management, AppOmni, CASB",
            "salary_text": "",
            "location": "Remote",
        }
        body = "Mandatory Skills:\nSaaS Security Posture Management\nAppOmni\nCASB"

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        resume_a = Resume(1, "resume_a.docx", "Java, Spring Boot")
        resume_b = Resume(2, "resume_b.docx", "Terraform")

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[resume_a, resume_b],
            fallback_resume=resume_a,
        )
        rankings = json.loads(selection.candidate_rankings_json or "{}").get("rankings", [])
        self.assertEqual(selection.mandatory_gate_status, "fail")
        self.assertTrue(rankings)
        self.assertIn("mandatory_gate_status", rankings[0]["picker_breakdown"])
        self.assertIn("mandatory_missing_skills", rankings[0]["picker_breakdown"])

    def test_grouped_skills_decompose_for_raw_overlap_without_duplicate_missing_phrase(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        overlap, matched, missing = service._raw_skill_overlap(
            "Spring Boot & Microservices, Docker & Kubernetes, SQL/NoSQL Databases",
            "Spring Boot, Microservices, Docker, Kubernetes, SQL",
        )
        self.assertAlmostEqual(overlap, 5 / 6)
        self.assertIn("spring boot", matched)
        self.assertIn("microservices", matched)
        self.assertIn("docker", matched)
        self.assertIn("kubernetes", matched)
        self.assertIn("sql", matched)
        self.assertIn("nosql", missing)
        self.assertNotIn("spring boot & microservices", missing)
        self.assertNotIn("docker & kubernetes", missing)
        self.assertNotIn("sql/nosql databases", missing)

    def test_preferred_grouped_skills_do_not_create_mandatory_fail(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java, Spring Boot & Microservices, REST APIs, Docker & Kubernetes",
            "salary_text": "",
            "location": "Remote",
        }
        body = (
            "Required Skills:\n"
            "Java\n"
            "Spring Boot & Microservices\n"
            "REST APIs\n\n"
            "Preferred Skills:\n"
            "Docker & Kubernetes\n"
            "Maven/Gradle\n"
            "SQL/NoSQL Databases"
        )

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        resume = Resume(1, "java_resume.docx", "Java, Spring Boot, Microservices, REST APIs")

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[resume],
            fallback_resume=resume,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertEqual(breakdown.get("mandatory_gate_status"), "pass")
        self.assertNotIn("Docker", breakdown.get("mandatory_missing_skills", []))
        self.assertNotIn("Kubernetes", breakdown.get("mandatory_missing_skills", []))

    def test_structured_any_group_is_satisfied_by_single_matching_alternative(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Backend Engineer",
            "skills_text": "Kafka, JMS, RabbitMQ",
            "salary_text": "",
            "location": "Remote",
        }
        parser_details = {
            "structured_requirements": {
                "schema_version": 1,
                "required_groups": [
                    {
                        "group_id": "g1",
                        "level": "mandatory",
                        "mode": "any",
                        "skills": [
                            {"skill_id": "kafka", "canonical_name": "Kafka", "matched_alias": "kafka", "evidence_text": "Kafka, JMS, or RabbitMQ", "versions": [], "qualifiers": []},
                            {"skill_id": "jms", "canonical_name": "JMS", "matched_alias": "jms", "evidence_text": "Kafka, JMS, or RabbitMQ", "versions": [], "qualifiers": []},
                            {"skill_id": "rabbitmq", "canonical_name": "RabbitMQ", "matched_alias": "rabbitmq", "evidence_text": "Kafka, JMS, or RabbitMQ", "versions": [], "qualifiers": []},
                        ],
                        "evidence_text": "Kafka, JMS, or RabbitMQ",
                        "section_heading": "Required Skills",
                        "section_bucket": "required",
                    }
                ],
                "preferred_groups": [],
                "informational_groups": [],
                "experience_years_min": None,
                "local_required": False,
                "work_mode": None,
                "locations": [],
                "warnings": [],
                "preferred_domains": [],
            }
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self) -> None:
                self.id = 1
                self.file_name = "backend.docx"
                self.skills_text = "Java, RabbitMQ, Spring Boot"
                self.semantic_embedding = None
                self.file_path = "backend.docx"
                self.is_enabled = True
                self.is_current = False

        selection = service.select_best_resume_match(
            subject="",
            body="Required Skills: Kafka, JMS, or RabbitMQ",
            parsed=parsed,
            parser_details=parser_details,
            user_settings=Settings(),
            email_row=None,
            resumes=[Resume()],
            fallback_resume=None,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertEqual(breakdown.get("mandatory_gate_status"), "pass")
        self.assertNotIn("Kafka", breakdown.get("mandatory_missing_skills", []))
        self.assertNotIn("JMS", breakdown.get("mandatory_missing_skills", []))
        self.assertEqual(breakdown.get("matched_alternatives", {}).get("Kafka or JMS or RabbitMQ"), "RabbitMQ")

    def test_version_unverified_is_reported_without_hard_fail(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java",
            "salary_text": "",
            "location": "Remote",
        }
        parser_details = {
            "structured_requirements": {
                "schema_version": 1,
                "required_groups": [
                    {
                        "group_id": "java-version",
                        "level": "mandatory",
                        "mode": "all",
                        "skills": [
                            {"skill_id": "java", "canonical_name": "Java", "matched_alias": "java", "evidence_text": "Java 17/21", "versions": ["17", "21"], "qualifiers": []},
                        ],
                        "evidence_text": "Java 17/21",
                        "section_heading": "Required Skills",
                        "section_bucket": "required",
                    }
                ],
                "preferred_groups": [],
                "informational_groups": [],
                "experience_years_min": None,
                "local_required": False,
                "work_mode": None,
                "locations": [],
                "warnings": [],
                "preferred_domains": [],
            }
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self) -> None:
                self.id = 1
                self.file_name = "java.docx"
                self.skills_text = "Java, Spring Boot"
                self.semantic_embedding = None
                self.file_path = "java.docx"
                self.is_enabled = True
                self.is_current = False

        selection = service.select_best_resume_match(
            subject="",
            body="Required Skills: Java 17/21",
            parsed=parsed,
            parser_details=parser_details,
            user_settings=Settings(),
            email_row=None,
            resumes=[Resume()],
            fallback_resume=None,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertIn("Java", breakdown.get("version_unverified", []))
        self.assertNotEqual(breakdown.get("mandatory_gate_status"), "fail")

    def test_compute_ats_score_uses_structured_required_and_preferred_group_coverage(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Backend Engineer",
            "skills_text": "Kafka, RabbitMQ, Docker",
            "salary_text": "",
            "location": "Remote",
        }
        parser_details = {
            "structured_requirements": {
                "schema_version": 1,
                "required_groups": [
                    {
                        "group_id": "req-any",
                        "level": "mandatory",
                        "mode": "any",
                        "skills": [
                            {"skill_id": "kafka", "canonical_name": "Kafka", "matched_alias": "kafka", "evidence_text": "Kafka or RabbitMQ", "versions": [], "qualifiers": []},
                            {"skill_id": "rabbitmq", "canonical_name": "RabbitMQ", "matched_alias": "rabbitmq", "evidence_text": "Kafka or RabbitMQ", "versions": [], "qualifiers": []},
                        ],
                        "evidence_text": "Kafka or RabbitMQ",
                        "section_heading": "Required Skills",
                        "section_bucket": "required",
                    }
                ],
                "preferred_groups": [
                    {
                        "group_id": "pref-docker",
                        "level": "preferred",
                        "mode": "all",
                        "skills": [
                            {"skill_id": "docker", "canonical_name": "Docker", "matched_alias": "docker", "evidence_text": "Docker", "versions": [], "qualifiers": []},
                        ],
                        "evidence_text": "Docker",
                        "section_heading": "Preferred Skills",
                        "section_bucket": "preferred",
                    }
                ],
                "informational_groups": [],
                "experience_years_min": None,
                "local_required": False,
                "work_mode": None,
                "locations": [],
                "warnings": [],
                "preferred_domains": [],
            }
        }

        class Settings:
            feature_semantic_enabled = False

        class Resume:
            file_name = "backend.docx"
            skills_text = "RabbitMQ, Docker, Java"

        score, _source, summary, breakdown_json = service.compute_ats_score(
            subject="Backend Engineer",
            body="Need Kafka or RabbitMQ. Docker preferred.",
            parsed=parsed,
            parser_details=parser_details,
            user_settings=Settings(),
            resume=Resume(),
        )

        self.assertIsNotNone(score)
        breakdown = json.loads(breakdown_json or "{}")
        self.assertEqual(breakdown.get("required_group_coverage"), 1.0)
        self.assertEqual(breakdown.get("preferred_group_coverage"), 1.0)
        self.assertEqual(breakdown.get("matched_alternatives", {}).get("Kafka or RabbitMQ"), "RabbitMQ")
        self.assertEqual(breakdown.get("unmet_required_groups"), [])
        self.assertIn("required_group_coverage=1.00", summary or "")

    def test_compute_ats_score_falls_back_to_legacy_overlap_when_structured_requirements_missing(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java, Spring Boot, AWS, Microservices",
            "salary_text": "",
            "location": "Remote",
        }

        class Settings:
            feature_semantic_enabled = False

        class Resume:
            file_name = "legacy.docx"
            skills_text = "Java, Spring Boot"

        _score, _source, _summary, breakdown_json = service.compute_ats_score(
            subject="Java Developer",
            body="Need Java, Spring Boot, AWS, and Microservices",
            parsed=parsed,
            user_settings=Settings(),
            resume=Resume(),
        )
        breakdown = json.loads(breakdown_json or "{}")
        self.assertAlmostEqual(breakdown.get("raw_overlap", 0.0), 0.5)
        self.assertAlmostEqual(breakdown.get("primary_overlap", 0.0), 0.5)
        self.assertIsNone(breakdown.get("required_group_coverage"))

    def test_email_4108_style_all_fail_selects_highest_failed_candidate_with_warning(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Lead Developer",
            "skills_text": (
                "Java, Spring Boot & Microservices, GitLab, CI/CD Pipeline Implementation, "
                "Twistlock (Prisma Cloud) Security Scanning, REST APIs, Agile/Scrum Methodologies, "
                "P&C Knowledge, AWS Cloud Services, Docker & Kubernetes, Maven/Gradle, SQL/NoSQL Databases"
            ),
            "salary_text": "",
            "location": "Remote",
        }
        body = (
            "Mandatory Skills:\n"
            "Java (Java 8+)\n"
            "Spring Boot & Microservices\n"
            "GitLab\n"
            "CI/CD Pipeline Implementation\n"
            "Twistlock (Prisma Cloud) Security Scanning\n"
            "REST APIs\n"
            "Agile/Scrum Methodologies\n"
            "P&C Knowledge\n\n"
            "Preferred Skills:\n"
            "AWS Cloud Services\n"
            "Docker & Kubernetes\n"
            "Maven/Gradle\n"
            "SQL/NoSQL Databases"
        )

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        lower_failed = Resume(
            1,
            "JJTNG.docx",
            "Java, Spring Boot, Microservices, REST APIs, AWS, Docker, Kubernetes, Maven",
        )
        higher_failed = Resume(
            2,
            "JJAS.docx",
            "Java, Spring Boot, Microservices, REST APIs, Agile, Scrum, AWS, Docker, Kubernetes, Maven, SQL",
        )

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[lower_failed, higher_failed],
            fallback_resume=lower_failed,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        rankings = json.loads(selection.candidate_rankings_json or "{}").get("rankings", [])
        self.assertEqual(selection.resume.file_name, "JJAS.docx")
        self.assertEqual(selection.mandatory_gate_status, "fail")
        self.assertEqual(breakdown.get("selection_status"), "needs_review")
        self.assertIn("closest available resume selected", selection.selection_reason or "")
        self.assertIn("new resume generation may be needed", selection.selection_reason or "")
        self.assertIn("GitLab", breakdown.get("mandatory_missing_skills", []))
        self.assertIn("Twistlock (Prisma Cloud) Security Scanning", breakdown.get("mandatory_missing_skills", []))
        self.assertIn("P&C Knowledge", breakdown.get("mandatory_missing_skills", []))
        self.assertTrue(rankings)
        self.assertEqual(rankings[0]["resume_file_name"], "JJAS.docx")

    def test_gitlab_twistlock_and_pc_do_not_collapse_to_generic_equivalents(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Lead Developer",
            "skills_text": "GitLab CI/CD Pipeline Implementation, Twistlock (Prisma Cloud) Security Scanning, P&C Knowledge",
            "salary_text": "",
            "location": "Remote",
        }
        body = (
            "Required Skills:\n"
            "GitLab CI/CD Pipeline Implementation\n"
            "Twistlock (Prisma Cloud) Security Scanning\n"
            "P&C Knowledge"
        )

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        generic_resume = Resume(
            1,
            "generic_resume.docx",
            "Jenkins CI/CD, Security Scanning, Insurance Domain",
        )

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[generic_resume],
            fallback_resume=generic_resume,
        )
        breakdown = json.loads(selection.picker_breakdown_json or "{}")
        self.assertEqual(breakdown.get("mandatory_gate_status"), "fail")
        self.assertIn("GitLab CI/CD Pipeline Implementation", breakdown.get("mandatory_missing_skills", []))
        self.assertIn("Twistlock (Prisma Cloud) Security Scanning", breakdown.get("mandatory_missing_skills", []))
        self.assertIn("P&C Knowledge", breakdown.get("mandatory_missing_skills", []))

    def test_fail_group_prefers_highest_final_score_over_higher_coverage(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Lead Developer",
            "skills_text": (
                "Java, Spring Boot & Microservices, GitLab CI/CD Pipeline Implementation, "
                "Twistlock (Prisma Cloud) Security Scanning, P&C Knowledge, REST APIs, Agile/Scrum Methodologies, "
                "AWS Cloud Services, Docker & Kubernetes"
            ),
            "salary_text": "",
            "location": "Remote",
        }
        body = (
            "Mandatory Skills:\n"
            "Java\n"
            "Spring Boot & Microservices\n"
            "GitLab CI/CD Pipeline Implementation\n"
            "Twistlock (Prisma Cloud) Security Scanning\n"
            "P&C Knowledge\n"
            "REST APIs\n"
            "Agile/Scrum Methodologies\n\n"
            "Preferred Skills:\n"
            "AWS Cloud Services\n"
            "Docker & Kubernetes"
        )

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        higher_coverage_lower_score = Resume(
            1,
            "coverage_first.docx",
            "Java, Spring Boot, Microservices, REST APIs",
        )
        lower_coverage_higher_score = Resume(
            2,
            "score_first.docx",
            "Java, Spring Boot, Microservices, REST APIs, Agile, Scrum, AWS, Docker, Kubernetes",
        )

        selection = service.select_best_resume_match(
            subject="",
            body=body,
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[higher_coverage_lower_score, lower_coverage_higher_score],
            fallback_resume=higher_coverage_lower_score,
        )
        rankings = json.loads(selection.candidate_rankings_json or "{}").get("rankings", [])
        self.assertEqual(selection.mandatory_gate_status, "fail")
        self.assertEqual(selection.resume.file_name, "score_first.docx")
        self.assertTrue(rankings)
        self.assertEqual(rankings[0]["resume_file_name"], "score_first.docx")
        self.assertGreater(rankings[0]["final_resume_score"], rankings[1]["final_resume_score"])

    def test_partial_credit_scores_concepts_and_awareness_below_direct(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))
        parsed = {
            "role": "Java Full Stack Developer",
            "skills_text": "Oracle, PL/SQL, Architecture, Java",
            "salary_text": "",
            "location": "Remote",
        }
        parser_details = {"skills_audit": {"known": ["Oracle", "PL/SQL", "Architecture", "Java"], "unknown": []}}

        class Resume:
            file_name = "credit.docx"
            skills_text = "Oracle Concepts, PL/SQL Concepts, Architecture Awareness, Java"

        scores = service._compute_jd_priority_scores(parsed=parsed, parser_details=parser_details, resume=Resume())
        evidence = scores["priority_evidence"]
        assert isinstance(evidence, dict)
        self.assertEqual(evidence["Oracle"]["evidence_score"], 0.6)
        self.assertEqual(evidence["PL/SQL"]["evidence_score"], 0.6)
        self.assertEqual(evidence["Architecture"]["evidence_score"], 0.4)
        self.assertEqual(evidence["Java"]["evidence_score"], 1.0)
        self.assertGreater(scores["partial_credit_score"], 0.0)

    def test_raw_skills_overlap_can_match_unknown_terms_directly(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Workflow Engineer",
            "skills_text": "Temporal Workflow, Java",
            "salary_text": "",
            "location": "Remote",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        temporal_resume = Resume(1, "temporal_resume.docx", "Temporal Workflow, Java, Spring Boot")
        plain_resume = Resume(2, "plain_java_resume.docx", "Java, Spring Boot")

        selection = service.select_best_resume_match(
            subject="",
            body="Need Temporal Workflow and Java experience.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[temporal_resume, plain_resume],
            fallback_resume=temporal_resume,
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "temporal_resume.docx")
        self.assertIn("raw_overlap=", selection.ai_summary)
        self.assertIn("matched_raw_skills=temporal workflow, java", selection.ai_summary)

    def test_resume_without_saved_skills_is_scored_weak_without_file_extraction(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java, Spring Boot",
            "salary_text": "",
            "location": "Dallas, TX",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        weak_resume = Resume(1, "missing_skills.docx", "")
        strong_resume = Resume(2, "strong_skills.docx", "Java, Spring Boot, Microservices")

        selection = service.select_best_resume_match(
            subject="",
            body="Need Java and Spring Boot.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[weak_resume, strong_resume],
            fallback_resume=weak_resume,
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "strong_skills.docx")
        weak_score, weak_summary = service._intent_weighted_keyword_score(
            parsed=parsed,
            user_settings=Settings(),
            resume=weak_resume,
        )
        self.assertLessEqual(weak_score, 0.18)
        self.assertIn("Resume skills unavailable", weak_summary)


if __name__ == "__main__":
    unittest.main()
