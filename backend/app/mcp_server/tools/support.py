from __future__ import annotations


def propose_create_github_issue(user_report: str, ai_summary: str, context: str = "") -> dict[str, object]:
    """Prepare, but never file, a GitHub issue for a problem the assistant could not resolve.

    Call this when a tool call fails, when required data is missing, or when the
    user explicitly asks to report or raise a ticket about anything in the app -
    not for a routine "I don't know" answer with no underlying failure.
    user_report must be the user's own words, unedited. ai_summary is your own
    clear, concise restatement of the same problem for a developer - do not
    change its meaning. context is optional supporting detail (candidate/email
    IDs, expected vs actual values, tool error text) to help reproduce it.
    """
    if not user_report.strip():
        return {"hint": 'Ask the user for user_report. Do not guess.', "status": "missing_fields", "missing": ["user_report"]}
    if not ai_summary.strip():
        return {"hint": 'Ask the user for ai_summary. Do not guess.', "status": "missing_fields", "missing": ["ai_summary"]}
    return {
        "action": "create_github_issue",
        "title": ai_summary.strip().splitlines()[0][:120],
        "user_report": user_report.strip(),
        "ai_summary": ai_summary.strip(),
        "context": context.strip(),
    }
