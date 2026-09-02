import { describe, expect, it } from 'vitest'

import { roleProvenanceHint } from './CandidateCard'

describe('roleProvenanceHint', () => {
  it('shows nothing for an extracted title', () => {
    // Absence of a marker is the signal that the extractor produced this value.
    expect(roleProvenanceHint('extracted')).toBeNull()
  })

  it('marks a subject fallback as unverified', () => {
    const hint = roleProvenanceHint('subject_fallback')
    expect(hint?.label).toBe('from subject')
    expect(hint?.detail).toContain('Unverified')
  })

  it('distinguishes a taxonomy match from an extraction', () => {
    expect(roleProvenanceHint('taxonomy_matched')?.label).toBe('matched')
  })

  it('renders nothing for NULL, because every legacy row would otherwise be badged', () => {
    // All 8,508 pre-existing rows carry NULL. Badging them puts an identical
    // marker on every card, which is noise rather than signal. The DATA meaning
    // stays "unverified" for machine consumers; this is only the display call.
    for (const value of [null, undefined]) {
      expect(roleProvenanceHint(value)).toBeNull()
    }
  })

  it('marks an explicitly unknown source, which is a real determination', () => {
    expect(roleProvenanceHint('unknown')?.label).toBe('unverified')
  })
})
