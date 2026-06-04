import { describe, expect, it } from 'vitest'

import { buildPremiumScopeUrl, defaultPremiumPageMeta } from './premiumNumbers'

describe('premiumNumbers helpers', () => {
  it('creates default page meta for every premium scope', () => {
    expect(defaultPremiumPageMeta()).toEqual({
      all_review: { nextCursor: null, hasNext: false },
      recruiter_numbers: { nextCursor: null, hasNext: false },
      employer_numbers: { nextCursor: null, hasNext: false },
      recruiter_opportunities: { nextCursor: null, hasNext: false },
    })
  })

  it('builds recruiter-number URL with cursor search and mail_date', () => {
    const url = buildPremiumScopeUrl({
      apiBase: 'http://localhost:8000',
      scope: 'recruiter_numbers',
      cursor: 25,
      limit: 25,
      q: 'nancy',
      mailDate: '2026-06-03',
      opportunityStatus: 'all',
      opportunitySource: 'all',
    })

    expect(url).toContain('/recruiter-numbers?')
    expect(url).toContain('cursor=25')
    expect(url).toContain('limit=25')
    expect(url).toContain('q=nancy')
    expect(url).toContain('mail_date=2026-06-03')
  })

  it('builds recruiter-opportunity URL with status and source filters', () => {
    const url = buildPremiumScopeUrl({
      apiBase: 'http://localhost:8000',
      scope: 'recruiter_opportunities',
      cursor: 0,
      limit: 25,
      q: 'java',
      mailDate: '2026-06-03',
      opportunityStatus: 'New',
      opportunitySource: 'nvoids',
    })

    expect(url).toContain('/recruiter-opportunities?')
    expect(url).toContain('cursor=0')
    expect(url).toContain('limit=25')
    expect(url).toContain('q=java')
    expect(url).toContain('mail_date=2026-06-03')
    expect(url).toContain('status=New')
    expect(url).toContain('source_type=nvoids')
  })
})
