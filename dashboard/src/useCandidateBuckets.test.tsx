// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useCandidateBuckets, type Candidate, type CandidateState } from './candidateBuckets'

type HookApi = ReturnType<typeof useCandidateBuckets<Candidate>>

function flushMicrotasks(): Promise<void> {
  return Promise.resolve()
}

async function waitForCalls(calls: unknown[], expected: number): Promise<void> {
  for (let i = 0; i < 50; i += 1) {
    if (calls.length >= expected) return
    await flushMicrotasks()
  }
  throw new Error(`Timed out waiting for ${expected} calls, got ${calls.length}`)
}

function setupHarness(args?: {
  fetchImpl?: typeof fetch
  onNeedsReviewItems?: (items: Candidate[]) => void
  onFailedItems?: (items: Candidate[]) => void
}) {
  let latest: HookApi | null = null
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root: Root = createRoot(container)

  function Harness() {
    latest = useCandidateBuckets<Candidate>({
      apiBase: 'http://localhost:8000',
      pageBucketLimit: 25,
      initialBucketLimit: 10,
      fetchImpl: args?.fetchImpl,
      onNeedsReviewItems: args?.onNeedsReviewItems,
      onFailedItems: args?.onFailedItems,
    })
    return null
  }

  act(() => {
    root.render(<Harness />)
  })

  const getHook = () => {
    if (!latest) throw new Error('Hook not ready')
    return latest
  }

  const cleanup = () => {
    act(() => {
      root.unmount()
    })
    container.remove()
  }

  return { getHook, cleanup }
}

function makeResponse(
  items: Array<{ id: number; recipient_email: string | null; cc_email: string | null; draft_reply: string }>,
  opts?: { nextCursor?: number | null; hasNext?: boolean },
): Response {
  return new Response(
    JSON.stringify({
      items,
      next_cursor: opts?.nextCursor ?? null,
      has_next: opts?.hasNext ?? false,
    }),
    { status: 200 },
  )
}

type QueuedCall = {
  url: string
  state: CandidateState
  resolve: (value: Response) => void
}

function setupQueuedFetch() {
  const calls: QueuedCall[] = []
  const fetchImpl = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    const parsed = new URL(url)
    const state = parsed.searchParams.get('state') as CandidateState
    return new Promise<Response>((resolve) => {
      calls.push({ url, state, resolve })
    })
  })
  return { fetchImpl: fetchImpl as unknown as typeof fetch, calls }
}

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('useCandidateBuckets', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    while (cleanups.length) {
      cleanups.pop()?.()
    }
  })

  it('refreshCandidates activeOnly true refreshes only active bucket and marks it loaded', async () => {
    const { fetchImpl, calls } = setupQueuedFetch()
    const { getHook, cleanup } = setupHarness({ fetchImpl })
    cleanups.push(cleanup)

    await act(async () => {
      const promise = getHook().refreshCandidates('2026-05-11', 'failed', { activeOnly: true })
      expect(calls.length).toBe(1)
      expect(calls[0].state).toBe('failed')
      calls[0].resolve(makeResponse([{ id: 1, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await promise
      await flushMicrotasks()
    })

    const hook = getHook()
    expect(hook.failedQueue.map((i) => i.id)).toEqual([1])
    expect(hook.bucketMeta.failed.loaded).toBe(true)
    expect(hook.bucketMeta.needs_review.loaded).toBe(false)
    expect(hook.bucketMeta.approved_sent.loaded).toBe(false)
  })

  it('refreshCandidates includeLoaded true refreshes active plus already-loaded buckets', async () => {
    const { fetchImpl, calls } = setupQueuedFetch()
    const { getHook, cleanup } = setupHarness({ fetchImpl })
    cleanups.push(cleanup)

    await act(async () => {
      const first = getHook().refreshCandidates(null, 'needs_review', { activeOnly: true })
      calls[0].resolve(makeResponse([{ id: 10, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await first
      await flushMicrotasks()
    })

    await act(async () => {
      const second = getHook().refreshCandidates(null, 'failed', { activeOnly: true })
      calls[1].resolve(makeResponse([{ id: 20, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await second!
      await flushMicrotasks()
    })

    await act(async () => {
      const third = getHook().refreshCandidates('2026-05-12', 'approved_sent', {
        activeOnly: true,
        includeLoaded: true,
      })
      expect(calls.length).toBe(3)
      expect(calls[2].state).toBe('approved_sent')
      calls[2].resolve(makeResponse([{ id: 30, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await waitForCalls(calls, 4)
      expect(calls.length).toBe(4)
      expect(calls[3].state).toBe('needs_review')
      calls[3].resolve(makeResponse([{ id: 31, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await waitForCalls(calls, 5)
      expect(calls.length).toBe(5)
      expect(calls[4].state).toBe('failed')
      calls[4].resolve(makeResponse([{ id: 32, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await third
      await flushMicrotasks()
    })

    const hook = getHook()
    expect(hook.sentQueue.map((i) => i.id)).toEqual([30])
    expect(hook.queue.map((i) => i.id)).toEqual([31])
    expect(hook.failedQueue.map((i) => i.id)).toEqual([32])
  })

  it('refreshCandidates activeOnly false refreshes all buckets', async () => {
    const { fetchImpl, calls } = setupQueuedFetch()
    const { getHook, cleanup } = setupHarness({ fetchImpl })
    cleanups.push(cleanup)

    await act(async () => {
      const promise = getHook().refreshCandidates(null, 'needs_review', { activeOnly: false })
      expect(calls.length).toBe(1)
      expect(calls[0].state).toBe('needs_review')
      calls[0].resolve(makeResponse([{ id: 1, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await waitForCalls(calls, 2)
      expect(calls.length).toBe(2)
      expect(calls[1].state).toBe('failed')
      calls[1].resolve(makeResponse([{ id: 2, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await waitForCalls(calls, 3)
      expect(calls.length).toBe(3)
      expect(calls[2].state).toBe('approved_sent')
      calls[2].resolve(makeResponse([{ id: 3, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await promise
      await flushMicrotasks()
    })

    const hook = getHook()
    expect(hook.queue.map((i) => i.id)).toEqual([1])
    expect(hook.failedQueue.map((i) => i.id)).toEqual([2])
    expect(hook.sentQueue.map((i) => i.id)).toEqual([3])
    expect(hook.bucketMeta.needs_review.loaded).toBe(true)
    expect(hook.bucketMeta.failed.loaded).toBe(true)
    expect(hook.bucketMeta.approved_sent.loaded).toBe(true)
  })

  it('keeps latest request results when overlapping refresh calls resolve out of order', async () => {
    const { fetchImpl, calls } = setupQueuedFetch()
    const { getHook, cleanup } = setupHarness({ fetchImpl })
    cleanups.push(cleanup)

    let first: Promise<void> | undefined
    let second: Promise<void> | undefined
    await act(async () => {
      first = getHook().refreshCandidates(null, 'needs_review', { activeOnly: true })
      second = getHook().refreshCandidates('2026-05-11', 'needs_review', { activeOnly: true })
      await waitForCalls(calls, 2)
    })
    expect(calls.length).toBe(2)

    await act(async () => {
      calls[1].resolve(makeResponse([{ id: 200, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await second
      await flushMicrotasks()
    })

    await act(async () => {
      calls[0].resolve(makeResponse([{ id: 100, recipient_email: null, cc_email: null, draft_reply: '' }]))
      await first!
      await flushMicrotasks()
    })

    const hook = getHook()
    expect(hook.queue.map((i) => i.id)).toEqual([200])
  })

  it('loadMoreCandidates appends using nextCursor and respects no-op guards', async () => {
    const { fetchImpl, calls } = setupQueuedFetch()
    const { getHook, cleanup } = setupHarness({ fetchImpl })
    cleanups.push(cleanup)

    await act(async () => {
      const promise = getHook().loadCandidateBucket('needs_review', null, {
        append: false,
        cursor: null,
        limit: 5,
      })
      calls[0].resolve(
        makeResponse(
          [{ id: 1, recipient_email: null, cc_email: null, draft_reply: '' }],
          { nextCursor: 999, hasNext: true },
        ),
      )
      await promise
      await flushMicrotasks()
    })

    await act(async () => {
      const promise = getHook().loadMoreCandidates('needs_review', null)
      expect(calls.length).toBe(2)
      expect(calls[1].url).toContain('cursor=999')
      calls[1].resolve(
        makeResponse(
          [{ id: 2, recipient_email: null, cc_email: null, draft_reply: '' }],
          { nextCursor: null, hasNext: false },
        ),
      )
      await promise
      await flushMicrotasks()
    })

    expect(getHook().queue.map((i) => i.id)).toEqual([1, 2])

    const beforeNoOp = calls.length
    await act(async () => {
      await getHook().loadMoreCandidates('needs_review', null)
      await flushMicrotasks()
    })
    expect(calls.length).toBe(beforeNoOp)
  })

  it('fires onNeedsReviewItems only for needs_review and onFailedItems only for failed', async () => {
    const onNeedsReviewItems = vi.fn()
    const onFailedItems = vi.fn()
    const fetchImpl = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      const parsed = new URL(url)
      const state = parsed.searchParams.get('state')
      const idMap: Record<string, number> = {
        needs_review: 11,
        failed: 22,
        approved_sent: 33,
      }
      return Promise.resolve(
        makeResponse([{ id: idMap[state ?? 'needs_review'], recipient_email: null, cc_email: null, draft_reply: '' }]),
      )
    }) as unknown as typeof fetch
    const { getHook, cleanup } = setupHarness({ fetchImpl, onNeedsReviewItems, onFailedItems })
    cleanups.push(cleanup)

    await act(async () => {
      await getHook().refreshCandidates(null, 'needs_review', { activeOnly: false })
      await flushMicrotasks()
    })

    expect(onNeedsReviewItems).toHaveBeenCalledTimes(1)
    expect(onNeedsReviewItems.mock.calls[0][0][0].id).toBe(11)
    expect(onFailedItems).toHaveBeenCalledTimes(1)
    expect(onFailedItems.mock.calls[0][0][0].id).toBe(22)
  })
})
