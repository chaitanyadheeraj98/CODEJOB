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
): string {
  const params = new URLSearchParams({
    state,
    limit: String(limit),
    sort: 'newest',
  })
  if (mailDate) params.set('mail_date', mailDate)
  return `${apiBase}/candidates?${params.toString()}`
}

export async function fetchCandidatesByState(
  apiBase: string,
  state: CandidateState,
  limit: number,
  mailDate: string | null,
  fetchImpl: typeof fetch,
  signal?: AbortSignal,
): Promise<Candidate[]> {
  const res = await fetchImpl(buildCandidatesUrl(apiBase, state, limit, mailDate), { signal })
  if (!res.ok) {
    throw new Error(`Failed to load ${state} queue`)
  }
  const data = (await res.json()) as CandidateListResponse
  return data.items
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
