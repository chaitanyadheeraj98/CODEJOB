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
          <tr><td>Hiring Full Stack Developer at Lansing, Michigan</td></tr>
          <tr><td>Email: nitin.tehriya@tekinspirations.com</td></tr>
          <tr><td>Role: Full Stack Developer<br>Skills: Java, Spring Boot</td></tr>
          <tr><td>nitin.tehriya@tekinspirations.com | View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        <div>Pages not loading contact admin. Time Taken: 0. 4849540944</div>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "nitin.tehriya@tekinspirations.com")
        self.assertEqual(phone, "")
        self.assertEqual(name, "")

    def test_parse_job_detail_contacts_extracts_phone_and_name_from_row_3_only(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>Senior Java Developer at Dallas, Texas, USA</td></tr>
          <tr><td>Email: recruiter@example.com</td></tr>
          <tr><td>Role: Senior Java Developer<br>From: Jane Recruiter<br>Contact: +1 (214) 555-1212</td></tr>
          <tr><td>reply@example.com | View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "recruiter@example.com")
        self.assertIn("214", phone)
        self.assertEqual(name, "Jane Recruiter")

    def test_parse_job_detail_contacts_prefers_mailto_on_row_2(self) -> None:
        html = """
        <html><body>
        <a>Home</a>
        <table>
          <tr><td>Application Architect - AWS Cloud Migration at Dallas, Texas, USA</td></tr>
          <tr><td>Email: <a href='mailto:nupur.kumari@tanishasystems.com'>nupur.kumari@tanishasystems.com</a></td></tr>
          <tr><td>Nupur Kumari<br>AWS Outposts and Java 17 migration support.<br>Phone: +1 (214) 555-1212</td></tr>
          <tr><td>nupur.kumari@tanishasystems.com | View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        <div>job_kill Pages not loading Time Taken footer Location: Dallas, Texas</div>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "nupur.kumari@tanishasystems.com")
        self.assertEqual(name, "Nupur Kumari")
        self.assertIn("214", phone)

    def test_parse_job_detail_contacts_accepts_ph_no_variant_from_row_3(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>Java AWS Developer at Plano, Texas, USA</td></tr>
          <tr><td>Email: sharma.gopal@net2source.com</td></tr>
          <tr><td>Best Regards,<br>Gopal Sharma<br>Senior Talent Acquisition - USA<br>Email:<br>sharma.gopal@net2source.com<br>Ph no. (551) 303-0028</td></tr>
          <tr><td>sharma.gopal@net2source.com | View All</td></tr>
          <tr><td>02:27 AM 26-Jun-26</td></tr>
        </table>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "sharma.gopal@net2source.com")
        self.assertEqual(name, "Gopal Sharma")
        self.assertIn("551", phone)

    def test_parse_job_detail_contacts_accepts_phone_no_variant_from_row_3(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>Data Engineer at Remote, USA</td></tr>
          <tr><td>Email: recruiter@example.com</td></tr>
          <tr><td>Regards,<br>Jane Recruiter<br>Phone No: +1 551 303 0028<br>Snowflake, Kafka</td></tr>
          <tr><td>recruiter@example.com | View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "recruiter@example.com")
        self.assertEqual(name, "Jane Recruiter")
        self.assertIn("551", phone)

    def test_parse_job_detail_contacts_ignores_plain_numeric_jd_without_contact_context(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>Backend Engineer at Remote, USA</td></tr>
          <tr><td>Email: recruiter@example.com</td></tr>
          <tr><td>Need 5513030028 records processed daily with 2145567788 transactions and Java support.</td></tr>
          <tr><td>recruiter@example.com | View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        </body></html>
        """
        email, phone, name = parse_job_detail_contacts(html)
        self.assertEqual(email, "recruiter@example.com")
        self.assertEqual(phone, "")
        self.assertEqual(name, "")

    def test_parse_nvoids_detail_extracts_strict_five_rows(self) -> None:
        html = """
        <html><body>
        <a href='index.jsp'>Home</a>
        <table border="1">
          <tr><td>Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite at Irving, Texas, USA</td></tr>
          <tr><td>Email: <a href='mailto:tanuja@digitaldhara.com'>tanuja@digitaldhara.com</a></td></tr>
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
        self.assertEqual(detail.role, "Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite")
        self.assertEqual(detail.location, "Irving, Texas, USA")
        self.assertEqual(detail.jd_body, "Experience with Vertex AI, GKE, Python, and GenAI workflows.")
        self.assertEqual(detail.jd_body_source, "nvoids_detail_table_row_3")
        self.assertEqual(detail.body, "Experience with Vertex AI, GKE, Python, and GenAI workflows.")
        self.assertEqual(detail.recruiter_phone, "")
        self.assertEqual(detail.recruiter_name, "")
        self.assertNotIn("job_kill", detail.body)

    def test_parse_nvoids_detail_extracts_recruiter_identity_from_row_3_only(self) -> None:
        html = """
        <html><body>
        <table border="1">
          <tr><td>Kafka software Developer at Remote, Remote, USA</td></tr>
          <tr><td>Email: shivam.singh@jobvritta.com</td></tr>
          <tr><td>Hello Professional,<br>From: Shivam Singh<br>Contact: +1 240-657-1540<br>Java/Kafka software Developer - Dallas/onsite 5 days - CTH</td></tr>
          <tr><td>shivam.singh@jobvritta.com | View All</td></tr>
          <tr><td>11:00 PM 07-May-26</td></tr>
        </table>
        <div>Outside junk 9999999999</div>
        </body></html>
        """
        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")
        self.assertEqual(detail.recruiter_name, "Shivam Singh")
        self.assertIn("240", detail.recruiter_phone)

    def test_parse_nvoids_detail_extracts_signature_style_identity_from_row_3(self) -> None:
        html = """
        <html><body>
        <table border="1">
          <tr><td>Java AWS Developer at Plano, Texas, USA</td></tr>
          <tr><td>Email: sharma.gopal@net2source.com</td></tr>
          <tr><td>Best Regards,<br>Gopal Sharma<br>Senior Talent Acquisition - USA<br>Email:<br>sharma.gopal@net2source.com<br>Ph no. (551) 303-0028</td></tr>
          <tr><td>sharma.gopal@net2source.com | View All</td></tr>
          <tr><td>02:27 AM 26-Jun-26</td></tr>
        </table>
        </body></html>
        """
        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")
        self.assertEqual(detail.recruiter_name, "Gopal Sharma")
        self.assertIn("551", detail.recruiter_phone)

    def test_parse_nvoids_detail_uses_literal_third_row_for_full_jd_body(self) -> None:
        html = """
        <html><body>
        <table border="1">
          <tr><td>java Solution Architect :: Plano, TX :: Toyota at Plano, Texas, USA</td></tr>
          <tr><td>Email: prashanth@americantsystems.com</td></tr>
          <tr><td>
            http://bit.ly/4ey8w48<br>
            https://jobs.nvoids.com/job_details.jsp?id=3471874&uid=494b0d74577547d497857219800a595c<br><br>
            Hello Professional,<br><br>
            Role :: Solution Architect (java Solution Architect)<br>
            Client :: Toyota<br>
            Location : Plano, TX-5 days onsite ( Only Locals)<br>
            Rate :: 75/hr on C2 max<br>
            Must have skills.<br>
            Java, Spring Boot, NodeJS, AWS, Kafka<br>
            Responsibilities:<br>
            Lead design and scaling of Java, Spring Boot, NodeJS, and microservices applications.
          </td></tr>
          <tr><td>prashanth@americantsystems.com | View All</td></tr>
          <tr><td>04:49 AM 17-Jun-26</td></tr>
        </table>
        </body></html>
        """
        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")
        self.assertIn("Role :: Solution Architect", detail.jd_body)
        self.assertIn("Client :: Toyota", detail.jd_body)
        self.assertIn("Java, Spring Boot, NodeJS, AWS, Kafka", detail.jd_body)
        self.assertEqual(detail.jd_body_source, "nvoids_detail_table_row_3")

    def test_parse_nvoids_detail_returns_strict_fallback_when_rows_missing(self) -> None:
        html = """
        <html><body>
        <table border="1">
          <tr><td>Principal Software Engineer Java</td></tr>
          <tr><td>Email: recruiter@example.com</td></tr>
          <tr><td>Hi</td></tr>
        </table>
        </body></html>
        """
        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")
        self.assertEqual(detail.role, "role not fetched")
        self.assertEqual(detail.location, "")
        self.assertEqual(detail.recruiter_email, "")
        self.assertEqual(detail.jd_body, "")
        self.assertEqual(detail.jd_body_source, "")

    def test_parse_nvoids_detail_ignores_role_responsibilities_text_in_row_3(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>Kafka software Developer at Remote, Remote, USA</td></tr>
          <tr><td>Email: shivam.singh@jobvritta.com</td></tr>
          <tr><td>ROLE/RESPONSIBILITIES:<br>Java/Kafka software Developer - Dallas/onsite 5 days - CTH<br>MUST HAVE SKILLS:<br>Java<br>Kafka</td></tr>
          <tr><td>shivam.singh@jobvritta.com | View All</td></tr>
          <tr><td>11:00 PM 07-May-26</td></tr>
        </table>
        </body></html>
        """
        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")
        self.assertEqual(detail.role, "Kafka software Developer")
        self.assertEqual(detail.location, "Remote, Remote, USA")
        self.assertIn("ROLE/RESPONSIBILITIES", detail.jd_body)
        self.assertEqual(detail.recruiter_name, "")
        self.assertEqual(detail.recruiter_phone, "")

    def test_extract_nvoids_detail_title_uses_first_row_only(self) -> None:
        html = """
        <html><body>
        <a>Home</a>
        <table>
          <tr><td>Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA</td></tr>
          <tr><td>Email: saurabhampstek@gmail.com</td></tr>
          <tr><td>JD text</td></tr>
          <tr><td>saurabhampstek@gmail.com | View All</td></tr>
          <tr><td>11:00 PM 07-May-26</td></tr>
        </table>
        </body></html>
        """
        title = extract_nvoids_detail_title(html, "Fallback Title")
        self.assertEqual(title, "Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA")

    def test_extract_nvoids_detail_title_returns_fallback_for_empty_html(self) -> None:
        self.assertEqual(
            extract_nvoids_detail_title("", "Fallback Java Developer"),
            "Fallback Java Developer",
        )

    def test_parse_nvoids_detail_returns_safe_fallback_for_empty_html(self) -> None:
        detail = parse_nvoids_detail("", "Fallback Title", "Dallas, TX")
        self.assertEqual(detail.listing_subject, "Fallback Title")
        self.assertEqual(detail.role, "role not fetched")
        self.assertEqual(detail.location, "")
        self.assertEqual(detail.recruiter_email, "")
        self.assertEqual(detail.recruiter_phone, "")
        self.assertEqual(detail.body, "")

    def test_parse_job_detail_contacts_returns_empty_for_empty_html(self) -> None:
        self.assertEqual(parse_job_detail_contacts(""), ("", "", ""))

    def test_parse_nvoids_detail_malformed_html_does_not_recurse(self) -> None:
        html = """
        <html><body>
        <table>
          <tr><td>https://jobs.nvoids.com/job_details.jsp?id=1&uid=abc</td></tr>
          <tr><td>Email: recruiter@example.com</td></tr>
        </table>
        </body></html>
        """
        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")
        self.assertEqual(detail.listing_subject, "Fallback Title")
        self.assertEqual(detail.recruiter_email, "")
        self.assertEqual(detail.role, "role not fetched")

    def test_extract_nvoids_clean_body_uses_only_row_3(self) -> None:
        html = """
        <html><body>
        <a>Home</a>
        <table>
          <tr><td>Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA</td></tr>
          <tr><td>Email: saurabhampstek@gmail.com</td></tr>
          <tr><td>Backend Development Design, develop, and maintain scalable backend services.</td></tr>
          <tr><td>saurabhampstek@gmail.com | View All</td></tr>
          <tr><td>11:00 PM 07-May-26</td></tr>
        </table>
        <div>Thanks and Regards data-cfemail protected</div>
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
          <tr><td>Backend Development Design, develop, and maintain scalable backend services.</td></tr>
          <tr><td>recruiter@example.com | View All</td></tr>
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
        self.assertEqual(post.role, "Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX")
        self.assertEqual(post.location, "Charlotte, North Carolina, USA")
        self.assertEqual(post.recruiter_email, "recruiter@example.com")
        self.assertNotIn("<tr>", post.raw_body)
        self.assertIn("Backend Development Design, develop, and maintain scalable backend services.", post.raw_body)


if __name__ == "__main__":
    unittest.main()
