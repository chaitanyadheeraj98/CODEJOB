from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.ai.draft_quality import assess_draft_quality
from app.db import Base, UTCDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


class BulkActionIdempotencyKey(Base):
    __tablename__ = "bulk_action_idempotency_keys"

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class RecruiterEmail(Base):
    __tablename__ = "recruiter_emails"
    __table_args__ = (
        UniqueConstraint("source_parent_email_id", "requirement_key", name="ux_recruiter_email_parent_requirement"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    sender: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, default="")
    # Provenance for `role`. NULL means "written before provenance existed" and must
    # be read as unverified - never assume "extracted". See services/role_provenance.py.
    role_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Collapsed taxonomy title for aggregation ("Java Developer"), kept separate so
    # `role` can stay specific for the draft copy that interpolates it. String(255),
    # never Text: this one is indexed, and an unbounded indexed column blew the
    # Postgres btree key limit once already (migration 0051).
    role_canonical: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # The role family this JD was classified into, plus how sure the classifier was
    # and which vocabulary decided it. Previously this answer only ever existed
    # inside resume_picker_breakdown_json, so a JD with no resume selection had no
    # family at all and reclassifying meant rescoring every email.
    #
    # NULL means "never classified" and must NOT be read as "general" - the same
    # convention role_source establishes above. String(40) and indexed for the same
    # reason as role_canonical: bounded varchars are safe to index, unbounded Text
    # is what took the backend down on boot.
    role_family: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    role_family_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "<system>:<version>", e.g. "builtin:1". Swapping in an external occupation
    # standard later is then a value change, not another migration.
    role_family_taxonomy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    location: Mapped[str] = mapped_column(String(255), default="")
    salary_text: Mapped[str] = mapped_column(String(255), default="")
    skills_text: Mapped[str] = mapped_column(Text, default="")
    skills_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String(50), index=True, default="auto_rejected")
    state: Mapped[str] = mapped_column(String(50), index=True, default="auto_rejected")
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_filter_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_reject_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_score_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ats_score_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ats_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_picker_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resume_picker_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_picker_candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_picker_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_input_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    semantic_input_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semantic_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semantic_fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    thread_snapshot_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    thread_snapshot_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    intent_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_negative_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gate_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Why the gate fell back. Short slugs only (`deepseek_invalid_shape`,
    # `groq_timeout`), no index - this is for diagnosis, not filtering. Without it
    # the provider column can show *that* half the calls degraded to the taxonomy
    # but never *why*, which is how a 12-day systematic failure went unnoticed.
    gate_error: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_match_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_trusted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qualification_result: Mapped[str | None] = mapped_column(String(80), nullable=True)
    blocking_rule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qualification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_batch_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    draft_reply: Mapped[str] = mapped_column(Text, default="")
    draft_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    draft_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    draft_ai_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_resume_context_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    semantic_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marked_for_tracking: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_recruiter_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    resolved_recruiter_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    approval_status: Mapped[str] = mapped_column(String(50), default="pending")
    sent_status: Mapped[str] = mapped_column(String(50), default="not_sent")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    # The normalized-content fingerprint of a pasted requirement, so a second
    # paste of the same text can be recognised. Set on manual-intake rows only;
    # NULL for gmail and nvoids, which dedupe on their own delivery identity.
    # Bounded and fixed-width so the index is safe: migration 0051 took prod down
    # with an index over an unbounded Text column, and `alembic upgrade head`
    # runs on backend boot.
    manual_dedupe_hash: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gmail_received_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    applied_gmail_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    applied_gmail_label_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    applied_gmail_label_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cc_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_status: Mapped[str] = mapped_column(String(50), default="unverified")
    routing_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    routing_evidence: Mapped[str] = mapped_column(Text, default="[]")
    routing_candidates: Mapped[str] = mapped_column(Text, default="[]")
    routing_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    resume_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parser_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Provenance for company/location, same contract as role_source: NULL means the
    # value predates tracking (or came straight from the parser) and is unverified.
    # Set to "taxonomy_matched" only when an approved vocabulary entry filled a gap
    # the parser left empty - so a matched value is never mistaken for an extracted
    # one, which is the failure this whole mechanism exists to prevent.
    company_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    location_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    end_client: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    implementation_partner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    domain_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    interview_type: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    screening_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_parent_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    is_source_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_multi_role_child: Mapped[bool] = mapped_column(Boolean, default=False)
    requirement_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requirement_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    inherited_constraints_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_manifest_status: Mapped[str] = mapped_column(String(40), default="not_run")
    role_manifest_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    role_manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_manifest_diagnostics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    eligibility_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    eligibility_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sendability_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    gmail_sent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_attachment_file_names_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("candidate_records.id", name="fk_recruiter_emails_record"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    @property
    def gmail_message_url(self) -> str | None:
        if self.source != "gmail":
            return None
        if self.external_rfc_message_id:
            query = quote(f"rfc822msgid:{self.external_rfc_message_id}", safe="")
            return f"https://mail.google.com/mail/u/0/#search/{query}"
        # Prefer message id for precise targeting, fallback to thread id.
        token = self.external_message_id or self.external_thread_id
        if not token:
            return None
        return f"https://mail.google.com/mail/u/0/#all/{token}"

    @property
    def gmail_sent_message_url(self) -> str | None:
        token = (self.gmail_sent_id or "").strip()
        if not token:
            return None
        return f"https://mail.google.com/mail/u/0/#all/{quote(token, safe='')}"

    @property
    def draft_quality(self) -> dict[str, object]:
        return assess_draft_quality(
            draft_text=self.draft_reply or "",
            ai_score=self.ai_score,
            routing_confidence=self.routing_confidence,
            resume_context_status=self.draft_resume_context_status,
            recipient_email=self.recipient_email,
            cc_email=self.cc_email,
            draft_ai_error=self.draft_ai_error,
        ).to_payload()


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    gmail_query: Mapped[str] = mapped_column(Text, default="is:unread in:inbox recruiter")
    default_gmail_query: Mapped[str] = mapped_column(Text, default="is:unread in:inbox recruiter")
    saved_gmail_queries_json: Mapped[str] = mapped_column(Text, default="[]")
    mail_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    default_date_mode: Mapped[str] = mapped_column(String(20), default="today")
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepted_locations: Mapped[str] = mapped_column(Text, default="")
    visa_required_allowed: Mapped[bool] = mapped_column(default=False)
    remote_preference: Mapped[str] = mapped_column(String(50), default="any")
    role_keywords: Mapped[str] = mapped_column(Text, default="")
    must_have_skills: Mapped[str] = mapped_column(Text, default="")
    employer_domains: Mapped[str] = mapped_column(Text, default="")
    free_text_guidance: Mapped[str] = mapped_column(Text, default="")
    qualification_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    feature_auto_polling: Mapped[bool] = mapped_column(default=False)
    feature_auto_poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=10)
    feature_nvoids_enabled: Mapped[bool] = mapped_column(default=True)
    feature_nvoids_auto_sync: Mapped[bool] = mapped_column(default=False)
    feature_nvoids_poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    nvoids_batch_limit: Mapped[int] = mapped_column(Integer, default=10)
    nvoids_detail_title_mode: Mapped[str] = mapped_column(String(40), default="job_details")
    nvoids_locations: Mapped[str] = mapped_column(Text, default="")
    nvoids_job_role: Mapped[str] = mapped_column(Text, default="")
    nvoids_search_location: Mapped[str] = mapped_column(Text, default="")
    nvoids_custom_query: Mapped[str] = mapped_column(Text, default="")
    nvoids_end_client: Mapped[str] = mapped_column(Text, default="")
    # "composed" narrows an existing search; "end_client_only" is for discovery.
    # Composing role AND location AND client collapses hard - java 3,406 rows,
    # java+Texas 894, java+Texas+Citi 2 - so both modes exist (temp162.md §16.5).
    nvoids_query_mode: Mapped[str] = mapped_column(String(40), default="composed")
    feature_auto_send: Mapped[bool] = mapped_column(default=False)
    feature_retry_queue: Mapped[bool] = mapped_column(default=False)
    feature_ai_enabled: Mapped[bool] = mapped_column(default=False)
    feature_ai_extractor_enabled: Mapped[bool] = mapped_column(default=False)
    feature_semantic_enabled: Mapped[bool] = mapped_column(default=False)
    feature_groq_job_parser_enabled: Mapped[bool] = mapped_column(default=False)
    feature_gmail_requirement_groups_enabled: Mapped[bool] = mapped_column(default=False)
    feature_role_manifest_enabled: Mapped[bool] = mapped_column(default=False)
    feature_strict_candidate_screening_enabled: Mapped[bool] = mapped_column(default=False)
    feature_email_tracking_enabled: Mapped[bool] = mapped_column(default=False)
    feature_reply_inbox_enabled: Mapped[bool] = mapped_column(default=False)
    feature_applications_enabled: Mapped[bool] = mapped_column(default=False)
    feature_application_automation_enabled: Mapped[bool] = mapped_column(default=False)
    feature_application_outreach_drafts_enabled: Mapped[bool] = mapped_column(default=False)
    feature_reminder_sweep_interval_minutes: Mapped[int] = mapped_column(Integer, default=240)
    feature_resume_tracking_enabled: Mapped[bool] = mapped_column(default=False)
    feature_resume_tracking_sweep_interval_minutes: Mapped[int] = mapped_column(Integer, default=240)
    # Stamps an invisible "CJ-R14-8842" marker into the HTML part of outgoing mail so a
    # phoned-in callback can be traced to an exact resume variant and send. Defaults on
    # because it shipped that way; turning it off stops new sends carrying it.
    feature_resume_variant_marker_enabled: Mapped[bool] = mapped_column(default=True)
    candidate_work_authorizations_json: Mapped[str] = mapped_column(Text, default="[]")
    preferred_employment_types_json: Mapped[str] = mapped_column(Text, default="[]")
    visible_filters_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    preferred_minimum_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_total_experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_us_experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_current_location: Mapped[str] = mapped_column(String(255), default="")
    # The user's own account of themselves, in Markdown, uploaded as a file. The
    # structured columns above are what the *screening* rules read; this is what
    # the assistant reads when it has to write **as** the user - visa, notice
    # period, passport, rate - so it is free text on purpose and never parsed.
    # The text is stored here rather than on disk: the prompt needs the content
    # on every chat turn, and a file path would be a second source of truth.
    candidate_profile_markdown: Mapped[str] = mapped_column(Text, default="")
    # Both describe the upload rather than the profile, and exist so the panel
    # can say which file is loaded and how stale it is - a profile is set once
    # and then forgotten, which is exactly when a visa date goes wrong.
    candidate_profile_filename: Mapped[str] = mapped_column(String(255), default="")
    candidate_profile_uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    draft_text_size: Mapped[str] = mapped_column(String(20), default="normal")
    fallback_draft_template: Mapped[str] = mapped_column(Text, default="")
    signature_name: Mapped[str] = mapped_column(String(255), default="")
    signature_phone: Mapped[str] = mapped_column(String(80), default="")
    signature_email: Mapped[str] = mapped_column(String(255), default="")
    preferred_employer_cc_email: Mapped[str] = mapped_column(String(255), default="")
    preferred_employer_cc_emails: Mapped[str] = mapped_column(Text, default="")
    default_employer_cc_emails: Mapped[str] = mapped_column(Text, default="")
    resume_display_name: Mapped[str] = mapped_column(String(255), default="")
    policy_json: Mapped[str] = mapped_column(Text, default="")
    # An IANA zone name, not an offset. Offsets do not survive DST, and every
    # wall-clock schedule in the product ("every weekday at 9am") is meaningless
    # without one. Records stay naive-UTC via UTCDateTime; this interprets
    # schedules, it does not change how timestamps are stored.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    feature_scheduling_sweep_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ResumeAsset(Base):
    __tablename__ = "resume_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/pdf")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    skills_text: Mapped[str] = mapped_column(Text, default="")
    primary_role: Mapped[str] = mapped_column(String(255), default='')
    structured_skills_json: Mapped[str] = mapped_column(Text, default='[]')
    variant_label: Mapped[str] = mapped_column(String(120), default='')
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_current: Mapped[bool] = mapped_column(default=True)
    semantic_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class GmailRequirementGroup(Base):
    __tablename__ = "gmail_requirement_groups"
    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_group_email", name="ux_gmail_requirement_groups_owner_normalized"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    group_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    normalized_group_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    group_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class AttachmentAsset(Base):
    __tablename__ = "attachment_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class CandidateDocument(Base):
    """A document the user keeps on file to attach to a mail on request.

    Distinct from AttachmentAsset, which the automatic pipeline sends with
    *every* draft it approves and which is therefore an all-or-nothing switch.
    These are chosen one mail at a time - "attach the passport and the W2" - so
    they carry no enabled flag: nothing is ever sent unless the user names it and
    then confirms the send.

    The bytes live on disk and only the path is stored, because a passport scan
    is attached verbatim and never read, summarised, or put in a prompt.
    """

    __tablename__ = "candidate_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    # What the user calls it, which is rarely what the file is called. The
    # assistant matches "attach my passport" against this first, so a scan saved
    # as `CD_scan_0412.pdf` is still reachable by the name the user would say.
    label: Mapped[str] = mapped_column(String(120), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class CustomSkillTaxonomyEntry(Base):
    __tablename__ = "custom_skill_taxonomy_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    canonical_name: Mapped[str] = mapped_column(String(1000), index=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    category: Mapped[str] = mapped_column(String(120), default="custom")
    cluster_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    match_tier: Mapped[str] = mapped_column(String(40), default="supporting")
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class EmailOpenEvent(Base):
    __tablename__ = "email_open_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    recruiter_email_id: Mapped[int] = mapped_column(Integer, index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    user_agent: Mapped[str] = mapped_column(Text, default="")
    remote_ip: Mapped[str] = mapped_column(String(100), default="")
    is_likely_proxy: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailConversation(Base):
    __tablename__ = "email_conversations"
    __table_args__ = (
        UniqueConstraint("owner_id", "external_thread_id", name="ux_email_conversation_owner_thread"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    root_recruiter_email_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("recruiter_emails.id", name="fk_email_conversations_root_recruiter_email", ondelete="RESTRICT"),
        index=True,
    )
    external_thread_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(40), default="sent", index=True)
    last_message_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    unread_reply_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class EmailReplyMessage(Base):
    __tablename__ = "email_reply_messages"
    __table_args__ = (
        UniqueConstraint("owner_id", "external_message_id", name="ux_email_reply_owner_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    conversation_id: Mapped[int] = mapped_column(Integer, index=True)
    direction: Mapped[str] = mapped_column(String(20), default="inbound", index=True)
    external_message_id: Mapped[str] = mapped_column(String(255), index=True)
    external_rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    in_reply_to_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_call_args: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ChatAttachment(Base):
    """A file the user attached to a chat message.

    Distinct from AttachmentAsset, which is a file sent *out* with recruiter
    email. Different lifecycle, different trust posture: this one is parsed and
    read back to the model.
    """

    __tablename__ = "chat_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    # Null until the message that carries it is created - the upload happens first.
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"), nullable=True, index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class CanonicalEntityTaxonomyEntry(Base):
    __tablename__ = "canonical_entity_taxonomy_entries"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "entity_type",
            "canonical_name",
            name="ux_canonical_entity_owner_type_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class JobIntentTaxonomyEntry(Base):
    __tablename__ = "job_intent_taxonomy_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    phrase: Mapped[str] = mapped_column(String(255), index=True)
    normalized_phrase: Mapped[str] = mapped_column(String(255), index=True)
    polarity: Mapped[str] = mapped_column(String(80), index=True)
    source_examples_count: Mapped[int] = mapped_column(Integer, default=0)
    sample_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence_aggregate: Mapped[float] = mapped_column(Float, default=0.0)
    last_intent_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    sync_batch_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class RecentRun(Base):
    __tablename__ = "recent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    run_source: Mapped[str] = mapped_column(String(40), index=True)
    run_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    sync_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    external_scrape_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="ok")
    detail: Mapped[str] = mapped_column(Text, default="")
    matched_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_item_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    requirement_count: Mapped[int] = mapped_column(Integer, default=0)
    multi_role_source_count: Mapped[int] = mapped_column(Integer, default=0)
    manifest_review_count: Mapped[int] = mapped_column(Integer, default=0)
    job_backend_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    total_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    progress_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_name: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class RecentRunSkippedItem(Base):
    __tablename__ = "recent_run_skipped_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    run_source: Mapped[str] = mapped_column(String(40), index=True)
    run_key: Mapped[str] = mapped_column(String(160), index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="gmail", index=True)
    outcome: Mapped[str] = mapped_column(String(40), default="skipped", index=True)
    reason_code: Mapped[str] = mapped_column(String(120), default="", index=True)
    reason_detail: Mapped[str] = mapped_column(Text, default="")
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    candidate_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    external_opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title_or_subject: Mapped[str] = mapped_column(Text, default="")
    sender: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    gmail_message_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    intent_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_negative_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gate_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Why the gate fell back. Short slugs only (`deepseek_invalid_shape`,
    # `groq_timeout`), no index - this is for diagnosis, not filtering. Without it
    # the provider column can show *that* half the calls degraded to the taxonomy
    # but never *why*, which is how a 12-day systematic failure went unnoticed.
    gate_error: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_match_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_trusted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qualification_result: Mapped[str | None] = mapped_column(String(80), nullable=True)
    blocking_rule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qualification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class DraftEditFeedback(Base):
    __tablename__ = "draft_edit_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_draft: Mapped[str] = mapped_column(Text)
    edited_draft: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class RecipientRoutingFeedback(Base):
    __tablename__ = "recipient_routing_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    sender_domain: Mapped[str] = mapped_column(String(255), index=True)
    corrected_to: Mapped[str] = mapped_column(String(255))
    corrected_cc: Mapped[str] = mapped_column(String(255))
    sample_sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_to_present: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_cc_present: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class PremiumNumberLead(Base):
    __tablename__ = "premium_number_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    external_opportunity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "external_opportunities.id",
            name="fk_premium_number_leads_external_opportunity",
        ),
        nullable=True,
        index=True,
    )
    contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("premium_number_contacts.id", name="fk_premium_number_leads_contact"),
        nullable=True,
        index=True,
    )
    phone_number_normalized: Mapped[str] = mapped_column(String(40), index=True)
    phone_number_display: Mapped[str] = mapped_column(String(80))
    phone_extension: Mapped[str] = mapped_column(String(10), default="")
    role: Mapped[str] = mapped_column(String(20), default="recruiter")
    extraction_source: Mapped[str] = mapped_column(String(50), default="ai")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    owner_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    designation: Mapped[str] = mapped_column(String(255), default="Unknown")
    purpose: Mapped[str] = mapped_column(String(255), default="Recruiter contact")
    confidence: Mapped[str] = mapped_column(String(10), default="low")
    contact_type: Mapped[str] = mapped_column(String(40), default="unknown")
    recruiter_relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    is_recruiter_relevant: Mapped[bool] = mapped_column(Boolean, default=False)
    relevance_reason: Mapped[str] = mapped_column(String(255), default="")
    source_fragment: Mapped[str] = mapped_column(Text, default="")
    source_email_sender: Mapped[str] = mapped_column(String(255), default="")
    source_email_subject: Mapped[str] = mapped_column(String(500), default="")
    source_email_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    source_section: Mapped[str | None] = mapped_column(String(20), nullable=True)
    block_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_offset_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_offset_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    colocation_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class PremiumNumberExtractionAudit(Base):
    __tablename__ = "premium_number_extraction_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_external_opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    raw_value: Mapped[str] = mapped_column(String(120))
    normalized_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class RecruiterNumber(Base):
    __tablename__ = "recruiter_numbers"
    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_phone_number", name="ux_recruiter_numbers_owner_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_phone_number: Mapped[str] = mapped_column(String(40), index=True)
    display_phone_number: Mapped[str] = mapped_column(String(80))
    recruiter_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    designation: Mapped[str] = mapped_column(String(255), default="Unknown")
    recruiter_email: Mapped[str] = mapped_column(String(255), default="")
    first_detected_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class EmployerNumber(Base):
    __tablename__ = "employer_numbers"
    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_phone_number", name="ux_employer_numbers_owner_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_phone_number: Mapped[str] = mapped_column(String(40), index=True)
    display_phone_number: Mapped[str] = mapped_column(String(80))
    owner_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class PremiumNumberContact(Base):
    __tablename__ = "premium_number_contacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "normalized_phone_number",
            "phone_extension",
            name="ux_premium_number_contacts_owner_phone",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    display_phone_number: Mapped[str] = mapped_column(String(80))
    phone_extension: Mapped[str] = mapped_column(String(10), default="")
    phone_is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    is_recruiter: Mapped[bool] = mapped_column(Boolean, default=False)
    is_employer: Mapped[bool] = mapped_column(Boolean, default=False)
    recruiter_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    designation: Mapped[str] = mapped_column(String(255), default="Unknown")
    recruiter_email: Mapped[str] = mapped_column(String(255), default="")
    recruiter_email_domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    owner_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    employer_email: Mapped[str] = mapped_column(String(255), default="")
    employer_email_domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    secondary_company: Mapped[str] = mapped_column(String(255), default="")
    first_detected_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_recruiter_lead_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "premium_number_leads.id",
            name="fk_premium_number_contacts_active_recruiter_lead",
            use_alter=True,
        ),
        nullable=True,
    )
    active_employer_lead_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "premium_number_leads.id",
            name="fk_premium_number_contacts_active_employer_lead",
            use_alter=True,
        ),
        nullable=True,
    )
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    recruiter_verification_level: Mapped[str] = mapped_column(String(20), default="unverified")
    do_not_work_again: Mapped[bool] = mapped_column(Boolean, default=False)
    do_not_work_again_reason: Mapped[str] = mapped_column(Text, default="")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_link_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class PremiumContactEmail(Base):
    __tablename__ = "premium_contact_emails"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_email", name="ux_premium_contact_emails_owner_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    premium_contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("premium_number_contacts.id"), index=True)
    normalized_email: Mapped[str] = mapped_column(String(255), index=True)
    domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    # Which headline column this address feeds. A dual-role contact has two of them, so
    # without this the write-through sync in contact_identity_service is ambiguous.
    role: Mapped[str] = mapped_column(String(20), default="recruiter")
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class PremiumContactPhone(Base):
    __tablename__ = "premium_contact_phones"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_phone_number", "phone_extension", name="ux_premium_contact_phones_owner_phone"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    premium_contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("premium_number_contacts.id"), index=True)
    normalized_phone_number: Mapped[str] = mapped_column(String(40), index=True)
    phone_extension: Mapped[str] = mapped_column(String(10), default="")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(50), default="unknown")
    label: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class RecruiterOpportunity(Base):
    __tablename__ = "recruiter_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "recruiter_number_id",
            "gmail_message_id",
            name="ux_recruiter_opportunities_owner_recruiter_msg",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    # Fossil name: this targets premium_number_contacts.id, not the removed RecruiterNumber model.
    recruiter_number_id: Mapped[int] = mapped_column(Integer, index=True)
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gmail_message_id: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="gmail", index=True)
    source_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    external_opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    email_subject: Mapped[str] = mapped_column(String(500), default="")
    email_sender: Mapped[str] = mapped_column(String(255), default="")
    gmail_open_url: Mapped[str] = mapped_column(String(1000), default="")
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    job_title: Mapped[str] = mapped_column(Text, default="")
    end_client: Mapped[str] = mapped_column(Text, default="")
    # Transitional Python alias for callers migrating from the pre-unification name.
    client = synonym("end_client")
    location: Mapped[str] = mapped_column(String(255), default="")
    work_mode: Mapped[str] = mapped_column(String(80), default="")
    visa_restrictions: Mapped[str] = mapped_column(String(255), default="")
    resume_file_name: Mapped[str] = mapped_column(String(255), default="")
    resume_asset_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("resume_assets.id", name="fk_recruiter_opportunities_resume_asset", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    implementation_partner: Mapped[str] = mapped_column(String(255), default="")
    prime_vendor: Mapped[str] = mapped_column(String(255), default="")
    domain: Mapped[str] = mapped_column(String(255), default="")
    extracted_skills: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="New")
    notes: Mapped[str] = mapped_column(Text, default="")
    employment_type: Mapped[str] = mapped_column(String(40), default="")
    rate_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_currency: Mapped[str] = mapped_column(String(10), default="USD")
    rate_unit: Mapped[str] = mapped_column(String(20), default="")
    contract_duration: Mapped[str] = mapped_column(String(120), default="")
    relocation_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    extension_likely: Mapped[str] = mapped_column(String(20), default="unknown")
    end_client_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    job_confidence: Mapped[str] = mapped_column(String(20), default="unknown")
    cold_call_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    cold_call_script_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    record_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "candidate_records.id",
            name="fk_recruiter_opportunities_record",
            # Breaks the recruiter_opportunities -> candidate_records ->
            # opportunity_lineages -> recruiter_opportunities FK cycle for
            # Base.metadata.create_all()/drop_all() (used by unit tests), which
            # can't otherwise topologically sort the three tables. Matches
            # production reality: migration 20260826_0032 already adds this exact
            # constraint via a separate ALTER after all three tables exist, not
            # inline at CREATE TABLE time - use_alter just tells the ORM-level
            # DDL sorter the same thing. No migration or schema change implied.
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


APPLICATION_STATUS_VALUES = (
    "matched",
    "contacted",
    "recruiter_responded",
    "resume_shared",
    "rtr_requested",
    "rtr_confirmed",
    "submitted_to_client",
    "client_reviewing",
    "interview_1",
    "interview_2",
    "final_interview",
    "offer",
    "hired",
    "rejected",
    "withdrawn",
    "no_response",
    "position_closed",
    "duplicate",
)

APPLICATION_CLOSED_STATUS_VALUES = (
    "hired",
    "rejected",
    "withdrawn",
    "no_response",
    "position_closed",
    "duplicate",
)

RESUME_SUBMISSION_STATUS_VALUES = (
    'not_submitted',
    'submitted',
    'viewed',
    'shortlisted',
    'interview_scheduled',
    'offered',
    'hired',
    'rejected',
    'withdrawn',
)
SUBMISSION_METHOD_VALUES = ('email',)
REJECTION_DETAIL_TAG_VALUES = (
    'missing_skill',
    'missing_experience',
    'missing_domain_knowledge',
    'email_positioning',
    'rate_mismatch',
    'other',
)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            'owner_id',
            'dedupe_key',
            name='ux_applications_owner_dedupe_key',
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    resume_asset_id: Mapped[int] = mapped_column(Integer, index=True)
    resume_version_snapshot: Mapped[int] = mapped_column(Integer)
    resume_file_name_snapshot: Mapped[str] = mapped_column(String(255))
    resume_sha256_snapshot: Mapped[str] = mapped_column(String(64))
    recruiter_opportunity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recruiter_opportunities.id", name="fk_applications_recruiter_opportunity", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recruiter_contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("premium_number_contacts.id", name="fk_applications_recruiter_contact", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recruiter_name_snapshot: Mapped[str] = mapped_column(String(255), default="")
    recruiter_company_snapshot: Mapped[str] = mapped_column(String(255), default="")
    job_title_snapshot: Mapped[str] = mapped_column(Text, default="")
    end_client_snapshot: Mapped[str] = mapped_column(Text, default="")
    manual_recruiter_name: Mapped[str] = mapped_column(String(255), default='')
    manual_recruiter_company: Mapped[str] = mapped_column(String(255), default='')
    manual_recruiter_email: Mapped[str] = mapped_column(String(255), default='')
    manual_recruiter_phone: Mapped[str] = mapped_column(String(80), default='')
    manual_recruiter_linkedin_url: Mapped[str] = mapped_column(Text, default='')
    manual_job_title: Mapped[str] = mapped_column(Text, default='')
    manual_end_client: Mapped[str] = mapped_column(Text, default='')
    manual_jd_text: Mapped[str] = mapped_column(Text, default='')
    manual_source_note: Mapped[str] = mapped_column(Text, default='')
    resume_submission_status: Mapped[str] = mapped_column(String(30), index=True, default='not_submitted')
    resume_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submission_method: Mapped[str] = mapped_column(String(20), default='email')
    rejection_detail_tags_json: Mapped[str] = mapped_column(Text, default='[]')
    dedupe_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    promoted_to_appts_application_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    resume_skills_snapshot_json: Mapped[str] = mapped_column(Text, default='[]')
    resume_primary_role_snapshot: Mapped[str] = mapped_column(String(255), default='')
    milestones_reached_json: Mapped[str] = mapped_column(Text, default='{}')
    status: Mapped[str] = mapped_column(String(40), index=True, default="matched")
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    resume_shared_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submitted_to_client_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_action_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0)
    last_contact_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    closed_reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_source: Mapped[str] = mapped_column(String(40), default="user")
    note: Mapped[str] = mapped_column(Text, default="")
    linked_recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


RTR_STATUS_VALUES = ("requested", "confirmed", "expired", "revoked")


class ApplicationRTR(Base):
    __tablename__ = "application_rtrs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="requested")
    role_scope: Mapped[str] = mapped_column(Text, default="")
    end_client_scope: Mapped[str] = mapped_column(Text, default="")
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    proof_attachment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proof_recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


INTERVIEW_ROUND_TYPE_VALUES = (
    "recruiter_screen",
    "interview_1",
    "interview_2",
    "final_interview",
    "other",
)
INTERVIEW_RESULT_VALUES = ("scheduled", "completed", "passed", "failed", "cancelled", "rescheduled")


class ApplicationInterview(Base):
    __tablename__ = "application_interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True)
    round_type: Mapped[str] = mapped_column(String(40), default="interview_1")
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    format: Mapped[str] = mapped_column(String(40), default="")
    interviewer_names: Mapped[str] = mapped_column(Text, default="")
    feedback: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(20), default="scheduled")
    follow_up_task_note: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


APPLICATION_SUGGESTION_TYPE_VALUES = (
    'link_reply',
    'status_change',
    'next_action',
    'stale_prompt',
    'new_variant_needed',
    'email_positioning',
    'skill_gap_pattern',
)
# "expired" is new in v4. accept_suggestion already refuses anything that is not
# "pending", so adding the status makes expiry enforceable for free rather than
# needing a second guard.
APPLICATION_SUGGESTION_STATUS_VALUES = ("pending", "accepted", "dismissed", "expired")
# How long an unreviewed suggestion stays actionable. Lives here rather than in
# one of the two services that create suggestions, because both need it and
# neither should import the other.
SUGGESTION_RETENTION_HOURS = 168
APPLICATION_SUGGESTION_CONFIDENCE_VALUES = ("high", "medium")


class ApplicationSuggestion(Base):
    __tablename__ = "application_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True)
    suggestion_type: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    confidence: Mapped[str] = mapped_column(String(10), default="high")
    reply_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    suggested_next_action_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    suggested_next_action_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Mirrors ScheduledTaskRun's pair so one review surface can render both
    # sources without a special case. Indexed because the expiry sweep queries
    # it; String(20) rather than Text because the reason is a closed vocabulary.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    expiry_reason: Mapped[str] = mapped_column(String(20), default="")


class ApplicationSkillGapSnapshot(Base):
    __tablename__ = 'application_skill_gap_snapshots'
    __table_args__ = (
        UniqueConstraint('owner_id', 'application_id', name='ux_skill_gap_snapshot_application'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default='default-owner', index=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(20), default='fallback_text')
    matched_required_json: Mapped[str] = mapped_column(Text, default='[]')
    missing_required_json: Mapped[str] = mapped_column(Text, default='[]')
    matched_preferred_json: Mapped[str] = mapped_column(Text, default='[]')
    missing_preferred_json: Mapped[str] = mapped_column(Text, default='[]')
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ApplicationOutreachMessage(Base):
    __tablename__ = 'application_outreach_messages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default='default-owner', index=True)
    application_id: Mapped[int] = mapped_column(Integer, index=True)
    message_kind: Mapped[str] = mapped_column(String(40))
    draft_source: Mapped[str] = mapped_column(String(20), default='unknown')
    ai_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subject: Mapped[str] = mapped_column(Text, default='')
    body: Mapped[str] = mapped_column(Text, default='')
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class AppTSApplication(Base):
    __tablename__ = "appts_applications"
    __table_args__ = (UniqueConstraint("owner_id", "dedupe_key", name="ux_appts_applications_owner_dedupe_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    resume_asset_id: Mapped[int] = mapped_column(Integer, index=True)
    resume_version_snapshot: Mapped[int] = mapped_column(Integer)
    resume_file_name_snapshot: Mapped[str] = mapped_column(String(255))
    resume_sha256_snapshot: Mapped[str] = mapped_column(String(64))
    recruiter_opportunity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recruiter_opportunities.id", name="fk_appts_applications_recruiter_opportunity", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recruiter_contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("premium_number_contacts.id", name="fk_appts_applications_recruiter_contact", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recruiter_name_snapshot: Mapped[str] = mapped_column(String(255), default="")
    recruiter_company_snapshot: Mapped[str] = mapped_column(String(255), default="")
    job_title_snapshot: Mapped[str] = mapped_column(Text, default="")
    end_client_snapshot: Mapped[str] = mapped_column(Text, default="")
    location_snapshot: Mapped[str] = mapped_column(Text, default="")
    manual_recruiter_name: Mapped[str] = mapped_column(String(255), default="")
    manual_recruiter_company: Mapped[str] = mapped_column(String(255), default="")
    manual_recruiter_email: Mapped[str] = mapped_column(String(255), default="")
    manual_recruiter_phone: Mapped[str] = mapped_column(String(80), default="")
    manual_recruiter_linkedin_url: Mapped[str] = mapped_column(Text, default="")
    manual_job_title: Mapped[str] = mapped_column(Text, default="")
    manual_end_client: Mapped[str] = mapped_column(Text, default="")
    manual_jd_text: Mapped[str] = mapped_column(Text, default="")
    manual_source_note: Mapped[str] = mapped_column(Text, default="")
    resume_submission_status: Mapped[str] = mapped_column(String(30), index=True, default="not_submitted")
    resume_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submission_method: Mapped[str] = mapped_column(String(20), default="email")
    rejection_detail_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    dedupe_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_skills_snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    resume_primary_role_snapshot: Mapped[str] = mapped_column(String(255), default="")
    milestones_reached_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), index=True, default="matched")
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    resume_shared_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submitted_to_client_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_action_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0)
    last_contact_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    closed_reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_recruiter_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    resolved_recruiter_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_recruiter_email_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recruiter_emails.id", name="fk_appts_applications_source_recruiter_email", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class AppTSApplicationEvent(Base):
    __tablename__ = "appts_application_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("appts_applications.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_source: Mapped[str] = mapped_column(String(40), default="user")
    note: Mapped[str] = mapped_column(Text, default="")
    linked_recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class AppTSApplicationRTR(Base):
    __tablename__ = "appts_application_rtrs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("appts_applications.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="requested")
    role_scope: Mapped[str] = mapped_column(Text, default="")
    end_client_scope: Mapped[str] = mapped_column(Text, default="")
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    proof_attachment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proof_recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class AppTSApplicationInterview(Base):
    __tablename__ = "appts_application_interviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("appts_applications.id"), index=True)
    round_type: Mapped[str] = mapped_column(String(40), default="interview_1")
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    format: Mapped[str] = mapped_column(String(40), default="")
    interviewer_names: Mapped[str] = mapped_column(Text, default="")
    feedback: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(20), default="scheduled")
    follow_up_task_note: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class AppTSApplicationSuggestion(Base):
    __tablename__ = "appts_application_suggestions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("appts_applications.id"), index=True)
    suggestion_type: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    confidence: Mapped[str] = mapped_column(String(10), default="high")
    reply_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    suggested_next_action_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    suggested_next_action_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AppTSApplicationSkillGapSnapshot(Base):
    __tablename__ = "appts_application_skill_gap_snapshots"
    __table_args__ = (UniqueConstraint("owner_id", "application_id", name="ux_appts_skill_gap_snapshot_application"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("appts_applications.id"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="fallback_text")
    matched_required_json: Mapped[str] = mapped_column(Text, default="[]")
    missing_required_json: Mapped[str] = mapped_column(Text, default="[]")
    matched_preferred_json: Mapped[str] = mapped_column(Text, default="[]")
    missing_preferred_json: Mapped[str] = mapped_column(Text, default="[]")
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class AppTSApplicationOutreachMessage(Base):
    __tablename__ = "appts_application_outreach_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("appts_applications.id"), index=True)
    message_kind: Mapped[str] = mapped_column(String(40))
    draft_source: Mapped[str] = mapped_column(String(20), default="unknown")
    ai_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class RoleSimilarityCheck(Base):
    __tablename__ = "role_similarity_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    left_record_type: Mapped[str] = mapped_column(String(40))
    left_record_id: Mapped[int] = mapped_column(Integer)
    right_record_type: Mapped[str] = mapped_column(String(40))
    right_record_id: Mapped[int] = mapped_column(Integer)
    skill_overlap_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    embedding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(20))
    method: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class NumberReviewQueue(Base):
    __tablename__ = "number_review_queue"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "normalized_phone_number",
            "source_email_id",
            name="ux_number_review_queue_owner_phone_email",
        ),
        Index(
            "ix_number_review_queue_conflict_lookup",
            "owner_id",
            "normalized_phone_number",
            "role",
            "reason_code",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    lineage_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "opportunity_lineages.id",
            name="fk_number_review_queue_lineage",
        ),
        nullable=True,
        index=True,
    )
    record_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("candidate_records.id", name="fk_number_review_queue_record"),
        nullable=True,
        index=True,
    )
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_external_opportunity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "external_opportunities.id",
            name="fk_number_review_queue_external_opportunity",
        ),
        nullable=True,
        index=True,
    )
    source_lead_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("premium_number_leads.id", name="fk_number_review_queue_source_lead", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    secondary_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_phone_number: Mapped[str] = mapped_column(String(40), index=True)
    display_phone_number: Mapped[str] = mapped_column(String(80))
    owner_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    designation: Mapped[str] = mapped_column(String(255), default="Unknown")
    confidence: Mapped[str] = mapped_column(String(10), default="low")
    purpose: Mapped[str] = mapped_column(String(255), default="")
    evidence_snippet: Mapped[str] = mapped_column(Text, default="")
    email_subject: Mapped[str] = mapped_column(String(500), default="")
    email_sender: Mapped[str] = mapped_column(String(255), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    contact_type: Mapped[str] = mapped_column(String(40), default="unknown")
    recruiter_relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    relevance_reason: Mapped[str] = mapped_column(String(255), default="")
    extraction_source: Mapped[str] = mapped_column(String(50), default="ai")
    scored_with: Mapped[str] = mapped_column(String(20), default="legacy")
    gmail_open_url: Mapped[str] = mapped_column(String(1000), default="")
    state: Mapped[str] = mapped_column(String(40), default="pending")
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(40), default="new_number")
    field_changes_json: Mapped[str] = mapped_column(Text, default="")
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ContactIdentityAction(Base):
    __tablename__ = "contact_identity_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    action_type: Mapped[str] = mapped_column(String(30), index=True)
    primary_contact_id: Mapped[int] = mapped_column(Integer, index=True)
    secondary_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Holds JSON payloads (soft-delete identifier snapshots, migration reports), not just
    # a single identifier - 500 chars is not enough.
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class ProductivityEvent(Base):
    __tablename__ = "productivity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_source: Mapped[str] = mapped_column(String(40), default="system")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="", index=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class OpportunityLineage(Base):
    __tablename__ = "opportunity_lineages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    origin_type: Mapped[str] = mapped_column(String(20), index=True)
    recruiter_opportunity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "recruiter_opportunities.id",
            name="fk_opportunity_lineage_recruiter_opportunity",
        ),
        unique=True,
        index=True,
        nullable=True,
    )
    current_status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    # This is the current closure timestamp and is reset on reopen. The full
    # close/reopen history remains append-only in OpportunityLifecycleEvent.
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class OpportunitySourceReference(Base):
    __tablename__ = "opportunity_source_references"
    __table_args__ = (
        UniqueConstraint(
            "lineage_id",
            "source_type",
            "external_id",
            name="ux_opportunity_source_reference",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    lineage_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "opportunity_lineages.id",
            name="fk_source_reference_lineage",
        ),
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(String(1200), default="")
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class OpportunityLifecycleEvent(Base):
    __tablename__ = "opportunity_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    lineage_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "opportunity_lineages.id",
            name="fk_lifecycle_event_lineage",
        ),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    actor: Mapped[str] = mapped_column(String(20), default="system")
    process_name: Mapped[str] = mapped_column(String(60), default="")
    related_record_type: Mapped[str] = mapped_column(String(40), default="")
    related_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class CandidateRecord(Base):
    """Permanent, user-facing identity for a candidate, minted at the same two anchor
    points as OpportunityLineage (RecruiterEmail for Gmail, ExternalOpportunity for
    Nvoids) but independent of whether the candidate ever becomes an opportunity.
    Status is always resolved through the owning RecruiterEmail/ExternalOpportunity row
    or, once linked, through OpportunityLineage - this table is identity + link only.
    """

    __tablename__ = "candidate_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    origin_type: Mapped[str] = mapped_column(String(20), index=True)
    internal_lineage_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("opportunity_lineages.id", name="fk_candidate_record_lineage"),
        unique=True,
        index=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class RelationshipLabel(Base):
    """Ground truth, authored during a deliberate labeling session.

    Kept separate from RelationshipJudgment on purpose. These are training data;
    judgments are production feedback on claims the scorer already made. Mixing
    them means calibrating against data the scorer influenced, and the resulting
    precision figure would measure agreement with itself.
    """

    __tablename__ = "relationship_labels"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "left_opportunity_id",
            "right_opportunity_id",
            name="ux_relationship_label_owner_pair",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    # Canonical ordering (left < right) is enforced at the service boundary.
    # Without it the unique constraint permits both orderings, and one pair can
    # be labeled twice with opposite verdicts.
    left_opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("recruiter_opportunities.id", name="fk_relationship_label_left", ondelete="CASCADE"),
        index=True,
    )
    right_opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("recruiter_opportunities.id", name="fk_relationship_label_right", ondelete="CASCADE"),
        index=True,
    )
    # same_program | related_distinct | unrelated | unsure
    verdict: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    # Assigned at insert, never at evaluation time, so the held-out set cannot
    # drift as labeling continues.
    split: Mapped[str] = mapped_column(String(10), default="train", index=True)
    labeler: Mapped[str] = mapped_column(String(100), default="")
    # Which blocking key produced this pair. Without it, precision measured over
    # the set cannot be attributed to a block, and a block contributing mostly
    # false positives stays invisible.
    sampler: Mapped[str] = mapped_column(String(40), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class OpportunityCluster(Base):
    """A set of opportunities the scorer believes belong together.

    `status` defaults to "shadow": nothing is proposed until the Likely-band
    precision bar has been measured. The default has to be the safe state,
    because a default is what a forgotten code path gets.
    """

    __tablename__ = "opportunity_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    label: Mapped[str] = mapped_column(String(255), default="")
    inferred_end_client: Mapped[str] = mapped_column(String(255), default="")
    inferred_partner: Mapped[str] = mapped_column(String(255), default="")
    inferred_domain: Mapped[str] = mapped_column(String(255), default="")
    confidence: Mapped[str] = mapped_column(String(20), default="possible", index=True)
    # shadow | proposed | confirmed | rejected
    status: Mapped[str] = mapped_column(String(20), default="shadow", index=True)
    # Which scorer produced it. A re-scoring must leave old clusters
    # identifiable, or a confirmed judgment silently attaches to a new claim.
    method: Mapped[str] = mapped_column(String(40), default="v3_weighted_v1")
    # Which calibration population this cluster belongs to. A band derived from
    # the keyword-only population does not mean the same thing as one derived
    # from the semantic population, and the two must never be pooled.
    semantic_available: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Hash of the member set, so a rejected set can be suppressed on the next
    # pass without querying an unindexable Text column.
    member_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class OpportunityClusterMember(Base):
    __tablename__ = "opportunity_cluster_members"
    __table_args__ = (
        UniqueConstraint("cluster_id", "opportunity_id", name="ux_cluster_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("opportunity_clusters.id", name="fk_cluster_member_cluster", ondelete="CASCADE"),
        index=True,
    )
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("recruiter_opportunities.id", name="fk_cluster_member_opportunity", ondelete="CASCADE"),
        index=True,
    )
    confidence: Mapped[str] = mapped_column(String(20), default="possible", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    # Text, and never indexed.
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class RelationshipJudgment(Base):
    """Production feedback on a claim the scorer made. Never training data."""

    __tablename__ = "relationship_judgments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    # cluster | cluster_member
    subject_type: Mapped[str] = mapped_column(String(40), index=True)
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    # confirmed | rejected | corrected
    verdict: Mapped[str] = mapped_column(String(20), index=True)
    correction_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # A hash of the rejected member set: fixed width and indexable, unlike a
    # query over correction_json, which is Text and must never be indexed.
    suppression_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)



SCHEDULED_TASK_KINDS = ("reminder", "digest", "monitor", "workflow", "checklist")
SCHEDULED_TASK_STATUS_VALUES = ("active", "paused", "suspended", "deleted")
SCHEDULED_SCHEDULE_KINDS = ("once", "recurring", "condition", "none")
SCHEDULED_RUN_OUTCOMES = (
    "pending",
    "approved",
    "partially_approved",
    "discarded",
    "expired",
    "failed",
    "notified",
)
# Kinds whose newer run makes an older pending batch redundant. A drafted
# message addressed to a specific record never supersedes: each one targets
# different work, so expiring the older would silently drop real work.
SUPERSEDING_KINDS = ("digest", "monitor")
# Three consecutive failures suspend a task rather than retrying forever.
MAX_CONSECUTIVE_FAILURES = 3


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    schedule_kind: Mapped[str] = mapped_column(String(20), default="once")
    cron_expression: Mapped[str] = mapped_column(String(120), default="")
    # The zone the schedule was *authored* in. A user who travels should not
    # have every task shift under them: "9am" meant 9am where they set it.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    condition_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_json: Mapped[str] = mapped_column(Text, default="{}")
    subject_type: Mapped[str] = mapped_column(String(40), default="")
    subject_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    retention_hours: Mapped[int] = mapped_column(Integer, default=168)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ScheduledTaskRun(Base):
    __tablename__ = "scheduled_task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("scheduled_tasks.id", name="fk_scheduled_run_task", ondelete="CASCADE"),
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    prepared_json: Mapped[str] = mapped_column(Text, default="{}")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expiry_reason: Mapped[str] = mapped_column(String(20), default="")
    # Stamped when the half-window warning fires, so it fires exactly once
    # rather than on every sweep past the halfway point.
    warned_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduledTaskItem(Base):
    __tablename__ = "scheduled_task_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("scheduled_tasks.id", name="fk_scheduled_item_task", ondelete="CASCADE"),
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    # String(500), not Text: it stays safely indexable if a search is ever
    # wanted, and a checklist item longer than 500 characters is a note.
    text: Mapped[str] = mapped_column(String(500), default="")
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


# Register external feed models on shared Base metadata for test create_all flows.
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity, ExternalScrapeRun  # noqa: E402,F401
