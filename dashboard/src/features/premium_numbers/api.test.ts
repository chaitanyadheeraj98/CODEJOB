import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildApplicationListUrl,
  buildContactListUrl,
  buildOpportunityListUrl,
  createApplication,
  createApplicationEvent,
  deleteApplication,
  getApplicationsDashboardSummary,
  updateApplication,
} from './api'

describe('premium number API URLs', () => {
  afterEach(() => vi.unstubAllGlobals())
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

  it('builds the application list URL and uses the shared JSON request path for mutations', async () => {
    const url = buildApplicationListUrl({
      apiBase: 'http://localhost:8000',
      cursor: 100,
      q: 'java',
      status: 'interview_1',
    })
    expect(url).toContain('/applications?')
    expect(url).toContain('cursor=100')
    expect(url).toContain('q=java')
    expect(url).toContain('status=interview_1')

    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify({}), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    await createApplication('http://localhost:8000', { resume_asset_id: 1, recruiter_opportunity_id: 2 })
    await updateApplication('http://localhost:8000', 3, { status: 'contacted' })
    await createApplicationEvent('http://localhost:8000', 3, { event_type: 'note', note: 'Followed up' })
    await getApplicationsDashboardSummary('http://localhost:8000')
    await deleteApplication('http://localhost:8000', 3)

    expect(fetchMock.mock.calls.map(([calledUrl]) => String(calledUrl))).toEqual([
      'http://localhost:8000/applications',
      'http://localhost:8000/applications/3',
      'http://localhost:8000/applications/3/events',
      'http://localhost:8000/applications/dashboard-summary',
      'http://localhost:8000/applications/3',
    ])
    expect(fetchMock.mock.calls.map(([, init]) => init?.method ?? 'GET')).toEqual(['POST', 'PATCH', 'POST', 'GET', 'DELETE'])
  })
})
