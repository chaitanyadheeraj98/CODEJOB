from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from app.services import resume_render_service as render


def test_render_slots_reject_overload_and_are_released_after_timeout(tmp_path):
    for _ in range(4):
        assert render._RENDER_SLOTS.acquire(blocking=False)
    try:
        with patch.object(render, "_soffice", return_value="soffice"), pytest.raises(RuntimeError, match="busy"):
            render.build_pdf("# Alex", render.ResumeFormatSpec(), tmp_path / "r.pdf")
    finally:
        for _ in range(4):
            render._RENDER_SLOTS.release()
    with patch.object(render, "_soffice", return_value="soffice"), patch.object(render.subprocess, "run", side_effect=subprocess.TimeoutExpired("soffice", 75)):
        with pytest.raises(RuntimeError, match="timed out"):
            render.build_pdf("# Alex", render.ResumeFormatSpec(), tmp_path / "r.pdf")
    assert render._RENDER_SLOTS.acquire(blocking=False)
    render._RENDER_SLOTS.release()


def test_thumbnails_are_cached_by_spec_and_use_isolated_office_profiles():
    render.build_thumbnail.cache_clear()
    commands = []
    def convert(command, **kwargs):
        commands.append(command)
        Path(command[-1]).with_suffix(".png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        return subprocess.CompletedProcess(command, 0, b"", b"")
    with patch.object(render, "_soffice", return_value="soffice"), patch.object(render.subprocess, "run", side_effect=convert):
        first = render.ResumeFormatSpec().model_dump_json()
        assert render.build_thumbnail(first) == render.build_thumbnail(first)
        render.build_thumbnail(render.ResumeFormatSpec(page_size="A4").model_dump_json())
    assert len(commands) == 2
    assert commands[0][1].startswith("-env:UserInstallation=file:")
    assert commands[0][1] != commands[1][1]
    render.build_thumbnail.cache_clear()
