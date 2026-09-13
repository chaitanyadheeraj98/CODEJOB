from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 900
ROW_HEIGHT = 40


def render_chart_png(content: str, path: Path) -> None:
    payload = json.loads(content)
    provenance = payload.get("provenance") if isinstance(payload, dict) else None
    series = payload.get("series") if isinstance(payload, dict) else None
    max_value = payload.get("max_value") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("action") != "render_chart"
        or not isinstance(provenance, dict)
        or not isinstance(series, list)
        or isinstance(max_value, bool)
        or not isinstance(max_value, (int, float))
        or max_value < 0
    ):
        raise ValueError("invalid chart payload")
    assumptions = provenance.get("assumptions")
    assumption_lines = [str(value)[:110] for value in assumptions] if isinstance(assumptions, list) else []
    height = 150 + max(1, len(series)) * ROW_HEIGHT + len(assumption_lines) * 22
    image = Image.new("RGB", (WIDTH, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=22)
    font = ImageFont.load_default(size=16)
    draw.text((24, 18), str(payload.get("title") or "Chart")[:80], fill="#111827", font=title_font)
    if not series:
        draw.text((24, 70), "No data in this range.", fill="#4b5563", font=font)
    for index, point in enumerate(series):
        if not isinstance(point, dict) or not isinstance(point.get("value"), (int, float)):
            raise ValueError("invalid chart series")
        y = 66 + index * ROW_HEIGHT
        label = str(point.get("label") or "")[:40]
        value = float(point["value"])
        ratio = min(1.0, max(0.0, value / max_value)) if max_value else 0.0
        draw.text((24, y + 7), label, fill="#374151", font=font)
        draw.rounded_rectangle((300, y + 5, 800, y + 29), radius=6, fill="#e5e7eb")
        if ratio:
            draw.rounded_rectangle((300, y + 5, 300 + int(500 * ratio), y + 29), radius=6, fill="#2563eb")
        draw.text((815, y + 7), f"{value:g}", fill="#111827", font=font)
    footer_y = 78 + max(1, len(series)) * ROW_HEIGHT
    source = str(provenance.get("source") or "")[:90]
    rows = provenance.get("row_count", 0)
    draw.text((24, footer_y), f"Source: {source} · {rows} rows", fill="#4b5563", font=font)
    for index, assumption in enumerate(assumption_lines):
        draw.text((24, footer_y + 24 + index * 22), f"Assumption: {assumption}", fill="#4b5563", font=font)
    image.save(path, format="PNG", optimize=True)


def _render_process(content: str, path: str) -> None:
    try:
        render_chart_png(content, Path(path))
    except BaseException:
        raise SystemExit(1)


def render_chart_png_bounded(content: str, path: Path, timeout_seconds: float) -> None:
    process = multiprocessing.get_context("spawn").Process(target=_render_process, args=(content, str(path)))
    process.start()
    process.join(max(0.0, timeout_seconds))
    if process.is_alive():
        process.terminate()
        process.join()
        raise TimeoutError("chart render timed out")
    if process.exitcode != 0 or not path.is_file():
        raise RuntimeError("chart render failed")
