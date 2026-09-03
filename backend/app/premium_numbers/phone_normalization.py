from __future__ import annotations

import re


EXTENSION_RE = re.compile(r"(?:ext\.?|x|extension|\*)\s*[:\-]?\s*(\d{1,6})\b", re.IGNORECASE)


def _extract_extension(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    match = EXTENSION_RE.search(text)
    if not match:
        return text, ""
    extension = match.group(1)
    cleaned = (text[: match.start()] + " " + text[match.end() :]).strip()
    return cleaned, extension


def format_phone(raw: str) -> tuple[str, str, str]:
    base_text, extension = _extract_extension(raw)
    # Keep only digits for canonical matching while preserving ext separately.
    digits = re.sub(r"\D", "", base_text)
    if len(digits) == 11 and digits.startswith("1"):
        canonical = digits
    elif len(digits) == 10:
        canonical = f"1{digits}"
    elif (base_text.startswith("+") and 8 <= len(digits) <= 15) or 11 <= len(digits) <= 15:
        canonical = f"+{digits}"
        display = canonical
        if extension:
            display = f"{display} ext {extension}"
        return canonical, display, extension
    else:
        return "", "", ""

    national = canonical[1:]
    display = f"({national[:3]}) {national[3:6]}-{national[6:]}"
    if extension:
        display = f"{display} ext {extension}"
    return canonical, display, extension


def canonicalize_phone(raw: str) -> str:
    canonical, _display, _ext = format_phone(raw)
    return canonical


def best_display_phone(raw: str, fallback: str = "") -> str:
    canonical, display, _ext = format_phone(raw)
    if canonical and display:
        return display
    return fallback.strip()
