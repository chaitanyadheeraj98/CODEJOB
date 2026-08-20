import type {
  EmployerNumberCard,
  NumberReviewCard,
  OpportunityStatus,
  PaginatedListResponse,
  PremiumNumberVersion,
  RecruiterNumberCard,
  RecruiterOpportunityCard,
  ReviewEdits,
} from './types'

const MAX_INVENTORY_ROWS_PER_SOURCE = 300
const PAGE_LIMIT = 100

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
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
  patch: Partial<Pick<RecruiterNumberCard, 'recruiter_name' | 'company' | 'designation' | 'recruiter_email' | 'linkedin_url'>>,
): Promise<RecruiterNumberCard> {
  return requestJson(`${apiBase}/recruiter-numbers/${contactId}`, jsonInit('PATCH', patch))
}

export function updateEmployerNumber(
  apiBase: string,
  contactId: number,
  patch: Partial<Pick<EmployerNumberCard, 'owner_name' | 'company'>>,
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
