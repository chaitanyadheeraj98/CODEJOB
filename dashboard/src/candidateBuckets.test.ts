import { describe, expect, it, vi } from 'vitest'

import { buildCandidatesUrl, refreshCandidateBuckets } from './candidateBuckets'

describe('candidate bucket refresh', () => {
  it('builds URL with optional mail_date', () => {
    const dated = buildCandidatesUrl('http://localhost:8000', 'needs_review', 100, '2026-05-11')
    const anyDate = buildCandidatesUrl('http://localhost:8000', 'needs_review', 100, null)
    expect(dated).toContain('mail_date=2026-05-11')
    expect(anyDate).not.toContain('mail_date=')
  })

  it('ignores stale response from older request', async () => {
    const tracker = { current: 0 }
    const applied: string[] = []
    let resolvers: Array<(value: Response) => void> = []
    const fetchImpl = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolvers.push(resolve)
        }),
    )

    const first = refreshCandidateBuckets({
      apiBase: 'http://localhost:8000',
      limit: 20,
      mailDate: null,
      fetchImpl,
      tracker,
      onSuccess: () => applied.push('first'),
    })

    const second = refreshCandidateBuckets({
      apiBase: 'http://localhost:8000',
      limit: 20,
      mailDate: '2026-05-11',
      fetchImpl,
      tracker,
      onSuccess: () => applied.push('second'),
    })

    const ok = (items: Array<{ id: number; recipient_email: null; cc_email: null; draft_reply: string }>) =>
      new Response(JSON.stringify({ items, next_cursor: null, has_next: false }), { status: 200 })

    resolvers.slice(3, 6).forEach((resolve, idx) =>
      resolve(ok([{ id: 100 + idx, recipient_email: null, cc_email: null, draft_reply: '' }])),
    )
    await second

    resolvers.slice(0, 3).forEach((resolve, idx) =>
      resolve(ok([{ id: idx, recipient_email: null, cc_email: null, draft_reply: '' }])),
    )
    await first

    expect(applied).toEqual(['second'])
  })

  it('keeps latest data on failure by reporting error only for latest request', async () => {
    const tracker = { current: 0 }
    const errors: string[] = []
    const fetchImpl = vi.fn().mockResolvedValue(new Response('boom', { status: 500 }))

    await refreshCandidateBuckets({
      apiBase: 'http://localhost:8000',
      limit: 20,
      mailDate: '2026-05-11',
      fetchImpl,
      tracker,
      onSuccess: () => {
        throw new Error('should not succeed')
      },
      onError: (err) => errors.push(err.message),
    })

    expect(errors.length).toBe(1)
    expect(errors[0]).toContain('Failed to load')
  })
})
