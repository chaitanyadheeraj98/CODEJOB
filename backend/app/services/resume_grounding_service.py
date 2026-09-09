from collections import Counter
from difflib import SequenceMatcher
import re

GROUNDING_REVIEW_THRESHOLD = 0.45
_NUMBER = re.compile(r"(?<![\w.])[$€£]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|[kKmMbB](?!\w)|ms\b|gb\b|tb\b|x\b)?", re.I)
_DATE = re.compile(r"\b(?:19|20)\d{2}(?:[/-]\d{1,2}(?:[/-]\d{1,2})?)?\b|\b\d{1,2}[/-](?:\d{4}|\d{1,2}(?:[/-]\d{2,4})?)\b")
_STOP = frozenset("a an and are as at be been by for from has have in into is it of on or that the their to was were with".split())
_ORGANISATION = re.compile(r"\b(?:[A-Z][\w&.-]*[ \t]+){1,4}(?:Corp(?:oration)?|Inc|LLC|Ltd|Limited|Company|Technologies|Solutions|Systems|University|Bank)\b")


def _numbers(text: str) -> dict[str, str]:
    dates = [match.span() for match in _DATE.finditer(text)]
    values = {}
    for match in _NUMBER.finditer(text):
        value = match.group().strip()
        if any(start <= match.start() and match.start() + len(match.group().rstrip()) <= end for start, end in dates) and re.fullmatch(r"\d+", value):
            continue
        values[re.sub(r"[\s,]", "", value).casefold()] = value
    return values


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[\w%$]+", text.casefold()) if token not in _STOP]


def _similarity(source: str, candidate: str) -> float:
    before, after = _tokens(source), _tokens(candidate)
    if Counter(before) == Counter(after):
        return 1.0
    return SequenceMatcher(None, before, after, autojunk=False).ratio()


def check_grounding(source: str, current: str, replacement: str) -> dict[str, object]:
    existing = _numbers(source)
    novel_numbers = [value for key, value in _numbers(replacement).items() if key not in existing]
    # ponytail: organization suffix heuristic; add entity extraction only if these cautions miss real edits.
    organisations = sorted({match.group().strip() for match in _ORGANISATION.finditer(replacement)
                            if match.group().strip().casefold() not in source.casefold()})
    similarity = _similarity(current, replacement)
    # The threshold is applied here and travels as a boolean. The card needs to
    # know which caution to show, not how the number was judged, and a copy of
    # 0.45 in the client is a second place for this to be decided differently.
    low_similarity = similarity < GROUNDING_REVIEW_THRESHOLD
    return {"novel_numbers": novel_numbers, "novel_organisations": organisations,
            "similarity": similarity, "low_similarity": low_similarity,
            "review_required": bool(novel_numbers or organisations or low_similarity)}
