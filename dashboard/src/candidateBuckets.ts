import { useCallback, useEffect, useRef, useState } from 'react'

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
  total: number
}

type RequestTracker = { current: number }

export type BucketMeta = { nextCursor: number | null; hasNext: boolean; loaded: boolean; total: number | null }
export type BucketMetaMap = Record<CandidateState, BucketMeta>

export function defaultBucketMeta(): BucketMetaMap {
  return {
    needs_review: { nextCursor: null, hasNext: false, loaded: false, total: null },
    failed: { nextCursor: null, hasNext: false, loaded: false, total: null },
    approved_sent: { nextCursor: null, hasNext: false, loaded: false, total: null },
  }
}

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
  total: number
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
    total: data.total,
  }
}

type CandidateBucketsHookOptions<TCandidate extends Candidate> = {
  apiBase: string
  pageBucketLimit: number
  initialBucketLimit: number
  fetchImpl?: typeof fetch
  onNeedsReviewItems?: (items: TCandidate[]) => void
  onFailedItems?: (items: TCandidate[]) => void
}

export function useCandidateBuckets<TCandidate extends Candidate>(
  options: CandidateBucketsHookOptions<TCandidate>,
) {
  const {
    apiBase,
    pageBucketLimit,
    initialBucketLimit,
    fetchImpl,
    onNeedsReviewItems,
    onFailedItems,
  } = options
  const [queue, setQueue] = useState<TCandidate[]>([])
  const [failedQueue, setFailedQueue] = useState<TCandidate[]>([])
  const [sentQueue, setSentQueue] = useState<TCandidate[]>([])
  const [isCandidateRefreshing, setIsCandidateRefreshing] = useState(false)
  const [candidateRefreshError, setCandidateRefreshError] = useState('')
  const [loadingMoreKey, setLoadingMoreKey] = useState<CandidateState | null>(null)
  const [bucketMeta, setBucketMeta] = useState<BucketMetaMap>(defaultBucketMeta())
  const candidateRefreshTrackerRef = useRef<RequestTracker>({ current: 0 })
  const onNeedsReviewItemsRef = useRef<typeof onNeedsReviewItems>(onNeedsReviewItems)
  const onFailedItemsRef = useRef<typeof onFailedItems>(onFailedItems)
  const fetchFn = fetchImpl ?? fetch

  useEffect(() => {
    onNeedsReviewItemsRef.current = onNeedsReviewItems
    onFailedItemsRef.current = onFailedItems
  }, [onFailedItems, onNeedsReviewItems])

  const applyQueueForBucket = useCallback(
    (state: CandidateState, items: TCandidate[], append: boolean) => {
      if (state === 'needs_review') {
        setQueue((prev) => (append ? [...prev, ...items] : items))
        onNeedsReviewItemsRef.current?.(items)
        return
      }
      if (state === 'failed') {
        setFailedQueue((prev) => (append ? [...prev, ...items] : items))
        onFailedItemsRef.current?.(items)
        return
      }
      setSentQueue((prev) => (append ? [...prev, ...items] : items))
    },
    [],
  )

  const loadCandidateBucket = useCallback(
    async (
      state: CandidateState,
      mailDate: string | null,
      opts?: { append?: boolean; cursor?: number | null; limit?: number; markRefreshing?: boolean },
    ) => {
      const append = Boolean(opts?.append)
      const cursor = opts?.cursor ?? null
      const limit = opts?.limit ?? pageBucketLimit
      const shouldTrackRefreshing = opts?.markRefreshing ?? false
      const requestId = candidateRefreshTrackerRef.current.current + 1
      candidateRefreshTrackerRef.current.current = requestId
      if (shouldTrackRefreshing) {
        setIsCandidateRefreshing(true)
        setCandidateRefreshError('')
      }
      try {
        const page = await fetchCandidatesPageByState(apiBase, state, limit, mailDate, fetchFn, cursor)
        if (requestId !== candidateRefreshTrackerRef.current.current) return
        applyQueueForBucket(state, page.items as TCandidate[], append)
        setBucketMeta((prev) => ({
          ...prev,
          [state]: { nextCursor: page.nextCursor, hasNext: page.hasNext, loaded: true, total: page.total },
        }))
      } catch (error) {
        if (requestId === candidateRefreshTrackerRef.current.current) {
          setCandidateRefreshError((error as Error).message)
        }
      } finally {
        if (shouldTrackRefreshing && requestId === candidateRefreshTrackerRef.current.current) {
          setIsCandidateRefreshing(false)
        }
      }
    },
    [apiBase, applyQueueForBucket, fetchFn, pageBucketLimit],
  )

  const refreshCandidates = useCallback(
    async (
      mailDate: string | null,
      activeBucket: CandidateState,
      opts?: { activeOnly?: boolean; includeLoaded?: boolean; initialLoad?: boolean },
    ) => {
      const targets: CandidateState[] = []
      if (opts?.activeOnly !== false) {
        targets.push(activeBucket)
      }
      if (opts?.includeLoaded) {
        for (const key of ['needs_review', 'failed', 'approved_sent'] as CandidateState[]) {
          if (!targets.includes(key) && bucketMeta[key].loaded) targets.push(key)
        }
      } else if (opts?.activeOnly === false) {
        targets.push('needs_review', 'failed', 'approved_sent')
      }

      for (let i = 0; i < targets.length; i += 1) {
        const key = targets[i]
        await loadCandidateBucket(key, mailDate, {
          append: false,
          cursor: null,
          limit: opts?.initialLoad ? initialBucketLimit : pageBucketLimit,
          markRefreshing: i === 0,
        })
      }
    },
    [bucketMeta, initialBucketLimit, loadCandidateBucket, pageBucketLimit],
  )

  const loadMoreCandidates = useCallback(
    async (state: CandidateState, mailDate: string | null) => {
      const meta = bucketMeta[state]
      if (!meta.hasNext || meta.nextCursor === null || loadingMoreKey) return
      setLoadingMoreKey(state)
      try {
        await loadCandidateBucket(state, mailDate, {
          append: true,
          cursor: meta.nextCursor,
          limit: pageBucketLimit,
        })
      } finally {
        setLoadingMoreKey(null)
      }
    },
    [bucketMeta, loadCandidateBucket, loadingMoreKey, pageBucketLimit],
  )

  return {
    queue,
    failedQueue,
    sentQueue,
    isCandidateRefreshing,
    candidateRefreshError,
    loadingMoreKey,
    bucketMeta,
    refreshCandidates,
    loadCandidateBucket,
    loadMoreCandidates,
  }
}
