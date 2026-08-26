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
      "evidence_text": "RAM | ram@example.com | +1 512 271 9173"
    }}
  ]
}}

Now analyze the following email:

{email_content}
""".strip()
    return system_prompt, user_prompt
