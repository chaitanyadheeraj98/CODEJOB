import os
import unittest

os.environ["DEBUG"] = "false"

from app.gmail_client import _extract_email_address, _extract_name_from_sender


class GmailSenderParsingTests(unittest.TestCase):
    def test_current_sender_header_behavior_is_preserved(self) -> None:
        cases = (
            ("user@example.com", "", "user@example.com"),
            ('"Quoted Name" <user@example.com>', "Quoted Name", "user@example.com"),
            ("Display Name Only", "", "Display Name Only"),
            ("Name <user@example.com", "Name", "Name <user@example.com"),
            ("Name <>", "Name", "Name <>"),
        )
        for header, expected_name, expected_address in cases:
            with self.subTest(header=header):
                self.assertEqual(_extract_name_from_sender(header), expected_name)
                self.assertEqual(_extract_email_address(header), expected_address)


if __name__ == "__main__":
    unittest.main()
