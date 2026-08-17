import { describe, expect, it } from 'vitest'

import { buildCandidatesUrl } from './candidateBuckets'

describe('candidate bucket refresh', () => {
  it('builds URL with optional mail_date', () => {
    const dated = buildCandidatesUrl('http://localhost:8000', 'needs_review', 100, '2026-05-11')
    const anyDate = buildCandidatesUrl('http://localhost:8000', 'needs_review', 100, null)
    expect(dated).toContain('mail_date=2026-05-11')
    expect(anyDate).not.toContain('mail_date=')
  })
})
