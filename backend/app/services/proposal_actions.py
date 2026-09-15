from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Mapping

from fastapi import Response
from sqlalchemy.orm import Session

from app.schemas import (
    ApplicationEventCreateRequest,
    ApplicationPatchRequest,
    AppTSApplicationCreateRequest,
    BulkApproveRequest,
    BulkRegenerateRequest,
    BulkRejectRequest,
    BulkReviewApplyRequest,
    BulkSendToFailedMappingRequest,
    BulkTrackRequest,
    ChatNewEmailRequest,
    ChatSendReplyRequest,
    GithubIssueCreateRequest,
    LabelThreadPromoteRequest,
    ManualPremiumContactRequest,
    ManualRequirementFromChatRequest,
    NvoidsClientSearchRequest,
    ProfileAppendRequest,
    ProfileDeleteRequest,
    ProfileReplaceFromAttachmentRequest,
    RecruiterNumberPatchRequest,
    RecruiterOpportunityPatchRequest,
    ResumeDraftCreateRequest,
    ResumeDraftSectionRequest,
    ScheduledTaskCreateRequest,
    ScheduledTaskPatchRequest,
)

ProposalFields = Mapping[str, object]
Summary = Callable[[ProposalFields], list[tuple[str, str]]]
ConfirmLabel = Callable[[ProposalFields], str]
Execute = Callable[[Session, ProposalFields], object]
Reversible = bool | Callable[[ProposalFields], bool]


@dataclass(frozen=True)
class ProposalAction:
    summary: Summary
    confirm_label: ConfirmLabel
    execute: Execute
    reversible: Reversible


def _record(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _number(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return 0


def _numbers(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [
        int(item)
        for item in value
        if isinstance(item, int) and not isinstance(item, bool)
    ]


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [shown for item in value if (shown := _text(item))]


def _candidate_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    rows = [
        ("Action", _text(fields.get("label"))),
        ("Affected emails", str(_number(fields.get("count")))),
    ]
    if fields.get("reversible_detail"):
        rows.append(("Detail", _text(fields["reversible_detail"])))
    if fields.get("roles"):
        rows.append(("Roles", ", ".join(_strings(fields["roles"]))))
    if fields.get("reason"):
        rows.append(("Reason", _text(fields["reason"])))
    dropped = fields.get("dropped")
    if isinstance(dropped, list) and dropped:
        rows.append(("Not included", f"{len(dropped)} email(s) skipped"))
    return rows


def _record_update_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    rows = [("Record", _text(fields.get("record_label")))]
    changes = fields.get("changes")
    if isinstance(changes, list):
        for item in changes:
            change = _record(item)
            before = _text(change.get("from")) or "(empty)"
            after = _text(change.get("to")) or "(empty)"
            rows.append((_text(change.get("field")), f"{before} -> {after}"))
    return rows


def _add_note_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    rows = [
        ("Record", _text(fields.get("record_label"))),
        ("Note", _text(fields.get("note"))),
    ]
    if fields.get("replaces") is True:
        rows.extend(
            [
                ("Existing note", _text(fields.get("existing_notes")) or "(none)"),
                ("Will be stored", _text(fields.get("combined_notes"))),
            ]
        )
    else:
        rows.append(("Appended as", "A new note event on the application"))
    rows.append(("Written by", "The assistant, from your records - check it before saving"))
    return rows


def _contact_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    values = _record(fields.get("fields"))
    rows = [
        ("Name", _text(values.get("name"))),
        ("Title", _text(values.get("title"))),
        ("Company", _text(values.get("company"))),
        ("Email", _text(values.get("email"))),
        ("Phone", _text(values.get("phone_display") or values.get("phone"))),
        ("Role", _text(values.get("role"))),
    ]
    if fields.get("duplicate_of_id"):
        rows.append(("Existing contact", f"ID {fields['duplicate_of_id']}"))
    return rows


def _manual_requirement_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    rows = [
        ("Source", _text(fields.get("source_label"))),
        ("Characters", f"{_number(fields.get('characters')):,}"),
    ]
    duplicate = _record(fields.get("duplicate_of"))
    if duplicate.get("id"):
        rows.append(
            (
                "Possible duplicate",
                f"#{_number(duplicate.get('id'))} - "
                f"{_text(duplicate.get('role')) or 'role unknown'} at "
                f"{_text(duplicate.get('client')) or 'client unknown'}, pasted "
                f"{_text(duplicate.get('created_at'))}. It will still be created.",
            )
        )
    rows.append(("Complete requirement that will be ingested", _text(fields.get("jd_text"))))
    return rows


def _nvoids_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    criteria = _record(fields.get("criteria"))
    composed = _text(criteria.get("query_mode")) != "end_client_only"
    rows = [("End client", _text(criteria.get("end_client")) or _text(fields.get("company")))]
    if composed and criteria.get("job_role"):
        rows.append(("Role", _text(criteria["job_role"])))
    if composed and criteria.get("search_location"):
        rows.append(("Location", _text(criteria["search_location"])))
    rows.extend(
        [
            ("Mode", "Composed - role and location applied" if composed else "End client only"),
            ("Batch limit", str(_number(criteria.get("batch_limit") or 10))),
            ("Query", _text(criteria.get("generated_query"))),
            ("Already stored", f"{_number(fields.get('already_stored'))} record(s) for this company"),
        ]
    )
    return rows


def _taxonomy_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    keys = _strings(fields.get("keys"))
    rows = [
        ("Action", f"{_text(fields.get('label'))} pending {_text(fields.get('scope'))} values"),
        ("Values", str(len(keys))),
        ("For example", ", ".join(_strings(fields.get("sample_names")))),
    ]
    if fields.get("reversible_detail"):
        rows.append(("Detail", _text(fields["reversible_detail"])))
    remaining = _number(fields.get("remaining_after_batch"))
    if remaining > 0:
        rows.append(("Not in this batch", f"{remaining} more - ask again to continue"))
    needs_human = _number(fields.get("needs_human_count"))
    if needs_human > 0:
        rows.append(("Left for you", f"{needs_human} undecided - use Bulk review in Settings"))
    if fields.get("model_error"):
        rows.append(("Model", _text(fields["model_error"])))
    return rows


def _email_summary(fields: ProposalFields, *, new_thread: bool) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if new_thread:
        rows.append(("Heads up", "This starts a new email thread."))
    elif fields.get("to_changed"):
        rows.append(("Heads up", "This goes to a different address than the thread."))
    unknown = _strings(fields.get("unknown_recipients"))
    if unknown:
        rows.append(("Not in your records", ", ".join(unknown)))
    rows.extend(
        [
            ("To", _text(fields.get("to"))),
            ("CC", "none" if fields.get("cc_changed") and not fields.get("cc") else _text(fields.get("cc"))),
            ("Subject", _text(fields.get("subject"))),
        ]
    )
    attachments = [*_strings(fields.get("document_names")), _text(fields.get("resume_name"))]
    if any(attachments):
        rows.append(("Attachments", ", ".join(value for value in attachments if value)))
    rows.append(("Body", _text(fields.get("body"))))
    return rows


def _scheduled_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    rows = [
        ("Task", _text(fields.get("title"))),
        ("Kind", _text(fields.get("kind"))),
        ("Runs", _text(fields.get("trigger"))),
    ]
    if fields.get("first_run"):
        rows.append(("First run", _text(fields["first_run"])))
    rows.append(("May do", _text(fields.get("permitted_actions"))))
    if fields.get("granularity_note"):
        rows.append(("Timing", _text(fields["granularity_note"])))
    if fields.get("reversible_detail"):
        rows.append(("Detail", _text(fields["reversible_detail"])))
    return rows


def _profile_summary(fields: ProposalFields) -> list[tuple[str, str]]:
    operation = _text(fields.get("operation"))
    if operation == "delete":
        return [
            ("Characters", f"{_number(fields.get('characters_before')):,}"),
            ("Complete profile that will be deleted", _text(fields.get("existing_profile"))),
        ]
    if operation == "replace":
        return [
            ("From file", _text(fields.get("source_file_name"))),
            (
                "Characters",
                f"{_number(fields.get('characters_before')):,} -> "
                f"{_number(fields.get('characters_after')):,}",
            ),
            ("Complete profile after replacing", _text(fields.get("resulting_profile"))),
        ]
    replaces = _strings(fields.get("replaces"))
    conflicts = _strings(fields.get("conflicts"))
    rows = [
        ("Field", _text(fields.get("field"))),
        ("Value", _text(fields.get("value"))),
    ]
    if replaces:
        rows.append(("Replacing", "\n".join(replaces)))
    rows.append(("Entry after this change" if replaces else "Entry added", _text(fields.get("entry"))))
    if conflicts:
        rows.append(
            (
                "Your uploaded text also says",
                "\n".join(conflicts)
                + '\n\nThis is left as it is; only the "Saved from chat" section changes.',
            )
        )
    rows.extend(
        [
            ("Complete profile after saving", _text(fields.get("resulting_profile"))),
            (
                "Saved as",
                {
                    "assistant_asked": "Your own words, from your answer",
                    "user_directed": "Your own words, from what you asked me to save",
                }.get(_text(fields.get("provenance")), "Your own words"),
            ),
        ]
    )
    return rows


def _main():
    from app import main

    return main


def _execute_candidate_action(db: Session, fields: ProposalFields) -> object:
    main = _main()
    ids = _numbers(fields.get("candidate_ids"))
    action = _text(fields.get("candidate_action"))
    if action == "reject":
        return main.reject_bulk(BulkRejectRequest(ids=ids, reason=_text(fields.get("reason")) or None), db)
    if action in {"track", "untrack"}:
        return main.track_bulk(BulkTrackRequest(ids=ids, tracked=fields.get("tracked") is True), db)
    if action == "regenerate":
        return main.regenerate_bulk_candidates(BulkRegenerateRequest(ids=ids), db)
    if action == "send_to_failed_mapping":
        return main.send_to_failed_mapping_bulk(BulkSendToFailedMappingRequest(ids=ids), db)
    raise ValueError("Unknown candidate action")


def _execute_bulk_approve(db: Session, fields: ProposalFields) -> object:
    return _main().approve_bulk_candidates(
        BulkApproveRequest(
            ids=_numbers(fields.get("candidate_ids")),
            idempotency_key=_text(fields.get("idempotency_key")) or None,
        ),
        db,
    )


def _execute_record_update(db: Session, fields: ProposalFields) -> object:
    main = _main()
    kind = _text(fields.get("record_kind"))
    record_id = _number(fields.get("record_id"))
    values = dict(_record(fields.get("fields")))
    if kind == "opportunity":
        return main.patch_recruiter_opportunity(record_id, RecruiterOpportunityPatchRequest(**values), db)
    if kind == "application":
        return main.patch_application(record_id, ApplicationPatchRequest(**values), db)
    if kind == "contact":
        return main.patch_recruiter_number(record_id, RecruiterNumberPatchRequest(**values), db)
    raise ValueError("Unknown record kind")


def _execute_track_record(db: Session, fields: ProposalFields) -> object:
    main = _main()
    values = _record(fields.get("fields"))
    response = Response()
    if fields.get("record_kind") == "label_thread":
        return main.promote_appts_label_thread(
            _text(fields.get("thread_id")),
            LabelThreadPromoteRequest(resume_asset_id=_number(values.get("resume_asset_id"))),
            response,
            db,
        )
    return main.create_appts_from_opportunity(AppTSApplicationCreateRequest(**dict(values)), response, db)


def _execute_add_note(db: Session, fields: ProposalFields) -> object:
    main = _main()
    record_id = _number(fields.get("record_id"))
    if fields.get("record_kind") == "application":
        return main.create_application_event(
            record_id,
            ApplicationEventCreateRequest(event_type="note", note=_text(fields.get("note"))),
            db,
        )
    return main.patch_recruiter_opportunity(
        record_id,
        RecruiterOpportunityPatchRequest(notes=_text(fields.get("combined_notes"))),
        db,
    )


def _execute_create_contact(db: Session, fields: ProposalFields) -> object:
    return _main().create_premium_contact(
        ManualPremiumContactRequest(**dict(_record(fields.get("fields")))),
        db,
    )


def _execute_manual_requirement(db: Session, fields: ProposalFields) -> object:
    duplicate = _record(fields.get("duplicate_of"))
    return _main().create_manual_requirement_from_chat(
        ManualRequirementFromChatRequest(
            attachment_id=fields.get("attachment_id"),
            message_id=fields.get("message_id"),
            acknowledged_duplicate_of=duplicate.get("id"),
        ),
        db,
    )


def _execute_nvoids_search(db: Session, fields: ProposalFields) -> object:
    criteria = _record(fields.get("criteria"))
    return _main().enqueue_nvoids_client_search(
        NvoidsClientSearchRequest(
            end_client=_text(criteria.get("end_client")) or _text(fields.get("company")),
            job_role=_text(criteria.get("job_role")),
            search_location=_text(criteria.get("search_location")),
            query_mode=_text(criteria.get("query_mode")) or "composed",
            batch_limit=_number(criteria.get("batch_limit")) or 10,
        ),
        db,
    )


def _execute_taxonomy_review(db: Session, fields: ProposalFields) -> object:
    keys = _strings(fields.get("keys"))
    return _main().apply_taxonomy_bulk_review(
        BulkReviewApplyRequest(
            scope=_text(fields.get("scope")),
            action=_text(fields.get("taxonomy_action")),
            keys=keys,
            expected_count=len(keys),
        ),
        db,
    )


def _execute_send_email(db: Session, fields: ProposalFields) -> object:
    return _main().send_chat_reply(
        _number(fields.get("candidate_email_id")),
        ChatSendReplyRequest(
            body=_text(fields.get("body")),
            subject=_text(fields.get("subject")),
            to=_text(fields.get("to")) if fields.get("to") is not None else None,
            cc=_text(fields.get("cc")) if fields.get("cc") is not None else None,
            resume_id=_number(fields.get("resume_id")),
            document_ids=_numbers(fields.get("document_ids")),
            confirm_new_recipients=fields.get("requires_recipient_confirmation") is True,
        ),
        db,
    )


def _execute_new_email(db: Session, fields: ProposalFields) -> object:
    return _main().send_chat_new_email(
        ChatNewEmailRequest(
            to=_text(fields.get("to")),
            cc=_text(fields.get("cc")),
            subject=_text(fields.get("subject")),
            body=_text(fields.get("body")),
            resume_id=_number(fields.get("resume_id")),
            document_ids=_numbers(fields.get("document_ids")),
            confirm_new_recipients=fields.get("requires_recipient_confirmation") is True,
        ),
        db,
    )


def _execute_scheduled_task(db: Session, fields: ProposalFields) -> object:
    main = _main()
    operation = _text(fields.get("operation"))
    if operation == "create":
        return main.create_scheduled_task(
            ScheduledTaskCreateRequest(
                title=_text(fields.get("title")),
                kind=_text(fields.get("kind")) or "reminder",
                when=_text(fields.get("when_phrase")),
                note=_text(fields.get("note")),
                subject_type=_text(fields.get("subject_type")),
                subject_id=_text(fields.get("subject_id")),
            ),
            db,
        )
    task_id = _number(fields.get("task_id"))
    if operation == "delete":
        return main.delete_scheduled_task(task_id, db)
    return main.patch_scheduled_task(task_id, ScheduledTaskPatchRequest(operation=operation), db)


def _execute_profile_update(db: Session, fields: ProposalFields) -> object:
    main = _main()
    operation = _text(fields.get("operation"))
    if operation == "append":
        return main.append_candidate_profile_entry(
            ProfileAppendRequest(
                entry=_text(fields.get("entry")),
                base_sha256=_text(fields.get("base_sha256")),
            ),
            db,
        )
    if operation == "replace":
        return main.replace_candidate_profile_from_attachment(
            ProfileReplaceFromAttachmentRequest(
                attachment_id=_number(fields.get("attachment_id")),
                base_sha256=_text(fields.get("base_sha256")),
            ),
            db,
        )
    if operation == "delete":
        return main.delete_candidate_profile(
            ProfileDeleteRequest(base_sha256=_text(fields.get("base_sha256")) or None),
            db,
        )
    raise ValueError("Unknown profile operation")


def _execute_resume_draft(db: Session, fields: ProposalFields) -> object:
    from app.routers import resume_editor

    return resume_editor.create_draft(
        ResumeDraftCreateRequest(
            name=_text(fields.get("name")),
            source_resume_id=(
                None if fields.get("from_scratch") is True else _number(fields.get("source_resume_id"))
            ),
            content_markdown=(
                _text(fields.get("initial_content")) if fields.get("from_scratch") is True else ""
            ),
        ),
        db,
    )


def _execute_resume_section(db: Session, fields: ProposalFields) -> object:
    from app.routers import resume_editor

    return resume_editor.replace_draft_section(
        _number(fields.get("draft_id")),
        ResumeDraftSectionRequest(
            section=_text(fields.get("section")),
            replacement=_text(fields.get("replacement")),
            base_sha256=_text(fields.get("base_sha256")),
        ),
        db,
    )


def _execute_github_issue(_db: Session, fields: ProposalFields) -> object:
    return _main().create_support_github_issue(
        GithubIssueCreateRequest(
            title=_text(fields.get("title")),
            user_report=_text(fields.get("user_report")),
            ai_summary=_text(fields.get("ai_summary")),
            context=_text(fields.get("context")),
        )
    )


def _label(value: str) -> ConfirmLabel:
    return lambda _fields: value


def _payload_reversible(fields: ProposalFields) -> bool:
    return fields.get("reversible") is True


PROPOSAL_ACTIONS: dict[str, ProposalAction] = {
    "propose_candidate_action": ProposalAction(
        summary=_candidate_summary,
        confirm_label=lambda fields: f"{_text(fields.get('label')) or 'Apply'} {_number(fields.get('count'))}",
        execute=_execute_candidate_action,
        reversible=_payload_reversible,
    ),
    "propose_bulk_approve_candidates": ProposalAction(
        summary=lambda fields: [
            ("Action", "Approve and send candidate emails"),
            ("Email IDs", ", ".join(str(value) for value in _numbers(fields.get("candidate_ids")))),
        ],
        confirm_label=lambda fields: f"Approve {_number(fields.get('count'))} Emails",
        execute=_execute_bulk_approve,
        reversible=False,
    ),
    "propose_record_update": ProposalAction(
        summary=_record_update_summary,
        confirm_label=lambda fields: f"Update {_text(fields.get('record_kind')) or 'record'}",
        execute=_execute_record_update,
        reversible=False,
    ),
    "propose_track_record": ProposalAction(
        summary=lambda fields: [
            ("Subject", _text(fields.get("record_label"))),
            ("Recruiter", _text(fields.get("recruiter"))),
            ("Labels", ", ".join(_strings(fields.get("labels")))),
            ("Resume", _text(fields.get("resume"))),
        ],
        confirm_label=_label("Confirm & Track"),
        execute=_execute_track_record,
        reversible=False,
    ),
    "propose_add_note": ProposalAction(
        summary=_add_note_summary,
        confirm_label=_label("Add Note"),
        execute=_execute_add_note,
        reversible=False,
    ),
    "propose_create_premium_contact": ProposalAction(
        summary=_contact_summary,
        confirm_label=_label("Save Contact"),
        execute=_execute_create_contact,
        reversible=False,
    ),
    "propose_manual_requirement": ProposalAction(
        summary=_manual_requirement_summary,
        confirm_label=_label("Add to Needs Review"),
        execute=_execute_manual_requirement,
        reversible=False,
    ),
    "propose_nvoids_search": ProposalAction(
        summary=_nvoids_summary,
        confirm_label=lambda fields: f"Search Nvoids for {_text(fields.get('company')) or 'this company'}",
        execute=_execute_nvoids_search,
        reversible=False,
    ),
    "propose_taxonomy_bulk_review": ProposalAction(
        summary=_taxonomy_summary,
        confirm_label=lambda fields: (
            f"{_text(fields.get('label')) or 'Apply'} {len(_strings(fields.get('keys')))} "
            f"{_text(fields.get('scope'))} value(s)"
        ),
        execute=_execute_taxonomy_review,
        reversible=_payload_reversible,
    ),
    "propose_send_email": ProposalAction(
        summary=lambda fields: _email_summary(fields, new_thread=False),
        confirm_label=_label("Send Email"),
        execute=_execute_send_email,
        reversible=False,
    ),
    "propose_new_email": ProposalAction(
        summary=lambda fields: _email_summary(fields, new_thread=True),
        confirm_label=_label("Send Email"),
        execute=_execute_new_email,
        reversible=False,
    ),
    "propose_scheduled_task": ProposalAction(
        summary=_scheduled_summary,
        confirm_label=lambda fields: {
            "create": "Create Task",
            "pause": "Pause Task",
            "resume": "Resume Task",
            "edit": "Save Changes",
            "delete": "Delete Task",
        }.get(_text(fields.get("operation")), "Confirm"),
        execute=_execute_scheduled_task,
        reversible=_payload_reversible,
    ),
    "propose_profile_update": ProposalAction(
        summary=_profile_summary,
        confirm_label=lambda fields: (
            "Update Profile"
            if fields.get("operation") == "append" and _strings(fields.get("replaces"))
            else {
                "append": "Save to Profile",
                "replace": "Replace Profile",
                "delete": "Delete Profile",
            }.get(_text(fields.get("operation")), "Confirm")
        ),
        execute=_execute_profile_update,
        reversible=False,
    ),
    "propose_resume_draft": ProposalAction(
        summary=lambda fields: [
            ("Draft name", _text(fields.get("name"))),
            *(
                [("Initial draft", _text(fields.get("initial_content")))]
                if fields.get("from_scratch") is True
                else []
            ),
            (
                "Copied from",
                " - ".join(
                    value
                    for value in (
                        _text(fields.get("source_variant_code")),
                        _text(fields.get("source_file_name")),
                    )
                    if value
                ),
            ),
            ("Text", f"{_number(fields.get('source_characters')):,} characters"),
        ],
        confirm_label=_label("Create Draft"),
        execute=_execute_resume_draft,
        reversible=False,
    ),
    "propose_resume_section": ProposalAction(
        summary=lambda fields: [
            ("Draft", _text(fields.get("draft_name"))),
            ("Section", _text(fields.get("section"))),
            ("Now", _text(fields.get("current"))),
            ("Becomes", _text(fields.get("replacement"))),
        ],
        confirm_label=_label("Apply Rewrite"),
        execute=_execute_resume_section,
        reversible=False,
    ),
    "propose_create_github_issue": ProposalAction(
        summary=lambda fields: [
            ("Title", _text(fields.get("title"))),
            ("Your report", _text(fields.get("user_report"))),
            ("AI summary", _text(fields.get("ai_summary"))),
            *([("Context", _text(fields.get("context")))] if fields.get("context") else []),
        ],
        confirm_label=_label("Create GitHub Issue"),
        execute=_execute_github_issue,
        reversible=False,
    ),
}
