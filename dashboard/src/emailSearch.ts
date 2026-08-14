export type EmailSearchSection =
  | 'needs_review'
  | 'failed_mapping'
  | 'sent_items'
  | 'premium_numbers'
  | 'inbox'
  | 'recent_runs'
  | 'other'

export type EmailSearchHit = {
  section: EmailSearchSection
  recruiter_email_id: number | null
  sender: string
  subject: string
  state: string
  detail: Record<string, unknown>
  occurred_at: string
}

export type EmailSearchResponse = {
  query: string
  hits: EmailSearchHit[]
  truncated: boolean
}

export function isEmailSearchQueryValid(query: string): boolean {
  const normalized = query.trim()
  return /^\d+$/.test(normalized) || normalized.length >= 2
}

export function buildEmailSearchUrl(apiBase: string, query: string, currentSection?: string): string {
  const params = new URLSearchParams({ q: query.trim() })
  if (currentSection) params.set('section', currentSection)
  return `${apiBase}/search/email?${params.toString()}`
}

export async function fetchEmailSearch(
  apiBase: string,
  query: string,
  fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal,
  currentSection?: string,
): Promise<EmailSearchResponse> {
  const response = await fetchImpl(buildEmailSearchUrl(apiBase, query, currentSection), { signal })
  if (!response.ok) throw new Error('Email search failed')
  return response.json() as Promise<EmailSearchResponse>
}
