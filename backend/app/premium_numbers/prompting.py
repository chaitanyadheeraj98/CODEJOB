from __future__ import annotations


def build_premium_numbers_prompts(
    email_content: str,
    employer_domains: set[str] | None = None,
) -> tuple[str, str]:
    system_prompt = (
        "You are an intelligent email information extraction system. "
        "Extract phone numbers and attribution details exactly as instructed. "
        "Return strict JSON only."
    )
    user_prompt = f"""
Your task is to analyze recruiter/job-related emails and extract ALL phone numbers mentioned in the email.

For EACH phone number found, identify:

1. Role: recruiter, employer, or unknown
2. Phone Number
3. Contact Name
4. Contact Email
5. Company Name
6. Role/Designation (if available)
7. LinkedIn Profile URL (if mentioned)
8. Why the number is present
9. Confidence Level (High / Medium / Low)
10. Source section: body, signature, or unknown
11. Block ID shared by every phone and identity field in the same signature block
12. Verbatim evidence text containing the phone number
13. Line type: "phone" (a number someone can be called on - direct/desk/cell/switchboard), "fax", or "other" (explicitly labeled as something other than a callable line, e.g. "Toll Free:", "Main:", "Support:")

Rules:
- Carefully distinguish between main submission contacts, recruiter signatures, and office numbers.
- Use surrounding context to determine ownership.
- Each contact object's phone, email, and name must come from the SAME signature block or the same person's mention - never combine attributes from two different people (e.g. do not pair one person's phone with another person's email). Python verifies this attribution after extraction.
- Do NOT guess unknown names.
- If ownership is unclear, mark it as "Unknown".
- Return results in structured JSON format only.
- Extract multiple phone numbers if present.
- Ignore invalid or incomplete numbers.
- Support international phone number formats.
- Known employer domains are employer contacts, not recruiter contacts.
- If the phone number has an extension (e.g. "Ext: 2162", "x2162", "extension 2162"), include it verbatim in "phone_number" (e.g. "+1 (609) 897-9670 ext 2162") - never drop it. Different people at the same company often share one switchboard number with different extensions, and the extension is what tells them apart.
- If a LinkedIn profile is mentioned for this person (a linkedin.com/in/... URL, whether plain text or the target of a hyperlink like "linkedin.com/in/jane-doe-recruiter"), copy it verbatim into "linkedin_url". Include the domain even if the email only shows the path (e.g. "linkedin.com/in/jane-doe"). Leave "linkedin_url" as "" only when no LinkedIn profile is mentioned for this person - never invent one.
- A number explicitly labeled "Fax:" or "Facsimile:" is a fax line, not a phone line - still extract it (as its own contact entry, same block_id as the rest of that person's signature), but set "line_type" to "fax". Set "line_type" to "other" for a number labeled as something else that isn't a way to reach the person directly (e.g. "Toll Free:", "Main:", "Support:"). Every other number - unlabeled, or labeled "Ph:", "Tel:", "Phone:", "Cell:", "Direct:", "Office:" - is "line_type": "phone". When unsure, default to "phone".

Known employer domains: {', '.join(sorted(employer_domains or set())) or 'none'}

Expected JSON format:
{{
  "contacts": [
    {{
      "role": "recruiter",
      "phone_number": "+1 512 271 9173",
      "name": "RAM",
      "email": "ram@example.com",
      "company": "TekWings",
      "designation": "Unknown",
      "linkedin_url": "",
      "purpose": "Resume submission contact",
      "confidence": "High",
      "source_section": "signature",
      "block_id": "signature-1",
      "evidence_text": "RAM | ram@example.com | +1 512 271 9173",
      "line_type": "phone"
    }},
    {{
      "role": "recruiter",
      "phone_number": "+1 (609) 897-9670 ext 2162",
      "name": "Priya",
      "email": "priya@example.com",
      "company": "TekWings",
      "designation": "Sr. Technical Recruiter",
      "linkedin_url": "linkedin.com/in/priya-recruiter",
      "purpose": "Recruiter direct number",
      "confidence": "High",
      "source_section": "signature",
      "block_id": "signature-2",
      "evidence_text": "Priya | priya@example.com | Cell: +1 (609) 897-9670 Ext: 2162",
      "line_type": "phone"
    }},
    {{
      "role": "employer",
      "phone_number": "+1 248-688-9655",
      "name": "Mohan Edara",
      "email": "mohan@horizonsoftech.net",
      "company": "Horizon Softech Inc",
      "designation": "Unknown",
      "linkedin_url": "",
      "purpose": "Company fax line",
      "confidence": "High",
      "source_section": "signature",
      "block_id": "signature-3",
      "evidence_text": "Mohan Edara | Horizon Softech Inc | Ph: 248-722-2694 | Fax: 248-688-9655",
      "line_type": "fax"
    }}
  ]
}}

Now analyze the following email:

{email_content}
""".strip()
    return system_prompt, user_prompt
