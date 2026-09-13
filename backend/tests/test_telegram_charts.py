import json

from app.services.telegram_format import chunk, plain_text, unicode_chart


PROVENANCE = {
    "metric": "Approved sends",
    "source": "get_chart/activity_trend",
    "row_count": 3,
    "date_range": {"from": "2026-09-01", "to": "2026-09-03"},
    "filters": {"range": "current_month", "bucket": "day"},
    "assumptions": ["Empty buckets are shown as zero rather than omitted."],
}

PAYLOAD = {
    "action": "render_chart",
    "chart_type": "activity_trend",
    "title": "Approved sends",
    "max_value": 8,
    "series": [
        {"label": "2026-09-01", "value": 8, "rate_of_previous": None, "drill_to": {"page": "sent_items", "tab": None, "filters": {}}},
        {"label": "2026-09-02", "value": 4, "rate_of_previous": None, "drill_to": None},
        {"label": "2026-09-03", "value": 0, "rate_of_previous": None, "drill_to": None},
    ],
    "provenance": PROVENANCE,
}


def test_dashboard_fixture_renders_known_bars_character_for_character() -> None:
    rendered = unicode_chart(json.dumps(PAYLOAD))
    assert rendered is not None
    assert plain_text(rendered) == (
        "Approved sends\n"
        "2026-09-01  ████████  8\n"
        "2026-09-02  ████      4\n"
        "2026-09-03            0\n"
        "Source: get_chart/activity_trend · 3 rows · 2026-09-01 → 2026-09-03\n"
        "Assumptions: Empty buckets are shown as zero rather than omitted."
    )


def test_chart_without_provenance_is_omitted_with_a_reason() -> None:
    payload = dict(PAYLOAD)
    payload.pop("provenance")
    rendered = unicode_chart(json.dumps(payload))
    assert rendered == "Chart omitted: source provenance was missing."
    assert "█" not in rendered


def test_funnel_includes_rate_of_previous() -> None:
    payload = {
        **PAYLOAD,
        "chart_type": "resume_funnel",
        "title": "Resume funnel",
        "max_value": 10,
        "series": [
            {"label": "Submitted", "value": 10, "rate_of_previous": None, "drill_to": None},
            {"label": "Viewed", "value": 5, "rate_of_previous": 50, "drill_to": None},
        ],
    }
    assert "Viewed     ████      5 (50%)" in plain_text(unicode_chart(json.dumps(payload)) or "")


def test_forty_character_label_stays_aligned_and_inside_chunk_limit() -> None:
    payload = {**PAYLOAD, "series": [{"label": "L" * 40, "value": 8, "rate_of_previous": None}]}
    rendered = unicode_chart(json.dumps(payload))
    assert rendered is not None
    assert "L" * 40 + "  ████████  8" in plain_text(rendered)
    assert all(len(part) <= 4096 for part in chunk(rendered))


def test_bars_scale_against_server_max_value() -> None:
    payload = {**PAYLOAD, "max_value": 16, "series": [{"label": "Only", "value": 8, "rate_of_previous": None}]}
    assert "Only  ████      8" in plain_text(unicode_chart(json.dumps(payload)) or "")
