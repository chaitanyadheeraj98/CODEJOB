import type {
  ApplicationDuplicateSummary,
  ApplicationCard,
  ApplicationDashboardSummary,
  ApplicationEventCard,
  ApplicationInterview,
  ApplicationDraftMessage,
  ApplicationMessageKind,
  ApplicationSendMessagePayload,
  ApplicationStatus,
  ApplicationSuggestion,
  AttachmentAssetOption,
  EmployerNumberCard,
  ExtractionAuditEntry,
  NumberReviewCard,
  OpportunityStatus,
  OpportunityMatch,
  PaginatedListResponse,
  PremiumNumberVersion,
  RecruiterNumberCard,
  RecruiterReputation,
  RecruiterOpportunityCard,
  ResumeAssetOption,
  ReviewEdits,
} from './types'

const MAX_INVENTORY_ROWS_PER_SOURCE = 300
const PAGE_LIMIT = 100

export class ApplicationDuplicateConflictError extends Error {
  duplicates: ApplicationDuplicateSummary[]

  constructor(message: string, duplicates: ApplicationDuplicateSummary[]) {
    super(message)
    this.name = 'ApplicationDuplicateConflictError'
    this.duplicates = duplicates
  }
}

export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    if (response.status === 409 && detail) {
      try {
        const payload = JSON.parse(detail) as { detail?: { message?: string; duplicates?: ApplicationDuplicateSummary[] } }
        if (Array.isArray(payload.detail?.duplicates)) {
          throw new ApplicationDuplicateConflictError(
            payload.detail?.message || 'Possible duplicate submission',
            payload.detail.duplicates,
          )
        }
      } catch (reason) {
        if (reason instanceof ApplicationDuplicateConflictError) throw reason
      }
    }
    throw new Error(detail || `Request failed (${response.status})`)
  }
  return await response.json() as T
}

async function listAll<T>(buildUrl: (cursor: number) => string, cap = MAX_INVENTORY_ROWS_PER_SOURCE): Promise<T[]> {
  const items: T[] = []
  let cursor = 0
  while (items.length < cap) {
    const payload = await requestJson<PaginatedListResponse<T>>(buildUrl(cursor))
    items.push(...payload.items)
    if (!payload.has_next || payload.next_cursor == null) break
    cursor = payload.next_cursor
  }
  return items.slice(0, cap)
}

function listParams(cursor: number, q: string): URLSearchParams {
  const params = new URLSearchParams({ cursor: String(cursor), limit: String(PAGE_LIMIT) })
  if (q.trim()) params.set('q', q.trim())
  return params
}

export function listReviewNumbers(apiBase: string, q: string): Promise<NumberReviewCard[]> {
  return listAll((cursor) => `${apiBase}/number-review?${listParams(cursor, q)}`)
}

export async function listExtractionAudit(apiBase: string, sourceEmailId: number): Promise<ExtractionAuditEntry[]> {
  const params = new URLSearchParams({ source_email_id: String(sourceEmailId) })
  return (await requestJson<{ items: ExtractionAuditEntry[] }>(`${apiBase}/premium-numbers/extraction-audit?${params}`)).items
}

export function buildContactListUrl(args: {
  apiBase: string
  role: 'recruiter' | 'employer'
  cursor: number
  q: string
  sourceType: 'all' | 'gmail' | 'nvoids'
  flagged: boolean
}): string {
  const params = listParams(args.cursor, args.q)
  params.set('flagged', String(args.flagged))
  if (args.sourceType !== 'all') params.set('source_type', args.sourceType)
  return `${args.apiBase}/${args.role}-numbers?${params}`
}

export function listRecruiterNumbers(
  apiBase: string,
  q: string,
  sourceType: 'all' | 'gmail' | 'nvoids',
  flagged: boolean,
): Promise<RecruiterNumberCard[]> {
  return listAll((cursor) => buildContactListUrl({ apiBase, role: 'recruiter', cursor, q, sourceType, flagged }))
}

export function listEmployerNumbers(
  apiBase: string,
  q: string,
  sourceType: 'all' | 'gmail' | 'nvoids',
  flagged: boolean,
): Promise<EmployerNumberCard[]> {
  return listAll((cursor) => buildContactListUrl({ apiBase, role: 'employer', cursor, q, sourceType, flagged }))
}

export function listOpportunities(args: {
  apiBase: string
  q: string
  status: 'all' | OpportunityStatus
  sourceType: 'all' | 'gmail' | 'nvoids'
  mailDate: string | null
}): Promise<RecruiterOpportunityCard[]> {
  return listAll((cursor) => buildOpportunityListUrl({ ...args, cursor }))
}

export function buildOpportunityListUrl(args: {
  apiBase: string
  cursor: number
  q: string
  status: 'all' | OpportunityStatus
  sourceType: 'all' | 'gmail' | 'nvoids'
  mailDate: string | null
}): string {
  const params = listParams(args.cursor, args.q)
  if (args.status !== 'all') params.set('status', args.status)
  if (args.sourceType !== 'all') params.set('source_type', args.sourceType)
  if (args.mailDate) params.set('mail_date', args.mailDate)
  return `${args.apiBase}/recruiter-opportunities?${params}`
}

export function listApplications(args: {
  apiBase: string
  q: string
  status: 'all' | ApplicationStatus
  resumeAssetId?: number | null
  resumeSubmissionStatus?: ApplicationCard['resume_submission_status'] | 'all'
}): Promise<ApplicationCard[]> {
  return listAll((cursor) => buildApplicationListUrl({ ...args, cursor }))
}

export function buildApplicationListUrl(args: {
  apiBase: string
  cursor: number
  q: string
  status: 'all' | ApplicationStatus
  resumeAssetId?: number | null
  resumeSubmissionStatus?: ApplicationCard['resume_submission_status'] | 'all'
}): string {
  const params = listParams(args.cursor, args.q)
  if (args.status !== 'all') params.set('status', args.status)
  if (args.resumeAssetId != null) params.set('resume_asset_id', String(args.resumeAssetId))
  if (args.resumeSubmissionStatus && args.resumeSubmissionStatus !== 'all') params.set('resume_submission_status', args.resumeSubmissionStatus)
  return `${args.apiBase}/applications?${params}`
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

export async function runReviewBulkAction(
  apiBase: string,
  action: 'mark-recruiter' | 'mark-employer' | 'delete' | 'rescore',
  reviewIds: number[],
): Promise<void> {
  if (reviewIds.length === 0) return
  await requestJson(`${apiBase}/number-review/bulk-${action}`, jsonInit('POST', { review_ids: reviewIds }))
}

export async function runContactBulkAction(
  apiBase: string,
  role: 'recruiter' | 'employer',
  action: 'mark-recruiter' | 'mark-employer' | 'delete' | 'rescore',
  contactIds: number[],
): Promise<void> {
  if (contactIds.length === 0) return
  if ((role === 'recruiter' && action === 'mark-recruiter') || (role === 'employer' && action === 'mark-employer')) return
  await requestJson(`${apiBase}/${role}-numbers/bulk-${action}`, jsonInit('POST', { contact_ids: contactIds }))
}

export async function runReviewAction(
  apiBase: string,
  reviewId: number,
  action: 'mark-recruiter' | 'mark-employer',
  edits: ReviewEdits,
): Promise<void> {
  await requestJson(`${apiBase}/number-review/${reviewId}/${action}`, jsonInit('POST', edits))
}

export function listContactVersions(
  apiBase: string,
  role: 'recruiter' | 'employer',
  contactId: number,
): Promise<PremiumNumberVersion[]> {
  return requestJson(`${apiBase}/${role}-numbers/${contactId}/versions`)
}

export async function selectContactVersion(
  apiBase: string,
  role: 'recruiter' | 'employer',
  contactId: number,
  leadId: number,
): Promise<void> {
  await requestJson(`${apiBase}/${role}-numbers/${contactId}/select-version/${leadId}`, { method: 'POST' })
}

export async function deleteContactVersion(
  apiBase: string,
  role: 'recruiter' | 'employer',
  contactId: number,
  leadId: number,
): Promise<void> {
  await requestJson(`${apiBase}/${role}-numbers/${contactId}/versions/${leadId}`, { method: 'DELETE' })
}

export function updateRecruiterNumber(
  apiBase: string,
  contactId: number,
  patch: Partial<Pick<RecruiterNumberCard, 'recruiter_name' | 'company' | 'designation' | 'recruiter_email' | 'linkedin_url' | 'recruiter_verification_level' | 'do_not_work_again' | 'do_not_work_again_reason'>>,
): Promise<RecruiterNumberCard> {
  return requestJson(`${apiBase}/recruiter-numbers/${contactId}`, jsonInit('PATCH', patch))
}

export function updateEmployerNumber(
  apiBase: string,
  contactId: number,
  patch: Partial<Pick<EmployerNumberCard, 'owner_name' | 'company' | 'employer_email'>>,
): Promise<EmployerNumberCard> {
  return requestJson(`${apiBase}/employer-numbers/${contactId}`, jsonInit('PATCH', patch))
}

export function updateOpportunity(
  apiBase: string,
  opportunityId: number,
  patch: Partial<RecruiterOpportunityCard>,
): Promise<RecruiterOpportunityCard> {
  return requestJson(`${apiBase}/recruiter-opportunities/${opportunityId}`, jsonInit('PATCH', patch))
}

export function listResumeOptions(apiBase: string): Promise<ResumeAssetOption[]> {
  return requestJson<ResumeAssetOption[]>(`${apiBase}/settings/resumes`)
}

export function listAttachmentOptions(apiBase: string): Promise<AttachmentAssetOption[]> {
  return requestJson<AttachmentAssetOption[]>(`${apiBase}/settings/attachments`)
}

export function createApplication(
  apiBase: string,
  payload: { resume_asset_id: number; recruiter_opportunity_id: number; dedupe_key: string },
): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications`, jsonInit('POST', payload))
}

export function getApplication(apiBase: string, applicationId: number): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}`)
}

export function updateApplication(
  apiBase: string,
  applicationId: number,
  patch: Partial<Pick<ApplicationCard, 'status' | 'next_action_type' | 'next_action_at' | 'closed_reason' | 'closed_reason_code'>>,
): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}`, jsonInit('PATCH', patch))
}

export async function deleteApplication(apiBase: string, applicationId: number): Promise<void> {
  await requestJson(`${apiBase}/applications/${applicationId}`, { method: 'DELETE' })
}

export function createApplicationEvent(
  apiBase: string,
  applicationId: number,
  payload: { event_type: 'note' | 'email_linked' | 'call_note'; note: string; linked_recruiter_email_id?: number },
): Promise<ApplicationEventCard> {
  return requestJson(`${apiBase}/applications/${applicationId}/events`, jsonInit('POST', payload))
}

export function getApplicationsDashboardSummary(apiBase: string): Promise<ApplicationDashboardSummary> {
  return requestJson(`${apiBase}/applications/dashboard-summary`)
}

export async function matchOpportunitiesForResume(
  apiBase: string,
  resumeAssetId: number,
  options: { limit?: number; excludeAlreadyApplied?: boolean } = {},
): Promise<OpportunityMatch[]> {
  const params = new URLSearchParams({ resume_asset_id: String(resumeAssetId) })
  if (options.limit != null) params.set('limit', String(options.limit))
  if (options.excludeAlreadyApplied != null) params.set('exclude_already_applied', String(options.excludeAlreadyApplied))
  return (await requestJson<{ items: OpportunityMatch[] }>(`${apiBase}/applications/match?${params}`)).items
}

export function getRecruiterReputation(apiBase: string, recruiterNumberId: number): Promise<RecruiterReputation> {
  return requestJson(`${apiBase}/recruiter-numbers/${recruiterNumberId}/reputation`)
}

export async function listApplicationSuggestions(
  apiBase: string,
  status: ApplicationSuggestion['status'] = 'pending',
): Promise<ApplicationSuggestion[]> {
  const params = new URLSearchParams({ status })
  return (await requestJson<{ items: ApplicationSuggestion[] }>(`${apiBase}/applications/suggestions?${params}`)).items
}

export function acceptApplicationSuggestion(
  apiBase: string,
  suggestionId: number,
  overrideNextActionAt?: string,
): Promise<ApplicationCard> {
  return requestJson(
    `${apiBase}/applications/suggestions/${suggestionId}/accept`,
    jsonInit('POST', overrideNextActionAt ? { override_next_action_at: overrideNextActionAt } : {}),
  )
}

export function dismissApplicationSuggestion(apiBase: string, suggestionId: number): Promise<ApplicationSuggestion> {
  return requestJson(`${apiBase}/applications/suggestions/${suggestionId}/dismiss`, { method: 'POST' })
}

export async function runReminderSweepNow(apiBase: string): Promise<ApplicationSuggestion[]> {
  return (await requestJson<{ items: ApplicationSuggestion[] }>(`${apiBase}/applications/reminders/run`, { method: 'POST' })).items
}

export function draftApplicationMessage(
  apiBase: string,
  applicationId: number,
  messageKind: ApplicationMessageKind,
): Promise<ApplicationDraftMessage> {
  return requestJson(
    `${apiBase}/applications/${applicationId}/draft-message`,
    jsonInit('POST', { message_kind: messageKind }),
  )
}

export function sendApplicationMessage(
  apiBase: string,
  applicationId: number,
  payload: ApplicationSendMessagePayload,
): Promise<{ sent: boolean; gmail_message_id: string; application: ApplicationCard }> {
  return requestJson(
    `${apiBase}/applications/${applicationId}/send-message`,
    jsonInit('POST', payload),
  )
}

export function requestApplicationRtr(
  apiBase: string,
  applicationId: number,
  payload: { role_scope: string; end_client_scope: string; expires_at: string | null },
): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}/rtr`, jsonInit('POST', payload))
}

export function updateApplicationRtr(
  apiBase: string,
  applicationId: number,
  rtrId: number,
  payload: { status: 'confirmed' | 'expired' | 'revoked'; proof_attachment_id?: number; proof_recruiter_email_id?: number },
): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}/rtr/${rtrId}`, jsonInit('PATCH', payload))
}

export function addApplicationInterview(
  apiBase: string,
  applicationId: number,
  payload: Pick<ApplicationInterview, 'round_type' | 'format' | 'interviewer_names'> & {
    scheduled_at: string | null
    sync_application_status: boolean
  },
): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}/interviews`, jsonInit('POST', payload))
}

export function updateApplicationInterview(
  apiBase: string,
  applicationId: number,
  interviewId: number,
  patch: Partial<Pick<ApplicationInterview, 'scheduled_at' | 'format' | 'interviewer_names' | 'feedback' | 'result' | 'follow_up_task_note'>>,
): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}/interviews/${interviewId}`, jsonInit('PATCH', patch))
}

export function deleteApplicationInterview(
  apiBase: string,
  applicationId: number,
  interviewId: number,
): Promise<ApplicationCard> {
  return requestJson(`${apiBase}/applications/${applicationId}/interviews/${interviewId}`, { method: 'DELETE' })
}

export function submitApplicationToClient(
  apiBase: string,
  applicationId: number,
  overrideDuplicateWarning = false,
): Promise<ApplicationCard> {
  return requestJson(
    `${apiBase}/applications/${applicationId}/submit-to-client`,
    jsonInit('POST', { override_duplicate_warning: overrideDuplicateWarning }),
  )
}

export function generateColdCallScript(apiBase: string, opportunityId: number): Promise<RecruiterOpportunityCard> {
  return requestJson(`${apiBase}/recruiter-opportunities/${opportunityId}/generate-cold-call-script`, { method: 'POST' })
}

export function refreshOpportunityAiMetadata(apiBase: string, opportunityId: number): Promise<RecruiterOpportunityCard> {
  return requestJson(`${apiBase}/recruiter-opportunities/${opportunityId}/refresh-ai-metadata`, { method: 'POST' })
}

export async function deleteOpportunity(apiBase: string, opportunityId: number): Promise<void> {
  await requestJson(`${apiBase}/recruiter-opportunities/${opportunityId}`, { method: 'DELETE' })
}

export async function pendingReviewCount(apiBase: string): Promise<number> {
  const payload = await requestJson<{ count: number }>(`${apiBase}/number-review/pending-count`)
  return payload.count
}
