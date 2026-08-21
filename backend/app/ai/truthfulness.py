from __future__ import annotations

import re


def max_claimed_years_from_resume(resume_text: str) -> int | None:
    matches = re.findall(r"\b(\d{1,2})\s*\+?\s*years?\b", resume_text or "", flags=re.IGNORECASE)
    values = [int(value) for value in matches if value.isdigit()]
    if not values:
        return None
    return max(values)


def enforce_truthfulness(text: str, resume_text: str) -> str:
    cleaned = text
    max_years = max_claimed_years_from_resume(resume_text)
    if max_years is not None:
        def _cap_years(match: re.Match[str]) -> str:
            claimed = int(match.group(1))
            if claimed > max_years:
                return f"{max_years}+ years"
            suffix = "+" if "+" in match.group(0) else ""
            return f"{claimed}{suffix} years"

        cleaned = re.sub(r"\b(\d{1,2})\s*\+?\s*years\b", _cap_years, cleaned, flags=re.IGNORECASE)

    resume_lower = (resume_text or "").lower()
    if "aop" not in resume_lower and "aspect oriented" not in resume_lower:
        cleaned = re.sub(
            r"\bSpring\s+Aspect\s+Oriented\s+Programming\b",
            "Spring-based transaction management and security implementations",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\bSpring\s+AOP\b",
            "Spring-based transaction management and security implementations",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned
