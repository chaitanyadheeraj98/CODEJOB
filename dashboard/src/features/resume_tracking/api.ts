import { requestJson } from '../premium_numbers/api'
import type { ApplicationCard, ApplicationSuggestion } from '../premium_numbers/types'
import type {
  ApplicationOutreachMessage,
  ApplicationSkillGap,
  ManualApplicationInput,
  RejectionDetailTagInput,
  ResumeFunnelMetrics,
  ResumePerformanceSummaryItem,
  ResumeSubmissionStatus,
  RoleGapReport,
  VariantLookupResult,
  WhyThisResume,
} from './types'

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export function createManualApplication(apiBase: string, payload: ManualApplicationInput): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/manual`, jsonInit('POST', payload))
}

export function updateResumeSubmissionStatus(
  apiBase: string,
  applicationId: number,
  newStatus: Exclude<ResumeSubmissionStatus, 'not_submitted' | 'submitted' | 'interview_scheduled'>,
  options: { rejection_detail_tags?: RejectionDetailTagInput[]; note?: string; force?: boolean } = {},
): Promise<ApplicationCard> {
  return requestJson(
    `${apiBase}/applications/${applicationId}/resume-submission-status`,
    jsonInit('PATCH', { new_status: newStatus, ...options }),
  )
}

export function getSkillGap(apiBase: string, applicationId: number): Promise<ApplicationSkillGap> {
  return requestJson(`${apiBase}/applications/${applicationId}/skill-gap`)
}

export function recomputeSkillGap(apiBase: string, applicationId: number): Promise<ApplicationSkillGap> {
  return requestJson(`${apiBase}/applications/${applicationId}/skill-gap/recompute`, { method: 'POST' })
}

export function getResumeFunnel(apiBase: string, resumeId: number): Promise<ResumeFunnelMetrics> {
  return requestJson(`${apiBase}/resumes/${resumeId}/funnel`)
}

export async function getResumePerformanceSummary(apiBase: string, sort = 'recent'): Promise<ResumePerformanceSummaryItem[]> {
  return (await requestJson<{ items: ResumePerformanceSummaryItem[] }>(`${apiBase}/resumes/performance-summary?sort=${encodeURIComponent(sort)}`)).items
}

export function getOutreachMessage(apiBase: string, applicationId: number, messageId: number): Promise<ApplicationOutreachMessage> {
  return requestJson(`${apiBase}/applications/${applicationId}/outreach-messages/${messageId}`)
}

export async function runResumeTrackingSuggestions(apiBase: string): Promise<ApplicationSuggestion[]> {
  return (await requestJson<{ items: ApplicationSuggestion[] }>(`${apiBase}/applications/resume-tracking/suggestions/run`, { method: 'POST' })).items
}

export function getRoleGaps(apiBase: string, windowDays = 90, minJds = 5): Promise<RoleGapReport> {
  return requestJson(`${apiBase}/resumes/role-gaps?window_days=${windowDays}&min_jds=${minJds}`)
}

export function lookupVariantToken(apiBase: string, token: string): Promise<VariantLookupResult> {
  return requestJson(`${apiBase}/resumes/variant-lookup?token=${encodeURIComponent(token)}`)
}

export function getWhyThisResume(apiBase: string, applicationId: number): Promise<WhyThisResume> {
  return requestJson(`${apiBase}/applications/${applicationId}/why-this-resume`)
}
