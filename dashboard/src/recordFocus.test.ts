import { describe, expect, it } from 'vitest'

import { focusedCandidateMissing } from './recordFocus'

const target = (recruiter_email_id: number | null, section = 'needs_review') => ({ section, recruiter_email_id })
const loaded = (...ids: number[]) => ids.map((id) => ({ id }))

describe('focusedCandidateMissing', () => {
  it('says nothing when no record is focused', () => {
    expect(focusedCandidateMissing(null, loaded(1, 2), false)).toBeNull()
  })

  it('says nothing when the focused record is on screen', () => {
    expect(focusedCandidateMissing(target(7323), loaded(7322, 7323), false)).toBeNull()
  })

  // Auto-pagination is still working through the remaining pages; a notice here
  // would flash on every normal deep link into a long queue.
  it('stays quiet while more pages are still loading', () => {
    expect(focusedCandidateMissing(target(7323), loaded(7322), true)).toBeNull()
  })

  it('reports the id once every page is loaded and it is still absent', () => {
    expect(focusedCandidateMissing(target(7323), loaded(7322, 7324), false)).toBe(7323)
  })

  it('ignores focus targets belonging to another section', () => {
    expect(focusedCandidateMissing(target(7323, 'sent_items'), loaded(), false)).toBeNull()
    expect(focusedCandidateMissing(target(7323, 'inbox'), loaded(), false)).toBeNull()
  })

  it('ignores a target with no candidate id', () => {
    expect(focusedCandidateMissing(target(null), loaded(), false)).toBeNull()
  })

  it('reports against an empty queue, which is the filtered-to-nothing case', () => {
    expect(focusedCandidateMissing(target(7323), loaded(), false)).toBe(7323)
  })
})
