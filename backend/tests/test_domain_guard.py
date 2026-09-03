import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import UserSettings
from app.premium_numbers.domain_guard import is_derivable_company_domain


class IsDerivableCompanyDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_ordinary_business_domain_is_derivable(self) -> None:
        with Session(self.engine) as db:
            self.assertTrue(is_derivable_company_domain(db, "default-owner", "ram@tekwings.com"))

    def test_personal_email_provider_is_not_derivable(self) -> None:
        with Session(self.engine) as db:
            self.assertFalse(is_derivable_company_domain(db, "default-owner", "ram@gmail.com"))

    def test_default_employer_domain_is_not_derivable(self) -> None:
        with Session(self.engine) as db:
            self.assertFalse(is_derivable_company_domain(db, "default-owner", "hr@horizonsofttech.net"))

    def test_configured_employer_domain_is_not_derivable(self) -> None:
        with Session(self.engine) as db:
            db.add(UserSettings(owner_id="default-owner", employer_domains="metrixit.com"))
            db.commit()
            self.assertFalse(is_derivable_company_domain(db, "default-owner", "amankumar@metrixit.com"))

    def test_blank_email_is_not_derivable(self) -> None:
        with Session(self.engine) as db:
            self.assertFalse(is_derivable_company_domain(db, "default-owner", ""))


if __name__ == "__main__":
    unittest.main()
