import { requestJson } from '../premium_numbers/api'
import type { ApplicationCard, ApplicationSuggestion } from '../premium_numbers/types'
import type {
  ApplicationOutreachMessage,
  ApplicationSkillGap,
  ManualApplicationInput,
  RejectionDetailTagInput,
  ResumeAssetPatch,
  ResumeDraft,
  ResumeDraftPublishInput,
  ResumeDraftPublishResult,
  ResumeDraftSummary,
  ResumeExportFormat,
  ResumeFormatProfile,
  ResumeFormatSpec,
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

/*
 * The Editor.
 *
 * Every write here lands on a draft. Nothing in this group can modify a stored
 * variant, which is the point: a variant's text and its file have to keep saying
 * the same thing, and an edit that changed only the text would have the app
 * arguing for a resume the recruiter never received.
 */

export function listResumeDrafts(apiBase: string): Promise<ResumeDraftSummary[]> {
  return requestJson(`${apiBase}/resume-editor/drafts`)
}

export function getResumeDraft(apiBase: string, draftId: number): Promise<ResumeDraft> {
  return requestJson(`${apiBase}/resume-editor/drafts/${draftId}`)
}

/** Seeding from a variant copies its text. The variant itself is not touched. */
export function createResumeDraft(
  apiBase: string,
  input: { name?: string; source_resume_id?: number; content_markdown?: string },
): Promise<ResumeDraft> {
  return requestJson(`${apiBase}/resume-editor/drafts`, jsonInit('POST', input))
}

export function saveResumeDraft(
  apiBase: string,
  draftId: number,
  patch: { name?: string; content_markdown?: string; format_profile_id?: number | null },
): Promise<ResumeDraft> {
  return requestJson(`${apiBase}/resume-editor/drafts/${draftId}`, jsonInit('PUT', patch))
}

/*
 * Move a section past its neighbour, subsections and all.
 *
 * `base_sha256` is the digest of the whole draft, not of one section: the thing
 * being changed is the order, and a per-section digest would not notice another
 * section arriving between the two.
 */
export function reorderResumeDraftSection(
  apiBase: string,
  draftId: number,
  input: { section: string; direction: 'up' | 'down'; base_sha256?: string },
): Promise<ResumeDraft> {
  return requestJson(`${apiBase}/resume-editor/drafts/${draftId}/sections/reorder`, jsonInit('POST', input))
}

export function deleteResumeDraft(apiBase: string, draftId: number): Promise<{ id: number; deleted: boolean }> {
  return requestJson(`${apiBase}/resume-editor/drafts/${draftId}`, { method: 'DELETE' })
}

/*
 * Turn a draft into a variant.
 *
 * The server renders the document, stores it, and extracts its text back out of
 * the stored file through the same path an upload takes - so this is the
 * download-and-re-upload loop done in one call, not a shortcut around it.
 */
export function publishResumeDraft(
  apiBase: string,
  draftId: number,
  input: ResumeDraftPublishInput,
): Promise<ResumeDraftPublishResult> {
  return requestJson(`${apiBase}/resume-editor/drafts/${draftId}/publish`, jsonInit('POST', input))
}

export function listFormatProfiles(apiBase: string): Promise<ResumeFormatProfile[]> {
  return requestJson(`${apiBase}/resume-editor/profiles`)
}

export function createFormatProfile(apiBase: string, file: File, name: string, makeDefault: boolean): Promise<ResumeFormatProfile> {
  const form = new FormData()
  form.append('file', file)
  form.append('name', name)
  form.append('make_default', String(makeDefault))
  // No Content-Type header: the browser has to set the multipart boundary itself.
  return requestJson(`${apiBase}/resume-editor/profiles`, { method: 'POST', body: form })
}

export function updateFormatProfile(
  apiBase: string,
  profileId: number,
  patch: { name?: string; is_default?: boolean; spec?: ResumeFormatSpec },
): Promise<ResumeFormatProfile> {
  return requestJson(`${apiBase}/resume-editor/profiles/${profileId}`, jsonInit('PATCH', patch))
}

export function deleteFormatProfile(apiBase: string, profileId: number): Promise<{ id: number; deleted: boolean }> {
  return requestJson(`${apiBase}/resume-editor/profiles/${profileId}`, { method: 'DELETE' })
}

const filenameFrom = (disposition: string | null, fallback: string) =>
  disposition?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)?.[1] ?? fallback

/*
 * Download a draft.
 *
 * This is the only thing editing produces - no variant is created here. Fetched
 * as a blob rather than pointed at with a link so a refusal - an empty draft,
 * LibreOffice missing - arrives as a message in the tab instead of a new browser
 * window showing raw JSON.
 */
export async function downloadResumeDraft(
  apiBase: string,
  draftId: number,
  format: ResumeExportFormat,
  profileId: number | null,
): Promise<string> {
  const response = await fetchResumeDraftExport(apiBase, draftId, format, profileId)
  const blob = await response.blob()
  const name = filenameFrom(response.headers.get('content-disposition'), `resume-draft-${draftId}.${format}`)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
  return name
}

export function createLayoutProfile(apiBase: string, name: string, spec: ResumeFormatSpec): Promise<ResumeFormatProfile> {
  const form = new FormData()
  form.append('name', name)
  form.append('spec_json', JSON.stringify(spec))
  return requestJson(`${apiBase}/resume-editor/profiles`, { method: 'POST', body: form })
}

export async function fetchResumeDraftExport(apiBase: string, draftId: number, format: ResumeExportFormat, profileId: number | null, signal?: AbortSignal): Promise<Response> {
  const query = new URLSearchParams({ fmt: format })
  query.set('profile_id', String(profileId ?? 0))
  const response = await fetch(`${apiBase}/resume-editor/drafts/${draftId}/export?${query}`, { signal })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    try {
      throw new Error((JSON.parse(detail) as { detail?: string }).detail || detail)
    } catch (parsed) {
      throw parsed instanceof Error && parsed.message ? parsed : new Error(detail || `Export failed (${response.status})`)
    }
  }
  return response
}
