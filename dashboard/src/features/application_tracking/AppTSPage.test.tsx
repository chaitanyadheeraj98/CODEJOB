// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AppTSPage from './AppTSPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('AppTSPage filter/sort wiring', () => {
  const cleanups: Array<() => void> = []
  // The Gmail-labels view is its own page now; see features/labels. What is
  // pinned here is that Application Tracking no longer offers it as a tab, so
  // the two lists cannot drift back into showing the same threads twice.
  it('offers only the bookmarked and tracked tabs', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ items: [], total: 0, has_next: false })))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })
    await act(async () => { root.render(<AppTSPage apiBase="http://localhost:8000" refreshToken={0} />) })

    const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).map((b) => b.textContent)

    expect(tabs).toEqual(['Bookmarked Requirements', 'Tracked and Applied'])
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/appts/label-threads?'))).toBe(false)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    while (cleanups.length) cleanups.pop()?.()
  })

  it('sends the selected sort and the translated filters to /appts/bookmarked-requirements', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/appts/bookmarked-requirements')) return jsonResponse({ items: [] })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })

    await act(async () => {
      root.render(
        <AppTSPage
          apiBase="http://localhost:8000"
          refreshToken={0}
          activeTab="bookmarked"
          filterValues={{ source: 'gmail', ats_score: { min: 80, max: null }, has_resume: true, sendability: ['sendable'] }}
          sortValue="highest_score"
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 250)) })

    const call = fetchMock.mock.calls.find(([requestUrl]) => String(requestUrl).includes('/appts/bookmarked-requirements'))
    expect(call).toBeDefined()
    const url = new URL(String(call?.[0]))
    expect(url.searchParams.get('sort')).toBe('highest_score')
    expect(url.searchParams.get('source')).toBe('gmail')
    expect(url.searchParams.get('min_ats_score')).toBe('80')
    expect(url.searchParams.get('has_resume')).toBe('true')
    expect(url.searchParams.get('sendability')).toBe('sendable')
  })

  it('sends the Tracked and Applied snapshot filters', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/appts/applications')) return jsonResponse({ items: [], total: 0, next_cursor: null, has_next: false, watch_count: 7, watch_limit: 200, watch_limit_reached: false })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })

    await act(async () => {
      root.render(
        <AppTSPage
          apiBase="http://localhost:8000"
          refreshToken={0}
          activeTab="tracked"
          filterValues={{ q: 'nancy', company: 'Acme', recruiter: 'Priya', end_client: 'Bank X', role: 'Java', has_premium_contact: true, tracked: false }}
          sortValue="next_action"
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 250)) })

    const call = fetchMock.mock.calls.find(([requestUrl]) => String(requestUrl).includes('/appts/applications'))
    expect(call).toBeDefined()
    const url = new URL(String(call?.[0]))
    expect(url.searchParams.get('sort')).toBe('next_action')
    expect(url.searchParams.get('q')).toBe('nancy')
    expect(url.searchParams.get('company')).toBe('Acme')
    expect(url.searchParams.get('recruiter')).toBe('Priya')
    expect(url.searchParams.get('end_client')).toBe('Bank X')
    expect(url.searchParams.get('role')).toBe('Java')
    expect(url.searchParams.get('has_premium_contact')).toBe('true')
    expect(url.searchParams.get('tracked')).toBe('false')
    expect(container.textContent).toContain('Recruiter watch budget: 7 of 200 used.')
  })

  function makeApplication(overrides: Record<string, unknown> = {}) {
    return {
      id: 501,
      resume_asset_id: 1,
      resume_version_snapshot: 1,
      resume_file_name_snapshot: 'resume.pdf',
      recruiter_opportunity_id: 9,
      recruiter_contact_id: 7,
      recruiter_name_snapshot: 'Harshitha Voddepally',
      recruiter_company_snapshot: 'Ideate Technologies LLC',
      job_title_snapshot: 'Java Developer with AWS',
      end_client_snapshot: 'Ideate Technologies LLC',
      location_snapshot: 'Charlotte, NC',
      resume_skills_snapshot: ['Java', 'Spring Boot', 'AWS'],
      record_id: 'a1b2c3d4-0000-4000-8000-000000000501',
      current_recruiter_name: 'Harshitha Voddepally',
      current_recruiter_company: 'Ideate Technologies LLC',
      current_recruiter_phone_display: '+1 555-123-0000',
      current_recruiter_email: 'harshitha@ideate.example.com',
      current_recruiter_linkedin_url: '',
      current_job_title: 'Java Developer with AWS',
      current_end_client: 'Ideate Technologies LLC',
      current_recruiter_categories: ['Employer'],
      current_recruiter_status: 'Active',
      current_recruiter_verification_level: 'trusted',
      current_source_url: 'https://nvoids.com/job_details.jsp?id=42',
      source_recruiter_email_id: null,
      ats_score: null,
      ats_summary: null,
      sent_gmail_message_link: null,
      status: 'matched',
      status_changed_at: '2026-01-01T00:00:00Z',
      resume_shared_at: null,
      submitted_to_client_at: null,
      next_action_type: null,
      next_action_at: null,
      follow_up_count: 0,
      last_contact_at: null,
      closed_at: null,
      closed_reason: null,
      closed_reason_code: null,
      resume_submission_status: 'submitted',
      resume_submitted_at: null,
      submission_method: 'email',
      rejection_detail_tags: [],
      dedupe_key: 'dedupe-501',
      is_manual_entry: false,
      milestones_reached: {},
      manual_recruiter_name: '',
      manual_recruiter_company: '',
      manual_recruiter_email: '',
      manual_recruiter_phone: '',
      manual_recruiter_linkedin_url: '',
      manual_job_title: '',
      manual_end_client: '',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      events: [],
      rtr_history: [],
      interviews: [],
      skill_gap: null,
      ...overrides,
    }
  }

  it('renders recruiter classification, verification, and source-link details on the Tracked and Applied card', async () => {
    const application = makeApplication()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/appts/applications')) return jsonResponse({ items: [application], total: 1, next_cursor: null, has_next: false })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })

    await act(async () => {
      root.render(
        <AppTSPage apiBase="http://localhost:8000" refreshToken={0} activeTab="tracked" filterValues={{}} sortValue="newest" />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 250)) })

    const categoryChips = Array.from(container.querySelectorAll('.candidateCardBadges .categoryChip')).map((el) => el.textContent)
    expect(categoryChips).toEqual(['Employer'])
    expect(container.textContent).toContain('Trusted')
    const link = container.querySelector('a[href="https://nvoids.com/job_details.jsp?id=42"]')
    expect(link).not.toBeNull()
    expect(link?.textContent).toBe('Open source listing')
  })

  it('shows ATS score and a read-only status label, with a dash and disabled actions when no source email is linked', async () => {
    const application = makeApplication({ status: 'rtr_requested' })
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/appts/applications')) return jsonResponse({ items: [application], total: 1, next_cursor: null, has_next: false })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })

    await act(async () => {
      root.render(
        <AppTSPage apiBase="http://localhost:8000" refreshToken={0} activeTab="tracked" filterValues={{}} sortValue="newest" />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 250)) })

    expect(container.querySelector('.statusBadge--lg')).toBeNull()
    expect(container.querySelector('.statusBadge--neutral')?.textContent).toBe('Rtr Requested')
    const viewDetailsButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent === 'View Details')
    expect(viewDetailsButton?.disabled).toBe(true)
    const messageButtons = Array.from(container.querySelectorAll('button')).filter((btn) => btn.textContent === 'Message')
    expect(messageButtons).toHaveLength(1)
    expect(messageButtons[0].disabled).toBe(true)
    expect(container.querySelector('a[href][target="_blank"]:not([href^="https://nvoids"])')).toBeNull()
  })

  it('shows the ATS score, a working Message link, and fetches sourcing details via View Details when a source email is linked', async () => {
    const application = makeApplication({
      source_recruiter_email_id: 42,
      ats_score: 87.4,
      ats_summary: 'Strong match.',
      sent_gmail_message_link: 'https://mail.google.com/mail/u/0/#all/seed-thread',
    })
    const sentDetails = {
      source_label: 'Gmail', requirement_received_link: null, sent_gmail_message_link: 'https://mail.google.com/mail/u/0/#all/seed-thread',
      company: 'Ideate Technologies LLC', end_client: 'Ideate Technologies LLC', implementation_partner: null, vendor: null,
      domain_mentioned: null, experience_required: null, mandatory_skills: [], missing_skills: [],
      resume_variant_sent: 'resume.pdf', attached_files: [], to_email: null, cc_email: null,
      ats_score: 87.4, ats_summary: 'Strong match.', recruiter_name: 'Harshitha Voddepally', recruiter_email: 'harshitha@ideate.example.com',
      recruiter_email_domain: 'ideate.example.com', recruiter_phone: '', recruiter_company: 'Ideate Technologies LLC',
      employer_name: null, employer_email: null, employer_email_domain: null, employer_phone: null, employer_company: null,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/appts/applications/501/sent-details')) return jsonResponse(sentDetails)
      if (url.includes('/appts/applications')) return jsonResponse({ items: [application], total: 1, next_cursor: null, has_next: false })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })

    await act(async () => {
      root.render(
        <AppTSPage apiBase="http://localhost:8000" refreshToken={0} activeTab="tracked" filterValues={{}} sortValue="newest" />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 250)) })

    expect(container.querySelector('.statusBadge--lg')?.textContent).toBe('ATS Strong · 87')
    const messageLink = container.querySelector('a[href="https://mail.google.com/mail/u/0/#all/seed-thread"]')
    expect(messageLink?.textContent).toBe('Message')

    const viewDetailsButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent === 'View Details') as HTMLButtonElement
    expect(viewDetailsButton.disabled).toBe(false)
    await act(async () => { viewDetailsButton.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)) })

    const sentDetailsCall = fetchMock.mock.calls.find(([requestUrl]) => String(requestUrl).includes('/appts/applications/501/sent-details'))
    expect(sentDetailsCall).toBeDefined()
    expect(container.textContent).toContain('Strong match.')

    // View Details must reuse the card's own snapshot (role/location/skills/resume file), not dashes.
    expect(container.textContent).toContain('Role: Java Developer with AWS')
    expect(container.textContent).toContain('Location: Charlotte, NC')
    expect(container.textContent).toContain('Skills: Java, Spring Boot, AWS')
    expect(container.textContent).toContain('Resume Variant Sent: resume.pdf')
  })

  it('shows job location, skill snapshot, record id, and applied date in the card header', async () => {
    const application = makeApplication()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/appts/applications')) return jsonResponse({ items: [application], total: 1, next_cursor: null, has_next: false })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })

    await act(async () => {
      root.render(
        <AppTSPage apiBase="http://localhost:8000" refreshToken={0} activeTab="tracked" filterValues={{}} sortValue="newest" />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 250)) })

    expect(container.querySelector('.candidateCardSubtitle')?.textContent).toBe('Charlotte, NC · Java, Spring Boot, AWS')
    // Record ID must be the underlying candidate_records UUID (same id Sent Items shows
    // for this requirement), never the AppTSApplication row's own numeric id (501).
    expect(container.querySelector('.candidateCardRecordId')?.textContent).toBe('Record ID: a1b2c3d4-0000-4000-8000-000000000501')
    expect(container.textContent).toContain('resume.pdf · v1')
  })

  it('shows a dash for Record ID when the application has no underlying candidate_records link', async () => {
    const application = makeApplication({ record_id: null })
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/appts/applications')) return jsonResponse({ items: [application], total: 1, next_cursor: null, has_next: false })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals() })

    await act(async () => {
      root.render(
        <AppTSPage apiBase="http://localhost:8000" refreshToken={0} activeTab="tracked" filterValues={{}} sortValue="newest" />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 250)) })

    expect(container.querySelector('.candidateCardRecordId')?.textContent).toBe('Record ID: -')
  })
})
