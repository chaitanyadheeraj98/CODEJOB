import { describe, expect, it, vi } from 'vitest'

import { buildEmailSearchUrl, fetchEmailSearch, isEmailSearchQueryValid } from './emailSearch'

describe('email search client', () => {
  it('accepts a one-digit Email ID while rejecting a one-character text scan', () => {
    expect(isEmailSearchQueryValid('1')).toBe(true)
    expect(isEmailSearchQueryValid('x')).toBe(false)
    expect(isEmailSearchQueryValid('xy')).toBe(true)
  })

  it('trims and encodes the cross-section query', () => {
    expect(buildEmailSearchUrl('http://localhost:8000', '  recruiter+jobs@example.com  ')).toBe(
      'http://localhost:8000/search/email?q=recruiter%2Bjobs%40example.com',
    )
  })

  it('fetches the typed response and forwards the abort signal', async () => {
    const controller = new AbortController()
    const payload = { query: '42', hits: [], truncated: false }
    const fetchImpl = vi.fn(async () => ({ ok: true, json: async () => payload }) as Response)

    await expect(fetchEmailSearch('http://localhost:8000', '42', fetchImpl, controller.signal)).resolves.toEqual(payload)
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:8000/search/email?q=42',
      { signal: controller.signal },
    )
  })

  it('uses a stable error when the endpoint fails', async () => {
    const fetchImpl = vi.fn(async () => ({ ok: false }) as Response)

    await expect(fetchEmailSearch('http://localhost:8000', 'missing', fetchImpl)).rejects.toThrow('Email search failed')
  })
})
