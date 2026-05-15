from __future__ import annotations


def build_premium_numbers_prompts(email_content: str) -> tuple[str, str]:
    system_prompt = (
        "You are an intelligent email information extraction system. "
        "Extract phone numbers and attribution details exactly as instructed. "
        "Return strict JSON only."
    )
    user_prompt = f"""
Your task is to analyze recruiter/job-related emails and extract ALL phone numbers mentioned in the email.

For EACH phone number found, identify:

1. Phone Number
2. Owner Name
3. Company Name
4. Role/Designation (if available)
5. Why the number is present
6. Confidence Level (High / Medium / Low)

Rules:
- Carefully distinguish between main submission contacts, recruiter signatures, and office numbers.
- Use surrounding context to determine ownership.
- Do NOT guess unknown names.
- If ownership is unclear, mark it as "Unknown".
- Return results in structured JSON format only.
- Extract multiple phone numbers if present.
- Ignore invalid or incomplete numbers.
- Support international phone number formats.

Expected JSON format:
{{
  "phone_numbers": [
    {{
      "phone_number": "+1 512 271 9173",
      "owner_name": "RAM",
      "company": "TekWings",
      "designation": "Unknown",
      "purpose": "Resume submission contact",
      "confidence": "High"
    }}
  ]
}}

Now analyze the following email:

{email_content}
""".strip()
    return system_prompt, user_prompt
