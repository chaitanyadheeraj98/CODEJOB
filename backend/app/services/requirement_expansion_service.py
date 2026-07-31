from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.models import RecruiterEmail
from app.services.role_manifest_service import RoleManifestResult


TERMINAL_STATES = {"approved_sent", "sent", "rejected", "auto_rejected"}


@dataclass(frozen=True)
class ExpansionResult:
    source_parent_id: int
    manifest_status: str
    requirement_count: int
    child_ids: tuple[int, ...] = ()


class RequirementExpansionService:
    def expand(
        self,
        db: Session,
        parent: RecruiterEmail,
        manifest_result: RoleManifestResult,
        *,
        materialize: bool,
    ) -> ExpansionResult:
        parent.role_manifest_status = manifest_result.status
        parent.role_manifest_confidence = (
            manifest_result.manifest.confidence if manifest_result.manifest is not None else None
        )
        parent.role_manifest_json = (
            json.dumps(manifest_result.manifest.model_dump(mode="json"), separators=(",", ":"))
            if manifest_result.manifest is not None
            else None
        )
        parent.role_manifest_diagnostics_json = json.dumps(asdict(manifest_result.diagnostics), separators=(",", ":"))

        if manifest_result.status in {"invalid", "uncertain"}:
            if not materialize:
                db.commit()
                return ExpansionResult(parent.id, manifest_result.status, 0)
            parent.sendability_status = "manifest_review"
            parent.draft_reply = ""
            parent.draft_source = None
            parent.resume_asset_id = None
            parent.resume_file_name = None
            db.commit()
            return ExpansionResult(parent.id, manifest_result.status, 0)

        if manifest_result.status == "single":
            parent.requirement_count = 1
            parent.requirement_index = 1
            if parent.sendability_status == "manifest_review":
                parent.sendability_status = None
            db.commit()
            return ExpansionResult(parent.id, "single", 1)

        if not materialize:
            db.commit()
            return ExpansionResult(parent.id, "multiple", len(manifest_result.requirements))

        had_candidate_history = bool(
            (parent.role or "").strip()
            or (parent.parser_details_json or "").strip()
            or (parent.draft_reply or "").strip()
            or parent.ats_score is not None
        )
        parent.is_source_parent = True
        parent.is_multi_role_child = False
        parent.requirement_count = len(manifest_result.requirements)
        parent.sendability_status = "superseded_multi_role" if had_candidate_history else "source_parent"
        parent.draft_reply = ""
        parent.draft_source = None
        parent.resume_asset_id = None
        parent.resume_file_name = None
        db.flush()

        inherited_json = json.dumps(
            [constraint.model_dump(mode="json") for constraint in manifest_result.inherited_constraints],
            separators=(",", ":"),
        )
        active_keys = {item.requirement_key for item in manifest_result.requirements}
        existing_children = {
            child.requirement_key: child
            for child in db.query(RecruiterEmail)
            .filter(RecruiterEmail.source_parent_email_id == parent.id)
            .all()
            if child.requirement_key
        }

        child_ids: list[int] = []
        source_identity = (parent.external_message_id or f"source-{parent.id}").strip()
        for requirement in manifest_result.requirements:
            child = existing_children.get(requirement.requirement_key)
            if child is None:
                child = RecruiterEmail(
                    owner_id=parent.owner_id,
                    sender=parent.sender,
                    subject=parent.subject,
                    body=requirement.source_text,
                    role=requirement.title_hint,
                    location="unknown",
                    salary_text="not_specified",
                    skills_text="none_detected",
                    score=0,
                    decision="Pending eligibility",
                    state="needs_review",
                    source=parent.source,
                    external_message_id=f"{source_identity}:req:{requirement.requirement_key}",
                    external_thread_id=parent.external_thread_id,
                    external_rfc_message_id=parent.external_rfc_message_id,
                    gmail_received_at=parent.gmail_received_at,
                    recipient_email=parent.recipient_email,
                    cc_email=parent.cc_email,
                    routing_status=parent.routing_status,
                    routing_confidence=parent.routing_confidence,
                    routing_reason=parent.routing_reason,
                    routing_evidence=parent.routing_evidence,
                    routing_candidates=parent.routing_candidates,
                    routing_confirmed=parent.routing_confirmed,
                    source_parent_email_id=parent.id,
                    is_multi_role_child=True,
                    requirement_key=requirement.requirement_key,
                )
                db.add(child)
            elif child.state in TERMINAL_STATES:
                child_ids.append(child.id)
                continue

            child.requirement_index = requirement.index
            child.requirement_count = len(manifest_result.requirements)
            child.requirement_source_text = requirement.source_text
            child.inherited_constraints_json = inherited_json
            child.role_manifest_status = "multiple"
            child.role_manifest_confidence = parent.role_manifest_confidence
            child.sendability_status = None
            db.flush()
            child_ids.append(child.id)

        for key, child in existing_children.items():
            if key not in active_keys and child.state not in TERMINAL_STATES:
                child.sendability_status = "superseded_multi_role"

        db.commit()
        return ExpansionResult(
            source_parent_id=parent.id,
            manifest_status="multiple",
            requirement_count=len(manifest_result.requirements),
            child_ids=tuple(child_ids),
        )
