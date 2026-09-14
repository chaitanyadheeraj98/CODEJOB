import json
from pathlib import Path

import pytest
from PIL import Image

from app.services.telegram_chart_render import WIDTH, render_chart_png, render_chart_png_bounded


PAYLOAD = {
    "action": "render_chart",
    "chart_type": "activity_trend",
    "title": "Approved sends",
    "max_value": 8,
    "series": [
        {"label": "2026-09-01", "value": 8},
        {"label": "2026-09-02", "value": 4},
        {"label": "2026-09-03", "value": 0},
    ],
    "provenance": {
        "source": "get_chart/activity_trend",
        "row_count": 3,
        "assumptions": ["Empty buckets are shown as zero rather than omitted."],
    },
}


def test_known_payload_produces_small_png_with_expected_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "chart.png"
    render_chart_png(json.dumps(PAYLOAD), path)
    with Image.open(path) as image:
        assert image.size == (WIDTH, 292)
        assert image.format == "PNG"
    assert path.stat().st_size < 200_000


def test_bounded_renderer_enforces_timeout_and_leaves_no_file(tmp_path: Path) -> None:
    path = tmp_path / "chart.png"
    with pytest.raises(TimeoutError):
        render_chart_png_bounded(json.dumps(PAYLOAD), path, 0.0)
    assert not path.exists()
