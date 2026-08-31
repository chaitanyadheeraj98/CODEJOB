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
  ContactMergePreviewResponse,
  ContactRescoreResponse,
  EmployerNumberCard,
  ExtractionAuditEntry,
  InventoryRow,
  ManualContactPayload,
  ManualContactResult,
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

export class ContactPhoneConflictError extends Error {
  conflictingContactId: number | null

  constructor(message: string, conflictingContactId: number | null) {
    super(message)
    this.name = 'ContactPhoneConflictError'
    this.conflictingContactId = conflictingContactId
  }
}

export class IdentityConflictError extends Error {
  targetContactId: number | null
  secondaryContactId: number | null

  constructor(message: string, targetContactId: number | null, secondaryContactId: number | null) {
    super(message)
    this.name = 'IdentityConflictError'
    this.targetContactId = targetContactId
    this.secondaryContactId = secondaryContactId
  }
}

export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    if (response.status === 409 && detail) {
      try {
        const payload = JSON.parse(detail) as { detail?: { message?: string; duplicates?: ApplicationDuplicateSummary[]; conflicting_contact_id?: number | null; target_contact_id?: number | null; secondary_contact_id?: number | null } }
        if (Array.isArray(payload.detail?.duplicates)) {
          throw new ApplicationDuplicateConflictError(
            payload.detail?.message || 'Possible duplicate submission',
            payload.detail.duplicates,
          )
        }
        if (payload.detail && typeof payload.detail === 'object' && 'conflicting_contact_id' in payload.detail) {
          throw new ContactPhoneConflictError(
            payload.detail.message || 'Already linked to a different contact',
            payload.detail.conflicting_contact_id ?? null,
          )
        }
        if (payload.detail && typeof payload.detail === 'object' && ('target_contact_id' in payload.detail || 'secondary_contact_id' in payload.detail)) {
          throw new IdentityConflictError(
            payload.detail.message || 'Resolve the identity conflict before marking this contact',
            payload.detail.target_contact_id ?? null,
            payload.detail.secondary_contact_id ?? null,
          )
        }
      } catch (reason) {
        if (reason instanceof ApplicationDuplicateConflictError || reason instanceof ContactPhoneConflictError || reason instanceof IdentityConflictError) throw reason
      }
    }
    // Most backend errors are a plain FastAPI HTTPException(detail="message") - unwrap
    // that JSON shape so callers (and the toast) show the message, not the raw body.
    let message = detail
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown }
      if (typeof parsed.detail === 'string' && parsed.detail) message = parsed.detail
    } catch {
      // raw response wasn't JSON - fall through to showing it as-is
    }
    throw new Error(message || `Request failed (${response.status})`)
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

export function listOpportunityPage(args:{apiBase:string;cursor:number;limit:number;q:string;status:'all'|OpportunityStatus;sourceType:'all'|'gmail'|'nvoids';mailDate:string|null;sort:string;filters?:Record<string,string>}):Promise<{items:RecruiterOpportunityCard[];total:number}>{
  const url=buildOpportunityListUrl(args);const params=new URLSearchParams(url.split('?')[1]);params.set('limit',String(args.limit));params.set('sort',args.sort);for(const [key,value] of Object.entries(args.filters??{}))params.set(key,value);return requestJson(`${args.apiBase}/recruiter-opportunities?${params}`)
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
  filters?: Record<string,string>
  sort?: string
}): Promise<ApplicationCard[]> {
  return listAll((cursor) => buildApplicationListUrl({ ...args, cursor }))
}

export function listApplicationPage(args:{apiBase:string;cursor:number;limit:number;q:string;status:'all'|ApplicationStatus;resumeAssetId?:number|null;resumeSubmissionStatus?:ApplicationCard['resume_submission_status']|'all';filters?:Record<string,string>;sort?:string}):Promise<{items:ApplicationCard[];total:number}>{const url=buildApplicationListUrl(args);const params=new URLSearchParams(url.split('?')[1]);params.set('limit',String(args.limit));return requestJson(`${args.apiBase}/applications?${params}`)}

export function buildApplicationListUrl(args: {
  apiBase: string
  cursor: number
  q: string
  status: 'all' | ApplicationStatus
  resumeAssetId?: number | null
  resumeSubmissionStatus?: ApplicationCard['resume_submission_status'] | 'all'
  filters?: Record<string,string>
  sort?: string
}): string {
  const params = listParams(args.cursor, args.q)
  if (args.status !== 'all') params.set('status', args.status)
  if (args.resumeAssetId != null) params.set('resume_asset_id', String(args.resumeAssetId))
  if (args.resumeSubmissionStatus && args.resumeSubmissionStatus !== 'all') params.set('resume_submission_status', args.resumeSubmissionStatus)
  params.set('sort',args.sort ?? 'newest')
  for(const [key,value] of Object.entries(args.filters ?? {})) params.set(key,value)
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

export function unmarkContactRole(apiBase: string, role: 'recruiter' | 'employer', contactId: number): Promise<{ id: number; status: string }> {
  return requestJson(`${apiBase}/${role}-numbers/${contactId}/unmark`, { method: 'POST' })
}

export function rescoreContactWithDiff(apiBase: string, role: 'recruiter' | 'employer', contactId: number): Promise<ContactRescoreResponse> {
  return requestJson(`${apiBase}/${role}-numbers/${contactId}/rescore`, { method: 'POST' })
}

export async function runReviewAction(
  apiBase: string,
  reviewId: number,
  action: 'mark-recruiter' | 'mark-employer',
  edits: ReviewEdits,
): Promise<void> {
  await requestJson(`${apiBase}/number-review/${reviewId}/${action}`, jsonInit('POST', edits))
}

export function approveContactLink(apiBase: string, reviewId: number): Promise<{ review_id: number; contact_id: number; status: string }> {
  return requestJson(`${apiBase}/number-review/${reviewId}/approve-link`, { method: 'POST' })
}

export function approveContactMerge(apiBase: string, reviewId: number, canonicalContactId?: number): Promise<{ review_id: number; contact_id: number; status: string }> {
  return requestJson(`${apiBase}/number-review/${reviewId}/approve-merge`, jsonInit('POST', canonicalContactId != null ? { canonical_contact_id: canonicalContactId } : {}))
}

export function dismissReviewSuggestion(apiBase: string, reviewId: number): Promise<{ review_id: number; contact_id: number; status: string; follow_up_review_id?: number }> {
  return requestJson(`${apiBase}/number-review/${reviewId}/dismiss`, { method: 'POST' })
}

export function acknowledgeContactEnrichment(apiBase: string, reviewId: number): Promise<{ review_id: number; status: string }> {
  return requestJson(`${apiBase}/number-review/${reviewId}/acknowledge`, { method: 'POST' })
}

export function patchNumberReview(
  apiBase: string,
  reviewId: number,
  edits: Pick<ReviewEdits, 'owner_name' | 'company' | 'designation' | 'contact_email' | 'display_phone_number' | 'linkedin_url'>,
): Promise<NumberReviewCard> {
  return requestJson(`${apiBase}/number-review/${reviewId}`, jsonInit('PATCH', edits))
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

export async function deleteOlderContactVersions(
  apiBase: string,
  role: 'recruiter' | 'employer',
  contactId: number,
): Promise<{ id: number; deleted_count: number; status: string }> {
  return requestJson(`${apiBase}/${role}-numbers/${contactId}/versions`, { method: 'DELETE' })
}

export function getRecruiterNumber(apiBase: string, contactId: number): Promise<RecruiterNumberCard> {
  return requestJson(`${apiBase}/recruiter-numbers/${contactId}`)
}

export function getEmployerNumber(apiBase: string, contactId: number): Promise<EmployerNumberCard> {
  return requestJson(`${apiBase}/employer-numbers/${contactId}`)
}

export function updateRecruiterNumber(
  apiBase: string,
  contactId: number,
  patch: Partial<Pick<RecruiterNumberCard, 'recruiter_name' | 'company' | 'secondary_company' | 'designation' | 'recruiter_email' | 'linkedin_url' | 'recruiter_verification_level' | 'do_not_work_again' | 'do_not_work_again_reason' | 'is_favorite'>> & { phone_number?: string; phones?: string[]; emails?: string[] },
): Promise<RecruiterNumberCard> {
  return requestJson(`${apiBase}/recruiter-numbers/${contactId}`, jsonInit('PATCH', patch))
}

export function updateEmployerNumber(
  apiBase: string,
  contactId: number,
  patch: Partial<Pick<EmployerNumberCard, 'owner_name' | 'company' | 'secondary_company' | 'designation' | 'employer_email' | 'linkedin_url' | 'recruiter_verification_level' | 'do_not_work_again' | 'do_not_work_again_reason' | 'is_favorite'>> & { phones?: string[]; emails?: string[] },
): Promise<EmployerNumberCard> {
  return requestJson(`${apiBase}/employer-numbers/${contactId}`, jsonInit('PATCH', patch))
}

export function createManualContact(apiBase: string, payload: ManualContactPayload): Promise<ManualContactResult> {
  return requestJson(`${apiBase}/premium-numbers/contacts`, jsonInit('POST', payload))
}

export function getContactMergePreview(apiBase: string, contactIdA: number, contactIdB: number): Promise<ContactMergePreviewResponse> {
  const params = new URLSearchParams({ contact_id_a: String(contactIdA), contact_id_b: String(contactIdB) })
  return requestJson(`${apiBase}/premium-numbers/contacts/merge-preview?${params}`)
}

export function mergeContacts(apiBase: string, canonicalContactId: number, loserContactId: number): Promise<{ canonical_contact_id: number; loser_contact_id: number; status: string }> {
  return requestJson(`${apiBase}/premium-numbers/contacts/merge`, jsonInit('POST', { canonical_contact_id: canonicalContactId, loser_contact_id: loserContactId }))
}

export function backfillDuplicateContacts(apiBase: string): Promise<{ groups_merged: number; contacts_merged: number }> {
  return requestJson(`${apiBase}/premium-numbers/contacts/backfill-duplicates`, { method: 'POST' })
}

export function listDeletedContacts(
  apiBase: string,
  params: Record<string, string>,
): Promise<{ items: InventoryRow[]; next_cursor: number | null; has_next: boolean; total: number }> {
  return requestJson(`${apiBase}/premium-numbers/deleted-contacts?${new URLSearchParams(params)}`)
}

export function restoreContact(apiBase: string, contactId: number): Promise<{ id: number; status: string }> {
  return requestJson(`${apiBase}/premium-numbers/contacts/${contactId}/restore`, { method: 'POST' })
}

export function bulkRestoreContacts(
  apiBase: string,
  contactIds: number[],
): Promise<{ results: Array<{ contact_id: number; status: string }> }> {
  return requestJson(`${apiBase}/premium-numbers/contacts/bulk-restore`, jsonInit('POST', { contact_ids: contactIds }))
}

export function purgeContact(apiBase: string, contactId: number): Promise<{ id: number; status: string }> {
  return requestJson(`${apiBase}/premium-numbers/contacts/${contactId}/purge`, { method: 'POST' })
}

export function bulkPurgeContacts(
  apiBase: string,
  contactIds: number[],
): Promise<{ results: Array<{ contact_id: number; status: string }> }> {
  return requestJson(`${apiBase}/premium-numbers/contacts/bulk-purge`, jsonInit('POST', { contact_ids: contactIds }))
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
