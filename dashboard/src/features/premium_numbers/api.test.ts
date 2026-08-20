import { describe, expect, it } from 'vitest'

import { buildContactListUrl, buildOpportunityListUrl } from './api'

describe('premium number API URLs', () => {
  it('builds a recruiter inventory URL with explicit status and source filters', () => {
    const url = buildContactListUrl({
      apiBase: 'http://localhost:8000',
      role: 'recruiter',
      cursor: 25,
      q: 'nancy',
      sourceType: 'gmail',
      flagged: false,
    })
    expect(url).toContain('/recruiter-numbers?')
    expect(url).toContain('cursor=25')
    expect(url).toContain('limit=100')
    expect(url).toContain('q=nancy')
    expect(url).toContain('source_type=gmail')
    expect(url).toContain('flagged=false')
  })

  it('ports the opportunity status, source, search, and mail-date URL coverage', () => {
    const url = buildOpportunityListUrl({
      apiBase: 'http://localhost:8000',
      cursor: 0,
      q: 'java',
      mailDate: '2026-06-03',
      status: 'New',
      sourceType: 'nvoids',
    })
    expect(url).toContain('/recruiter-opportunities?')
    expect(url).toContain('cursor=0')
    expect(url).toContain('q=java')
    expect(url).toContain('mail_date=2026-06-03')
    expect(url).toContain('status=New')
    expect(url).toContain('source_type=nvoids')
  })
})
