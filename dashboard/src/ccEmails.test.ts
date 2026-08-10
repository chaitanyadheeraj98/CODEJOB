import { describe, expect, it } from 'vitest'

import { addCcEmail, removeCcEmail } from './ccEmails'

describe('CC email helpers', () => {
  it('normalizes, deduplicates, validates, and removes addresses', () => {
    const first = addCcEmail([], ' Ops@Example.com ')
    expect(first.next).toEqual(['ops@example.com'])
    expect(addCcEmail(first.next, 'ops@example.com').added).toBe(false)
    expect(addCcEmail(first.next, 'not-an-email').error).toBe('Invalid email address')
    expect(removeCcEmail(first.next, 'OPS@example.com')).toEqual([])
  })
})
