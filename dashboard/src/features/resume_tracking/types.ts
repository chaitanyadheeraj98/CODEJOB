import type { ApplicationCard, ResumeAssetOption } from '../premium_numbers/types'

export type ResumeSubmissionStatus = ApplicationCard['resume_submission_status']
export type SubmissionMethod = string

export type RejectionDetailTagInput = { category: string; value: string }
export type RejectionDetailTag = RejectionDetailTagInput & {
  source: 'ai' | 'user'
  confirmed_at: string | null
}

export type ApplicationSkillGap = NonNullable<ApplicationCard['skill_gap']>

export type ResumeFunnelMetrics = {
  resume_asset_id: number
  total_submissions: number
  not_submitted_count: number
  view_rate: number
  shortlist_rate: number
  interview_rate: number
  offer_rate: number
  hire_rate: number
  rejection_rate: number
  acceptance_rate: number
  median_days_to_shortlist: number | null
  median_days_to_interview: number | null
  median_days_to_offer: number | null
  top_rejection_reasons: Array<{ value: string; count: number }>
  top_missing_skills: Array<{ value: string; count: number }>
}

export type ResumePerformanceSummaryItem = {
  resume: ResumeAssetOption
  submission_count: number
  acceptance_rate: number
}

export type ApplicationOutreachMessage = {
  id: number
  application_id: number
  message_kind: string
  draft_source: string
  ai_model: string | null
  subject: string
  body: string
  sent_at: string
}

export type ManualApplicationInput = {
  resume_asset_id: number
  dedupe_key: string
  manual_recruiter_name: string
  manual_recruiter_company: string
  manual_recruiter_email: string
  manual_recruiter_phone: string
  manual_recruiter_linkedin_url: string
  manual_job_title: string
  manual_end_client: string
  manual_jd_text: string
  manual_source_note: string
  submission_method: SubmissionMethod
  resume_submitted_at: string | null
}

export type RoleGapSkill = {
  skill: string
  jd_count: number
  concentration: number
  score: number
}

export type RoleGapSampleJD = {
  email_id: number
  role: string
  missing_skills: string[]
  created_at: string
}

export type RoleGapGroup = {
  role_family: string
  jd_count: number
  flagged_count: number
  flagged_share: number
  median_role_fit: number | null
  median_resume_score: number | null
  closest_variant_code: string
  closest_variant_label: string
  closest_variant_uses: number
  top_titles: string[]
  missing_skills: RoleGapSkill[]
  sample_jds: RoleGapSampleJD[]
}

export type RoleGapReport = {
  window_days: number
  analysed_jds: number
  groups: RoleGapGroup[]
}

export type RoleTargetSkill = {
  skill: string
  jd_count: number
  concentration: number
  score: number
  covered_by_closest: boolean
}

export type RoleTargetVariant = {
  variant_code: string
  variant_label: string
  coverage: number
  matched_skills: string[]
  missing_skills: string[]
  role_alignment_score: number
  foundation_score: number
}

export type RoleTargetSampleJD = {
  email_id: number
  role: string
  skills: string
  match_reason: string
  match_score: number
}

/** 'corpus' | 'thin' | 'none' - how much evidence the answer below is standing on. */
export type RoleTargetReport = {
  target_role: string
  window_days: number
  cohort_size: number
  evidence_tier: string
  demanded_skills: RoleTargetSkill[]
  variants: RoleTargetVariant[]
  closest_variant_code: string
  closest_variant_label: string
  verdict_tone: string
  verdict: string
  sample_jds: RoleTargetSampleJD[]
  narrative: string | null
}

export type VariantLookupResult = {
  email_id: number
  variant_code: string
  variant_label: string | null
  resume_file_name: string | null
  role: string | null
  subject: string | null
  recruiter_email: string | null
  sent_at: string | null
}

export type WhyThisResumeAlternative = {
  variant_code: string
  resume_file_name: string
  final_resume_score: number | null
  ats_score: number | null
  selection_reason: string | null
  is_selected: boolean
}

export type WhyThisResume = {
  available: boolean
  reason_unavailable: string | null
  email_id: number | null
  variant_code: string
  variant_label: string
  resume_file_name: string
  jd_role: string
  selection_status: string
  selection_warning: string | null
  mandatory_gate_status: string
  mandatory_coverage: number | null
  matched_required: string[]
  missing_required: string[]
  matched_priority: string[]
  missing_priority: string[]
  role_family_fit: number | null
  jd_role_family: string
  final_resume_score: number | null
  ats_score: number | null
  ats_summary: string | null
  picker_reason: string | null
  alternatives: WhyThisResumeAlternative[]
}

/**
 * A resume as the library manages it, rather than as a picker option: the same
 * fields the Settings panel edits, plus the timestamps a management view needs.
 */
export type ResumeLibraryItem = ResumeAssetOption & {
  created_at: string
  updated_at: string
  content_summary?: string | null
}

export type ResumeAssetPatch = {
  skills_text?: string
  primary_role?: string
  structured_skills?: string[]
  variant_label?: string
  is_enabled?: boolean
}

export type ResumeUploadInput = {
  file: File
  skills_text: string
  primary_role: string
  structured_skills_text: string
  variant_label: string
}
