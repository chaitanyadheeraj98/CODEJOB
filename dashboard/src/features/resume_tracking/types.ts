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
