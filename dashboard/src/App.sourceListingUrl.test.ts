import { describe, expect, it } from 'vitest'

import { sourceListingUrl } from './App'

describe('sourceListingUrl', () => {
  it('returns external_thread_id when nvoids source has absolute URL', () => {
    const result = sourceListingUrl({
      source: 'nvoids',
      external_thread_id: 'https://nvoids.com/job_details.jsp?id=3385623&uid=abc',
      external_message_id: 'nvoids:3385623',
    } as never)
    expect(result).toBe('https://nvoids.com/job_details.jsp?id=3385623&uid=abc')
  })

  it('builds URL from nvoids:<id> message format', () => {
    const result = sourceListingUrl({
      source: 'nvoids',
      external_thread_id: null,
      external_message_id: 'nvoids:3385623',
    } as never)
    expect(result).toBe('https://nvoids.com/job_details.jsp?id=3385623')
  })

  it('builds URL from legacy nvoids:nvoids:<id> message format', () => {
    const result = sourceListingUrl({
      source: 'nvoids',
      external_thread_id: null,
      external_message_id: 'nvoids:nvoids:3385623',
    } as never)
    expect(result).toBe('https://nvoids.com/job_details.jsp?id=3385623')
  })

  it('returns null for non-nvoids sources', () => {
    const result = sourceListingUrl({
      source: 'gmail',
      external_thread_id: 'https://nvoids.com/job_details.jsp?id=3385623',
      external_message_id: 'nvoids:3385623',
    } as never)
    expect(result).toBeNull()
  })
})

