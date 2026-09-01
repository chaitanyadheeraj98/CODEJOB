export type PaginatedListResponse<TItem> = {
  items: TItem[]
  next_cursor: number | null
  has_next: boolean
}

export type NumberReviewCard = {
  id: number
  source_email_id: number | null
  source_external_opportunity_id: number | null
  source_lead_id: number | null
  target_contact_id?: number | null
  secondary_contact_id?: number | null
  normalized_phone_number: string
  display_phone_number: string
  owner_name: string
  company: string
  designation: string
  confidence: 'high' | 'medium' | 'low'
  purpose: string
  evidence_snippet: string
  email_subject: string
  email_sender: string
  contact_email: string
  contact_type: string
  recruiter_relevance_score: number
  relevance_reason: string
  extraction_source: string
  scored_with: string
  gmail_open_url: string
  state: string
  role: 'recruiter' | 'employer' | null
  reason_code: string
  field_changes_json?: string
  occurrence_count: number
  created_at: string
  updated_at: string
  evidence_at?: string | null
  linkedin_url?: string
}

export type ExtractionAuditEntry = {
  id: number
  source_email_id: number | null
  source_external_opportunity_id: number | null
  raw_value: string
  normalized_value: string | null
  status: 'accepted' | 'rejected'
  stage: string
  reason: string
  created_at: string
}

export type PremiumNumberVersion = {
  id: number
  role: string
  owner_name: string
  company: string
  designation: string
  contact_email: string
  confidence: string
  extraction_source: string
  recruiter_email_id: number | null
  external_opportunity_id: number | null
  source_url: string | null
  linkedin_url: string
  created_at: string
}

type ContactCardBase = {
  id: number
  normalized_phone_number: string | null
  display_phone_number: string
  company: string
  secondary_company?: string
  source_type: 'gmail' | 'nvoids' | null
  source_id: number | null
  source_link_url: string | null
  active_lead_id: number | null
  version_count: number
  seen_count: number
  is_recruiter: boolean
  is_employer: boolean
  recruiter_relevance_score: number | null
  status: 'Active' | 'Flagged'
  flagged: boolean
  created_at: string
  updated_at: string
}

export type PhoneEntry = { phone: string; extension: string; display: string; is_primary: boolean; is_verified: boolean; label: string }

export type RecruiterNumberCard = ContactCardBase & {
  recruiter_name: string
  designation: string
  recruiter_email: string
  recruiter_email_domain?: string
  employer_email_domain?: string
  is_favorite?: boolean
  emails?: Array<{ email: string; domain: string; is_primary: boolean }>
  phones?: PhoneEntry[]
  first_detected_email_id: number | null
  linkedin_url: string
  recruiter_verification_level: 'unverified' | 'verified' | 'trusted'
  do_not_work_again: boolean
  do_not_work_again_reason: string
  total_opportunity_count: number
  last_email_received_at: string | null
}

export type EmployerNumberCard = ContactCardBase & {
  owner_name: string
  designation: string
  employer_email: string
  employer_email_domain?: string
  is_favorite?: boolean
  emails?: Array<{ email: string; domain: string; is_primary: boolean }>
  source_email_id: number | null
  phones?: PhoneEntry[]
  linkedin_url: string
  recruiter_verification_level: 'unverified' | 'verified' | 'trusted'
  do_not_work_again: boolean
  do_not_work_again_reason: string
}

export type ManualContactPayload = {
  name: string
  title?: string
  company?: string
  email?: string
  phone?: string
  role: 'recruiter' | 'employer'
}

export type ManualContactResult = {
  id: number
  created: boolean
  status: 'created' | 'confirmed' | 'pending_merge_approval' | 'pending_link_approval'
  review_id: number | null
  phone_display: string
  role: 'recruiter' | 'employer'
}

export type ContactMergePreviewLead = {
  id: number
  role: string
  company: string
  owner_name: string
  contact_email: string
  phone_number_display: string
  extraction_source: string
  created_at: string
}

export type ContactMergePreviewSide = {
  id: number
  recruiter_name: string
  owner_name: string
  company: string
  secondary_company: string
  recruiter_email: string
  employer_email: string
  normalized_phone_number: string | null
  display_phone_number: string
  phones: PhoneEntry[]
  emails: Array<{ email: string; domain: string; is_primary: boolean }>
  is_recruiter: boolean
  is_employer: boolean
  lead_count: number
  latest_evidence_at: string | null
  leads: ContactMergePreviewLead[]
}

export type ContactMergePreviewResponse = {
  contact_a: ContactMergePreviewSide
  contact_b: ContactMergePreviewSide
}

export type ContactFieldChange = {
  field: string
  label: string
  old: string
  new: string
}

export type ContactRescoreResponse = {
  id: number
  status: string
  changes: ContactFieldChange[]
}

export type OpportunityStatus = 'New' | 'Called' | 'Applied' | 'Follow Up' | 'Closed' | 'Not Interested'

export type RecruiterOpportunityCard = {
  id: number
  recruiter_number_id: number
  source_email_id: number | null
  gmail_message_id: string
  source_type: 'gmail' | 'nvoids'
  source_url: string | null
  external_opportunity_id: number | null
  email_id: number | null
  record_id: string | null
  email_subject: string
  email_sender: string
  gmail_open_url: string
  received_at: string | null
  job_title: string
  end_client: string
  location: string
  work_mode: string
  visa_restrictions: string
  resume_file_name: string
  resume_asset_id: number | null
  implementation_partner: string
  prime_vendor: string
  domain: string
  extracted_skills: string
  evidence: string
  recruiter_name: string
  recruiter_email: string
  recruiter_phone_display: string
  recruiter_phone_normalized: string
  recruiter_company: string
  linkedin_url: string
  status: OpportunityStatus
  notes: string
  employment_type: string
  rate_amount: number | null
  rate_currency: string
  rate_unit: string
  contract_duration: string
  relocation_required: boolean | null
  extension_likely: string
  end_client_confirmed: boolean
  job_confidence: string
  cold_call_script: string | null
  cold_call_script_updated_at: string | null
  created_at: string
  updated_at: string
}

export type ApplicationStatus =
  | 'matched'
  | 'contacted'
  | 'recruiter_responded'
  | 'resume_shared'
  | 'rtr_requested'
  | 'rtr_confirmed'
  | 'submitted_to_client'
  | 'client_reviewing'
  | 'interview_1'
  | 'interview_2'
  | 'final_interview'
  | 'offer'
  | 'hired'
  | 'rejected'
  | 'withdrawn'
  | 'no_response'
  | 'position_closed'
  | 'duplicate'

export type ApplicationEventCard = {
  id: number
  event_type: string
  event_source: string
  note: string
  linked_recruiter_email_id: number | null
  occurred_at: string
}

export type ApplicationRTR = {
  id: number
  status: 'requested' | 'confirmed' | 'expired' | 'revoked'
  role_scope: string
  end_client_scope: string
  requested_at: string
  confirmed_at: string | null
  expires_at: string | null
  proof_attachment_id: number | null
  proof_recruiter_email_id: number | null
  note: string
}

export type ApplicationInterview = {
  id: number
  round_type: 'recruiter_screen' | 'interview_1' | 'interview_2' | 'final_interview' | 'other'
  scheduled_at: string | null
  format: string
  interviewer_names: string
  feedback: string
  result: 'scheduled' | 'completed' | 'passed' | 'failed' | 'cancelled' | 'rescheduled'
  follow_up_task_note: string
}

export type ApplicationCard = {
  id: number
  resume_asset_id: number
  resume_version_snapshot: number
  resume_file_name_snapshot: string
  recruiter_opportunity_id: number | null
  recruiter_contact_id: number | null
  recruiter_name_snapshot: string
  recruiter_company_snapshot: string
  job_title_snapshot: string
  end_client_snapshot: string
  location_snapshot?: string
  resume_skills_snapshot: string[]
  record_id: string | null
  current_recruiter_name: string
  current_recruiter_company: string
  current_recruiter_phone_display: string
  current_recruiter_email: string
  current_recruiter_linkedin_url: string
  current_job_title: string
  current_end_client: string
  current_recruiter_categories: Array<'Recruiter' | 'Employer'>
  current_recruiter_status: string
  current_recruiter_verification_level: 'unverified' | 'verified' | 'trusted' | ''
  current_source_url: string | null
  source_recruiter_email_id: number | null
  ats_score: number | null
  ats_summary: string | null
  sent_gmail_message_link: string | null
  status: ApplicationStatus
  status_changed_at: string
  resume_shared_at: string | null
  submitted_to_client_at: string | null
  next_action_type: string | null
  next_action_at: string | null
  follow_up_count: number
  last_contact_at: string | null
  closed_at: string | null
  closed_reason: string | null
  closed_reason_code: string | null
  resume_submission_status: 'not_submitted' | 'submitted' | 'viewed' | 'shortlisted' | 'interview_scheduled' | 'offered' | 'hired' | 'rejected' | 'withdrawn'
  resume_submitted_at: string | null
  submission_method: string
  rejection_detail_tags: Array<{ category: string; value: string; source: 'ai' | 'user'; confirmed_at: string | null }>
  dedupe_key: string | null
  is_manual_entry: boolean
  milestones_reached: Record<string, string>
  manual_recruiter_name: string
  manual_recruiter_company: string
  manual_recruiter_email: string
  manual_recruiter_phone: string
  manual_recruiter_linkedin_url: string
  manual_job_title: string
  manual_end_client: string
  created_at: string
  updated_at: string
  events: ApplicationEventCard[]
  rtr_history: ApplicationRTR[]
  interviews: ApplicationInterview[]
  skill_gap: {
    source: string
    matched_required: string[]
    missing_required: string[]
    matched_preferred: string[]
    missing_preferred: string[]
    computed_at: string
  } | null
}

export type ApplicationDashboardSummary = {
  due_today: number
  waiting_on_recruiter: number
  interviews: number
  closed_recent: number
  pending_suggestions: number
}

export type OpportunityMatch = {
  opportunity: RecruiterOpportunityCard
  score: number
  reasons: string[]
}

export type RecruiterReputation = {
  recruiter_contact_id: number
  history_label: 'limited_history' | 'established'
  outreach_count: number
  replies_count: number
  median_first_reply_business_days: number | null
  submissions_count: number
  interviews_after_submission_count: number
  offers_count: number
  last_active_at: string | null
}

export type ApplicationSuggestion = {
  id: number
  application_id: number
  suggestion_type: 'link_reply' | 'status_change' | 'next_action' | 'stale_prompt' | 'new_variant_needed' | 'email_positioning' | 'skill_gap_pattern'
  status: 'pending' | 'accepted' | 'dismissed'
  confidence: 'high' | 'medium'
  recruiter_email_id: number | null
  suggested_status: string | null
  suggested_next_action_type: string | null
  suggested_next_action_at: string | null
  reason: string
  payload: Record<string, unknown>
  created_at: string
  resolved_at: string | null
}

export type ApplicationMessageKind = 'followup' | 'submission_to_recruiter'

export type ApplicationDraftMessage = {
  to: string
  cc: string | null
  thread_id: string | null
  subject: string
  body: string
  source: string
  ai_model: string | null
  ai_error: string | null
  resume_context_status: string
  resume_file_name: string
  message_kind: ApplicationMessageKind
}

export type ApplicationSendMessagePayload = {
  to: string
  cc?: string | null
  subject: string
  body: string
  thread_id?: string | null
  message_kind: ApplicationMessageKind
  include_resume: boolean
  attachment_asset_ids: number[]
  draft_source?: string
  ai_model?: string | null
}

export type ResumeAssetOption = {
  id: number
  file_name: string
  version: number
  skills_text: string
  primary_role: string
  structured_skills: string[]
  variant_label: string
  is_enabled: boolean
  is_current: boolean
}

export type AttachmentAssetOption = {
  id: number
  file_name: string
  is_enabled: boolean
}

export type ApplicationDuplicateSummary = {
  id: number
  job_title_snapshot: string
  end_client_snapshot: string
  status: ApplicationStatus
  created_at: string
}

export type InventoryStatusFilter = 'all' | 'Pending' | 'Active' | 'Flagged'
export type InventoryCategoryFilter = 'all' | 'Recruiter' | 'Employer'
export type InventorySourceFilter = 'all' | 'gmail' | 'nvoids'
export type InventoryAction = 'mark-recruiter' | 'mark-employer' | 'rescore' | 'delete'

export type InventoryRow = {
  key: string
  kind: 'review' | 'contact'
  id: number
  number: string
  owner: string
  company: string
  categories: Array<'Recruiter' | 'Employer'>
  status: 'Pending' | 'Active' | 'Flagged' | 'Unscored'
  score: number | null
  sourceType: 'gmail' | 'nvoids' | null
  lastCheckedAt: string
  review?: NumberReviewCard
  recruiter?: RecruiterNumberCard
  employer?: EmployerNumberCard
}

export type ReviewEdits = Partial<Pick<
  NumberReviewCard,
  'owner_name' | 'company' | 'display_phone_number' | 'contact_email' | 'designation' | 'linkedin_url'
>>
