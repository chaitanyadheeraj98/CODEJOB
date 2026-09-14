from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from app.services import resume_render_service as render


def test_render_admission_refusal_becomes_a_busy_message(tmp_path):
    """The four-slot semaphore moved to shared admission in C1.

    Forced rather than produced by filling a real pool: what matters here is
    that a refusal reaches the caller as "busy" rather than a traceback. The
    caps themselves are covered in test_admission_service.py.
    """
    with patch.object(
        render.admission_service, "acquire",
        side_effect=render.AdmissionRejected("global", "Rendering is busy."),
    ), patch.object(render, "_soffice", return_value="soffice"), pytest.raises(RuntimeError, match="busy"):
        render.build_pdf("# Alex", render.ResumeFormatSpec(), tmp_path / "r.pdf")


def test_the_slot_is_released_even_when_rendering_times_out(tmp_path):
    """A leaked lease is worse than a leaked semaphore: it outlives the process."""
    released = []
    real_release = render.admission_service.release

    def record(lease, **kwargs):
        released.append(lease)
        return real_release(lease, **kwargs)

    with patch.object(render, "_soffice", return_value="soffice"),             patch.object(render.subprocess, "run", side_effect=subprocess.TimeoutExpired("soffice", 75)),             patch.object(render.admission_service, "release", side_effect=record):
        with pytest.raises(RuntimeError, match="timed out"):
            render.build_pdf("# Alex", render.ResumeFormatSpec(), tmp_path / "r.pdf")

    assert len(released) == 1, "the lease must be given back on the failure path"


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
