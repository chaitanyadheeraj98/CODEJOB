"""The prospective question: "what would it take to apply for <role>?"

The gap report answers a retrospective one - which of seven families the library keeps
failing. These tests pin the thing that answer cannot do: assemble a cohort for a role
the taxonomy has no family for, rank the vocabulary that role actually demands, and say
how far each resume is from it. The motivating case is security work, which the
classifier scatters across six families and therefore ranks into invisibility.
"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RecruiterEmail, ResumeAsset
from app.schemas import RoleTargetResponse
from app.services import role_target_service

SECURITY_SKILLS = 'API Security, IAM, SailPoint, SOC 2, Penetration Testing, Splunk'
JAVA_SKILLS = 'Java, Spring Boot, Hibernate, React, Oracle, Microservices'


class RoleTargetTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(Base.metadata.drop_all, self.engine)

    @staticmethod
    def _jd(db: Session, *, count: int, role: str, skills: str, role_family: str = 'general') -> None:
        for index in range(count):
            db.add(
                RecruiterEmail(
                    owner_id='owner',
                    sender='rec@example.com',
                    subject=f'{role} {index}',
                    body='jd',
                    role=role,
                    skills_text=skills,
                    role_family=role_family,
                )
            )
        db.commit()

    @staticmethod
    def _resume(db: Session, *, label: str, skills: str, enabled: bool = True) -> None:
        db.add(
            ResumeAsset(
                owner_id='owner',
                file_path=f'/tmp/{label}.pdf',
                file_name=f'{label}.pdf',
                sha256=label,
                variant_label=label,
                skills_text=skills,
                is_enabled=enabled,
            )
        )
        db.commit()

    def _analyse(self, db: Session, role: str) -> dict:
        return role_target_service.analyse_role_target(db, owner_id='owner', target_role=role)


def test_skill_tokens_keeps_a_real_bracket_and_drops_a_severed_one():
    """The comma split cuts "Lightning Web Components (LWC)" in half often enough that
    "WSDL)" reaches the build list. Stripping brackets outright fixes that and mangles
    the intact one, so only the unbalanced bracket goes.
    """
    tokens = role_target_service._skill_tokens('Lightning Web Components (LWC), WSDL), API Security')
    assert tokens == ['Lightning Web Components (LWC)', 'WSDL', 'API Security']


def test_skill_tokens_drops_what_is_left_when_the_split_lands_inside_a_version():
    """No letter, no skill - "Java 17, 8" should not put "8" on anyone's resume."""
    assert role_target_service._skill_tokens('Java 17, 8, ---, SOC 2') == ['Java 17', 'SOC 2']


def test_a_short_acronym_survives_an_unrelated_longer_one():
    """The fragment rule is about a phrase and its words. Applied to raw characters it
    deletes IAM from every JD that also says CIAM, which is how a headline security
    skill goes missing from a security build list entirely.
    """
    tokens = role_target_service._skill_tokens('IAM, CIAM, GitHub Actions, GitHub, actions')
    assert set(tokens) == {'IAM', 'CIAM', 'GitHub Actions'}


class CohortAssemblyTests(RoleTargetTestCase):
    def test_the_cohort_crosses_the_families_the_classifier_split_the_role_across(self):
        """The whole reason this service exists. These postings are the same work; the
        classifier put them in three different families, so no family aggregate can ever
        show them together.
        """
        with Session(self.engine) as db:
            self._jd(db, count=10, role='IT Security Auditor', skills=SECURITY_SKILLS, role_family='java_fullstack')
            self._jd(db, count=10, role='Cloud Security Architect', skills=SECURITY_SKILLS, role_family='devops_cloud')
            self._jd(db, count=10, role='IAM Security Engineer', skills=SECURITY_SKILLS, role_family='java_backend')
            self._jd(db, count=30, role='Java Full Stack Developer', skills=JAVA_SKILLS, role_family='java_fullstack')
            report = self._analyse(db, 'IT Security Auditor')

        self.assertEqual(report['cohort_size'], 30)
        self.assertEqual(report['evidence_tier'], 'corpus')

    def test_a_differently_titled_posting_is_admitted_on_its_skills(self):
        """"BISO" shares no word with "IT Security Auditor". It is the same job, and the
        skills recall pass is the only thing that can know that.
        """
        with Session(self.engine) as db:
            self._jd(db, count=6, role='IT Security Auditor', skills=SECURITY_SKILLS)
            self._jd(db, count=4, role='BISO', skills=SECURITY_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        reasons = {sample['match_reason'] for sample in report['sample_jds']}
        self.assertEqual(report['cohort_size'], 10)
        self.assertIn('title', reasons)

    def test_a_java_posting_is_not_swept_into_a_security_cohort(self):
        """Recall has to stop somewhere. Sharing "Oracle" is not being the same role."""
        with Session(self.engine) as db:
            self._jd(db, count=6, role='IT Security Auditor', skills=SECURITY_SKILLS)
            self._jd(db, count=40, role='Java Backend Developer', skills=JAVA_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        self.assertEqual(report['cohort_size'], 6)

    def test_a_generic_word_in_the_title_is_not_a_match(self):
        """Every third posting says "Developer". Scoring on it makes every role match
        every other one, which is the failure mode this guards.
        """
        with Session(self.engine) as db:
            self._jd(db, count=8, role='Java Full Stack Developer', skills=JAVA_SKILLS)
            report = self._analyse(db, 'Salesforce Developer')

        self.assertEqual(report['cohort_size'], 0)
        self.assertEqual(report['evidence_tier'], 'none')


class DemandedVocabularyTests(RoleTargetTestCase):
    def test_the_roles_own_skills_outrank_the_java_noise_that_buried_them(self):
        """The direct inversion of the live finding: inside devops_cloud, IAM ranks last
        at concentration 0.21 because security is smeared across six families. Inside a
        cohort built around the role, the same skill is concentrated and ranks first.
        """
        with Session(self.engine) as db:
            self._jd(
                db,
                count=8,
                role='IT Security Auditor',
                skills=f'{SECURITY_SKILLS}, Java, Oracle',
            )
            self._jd(db, count=60, role='Java Full Stack Developer', skills=JAVA_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        ranked = [item['skill'] for item in report['demanded_skills']]
        self.assertIn('IAM', ranked)
        self.assertIn('API Security', ranked)
        self.assertLess(
            ranked.index('IAM'),
            ranked.index('Java'),
            'a skill this role owns has to outrank one every other role also demands',
        )

    def test_a_skill_demanded_everywhere_is_reported_as_unconcentrated(self):
        with Session(self.engine) as db:
            self._jd(db, count=8, role='IT Security Auditor', skills=f'{SECURITY_SKILLS}, Java')
            self._jd(db, count=60, role='Java Full Stack Developer', skills=JAVA_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        by_skill = {item['skill']: item for item in report['demanded_skills']}
        self.assertLess(by_skill['Java']['concentration'], 0.2)
        self.assertEqual(by_skill['IAM']['concentration'], 1.0)

    def test_an_unknown_role_returns_no_evidence_rather_than_a_fabricated_list(self):
        """The honest answer to a role the corpus has never seen is that it has never
        seen it. Falling back to the library's general strengths here would read as
        advice about the target role while being about something else.
        """
        with Session(self.engine) as db:
            self._jd(db, count=40, role='Java Full Stack Developer', skills=JAVA_SKILLS)
            self._resume(db, label='Java Full Stack', skills=JAVA_SKILLS)
            report = self._analyse(db, 'Veterinary Radiographer')

        self.assertEqual(report['evidence_tier'], 'none')
        self.assertEqual(report['cohort_size'], 0)
        self.assertEqual(report['demanded_skills'], [])
        self.assertEqual(report['variants'], [])
        self.assertEqual(report['closest_variant_code'], '')

    def test_a_small_cohort_still_produces_a_build_list(self):
        """The family floor of four mentions is a share of hundreds of JDs. Applied to a
        cohort of five it would suppress every skill in it and report nothing.
        """
        with Session(self.engine) as db:
            self._jd(db, count=5, role='IT Security Auditor', skills=SECURITY_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        self.assertEqual(report['evidence_tier'], 'thin')
        self.assertTrue(report['demanded_skills'])


class VariantDistanceTests(RoleTargetTestCase):
    def test_the_closest_variant_is_the_one_that_covers_most_of_the_demand(self):
        with Session(self.engine) as db:
            self._jd(db, count=8, role='IT Security Auditor', skills=SECURITY_SKILLS)
            self._resume(db, label='Java Full Stack', skills=JAVA_SKILLS)
            self._resume(db, label='Security Engineer', skills=f'{SECURITY_SKILLS}, Java')
            report = self._analyse(db, 'IT Security Auditor')

        self.assertEqual(report['closest_variant_label'], 'Security Engineer')
        self.assertGreater(report['variants'][0]['coverage'], report['variants'][1]['coverage'])

    def test_a_disabled_variant_is_not_offered_as_the_answer(self):
        with Session(self.engine) as db:
            self._jd(db, count=8, role='IT Security Auditor', skills=SECURITY_SKILLS)
            self._resume(db, label='Java Full Stack', skills=JAVA_SKILLS)
            self._resume(db, label='Security Engineer', skills=SECURITY_SKILLS, enabled=False)
            report = self._analyse(db, 'IT Security Auditor')

        self.assertEqual([variant['variant_label'] for variant in report['variants']], ['Java Full Stack'])

    def test_a_library_aimed_at_another_role_is_told_it_needs_a_new_resume(self):
        """The user's actual library: every variant is Java full stack. The useful answer
        is not a percentage, it is "this is not an edit".
        """
        with Session(self.engine) as db:
            self._jd(db, count=8, role='IT Security Auditor', skills=SECURITY_SKILLS)
            self._resume(db, label='Java Full Stack', skills=JAVA_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        self.assertEqual(report['verdict_tone'], 'wrong')
        self.assertIn('new resume', report['verdict'])
        self.assertFalse(any(item['covered_by_closest'] for item in report['demanded_skills']))

    def test_each_demanded_skill_says_whether_the_closest_resume_already_has_it(self):
        with Session(self.engine) as db:
            self._jd(db, count=8, role='IT Security Auditor', skills=f'{SECURITY_SKILLS}, Java')
            self._resume(db, label='Security Engineer', skills='IAM, SailPoint, Java')
            report = self._analyse(db, 'IT Security Auditor')

        covered = {item['skill'] for item in report['demanded_skills'] if item['covered_by_closest']}
        missing = {item['skill'] for item in report['demanded_skills'] if not item['covered_by_closest']}
        self.assertIn('IAM', covered)
        self.assertIn('API Security', missing)


class PayloadContractTests(RoleTargetTestCase):
    def test_the_response_model_preserves_every_key_the_service_emits(self):
        """Pydantic drops undeclared keys without complaining - that is how the gap
        report lost median_confidence. A round trip is the only thing that catches it.
        """
        with Session(self.engine) as db:
            self._jd(db, count=8, role='IT Security Auditor', skills=SECURITY_SKILLS)
            self._resume(db, label='Java Full Stack', skills=JAVA_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        round_tripped = RoleTargetResponse.model_validate(report).model_dump()
        self.assertEqual(set(round_tripped), set(report))
        self.assertEqual(set(round_tripped['demanded_skills'][0]), set(report['demanded_skills'][0]))
        self.assertEqual(set(round_tripped['variants'][0]), set(report['variants'][0]))
        self.assertEqual(set(round_tripped['sample_jds'][0]), set(report['sample_jds'][0]))

    def test_the_narrative_is_absent_unless_the_model_is_switched_on(self):
        """The deterministic payload is the product. The paragraph is decoration, and
        with the flag off nothing here should reach for a local model at all.
        """
        with Session(self.engine) as db:
            self._jd(db, count=8, role='IT Security Auditor', skills=SECURITY_SKILLS)
            self._resume(db, label='Java Full Stack', skills=JAVA_SKILLS)
            report = self._analyse(db, 'IT Security Auditor')

        self.assertIsNone(report['narrative'])

    def test_an_empty_role_is_answered_rather_than_raised(self):
        with Session(self.engine) as db:
            report = self._analyse(db, '   ')

        self.assertEqual(report['evidence_tier'], 'none')
        RoleTargetResponse.model_validate(report)
