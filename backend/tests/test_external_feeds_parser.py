import unittest

from app.external_feeds.parser import parse_external_post, parse_job_detail_contacts, parse_listing_rows


class ExternalFeedsParserTests(unittest.TestCase):
    def test_parse_listing_rows_extracts_title_location_time(self) -> None:
        html = """
        <table>
          <tr><td><a href='job1.jsp?id=1'>Senior Python Developer</a></td><td>Dallas, Texas, USA</td><td>11:00 PM 07-May-26</td></tr>
        </table>
        """
        rows = parse_listing_rows(html, "https://www.nvoids.com/index.jsp")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Senior Python Developer")
        self.assertEqual(rows[0].location, "Dallas, Texas, USA")
        self.assertIn("job1.jsp?id=1", rows[0].href)

    def test_parse_external_post_extracts_contact_hints(self) -> None:
        post = parse_external_post(
            source_type="nvoids",
            source_url="https://www.nvoids.com/job1.jsp?id=1",
            title="Java Developer",
            location="Remote, USA",
            posted_text="11:00 PM 07-May-26",
            raw_body="Contact recruiter@example.com and call +1 214-555-1212. Visa: H1B",
            raw_html="<html></html>",
        )
        self.assertEqual(post.recruiter_email, "recruiter@example.com")
        self.assertIn("214", post.recruiter_phone)
        self.assertEqual(post.work_mode, "Remote")
        self.assertEqual(post.visa_hints, "Mentioned")
        self.assertEqual(post.external_post_id, "nvoids:1")

    def test_parse_external_post_uses_stable_id_when_uid_changes(self) -> None:
        a = parse_external_post(
            source_type="nvoids",
            source_url="https://nvoids.com/job_details.jsp?id=3384005&uid=first",
            title="Java Developer",
            location="Texas, USA",
            posted_text="11:00 PM 07-May-26",
            raw_body="Contact a@example.com",
            raw_html="<html></html>",
        )
        b = parse_external_post(
            source_type="nvoids",
            source_url="https://nvoids.com/job_details.jsp?id=3384005&uid=second",
            title="Java Developer",
            location="Texas, USA",
            posted_text="11:00 PM 07-May-26",
            raw_body="Contact b@example.com",
            raw_html="<html></html>",
        )
        self.assertEqual(a.external_post_id, "nvoids:3384005")
        self.assertEqual(b.external_post_id, "nvoids:3384005")

    def test_parse_job_detail_contacts_ignores_noise_phone_without_contact_label(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>Email: nitin.tehriya@tekinspirations.com</td></tr>
          <tr><td>From: Nitin Tehriya</td></tr>
          <tr><td>Hiring Full Stack Developer role in Lansing, Michigan.</td></tr>
        </table>
        <div>Pages not loading contact admin. Time Taken: 0. 4849540944</div>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "nitin.tehriya@tekinspirations.com")
        self.assertEqual(name, "Nitin Tehriya")
        self.assertEqual(phone, "")

    def test_parse_job_detail_contacts_extracts_labeled_phone(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>Email: recruiter@example.com</td></tr>
          <tr><td>From: Jane Recruiter</td></tr>
          <tr><td>Contact: +1 (214) 555-1212</td></tr>
        </table>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "recruiter@example.com")
        self.assertEqual(name, "Jane Recruiter")
        self.assertIn("214", phone)


if __name__ == "__main__":
    unittest.main()
