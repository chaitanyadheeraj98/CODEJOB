from __future__ import annotations

import re


def canonicalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", (raw or "").strip())
    if len(digits) < 10:
        return ""
    if len(digits) == 10:
        return f"1{digits}"
    return digits
