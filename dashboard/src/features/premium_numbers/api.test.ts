import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  addApplicationInterview,
  acceptApplicationSuggestion,
  buildApplicationListUrl,
  buildContactListUrl,
  buildOpportunityListUrl,
  createApplication,
  createApplicationEvent,
  deleteApplication,
  deleteApplicationInterview,
  dismissApplicationSuggestion,
  getApplicationsDashboardSummary,
  getRecruiterReputation,
  listApplicationSuggestions,
  matchOpportunitiesForResume,
  requestApplicationRtr,
  runReminderSweepNow,
  submitApplicationToClient,
  updateApplication,
  updateApplicationInterview,
  updateApplicationRtr,
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

  it('uses the Phase 2 RTR, interview, and guarded-submission endpoints', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify({}), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await requestApplicationRtr('http://localhost:8000', 3, { role_scope: 'Java', end_client_scope: 'Bank X', expires_at: null })
    await updateApplicationRtr('http://localhost:8000', 3, 4, { status: 'confirmed', proof_attachment_id: 5 })
    await addApplicationInterview('http://localhost:8000', 3, {
      round_type: 'interview_1', scheduled_at: null, format: 'video', interviewer_names: 'Alex', sync_application_status: true,
    })
    await updateApplicationInterview('http://localhost:8000', 3, 6, { result: 'passed' })
    await deleteApplicationInterview('http://localhost:8000', 3, 6)
    await submitApplicationToClient('http://localhost:8000', 3, true)

    expect(fetchMock.mock.calls.map(([calledUrl]) => String(calledUrl))).toEqual([
      'http://localhost:8000/applications/3/rtr',
      'http://localhost:8000/applications/3/rtr/4',
      'http://localhost:8000/applications/3/interviews',
      'http://localhost:8000/applications/3/interviews/6',
      'http://localhost:8000/applications/3/interviews/6',
      'http://localhost:8000/applications/3/submit-to-client',
    ])
    expect(fetchMock.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'PATCH', 'POST', 'PATCH', 'DELETE', 'POST'])
  })

  it('surfaces structured duplicate conflicts for explicit override UI', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      detail: {
        message: 'Possible duplicate submission to the same end client',
        duplicates: [{ id: 9, job_title_snapshot: 'Java', end_client_snapshot: 'Bank X', status: 'matched', created_at: '2026-08-21T12:00:00Z' }],
      },
    }), { status: 409, headers: { 'Content-Type': 'application/json' } })))

    await expect(submitApplicationToClient('http://localhost:8000', 3)).rejects.toMatchObject({
      name: 'ApplicationDuplicateConflictError',
      duplicates: [{ id: 9 }],
    })
  })

  it('uses the Phase 3 match, reputation, suggestion, and reminder endpoints', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (input) => {
      const url = String(input)
      const body = url.includes('/applications/match') || url.includes('/applications/suggestions?') || url.endsWith('/applications/reminders/run')
        ? { items: [] }
        : {}
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchMock)

    await matchOpportunitiesForResume('http://localhost:8000', 7, { limit: 10, excludeAlreadyApplied: false })
    await getRecruiterReputation('http://localhost:8000', 11)
    await listApplicationSuggestions('http://localhost:8000')
    await acceptApplicationSuggestion('http://localhost:8000', 21)
    await dismissApplicationSuggestion('http://localhost:8000', 22)
    await runReminderSweepNow('http://localhost:8000')

    expect(fetchMock.mock.calls.map(([calledUrl]) => String(calledUrl))).toEqual([
      'http://localhost:8000/applications/match?resume_asset_id=7&limit=10&exclude_already_applied=false',
      'http://localhost:8000/recruiter-numbers/11/reputation',
      'http://localhost:8000/applications/suggestions?status=pending',
      'http://localhost:8000/applications/suggestions/21/accept',
      'http://localhost:8000/applications/suggestions/22/dismiss',
      'http://localhost:8000/applications/reminders/run',
    ])
    expect(fetchMock.mock.calls.map(([, init]) => init?.method ?? 'GET')).toEqual(['GET', 'GET', 'GET', 'POST', 'POST', 'POST'])
  })
})
