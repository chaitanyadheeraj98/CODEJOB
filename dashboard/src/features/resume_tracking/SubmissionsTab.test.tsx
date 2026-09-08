// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SubmissionsTab from './SubmissionsTab'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('SubmissionsTab', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => { vi.unstubAllGlobals(); while (cleanups.length) cleanups.pop()?.() })

  it('opens a backdate-capable manual submission form with a resume selected', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const body = String(input).includes('/applications/suggestions') ? { items: [] } : { items: [], next_cursor: null, has_next: false }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => {
      root.render(<SubmissionsTab apiBase="http://localhost:8000" resumes={[{ id: 7, file_name: 'java.pdf', version: 3, skills_text: 'Java', primary_role: 'Java Developer', structured_skills: ['Java'], variant_label: 'Banking', is_enabled: true, is_current: true }]} />)
      await new Promise((resolve) => setTimeout(resolve, 150))
    })
    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'Log submission')
    act(() => button?.click())
    expect(container.textContent).toContain('Submission date')
    expect(container.querySelector<HTMLInputElement>('input[type="date"]')?.value).toBe(new Date().toISOString().slice(0, 10))
    expect(container.querySelector('.resumeTrackingForm select')?.textContent).toContain('Banking')
  })

  it('resolves a pasted variant marker to the send it came from', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url.includes('/resumes/variant-lookup')) {
        return new Response(JSON.stringify({
          email_id: 8919,
          variant_code: 'R13',
          variant_label: 'banking, payments',
          resume_file_name: 'resume.docx',
          role: 'Java Developer',
          subject: 'Java Developer',
          recruiter_email: 'amir@example.com',
          sent_at: '2026-09-04T22:57:39Z',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      const body = url.includes('/applications/suggestions') ? { items: [] } : { items: [], next_cursor: null, has_next: false }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => {
      root.render(<SubmissionsTab apiBase="http://localhost:8000" resumes={[]} />)
      await new Promise((resolve) => setTimeout(resolve, 150))
    })

    const input = container.querySelector<HTMLInputElement>('#variantLookupInput')!
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'CJ-R13-8919')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      container.querySelector<HTMLFormElement>('.variantLookup')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 100))
    })

    expect(calls.some((url) => url.includes('variant-lookup') && url.includes('CJ-R13-8919'))).toBe(true)
    const result = container.querySelector('.variantLookupResult')?.textContent ?? ''
    expect(result).toContain('R13')
    expect(result).toContain('Java Developer')
    expect(result).toContain('amir@example.com')
  })

  // A card built the way the auto-log path built 3,357 of them: the recruiter's
  // own company copied into the end-client column because the posting named none.
  const submissionRow = (overrides: Partial<Record<string, unknown>> = {}) => ({
    id: 4102,
    resume_asset_id: 15,
    resume_version_snapshot: 2,
    resume_file_name_snapshot: 'ChaithanyaRaj_UpdatedResume.docx',
    recruiter_opportunity_id: null,
    recruiter_contact_id: null,
    recruiter_name_snapshot: 'Pranay Gondela',
    recruiter_company_snapshot: 'Centillion Infotech',
    job_title_snapshot: 'Java Full Stack Developer',
    end_client_snapshot: 'Centillion Infotech',
    location_snapshot: 'New Jersey',
    resume_skills_snapshot: [],
    record_id: 'REC-1',
    current_recruiter_name: 'Pranay Gondela',
    current_recruiter_company: 'Horizon Softech',
    current_recruiter_phone_display: '',
    current_recruiter_email: 'pranay@horizonsoftech.net',
    current_recruiter_linkedin_url: '',
    current_job_title: '',
    current_end_client: '',
    current_recruiter_categories: [],
    current_recruiter_status: '',
    current_recruiter_verification_level: '',
    current_source_url: null,
    source_recruiter_email_id: 9032,
    ats_score: 71,
    ats_summary: 'Strong Java coverage',
    sent_gmail_message_link: null,
    status: 'resume_shared',
    status_changed_at: '2026-09-07T20:20:00Z',
    resume_shared_at: '2026-09-07T20:20:00Z',
    submitted_to_client_at: null,
    next_action_type: null,
    next_action_at: null,
    follow_up_count: 0,
    last_contact_at: null,
    closed_at: null,
    closed_reason: null,
    closed_reason_code: null,
    resume_submission_status: 'submitted',
    resume_submitted_at: '2026-09-07T20:20:00Z',
    submission_method: 'email',
    rejection_detail_tags: [],
    dedupe_key: 'recruiter_email:9032',
    is_manual_entry: false,
    milestones_reached: {},
    manual_recruiter_name: 'Pranay Gondela',
    manual_recruiter_company: 'Centillion Infotech',
    manual_recruiter_email: 'pranay@centillioninfotech.com',
    manual_recruiter_phone: '',
    manual_recruiter_linkedin_url: '',
    manual_job_title: 'Java Full Stack Developer',
    manual_end_client: 'Centillion Infotech',
    created_at: '2026-09-07T20:20:00Z',
    updated_at: '2026-09-07T20:20:00Z',
    events: [],
    rtr_history: [],
    interviews: [],
    skill_gap: null,
    ...overrides,
  })

  const sentDetails = {
    email_id: 9032, source_type: 'gmail', source_label: 'Gmail', requirement_received_link: null,
    sent_gmail_message_link: 'https://mail.google.com/x', resume_variant_sent: 'ChaithanyaRaj_UpdatedResume.docx',
    resume_variant_code: 'R15', resume_variant_token: 'CJ-R15-9032', attached_files: [], company: 'Centillion Infotech',
    recruiter_name: 'Pranay Gondela', recruiter_email: 'pranay@centillioninfotech.com', recruiter_email_domain: 'centillioninfotech.com',
    recruiter_phone: null, recruiter_company: 'Centillion Infotech', employer_name: null, employer_email: null,
    employer_email_domain: null, employer_phone: null, employer_company: null, end_client: null,
    implementation_partner: null, vendor: null, domain_mentioned: null, experience_required: null,
    mandatory_skills: [], missing_skills: [], ats_score: 71, ats_summary: 'Strong Java coverage',
    to_email: 'stripathi@alltechconsultinginc.com', cc_email: null, sent_at: '2026-09-07T20:20:00Z',
    opened_at: null, open_count: 0, reply_count: 0,
  }

  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

  // Answers both requests an opened card makes: the full record and its source email.
  const openedCardRoutes = (url: string) => {
    if (url.includes('/applications/4102') && !url.includes('?')) return json(submissionRow())
    if (url.includes('/candidates/9032/sent-details')) return json(sentDetails)
    return null
  }

  const mountWithRows = async (rows: unknown[], onFetch: (url: string) => Response | null) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const handled = onFetch(url)
      if (handled) return handled
      const body = url.includes('/applications/suggestions') ? { items: [] } : { items: rows, next_cursor: null, has_next: false }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => { root.render(<SubmissionsTab apiBase="http://localhost:8000" resumes={[]} />) })
    // Two passes on purpose: the list load sits behind a 120ms debounce that is
    // only scheduled once the mount effect flushes, which is when act returns.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)) })
    return container
  }

  it('does not repeat the recruiter company as an end client, and flags a stale snapshot', async () => {
    const container = await mountWithRows([submissionRow()], () => null)
    const identity = container.querySelector('.submissionIdentity')?.textContent ?? ''
    expect(identity).toContain('Pranay Gondela')
    expect(identity).toContain('Centillion Infotech')
    expect(identity).not.toContain('End client')
    // The snapshot is frozen; the contact record has moved on. Say both.
    expect(identity).toContain('now Horizon Softech')
    expect(container.querySelector('.submissionMeta')?.textContent).toContain('New Jersey')
    expect(container.querySelector('.submissionMeta')?.textContent).toContain('R15')
  })

  it('names an end client the posting actually stated', async () => {
    const container = await mountWithRows([submissionRow({ end_client_snapshot: 'State of New Jersey' })], () => null)
    const identity = container.querySelector('.submissionIdentity')?.textContent ?? ''
    expect(identity).toContain('End client')
    expect(identity).toContain('State of New Jersey')
  })

  it('opens the record and its source trail on demand, not on page load', async () => {
    const calls: string[] = []
    const container = await mountWithRows([submissionRow()], (url) => {
      calls.push(url)
      if (url.includes('/applications/4102') && !url.includes('?')) {
        return new Response(JSON.stringify(submissionRow({
          events: [{ id: 1, event_type: 'resume_submission_status_changed', event_source: 'user', note: '', linked_recruiter_email_id: null, metadata_json: '{"trigger":"user_correction"}', occurred_at: '2026-09-07T21:00:00Z' }],
        })), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/candidates/9032/sent-details')) {
        return json(sentDetails)
      }
      return null
    })

    expect(calls.some((url) => url.includes('sent-details'))).toBe(false)

    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'View details')!
    await act(async () => {
      button.click()
      await new Promise((resolve) => setTimeout(resolve, 150))
    })

    expect(calls.some((url) => url.endsWith('/applications/4102'))).toBe(true)
    expect(calls.some((url) => url.includes('/candidates/9032/sent-details'))).toBe(true)
    const panel = container.querySelector('.submissionDetails')?.textContent ?? ''
    expect(panel).toContain('Not identified')
    expect(panel).toContain('CJ-R15-9032')
    expect(panel).toContain('user correction')
  })

  it('flips the label to Hide details and stops repeating the sent-message link', async () => {
    const container = await mountWithRows([submissionRow()], openedCardRoutes)
    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'View details')!
    await act(async () => {
      button.click()
      await new Promise((resolve) => setTimeout(resolve, 150))
    })

    // The panel is open, so the button that opened it has to say so.
    expect(button.textContent).toBe('Hide details')
    const panel = container.querySelector('.submissionDetails')!
    const sentLinks = Array.from(panel.querySelectorAll('a')).filter((link) => link.getAttribute('href') === 'https://mail.google.com/x')
    // One, in the Source card where it belongs. The card used to print a second
    // copy of it at the foot of the panel.
    expect(sentLinks).toHaveLength(1)

    await act(async () => { button.click() })
    expect(button.textContent).toBe('View details')
    expect(container.querySelector('.submissionDetails')).toBeNull()
  })

  it('disables the details button when nothing sourced the submission', async () => {
    const container = await mountWithRows([submissionRow({ source_recruiter_email_id: null, dedupe_key: 'typed-by-hand', is_manual_entry: true })], () => null)
    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'View details')!
    expect(button.disabled).toBe(true)
    expect(button.title).toContain('no source email')
  })
})
