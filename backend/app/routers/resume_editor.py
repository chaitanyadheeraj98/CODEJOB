"""Draft a resume, then download it in the shape an employer asked for.

A stored variant is a file plus the text extracted from it, and the two have to
agree: the text is what the matcher and the chatbot reason about, the file is
what the recruiter actually receives. So nothing here writes to a variant.
Editing happens in a `ResumeDraft`, and the only thing an edit produces is a
download. That download becomes a variant when the user uploads it back through
the library, where the file and its text are read from the same document.

Publishing is the one place a variant is created, and it holds the same
invariant by construction: the rendered file is written first, and its text is
extracted from that file by the same function an upload goes through.

Format profiles live here too, because the layout is what turns a draft into the
document an employer expects.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import settings
from app.db import get_db
from app.models import ResumeAsset, ResumeDraft, ResumeFormatProfile
from app.schemas import (
    ResumeDraftCreateRequest,
    ResumeDraftPublishRequest,
    ResumeDraftPublishResponse,
    ResumeDraftResponse,
    ResumeDraftSectionMoveRequest,
    ResumeDraftSectionRequest,
    ResumeDraftSummary,
    ResumeDraftUpdateRequest,
    ResumeFormatProfileResponse,
    ResumeFormatProfileUpdateRequest,
    resume_variant_code,
)
from app.services import resume_format_profile_service
from app.services.resume_render_service import (
    ResumeFormatSpec,
    build_docx,
    build_pdf,
    build_thumbnail,
    covers_whole_document,
    find_section,
    move_section,
    nested_headings,
    replace_section,
    section_digest,
    split_sections,
)
from app import tenancy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resume-editor", tags=["resume-editor"])

# The same ceiling the enrichment extractor works to, so text copied out of a
# variant can always be saved back into a draft.
MAX_CONTENT_CHARS = 50000
SAMPLE_SUFFIXES = frozenset({".docx", ".doc", ".pdf", ".md", ".txt"})
EXPORT_MEDIA_TYPES = {
    "md": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _draft_or_404(db: Session, draft_id: int) -> ResumeDraft:
    draft = (
        db.query(ResumeDraft)
        .filter(ResumeDraft.owner_id == tenancy.owner_id(), ResumeDraft.id == draft_id)
        .first()
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


def _profile_or_404(db: Session, profile_id: int) -> ResumeFormatProfile:
    profile = (
        db.query(ResumeFormatProfile)
        .filter(ResumeFormatProfile.owner_id == tenancy.owner_id(), ResumeFormatProfile.id == profile_id)
        .first()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Format profile not found")
    return profile


def _live_variant_codes(db: Session, draft_ids: list[int | None]) -> dict[int, str]:
    """Codes for the source variants that still exist.

    A draft whose source has been deleted keeps its `source_resume_id` and shows
    no code, rather than showing a code that now points at nothing.
    """
    wanted = {value for value in draft_ids if value is not None}
    if not wanted:
        return {}
    rows = (
        db.query(ResumeAsset.id)
        .filter(ResumeAsset.owner_id == tenancy.owner_id(), ResumeAsset.id.in_(wanted))
        .all()
    )
    return {row.id: resume_variant_code(row.id) for row in rows}


def _summary(draft: ResumeDraft, codes: dict[int, str]) -> ResumeDraftSummary:
    return ResumeDraftSummary(
        id=draft.id,
        name=draft.name,
        source_resume_id=draft.source_resume_id,
        format_profile_id=draft.format_profile_id,
        source_variant_code=codes.get(draft.source_resume_id or -1, ""),
        character_count=len(draft.content_markdown or ""),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _response(db: Session, draft: ResumeDraft) -> ResumeDraftResponse:
    codes = _live_variant_codes(db, [draft.source_resume_id])
    return ResumeDraftResponse(
        **_summary(draft, codes).model_dump(),
        content_markdown=draft.content_markdown or "",
        sections=[asdict(section) for section in split_sections(draft.content_markdown or "")],
    )


def _checked_content(content: str) -> str:
    cleaned = content.replace("\r\n", "\n").strip()
    if len(cleaned) > MAX_CONTENT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Draft is {len(cleaned)} characters; the limit is {MAX_CONTENT_CHARS}.",
        )
    return cleaned


def _spec_of(profile: ResumeFormatProfile) -> ResumeFormatSpec:
    """A stored spec that no longer parses falls back rather than 500s.

    Specs gain fields over time, and a profile written by an older version is
    still a usable description of a layout for every field it does carry.
    """
    try:
        return ResumeFormatSpec.model_validate(json.loads(profile.spec_json or "{}"))
    except Exception as exc:
        logger.warning("Format profile %s could not be read, using defaults: %s", profile.id, exc)
        return ResumeFormatSpec()


def _default_spec(db: Session) -> ResumeFormatSpec:
    """The layout used when no profile is named: the default one, or the built-in."""
    default = (
        db.query(ResumeFormatProfile)
        .filter(
            ResumeFormatProfile.owner_id == tenancy.owner_id(),
            ResumeFormatProfile.is_default.is_(True),
        )
        .first()
    )
    return _spec_of(default) if default else ResumeFormatSpec()


def _profile_response(profile: ResumeFormatProfile) -> ResumeFormatProfileResponse:
    return ResumeFormatProfileResponse(
        id=profile.id,
        name=profile.name,
        source_file_name=profile.source_file_name,
        is_default=profile.is_default,
        spec=_spec_of(profile),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _clear_other_defaults(db: Session, keep_id: int) -> None:
    for other in (
        db.query(ResumeFormatProfile)
        .filter(
            ResumeFormatProfile.owner_id == tenancy.owner_id(),
            ResumeFormatProfile.is_default.is_(True),
            ResumeFormatProfile.id != keep_id,
        )
        .all()
    ):
        other.is_default = False


def _download_name(draft: ResumeDraft, extension: str) -> str:
    """A safe filename built from the draft's name, never from caller input."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", draft.name or "").strip("._-")
    return f"{(safe or f'resume-draft-{draft.id}')[:120]}.{extension}"


@router.get("/drafts", response_model=list[ResumeDraftSummary])
def list_drafts(db: Session = Depends(get_db)) -> list[ResumeDraftSummary]:
    drafts = (
        db.query(ResumeDraft)
        .filter(ResumeDraft.owner_id == tenancy.owner_id())
        .order_by(ResumeDraft.updated_at.desc(), ResumeDraft.id.desc())
        .all()
    )
    codes = _live_variant_codes(db, [draft.source_resume_id for draft in drafts])
    return [_summary(draft, codes) for draft in drafts]


@router.post("/drafts", response_model=ResumeDraftResponse)
def create_draft(payload: ResumeDraftCreateRequest, db: Session = Depends(get_db)) -> ResumeDraftResponse:
    """Start a draft, optionally seeded from a stored variant.

    Seeding *copies* the variant's extracted text. The variant is opened
    read-only and is not written to here or anywhere else in this router.
    """
    content = payload.content_markdown
    name = payload.name.strip()

    if payload.source_resume_id is not None:
        source = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == tenancy.owner_id(), ResumeAsset.id == payload.source_resume_id)
            .first()
        )
        if source is None:
            raise HTTPException(status_code=404, detail="Resume not found")
        if not content:
            content = source.content_markdown or ""
        if not name:
            code = resume_variant_code(source.id)
            name = f"{code} copy" if not source.variant_label else f"{code} {source.variant_label}"

    draft = ResumeDraft(
        owner_id=tenancy.owner_id(),
        name=(name or "Untitled draft")[:200],
        source_resume_id=payload.source_resume_id,
        content_markdown=_checked_content(content),
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return _response(db, draft)


@router.get("/drafts/{draft_id}", response_model=ResumeDraftResponse)
def get_draft(draft_id: int, db: Session = Depends(get_db)) -> ResumeDraftResponse:
    return _response(db, _draft_or_404(db, draft_id))


@router.put("/drafts/{draft_id}", response_model=ResumeDraftResponse)
def save_draft(
    draft_id: int,
    payload: ResumeDraftUpdateRequest,
    db: Session = Depends(get_db),
) -> ResumeDraftResponse:
    draft = _draft_or_404(db, draft_id)
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="At least one draft field is required")
    if payload.name is not None:
        draft.name = (payload.name.strip() or "Untitled draft")[:200]
    if payload.content_markdown is not None:
        draft.content_markdown = _checked_content(payload.content_markdown)
    if "format_profile_id" in payload.model_fields_set:
        if payload.format_profile_id is not None:
            _profile_or_404(db, payload.format_profile_id)
        draft.format_profile_id = payload.format_profile_id
    db.commit()
    db.refresh(draft)
    return _response(db, draft)


@router.patch("/drafts/{draft_id}/section", response_model=ResumeDraftResponse)
def replace_draft_section(
    draft_id: int,
    payload: ResumeDraftSectionRequest,
    db: Session = Depends(get_db),
) -> ResumeDraftResponse:
    """Rewrite one section of a draft, leaving every other section untouched.

    The splice happens here, at the moment of the write, rather than travelling
    through the caller as a whole rebuilt document. A section is small enough for
    a model to write and for a person to read on a confirmation card; the draft
    around it is neither, and sending it back and forth is how a rewrite of the
    Summary quietly loses an edit made to the Experience below it.
    """
    draft = _draft_or_404(db, draft_id)
    current = draft.content_markdown or ""
    section = find_section(current, payload.section)
    if section is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No single section called “{payload.section}” in this draft. "
                f"It has: {', '.join(item.heading for item in split_sections(current)) or 'no headings'}."
            ),
        )
    # Checked here as well as in the tool that builds the card. The tool can be
    # reasoned around; a route cannot, and this is the last point before a
    # resume is overwritten by something claiming to be one of its sections.
    if covers_whole_document(current, section):
        inside = nested_headings(current, section)
        raise HTTPException(
            status_code=400,
            detail=(
                f"“{section.heading}” spans the whole draft - replacing it would delete "
                f"{', '.join(inside)}. Edit one of those, or rewrite the draft in the Editor."
            ),
        )
    if payload.base_sha256 and payload.base_sha256 != section_digest(section.body):
        raise HTTPException(
            status_code=409,
            detail=(
                f"“{section.heading}” has changed since this rewrite was written. "
                "Read the section again and redo it, so the newer edit is not lost."
            ),
        )

    rewritten = replace_section(current, payload.section, payload.replacement)
    assert rewritten is not None
    draft.content_markdown = _checked_content(rewritten)
    db.commit()
    db.refresh(draft)
    return _response(db, draft)


@router.post("/drafts/{draft_id}/sections/reorder", response_model=ResumeDraftResponse)
def reorder_draft_section(
    draft_id: int,
    payload: ResumeDraftSectionMoveRequest,
    db: Session = Depends(get_db),
) -> ResumeDraftResponse:
    """Move a section above or below its neighbour, subsections and all.

    Reordering is done here rather than by the user cutting and pasting in the
    textarea, because a section is a heading plus everything under it and the
    line where that ends is exactly what is easy to get wrong by hand - a
    mis-selected paste is how the last two bullets of a role end up under the
    next one.
    """
    draft = _draft_or_404(db, draft_id)
    current = draft.content_markdown or ""
    section = find_section(current, payload.section)
    if section is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No single section called “{payload.section}” in this draft. "
                f"It has: {', '.join(item.heading for item in split_sections(current)) or 'no headings'}."
            ),
        )
    # The digest covers the whole draft: see ResumeDraftSectionMoveRequest.
    if payload.base_sha256 and payload.base_sha256 != section_digest(current):
        raise HTTPException(
            status_code=409,
            detail="This draft has changed since the outline was read. Reopen it and move the section again.",
        )

    moved = move_section(current, payload.section, -1 if payload.direction == "up" else 1)
    if moved is None:
        edge = "first" if payload.direction == "up" else "last"
        raise HTTPException(
            status_code=400,
            detail=f"“{section.heading}” is already the {edge} section at its level.",
        )

    draft.content_markdown = _checked_content(moved)
    db.commit()
    db.refresh(draft)
    return _response(db, draft)


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    draft = _draft_or_404(db, draft_id)
    db.delete(draft)
    db.commit()
    return {"id": draft_id, "deleted": True}


@router.get("/drafts/{draft_id}/export")
def export_draft(
    draft_id: int,
    fmt: str = Query("docx", pattern="^(md|docx|pdf)$"),
    profile_id: int | None = Query(None),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Render a draft as markdown, Word or PDF.

    This is the only thing editing produces. The file is not stored and no
    variant is created: uploading the download back into the library is what
    makes it a variant, and that is a decision the user takes deliberately.
    """
    draft = _draft_or_404(db, draft_id)
    content = (draft.content_markdown or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="This draft is empty. Write something to download it.")

    spec = _export_spec(db, draft, profile_id)

    work = Path(tempfile.mkdtemp(prefix="resume-export-"))
    # The directory outlives this function - the response streams from it - so
    # it is removed once the bytes are on the wire, not before.
    cleanup = BackgroundTask(shutil.rmtree, work, ignore_errors=True)
    target = work / f"draft.{fmt}"
    try:
        if fmt == "md":
            target.write_text(content, encoding="utf-8")
        elif fmt == "docx":
            build_docx(content, spec, target)
        else:
            build_pdf(content, spec, target)
    except RuntimeError as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(work, ignore_errors=True)
        logger.exception("Draft export failed for draft %s as %s", draft_id, fmt)
        raise HTTPException(status_code=500, detail=f"Could not build the {fmt.upper()}: {exc}") from exc

    return FileResponse(
        target,
        media_type=EXPORT_MEDIA_TYPES[fmt],
        filename=_download_name(draft, fmt),
        background=cleanup,
    )


def _rendered_bytes(content: str, spec: ResumeFormatSpec, fmt: str) -> bytes:
    """Render once, into memory, so the stored file and the download agree."""
    work = Path(tempfile.mkdtemp(prefix="resume-publish-"))
    try:
        target = work / f"resume.{fmt}"
        if fmt == "docx":
            build_docx(content, spec, target)
        else:
            build_pdf(content, spec, target)
        return target.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


@router.post("/drafts/{draft_id}/publish", response_model=ResumeDraftPublishResponse)
def publish_draft(
    draft_id: int,
    payload: ResumeDraftPublishRequest,
    db: Session = Depends(get_db),
) -> ResumeDraftPublishResponse:
    """Render the draft and store the result as a new resume variant.

    This is the download-then-upload loop done in one step, and it holds the same
    invariant by construction: the file is written first, and the text is then
    extracted *from that file* by the same `store_resume_asset` an upload uses.
    No existing variant is modified - a new one is added beside them.
    """
    draft = _draft_or_404(db, draft_id)
    content = (draft.content_markdown or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="This draft is empty. Write something before saving it as a variant.")

    # Any extension the user typed is dropped here rather than with Path().stem:
    # a resume name reads "R07 Java / Banking" often enough, and Path would take
    # the slash for a directory separator and compare only "Banking".
    label = re.sub(r"\.(docx|pdf)$", "", payload.file_name.strip(), flags=re.IGNORECASE).strip()
    if not label:
        raise HTTPException(status_code=400, detail="Name the new variant")
    if label.casefold() == (draft.name or "").strip().casefold():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Give the variant a different name from the draft “{draft.name}”. "
                "Two things sharing one name is what makes them easy to confuse later."
            ),
        )

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._-")
    if not safe:
        raise HTTPException(status_code=400, detail="Use letters or numbers in the variant name")
    file_name = f"{safe[:120]}.{payload.fmt}"
    clash = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == tenancy.owner_id(), ResumeAsset.file_name == file_name)
        .first()
    )
    if clash is not None:
        raise HTTPException(
            status_code=400,
            detail=f"{resume_variant_code(clash.id)} is already called {file_name}. Pick another name.",
        )

    spec = _export_spec(db, draft, payload.profile_id)
    try:
        rendered = _rendered_bytes(content, spec, payload.fmt)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Publishing draft %s as %s failed", draft_id, payload.fmt)
        raise HTTPException(status_code=500, detail=f"Could not build the {payload.fmt.upper()}: {exc}") from exc

    # Imported here, not at module scope: main imports this router, so importing
    # main at the top would be a cycle. By request time it is fully loaded.
    from app.main import store_resume_asset

    resume = store_resume_asset(
        db,
        content=rendered,
        file_name=file_name,
        mime_type=EXPORT_MEDIA_TYPES[payload.fmt],
        primary_role=payload.primary_role,
        structured_skills_text=payload.structured_skills_text,
        variant_label=payload.variant_label,
    )
    return ResumeDraftPublishResponse(
        resume_id=resume.id,
        variant_code=resume_variant_code(resume.id),
        file_name=resume.file_name,
        version=resume.version,
    )


@router.get("/profiles", response_model=list[ResumeFormatProfileResponse])
def list_format_profiles(db: Session = Depends(get_db)) -> list[ResumeFormatProfileResponse]:
    profiles = (
        db.query(ResumeFormatProfile)
        .filter(ResumeFormatProfile.owner_id == tenancy.owner_id())
        .order_by(ResumeFormatProfile.is_default.desc(), ResumeFormatProfile.id.desc())
        .all()
    )
    return [_profile_response(profile) for profile in profiles]


@router.post("/profiles", response_model=ResumeFormatProfileResponse)
def create_format_profile(
    file: UploadFile | None = File(None),
    name: str = Form(""),
    make_default: bool = Form(False),
    spec_json: str | None = Form(None),
    db: Session = Depends(get_db),
) -> ResumeFormatProfileResponse:
    """Measure an employer's sample resume and store its layout as a profile."""
    if spec_json is not None:
        try:
            spec = ResumeFormatSpec.model_validate_json(spec_json)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not name.strip():
            raise HTTPException(status_code=400, detail="A profile needs a name")
        profile = ResumeFormatProfile(owner_id=tenancy.owner_id(), name=name.strip()[:120],
                                      source_file_name="", spec_json=spec.model_dump_json(), is_default=bool(make_default))
        db.add(profile)
        db.flush()
        if profile.is_default:
            _clear_other_defaults(db, profile.id)
        db.commit()
        db.refresh(profile)
        return _profile_response(profile)
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="File name required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SAMPLE_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload a {', '.join(sorted(SAMPLE_SUFFIXES))} sample. A .docx is measured most accurately.",
        )
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file not allowed")

    directory = Path(settings.resume_storage_dir) / "format-samples"
    directory.mkdir(parents=True, exist_ok=True)
    # Named from the hash, never from the upload: a filename is user input and
    # has no business deciding a path.
    sample = directory / f"{hashlib.sha256(content).hexdigest()}{suffix}"
    sample.write_bytes(content)

    spec = resume_format_profile_service.spec_from_sample(sample, file.filename)
    profile = ResumeFormatProfile(
        owner_id=tenancy.owner_id(),
        name=(name.strip() or Path(file.filename).stem)[:120],
        source_file_name=file.filename[:255],
        spec_json=spec.model_dump_json(),
        is_default=bool(make_default),
    )
    db.add(profile)
    db.flush()
    if profile.is_default:
        _clear_other_defaults(db, profile.id)
    db.commit()
    db.refresh(profile)
    return _profile_response(profile)


@router.patch("/profiles/{profile_id}", response_model=ResumeFormatProfileResponse)
def update_format_profile(
    profile_id: int,
    payload: ResumeFormatProfileUpdateRequest,
    db: Session = Depends(get_db),
) -> ResumeFormatProfileResponse:
    profile = _profile_or_404(db, profile_id)
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="At least one profile update field is required")

    if payload.name is not None:
        cleaned = payload.name.strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="A profile needs a name")
        profile.name = cleaned[:120]
    if payload.spec is not None:
        profile.spec_json = payload.spec.model_dump_json()
    if payload.is_default is not None:
        profile.is_default = payload.is_default
        if payload.is_default:
            _clear_other_defaults(db, profile.id)

    db.commit()
    db.refresh(profile)
    return _profile_response(profile)


@router.delete("/profiles/{profile_id}")
def delete_format_profile(profile_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    profile = _profile_or_404(db, profile_id)
    db.delete(profile)
    db.commit()
    # Drafts are not affected: a profile describes how to render text, and
    # deleting one only means the next download uses the built-in layout.
    return {"id": profile_id, "deleted": True}


def _export_spec(db: Session, draft: ResumeDraft, profile_id: int | None) -> ResumeFormatSpec:
    if profile_id is not None:
        return ResumeFormatSpec() if profile_id == 0 else _spec_of(_profile_or_404(db, profile_id))
    if draft.format_profile_id is not None:
        profile = db.query(ResumeFormatProfile).filter(
            ResumeFormatProfile.id == draft.format_profile_id,
            ResumeFormatProfile.owner_id == tenancy.owner_id(),
        ).first()
        if profile is not None:
            return _spec_of(profile)
    return _default_spec(db)


@router.get("/profiles/{profile_id}/preview.png")
def preview_profile(profile_id: int, db: Session = Depends(get_db)) -> Response:
    spec_json = _spec_of(_profile_or_404(db, profile_id)).model_dump_json()
    try:
        content = build_thumbnail(spec_json)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content, media_type="image/png", headers={
        "ETag": f'"{hashlib.sha256(spec_json.encode()).hexdigest()}"',
        "Cache-Control": "private, no-cache",
    })
