import { requestJson } from '../premium_numbers/api'
import type { ApplicationCard, ApplicationSuggestion } from '../premium_numbers/types'
import type {
  ApplicationOutreachMessage,
  ApplicationSkillGap,
  ManualApplicationInput,
  RejectionDetailTagInput,
  ResumeAssetPatch,
  ResumeFunnelMetrics,
  ResumeLibraryItem,
  ResumePerformanceSummaryItem,
  ResumeSubmissionStatus,
  ResumeUploadInput,
  RoleGapReport,
  RoleTargetReport,
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

export function getRoleTarget(apiBase: string, role: string, windowDays = 365): Promise<RoleTargetReport> {
  return requestJson(`${apiBase}/resumes/role-target?role=${encodeURIComponent(role)}&window_days=${windowDays}`)
}

export function lookupVariantToken(apiBase: string, token: string): Promise<VariantLookupResult> {
  return requestJson(`${apiBase}/resumes/variant-lookup?token=${encodeURIComponent(token)}`)
}

export function getWhyThisResume(apiBase: string, applicationId: number): Promise<WhyThisResume> {
  return requestJson(`${apiBase}/applications/${applicationId}/why-this-resume`)
}

/*
 * The full record behind one submission card.
 *
 * The list endpoint answers with `include_events=False`, so events, RTRs and
 * interviews come back empty on every card. This route already sets it True,
 * which is why the details panel fetches per row on open rather than asking the
 * list for everything up front and paying for it on page load.
 */
export function getApplicationDetail(apiBase: string, applicationId: number): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}`)
}

/*
 * The sourcing audit for the recruiter email a submission came from - the same
 * record Application Tracking shows, re-exported rather than reimplemented so
 * both tabs stay on one endpoint and one shape.
 */
export { fetchCandidateSentDetails } from '../application_tracking/api'

/*
 * Resume library.
 *
 * These are the same four endpoints the Settings > Resume Database panel calls.
 * The Manage tab deliberately shares the endpoints rather than the markup: the
 * two views are laid out differently on purpose, but an upload, edit or delete
 * in either place must mean exactly the same thing.
 */

export function listResumeLibrary(apiBase: string): Promise<ResumeLibraryItem[]> {
  return requestJson(`${apiBase}/settings/resumes`)
}

export function uploadResumeAsset(apiBase: string, input: ResumeUploadInput): Promise<ResumeLibraryItem> {
  const form = new FormData()
  form.append('file', input.file)
  form.append('skills_text', input.skills_text)
  form.append('primary_role', input.primary_role)
  form.append('structured_skills_text', input.structured_skills_text)
  form.append('variant_label', input.variant_label)
  // No Content-Type header: the browser has to set the multipart boundary itself.
  return requestJson(`${apiBase}/settings/resume`, { method: 'POST', body: form })
}

export function updateResumeAsset(apiBase: string, resumeId: number, patch: ResumeAssetPatch): Promise<ResumeLibraryItem> {
  return requestJson(`${apiBase}/settings/resumes/${resumeId}`, jsonInit('PATCH', patch))
}

export function deleteResumeAsset(apiBase: string, resumeId: number): Promise<{ id: number; deleted: boolean }> {
  return requestJson(`${apiBase}/settings/resumes/${resumeId}`, { method: 'DELETE' })
}
