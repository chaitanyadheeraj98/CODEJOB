from __future__ import annotations

# The pages the assistant may send the user to, and the URL parameters each one
# legitimately accepts. This is a deliberate duplicate of the frontend's
# filterSortRegistry field keys (dashboard/src/filterSortRegistry.ts): the server
# should not emit a payload the client will only reject, and the client
# re-validates every value against its own field options regardless.
#
# Keys here are URL parameter names, not field keys - a range field contributes
# min_/max_ and a daterange contributes date_filter/date_from/date_to, exactly
# as buildUrlSearch writes them.
_CANDIDATE_QUEUE_FILTERS = frozenset({
    "sender", "recipient", "role", "location", "interview_type", "source",
    "sendability", "has_resume", "min_ats_score", "max_ats_score",
    "ats_strength", "contact_status", "verification", "following",
    "date_filter", "date_from", "date_to",
})

NAVIGABLE_PAGES: dict[str, dict[str, object]] = {
    "needs_review": {
        "label": "Needs Review",
        "tab": None,
        "filters": _CANDIDATE_QUEUE_FILTERS,
    },
    "failed_mapping": {
        "label": "Failed Mapping",
        "tab": None,
        "filters": _CANDIDATE_QUEUE_FILTERS,
    },
    "sent_items": {
        "label": "Sent Items",
        "tab": None,
        "filters": _CANDIDATE_QUEUE_FILTERS,
    },
    "inbox": {
        "label": "Inbox",
        "tab": None,
        "filters": frozenset({"sender", "subject", "date_filter", "date_from", "date_to"}),
    },
    "recent_runs": {"label": "Run Queue", "tab": None, "filters": frozenset()},
    "premium_numbers": {
        "label": "Premium Numbers",
        "tab": "inventory",
        "tabs": {"inventory", "companies", "opportunities", "recycle_bin"},
        "filters": frozenset({
            "q", "domain", "favorite", "status", "source_type", "resume_asset_id",
            "date_filter", "date_from", "date_to",
        }),
    },
    "application_tracking": {
        "label": "Application Tracking",
        "tab": "tracked",
        "tabs": {"bookmarked", "tracked"},
        "filters": _CANDIDATE_QUEUE_FILTERS | {"status", "resume_asset_id"},
    },
    "resume_tracking": {
        "label": "Resume Tracking",
        "tab": "submissions",
        "tabs": {"resumes", "submissions"},
        "filters": frozenset({"status", "resume_asset_id", "date_filter", "date_from", "date_to"}),
    },
}

MAX_TITLE_CHARS = 120
MAX_VALUE_CHARS = 100


def navigate_to_queue(
    page: str, tab: str = "", filters: dict[str, str] | None = None, title: str = ""
) -> dict[str, object]:
    """Give the user a button that opens one of their work queues, pre-filtered.

    Call this when the user asks to see, open, or go to a queue - "show me the
    Java roles I have not replied to", "open my failed mapping queue". It draws
    a button; the user's click navigates. It changes no data, so it needs no
    confirmation.

    Valid pages: needs_review, failed_mapping, sent_items, inbox, recent_runs,
    premium_numbers, application_tracking, resume_tracking. Some take a tab -
    premium_numbers (inventory, companies, opportunities, recycle_bin),
    application_tracking (bookmarked, tracked), resume_tracking (resumes,
    submissions).

    Filters are URL parameter names, e.g. role, location, source, sendability,
    min_ats_score, max_ats_score, date_filter (today, yesterday, last_7_days).
    Unknown ones are dropped and reported rather than applied.

    Set `title` to the destination in the user's own words, e.g. "Java roles
    from last week".
    """
    spec = NAVIGABLE_PAGES.get(page)
    if spec is None:
        return {"error": f"Unknown page '{page}'.", "pages": sorted(NAVIGABLE_PAGES)}

    allowed = spec["filters"]
    assert isinstance(allowed, frozenset)

    resolved_tab = (tab or "").strip() or spec["tab"]
    tabs = spec.get("tabs")
    dropped: list[dict[str, str]] = []
    if tabs is not None and resolved_tab not in tabs:
        dropped.append({"key": "tab", "reason": "unknown_tab"})
        resolved_tab = spec["tab"]
    elif tabs is None and (tab or "").strip():
        dropped.append({"key": "tab", "reason": "unknown_tab"})
        resolved_tab = None

    accepted: dict[str, str] = {}
    for key, value in (filters or {}).items():
        if key not in allowed:
            # Reported, never silently ignored: a filter the user believes was
            # applied is worse than one they can see was refused.
            dropped.append({"key": str(key), "reason": "unknown_field"})
            continue
        accepted[str(key)] = str(value).strip()[:MAX_VALUE_CHARS]

    return {
        "action": "navigate_to_queue",
        "page": page,
        "tab": resolved_tab,
        "label": spec["label"],
        "title": (title or "").strip()[:MAX_TITLE_CHARS],
        "filters": accepted,
        "dropped": dropped,
    }
