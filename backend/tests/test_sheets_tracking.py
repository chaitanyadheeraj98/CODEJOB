import unittest

from app.gmail_client import build_tracking_sheet_row


class SheetsTrackingRowTests(unittest.TestCase):
    def test_build_tracking_sheet_row_has_expected_shape(self) -> None:
        row = build_tracking_sheet_row(
            role="Java Developer",
            sender="Sandesh Dhande <sandesh@horizonsoftech.net>",
            subject="Java Developer role",
            body="Client: YVSTECH\nCall me at +1 940-629-6920",
            to_email="jobs@yvstech.com",
            cc_email="sandesh@horizonsoftech.net",
        )
        self.assertEqual(len(row), 10)
        self.assertEqual(row[1], "Java Developer")
        self.assertEqual(row[4], "jobs@yvstech.com")
        self.assertEqual(row[8], "sandesh@horizonsoftech.net")

    def test_build_tracking_sheet_row_falls_back_to_subject_when_role_unknown(self) -> None:
        row = build_tracking_sheet_row(
            role="Unknown Role",
            sender="Recruiter <rec@example.com>",
            subject="Sr Full Stack Java Developer",
            body="",
            to_email="rec@example.com",
            cc_email=None,
        )
        self.assertEqual(row[1], "Sr Full Stack Java Developer")


if __name__ == "__main__":
    unittest.main()

