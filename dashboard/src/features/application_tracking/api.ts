import { ApplicationDuplicateConflictError, RoleManifestForkRequiredError, requestJson } from '../premium_numbers/api'
import type { ApplicationCard, ApplicationEventCard, ApplicationInterview, ApplicationStatus } from '../premium_numbers/types'
import type { Candidate, SentItemDetails } from '../../App'

export { ApplicationDuplicateConflictError, RoleManifestForkRequiredError }

const json = (method: string, body?: unknown): RequestInit => ({ method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
export type BookmarkedRequirement = Candidate

export type LabelThread = {
  thread_id: string; subject: string; recruiter: string; recruiter_email: string | null; labels: string[]
  last_message_at: string; message_count: number; unread_count: number; conversation_id: number | null
  gmail_thread_link: string | null; appts_application_id: number | null; record_id: string | null
}
export function fetchLabelThreads(apiBase: string, filters: Record<string, string> = {}, sort = 'newest', page = 1) {
  const params = new URLSearchParams({ ...filters, sort, page: String(page), limit: '25' })
  return requestJson<{ items: LabelThread[]; total: number; has_next: boolean; next_cursor: number | null }>(`${apiBase}/appts/label-threads?${params}`)
}
export function promoteLabelThread(apiBase: string, threadId: string, resumeAssetId: number) {
  return requestJson<ApplicationCard>(`${apiBase}/appts/label-threads/${encodeURIComponent(threadId)}/promote`, json('POST', { resume_asset_id: resumeAssetId }))
}

export async function listBookmarkedRequirements(apiBase: string, filters: Record<string, string> = {}, sort = 'newest'): Promise<BookmarkedRequirement[]> {
  const params = new URLSearchParams({ ...filters, sort })
  return (await requestJson<{ items: BookmarkedRequirement[] }>(`${apiBase}/appts/bookmarked-requirements?${params}`)).items
}
export async function listApplications(apiBase: string, filters: Record<string, string> = {}, sort = 'newest'): Promise<ApplicationCard[]> {
  const params = new URLSearchParams({ ...filters, sort, limit: '100' })
  return (await requestJson<{ items: ApplicationCard[] }>(`${apiBase}/appts/applications?${params}`)).items
}
export function listApplicationPage(args: { apiBase: string; cursor: number; limit: number; q: string; status: 'all' | ApplicationStatus; filters?: Record<string, string>; sort?: string }): Promise<{ items: ApplicationCard[]; total: number; watch_count: number; watch_limit: number; watch_limit_reached: boolean }> {
  const params = new URLSearchParams({ cursor: String(args.cursor), limit: String(args.limit), sort: args.sort ?? 'newest' })
  if (args.q.trim()) params.set('q', args.q.trim())
  if (args.status !== 'all') params.set('status', args.status)
  for (const [key, value] of Object.entries(args.filters ?? {})) params.set(key, value)
  return requestJson(`${args.apiBase}/appts/applications?${params}`)
}
export const getApplication = (apiBase: string, id: number) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${id}`)
export const updateApplication = (apiBase: string, id: number, patch: Partial<{ status: ApplicationStatus; next_action_type: string | null; next_action_at: string | null; closed_reason: string | null; closed_reason_code: string | null }>) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${id}`, json('PATCH', patch))
export const createApplicationEvent = (apiBase: string, id: number, payload: { event_type: 'note' | 'email_linked' | 'call_note'; note: string; linked_recruiter_email_id?: number }) => requestJson<ApplicationEventCard>(`${apiBase}/appts/applications/${id}/events`, json('POST', payload))
export const requestApplicationRtr = (apiBase: string, id: number, payload: { role_scope: string; end_client_scope: string; expires_at: string | null }) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${id}/rtr`, json('POST', payload))
export const updateApplicationRtr = (apiBase: string, applicationId: number, rtrId: number, payload: { status: 'confirmed' | 'expired' | 'revoked'; proof_attachment_id?: number; proof_recruiter_email_id?: number }) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${applicationId}/rtr/${rtrId}`, json('PATCH', payload))
export const addApplicationInterview = (apiBase: string, id: number, payload: Pick<ApplicationInterview, 'round_type' | 'format' | 'interviewer_names'> & { scheduled_at: string | null; sync_application_status: boolean }) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${id}/interviews`, json('POST', payload))
export const updateApplicationInterview = (apiBase: string, applicationId: number, interviewId: number, patch: Partial<Pick<ApplicationInterview, 'scheduled_at' | 'format' | 'interviewer_names' | 'feedback' | 'result' | 'follow_up_task_note'>>) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${applicationId}/interviews/${interviewId}`, json('PATCH', patch))
export const deleteApplicationInterview = (apiBase: string, applicationId: number, interviewId: number) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${applicationId}/interviews/${interviewId}`, { method: 'DELETE' })
export const submitApplicationToClient = (apiBase: string, id: number, override = false) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/${id}/submit-to-client`, json('POST', { override_duplicate_warning: override }))
export const createAppTSApplicationFromOpportunity = (apiBase: string, payload: { resume_asset_id?: number; recruiter_opportunity_id: number; dedupe_key: string }) => requestJson<ApplicationCard>(`${apiBase}/appts/applications`, json('POST', payload))
export const promoteSubmissionToAppts = (apiBase: string, id: number) => requestJson<ApplicationCard>(`${apiBase}/appts/applications/from-submission/${id}`, { method: 'POST' })

// Bookmarked Requirements card actions — same /candidates/* endpoints the Needs Review card uses.
export const approveSendCandidate = (apiBase: string, id: number, editedReply: string) =>
  requestJson<Candidate>(`${apiBase}/candidates/${id}/approve-send`, json('POST', { edited_reply: editedReply, confirm_same_source_additional_send: false }))
export const regenerateCandidateDraft = (apiBase: string, id: number, allowRoleManifestFork = false) =>
  requestJson<Candidate>(`${apiBase}/candidates/${id}/regenerate`, json('POST', { preserve_manual_routing: true, preserve_review_visibility: true, allow_role_manifest_fork: allowRoleManifestFork }))
export const retryRoleDetectionForCandidate = (apiBase: string, id: number) =>
  requestJson<void>(`${apiBase}/candidates/${id}/retry-role-detection`, { method: 'POST' })
export const rejectCandidate = (apiBase: string, id: number) =>
  requestJson<void>(`${apiBase}/candidates/${id}/reject`, json('POST', { reason: 'Rejected by user before send' }))
export const sendCandidateToFailedMapping = (apiBase: string, id: number) =>
  requestJson<void>(`${apiBase}/candidates/${id}/send-to-failed-mapping`, { method: 'POST' })
export const toggleCandidateTracking = (apiBase: string, id: number) =>
  requestJson<void>(`${apiBase}/candidates/${id}/track`, { method: 'POST' })
export const fetchCandidateSentDetails = (apiBase: string, id: number) =>
  requestJson<SentItemDetails>(`${apiBase}/candidates/${id}/sent-details`)
export const fetchApplicationSentDetails = (apiBase: string, id: number) =>
  requestJson<SentItemDetails>(`${apiBase}/appts/applications/${id}/sent-details`)
