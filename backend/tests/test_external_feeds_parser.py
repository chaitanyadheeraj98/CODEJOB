import unittest

from app.external_feeds.parser import (
    extract_nvoids_clean_body,
    extract_nvoids_detail_title,
    parse_nvoids_detail,
    parse_external_post,
    parse_job_detail_contacts,
    parse_listing_rows,
)


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

    def test_parse_listing_rows_rejects_fake_search_artifact_titles(self) -> None:
        html = """
        <table>
          <tr><td><a href='job_details.jsp?id=1'>A collection of search strings</a></td><td>Texas</td><td>11:00 PM 07-May-26</td></tr>
          <tr><td><a href='job_details.jsp?id=2'>Senior Java Developer</a></td><td>Dallas, Texas, USA</td><td>10:00 PM 07-May-26</td></tr>
        </table>
        """
        rows = parse_listing_rows(html, "https://www.nvoids.com/search_sph.jsp")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Senior Java Developer")

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

    def test_parse_job_detail_contacts_prefers_mailto_and_role_table_scope(self) -> None:
        html = """
        <html><body>
        <a>Home</a>
        <table>
          <tr><td>Application Architect - AWS Cloud Migration at Dallas, Texas, USA</td></tr>
          <tr><td>Email: <a href='mailto:nupur.kumari@tanishasystems.com'>nupur.kumari@tanishasystems.com</a></td></tr>
          <tr><td>From</td></tr>
          <tr><td>Nupur Kumari</td></tr>
          <tr><td>Phone: +1 (214) 555-1212</td></tr>
          <tr><td>Job Title: Application Architect - AWS Cloud Migration</td></tr>
          <tr><td>Location: Dallas TX (ONSITE)</td></tr>
          <tr><td>AWS Outposts and Java 17 migration support.</td></tr>
          <tr><td>View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        <div>job_kill Pages not loading Time Taken footer Location: Dallas, Texas</div>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "nupur.kumari@tanishasystems.com")
        self.assertEqual(name, "Nupur Kumari")
        self.assertIn("214", phone)

    def test_parse_nvoids_detail_extracts_structured_rows(self) -> None:
        html = """
        <html><body>
        <a href='index.jsp'>Home</a>
        <table border="1">
          <tr><td>Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite at Irving, Texas, USA</td></tr>
          <tr><td>Email: <a href='mailto:tanuja@digitaldhara.com'>tanuja@digitaldhara.com</a></td></tr>
          <tr><td>Job Title:</td></tr>
          <tr><td>GCP AI Engineer</td></tr>
          <tr><td>Location: Irving, TX, or Charlotte NC - Onsite</td></tr>
          <tr><td>Experience with Vertex AI, GKE, Python, and GenAI workflows.</td></tr>
          <tr><td>tanuja@digitaldhara.com | View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        <div>job_kill Time Taken footer Location: Dallas, Texas</div>
        </body></html>
        """
        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")
        self.assertEqual(detail.listing_subject, "Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite at Irving, Texas, USA")
        self.assertEqual(detail.recruiter_email, "tanuja@digitaldhara.com")
        self.assertEqual(detail.repeated_email, "tanuja@digitaldhara.com")
        self.assertEqual(detail.posted_text, "04:49 AM 17-Jun-26")
        self.assertEqual(detail.role, "GCP AI Engineer")
        self.assertEqual(detail.location, "Irving, TX, or Charlotte NC - Onsite")
        self.assertIn("Vertex AI, GKE, Python, and GenAI workflows.", detail.body)
        self.assertNotIn("job_kill", detail.body)

    def test_extract_nvoids_detail_title_prefers_first_meaningful_row_after_home(self) -> None:
        html = """
        <html><body>
        <a>Home</a>
        <table>
          <tr><td>Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA</td></tr>
          <tr><td>Email: saurabhampstek@gmail.com</td></tr>
          <tr><td>https://jobs.nvoids.com/job_details.jsp?id=3445247&uid=abc</td></tr>
          <tr><td>Job Role : Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS)</td></tr>
        </table>
        </body></html>
        """
        title = extract_nvoids_detail_title(html, "Fallback Title")
        self.assertEqual(
            title,
            "Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA",
        )

    def test_extract_nvoids_clean_body_removes_html_noise_and_preserves_readable_text(self) -> None:
        html = """
        <html><body>
        <a>Home</a>
        <table>
          <tr><td>Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA</td></tr>
          <tr><td>Email: saurabhampstek@gmail.com</td></tr>
          <tr><td>http://bit.ly/4ey8w48</td></tr>
          <tr><td>Hi,</td></tr>
          <tr><td>Job description</td></tr>
          <tr><td>Backend Development Design, develop, and maintain scalable backend services.</td></tr>
          <tr><td>Thanks and Regards</td></tr>
          <tr><td>data-cfemail protected</td></tr>
        </table>
        </body></html>
        """
        body = extract_nvoids_clean_body(
            html,
            "Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA",
            "Charlotte, North Carolina, USA",
        )
        self.assertIn("Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS)", body)
        self.assertIn("Backend Development Design, develop, and maintain scalable backend services.", body)
        self.assertNotIn("data-cfemail", body)
        self.assertNotIn("http://bit.ly", body)
        self.assertNotIn("Thanks and Regards", body)
        self.assertNotIn("Charlotte, North Carolina, USA\nCharlotte, North Carolina, USA", body)

    def test_parse_external_post_uses_clean_nvoids_title_and_plain_text_body(self) -> None:
        html = """
        <html><body>
        <a>Home</a>
        <table>
          <tr><td>Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA</td></tr>
          <tr><td>Email: recruiter@example.com</td></tr>
          <tr><td>Job Title: Full Stack Developer</td></tr>
          <tr><td>Location: Charlotte, NC, Islin, NJ & Irving, TX</td></tr>
          <tr><td>Backend Development Design, develop, and maintain scalable backend services.</td></tr>
          <tr><td>Thanks and Regards</td></tr>
          <tr><td>11:00 PM 07-May-26</td></tr>
        </table>
        </body></html>
        """
        post = parse_external_post(
            source_type="nvoids",
            source_url="https://nvoids.com/job_details.jsp?id=3445247&uid=abc",
            title="Dirty fallback title",
            location="Charlotte, North Carolina, USA",
            posted_text="11:00 PM 07-May-26",
            raw_body=html,
            raw_html=html,
        )
        self.assertEqual(post.role, "Full Stack Developer")
        self.assertEqual(post.location, "Charlotte, NC, Islin, NJ & Irving, TX")
        self.assertEqual(post.recruiter_email, "recruiter@example.com")
        self.assertNotIn("<tr>", post.raw_body)
        self.assertNotIn("Thanks and Regards", post.raw_body)
        self.assertIn("Backend Development Design, develop, and maintain scalable backend services.", post.raw_body)


if __name__ == "__main__":
    unittest.main()
