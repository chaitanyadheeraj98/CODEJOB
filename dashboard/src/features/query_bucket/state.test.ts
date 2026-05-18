import { describe, expect, it } from 'vitest'
import { addSavedQuery, findExactSavedQuery, removeSavedQuery } from './state'

describe('query bucket state', () => {
  it('adds a normalized unique query', () => {
    const result = addSavedQuery(['is:unread'], '  tx is:unread  ')
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.next).toEqual(['is:unread', 'tx is:unread'])
  })

  it('rejects duplicate case-insensitive query', () => {
    const result = addSavedQuery(['is:unread'], 'IS:UNREAD')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.reason).toBe('duplicate')
  })

  it('removes query case-insensitively', () => {
    const next = removeSavedQuery(['is:unread', 'tx is:unread'], 'TX IS:UNREAD')
    expect(next).toEqual(['is:unread'])
  })

  it('finds exact saved query by current value', () => {
    const matched = findExactSavedQuery(['is:unread', 'tx is:unread'], ' TX IS:UNREAD ')
    expect(matched).toBe('tx is:unread')
  })
})
