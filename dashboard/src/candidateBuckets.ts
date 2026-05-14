export type CandidateState = 'needs_review' | 'failed' | 'approved_sent'

export type Candidate = {
  id: number
  recipient_email: string | null
  cc_email: string | null
  draft_reply: string
}

export type CandidateListResponse = {
  items: Candidate[]
  next_cursor: number | null
  has_next: boolean
}

export type CandidateBuckets = {
  queue: Candidate[]
  failed: Candidate[]
  sent: Candidate[]
}

type RequestTracker = { current: number }

export function buildCandidatesUrl(
  apiBase: string,
  state: CandidateState,
  limit: number,
  mailDate: string | null,
  cursor?: number | null,
): string {
  const params = new URLSearchParams({
    state,
    limit: String(limit),
    sort: 'newest',
  })
  if (typeof cursor === 'number') params.set('cursor', String(cursor))
  if (mailDate) params.set('mail_date', mailDate)
  return `${apiBase}/candidates?${params.toString()}`
}

export type CandidatePage = {
  items: Candidate[]
  nextCursor: number | null
  hasNext: boolean
}

export async function fetchCandidatesPageByState(
  apiBase: string,
  state: CandidateState,
  limit: number,
  mailDate: string | null,
  fetchImpl: typeof fetch,
  cursor?: number | null,
  signal?: AbortSignal,
): Promise<CandidatePage> {
  const res = await fetchImpl(buildCandidatesUrl(apiBase, state, limit, mailDate, cursor), { signal })
  if (!res.ok) {
    throw new Error(`Failed to load ${state} queue`)
  }
  const data = (await res.json()) as CandidateListResponse
  return {
    items: data.items,
    nextCursor: data.next_cursor,
    hasNext: data.has_next,
  }
}

export async function fetchCandidatesByState(
  apiBase: string,
  state: CandidateState,
  limit: number,
  mailDate: string | null,
  fetchImpl: typeof fetch,
  signal?: AbortSignal,
): Promise<Candidate[]> {
  const page = await fetchCandidatesPageByState(apiBase, state, limit, mailDate, fetchImpl, undefined, signal)
  return page.items
}

export async function refreshCandidateBuckets(args: {
  apiBase: string
  limit: number
  mailDate: string | null
  fetchImpl?: typeof fetch
  tracker: RequestTracker
  onStart?: () => void
  onSuccess: (buckets: CandidateBuckets) => void
  onError?: (error: Error) => void
  onFinally?: () => void
}): Promise<{ applied: boolean }> {
  const fetchFn = args.fetchImpl ?? fetch
  const requestId = args.tracker.current + 1
  args.tracker.current = requestId
  args.onStart?.()

  try {
    const [queue, failed, sent] = await Promise.all([
      fetchCandidatesByState(args.apiBase, 'needs_review', args.limit, args.mailDate, fetchFn),
      fetchCandidatesByState(args.apiBase, 'failed', args.limit, args.mailDate, fetchFn),
      fetchCandidatesByState(args.apiBase, 'approved_sent', args.limit, args.mailDate, fetchFn),
    ])
    if (requestId !== args.tracker.current) return { applied: false }
    args.onSuccess({ queue, failed, sent })
    return { applied: true }
  } catch (error) {
    if (requestId === args.tracker.current) {
      args.onError?.(error as Error)
    }
    return { applied: false }
  } finally {
    if (requestId === args.tracker.current) {
      args.onFinally?.()
    }
  }
}
