// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import OpportunitiesTab from './OpportunitiesTab'
import type { RecruiterOpportunityCard } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const opportunity: RecruiterOpportunityCard = {
  id: 9,
  recruiter_number_id: 11,
  source_email_id: 21,
  gmail_message_id: 'message-9',
  source_type: 'gmail',
  source_url: null,
  external_opportunity_id: null,
  email_id: 21,
  record_id: '11111111-1111-1111-1111-111111111111',
  email_subject: 'Senior Java Developer',
  email_sender: 'priya@example.com',
  gmail_open_url: 'https://mail.google.test/21',
  received_at: '2026-08-20T12:00:00Z',
  job_title: 'Senior Java Developer',
  end_client: 'Bank X',
  location: 'Dallas',
  work_mode: 'Hybrid',
  visa_restrictions: '',
  resume_file_name: '',
  resume_asset_id: null,
  implementation_partner: '',
  prime_vendor: '',
  domain: 'Finance',
  extracted_skills: 'Java, Spring',
  evidence: '',
  recruiter_name: 'Priya Patel',
  recruiter_email: 'priya@example.com',
  recruiter_phone_display: '+1 214 555 1212',
  recruiter_phone_normalized: '12145551212',
  recruiter_company: 'ABC Staffing',
  linkedin_url: '',
  status: 'New',
  notes: '',
  employment_type: '',
  rate_amount: null,
  rate_currency: 'USD',
  rate_unit: '',
  contract_duration: '',
  relocation_required: null,
  extension_likely: 'unknown',
  end_client_confirmed: false,
  job_confidence: 'unknown',
  cold_call_script: null,
  cold_call_script_updated_at: null,
  created_at: '2026-08-20T12:00:00Z',
  updated_at: '2026-08-20T12:00:00Z',
}

describe('OpportunitiesTab application entry point', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    vi.restoreAllMocks()
    while (cleanups.length) cleanups.pop()?.()
  })

  it('locks an enabled resume snapshot when tracking an opportunity', async () => {
    let createAttempts = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/recruiter-opportunities?')) return jsonResponse({ items: [opportunity], next_cursor: null, has_next: false })
      if (url.endsWith('/settings/resumes')) return jsonResponse([{ id: 7, file_name: 'java-backend.pdf', version: 2, is_enabled: true, is_current: true }])
      if (url.endsWith('/applications') && init?.method === 'POST') {
        createAttempts += 1
        return createAttempts === 1 ? jsonResponse({ detail: 'temporary failure' }, 503) : jsonResponse({ id: 41 }, 201)
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    const onToast = vi.fn()

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })

    await act(async () => {
      root.render(
        <OpportunitiesTab
          apiBase="http://localhost:8000"
          mailDate={null}
          refreshToken={0}
          highlightedId={null}
          applicationsEnabled
          onToast={onToast}
          filterValues={{ status: ['New', 'Called'], employment_type: 'Contract', work_mode: 'Remote', job_confidence: 'high', extension_likely: 'yes' }}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const listUrl = String(fetchMock.mock.calls.find(([url]) => String(url).includes('/recruiter-opportunities?'))?.[0])
    expect(listUrl).toContain('status=New%2CCalled')
    expect(listUrl).toContain('employment_type=Contract')
    expect(listUrl).toContain('work_mode=Remote')
    expect(listUrl).toContain('job_confidence=high')
    expect(listUrl).toContain('extension_likely=yes')

    const track = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Track Application')
    await act(async () => {
      track?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    expect(container.textContent).toContain('java-backend.pdf (v2)')
    expect(container.textContent).toContain('locks this resume version')

    const confirm = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Confirm & Track')
    await act(async () => {
      confirm?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    await act(async () => {
      confirm?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    const createCalls = fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith('/applications') && init?.method === 'POST')
    const firstPayload = JSON.parse(String(createCalls[0]?.[1]?.body)) as Record<string, unknown>
    const secondPayload = JSON.parse(String(createCalls[1]?.[1]?.body)) as Record<string, unknown>
    expect(firstPayload.resume_asset_id).toBe(7)
    expect(firstPayload.recruiter_opportunity_id).toBe(9)
    expect(firstPayload.dedupe_key).toEqual(expect.any(String))
    expect(firstPayload.dedupe_key).toBe(secondPayload.dedupe_key)
    expect(onToast).toHaveBeenCalledWith('Application tracking started')
  })

  it('tracks in one click when the opportunity already records its resume', async () => {
    const linkedOpportunity = { ...opportunity, resume_asset_id: 7, resume_file_name: 'java-backend.pdf' }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/recruiter-opportunities?')) return jsonResponse({ items: [linkedOpportunity], next_cursor: null, has_next: false })
      if (url.endsWith('/settings/resumes')) return jsonResponse([{ id: 7, file_name: 'java-backend.pdf', version: 2, is_enabled: true, is_current: true }])
      if (url.endsWith('/applications') && init?.method === 'POST') return jsonResponse({ id: 41 }, 201)
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })
    await act(async () => {
      root.render(<OpportunitiesTab apiBase="http://localhost:8000" mailDate={null} refreshToken={0} highlightedId={null} applicationsEnabled onToast={vi.fn()} />)
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    const track = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Track Application')
    await act(async () => { track?.click(); await new Promise((resolve) => window.setTimeout(resolve, 50)) })
    const createCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/applications') && init?.method === 'POST')
    const payload = JSON.parse(String(createCall?.[1]?.body)) as Record<string, unknown>
    expect(payload.resume_asset_id).toBeUndefined()
    expect(payload.recruiter_opportunity_id).toBe(9)
    expect(container.textContent).not.toContain('Confirm & Track')
  })

  it('edits job-quality and risk fields in the existing opportunity card', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/recruiter-opportunities?')) return jsonResponse({ items: [opportunity], next_cursor: null, has_next: false })
      if (url.endsWith('/recruiter-opportunities/9') && init?.method === 'PATCH') {
        return jsonResponse({ ...opportunity, ...JSON.parse(String(init.body)) })
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })
    await act(async () => {
      root.render(
        <OpportunitiesTab
          apiBase="http://localhost:8000"
          mailDate={null}
          refreshToken={0}
          highlightedId={null}
          applicationsEnabled
          onToast={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const employmentInput = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.includes('Employment type'))?.querySelector('input')
    const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      inputSetter.call(employmentInput, 'contract')
      employmentInput?.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const clientConfirmed = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.includes('End client confirmed'))?.querySelector<HTMLInputElement>('input')
    await act(async () => { clientConfirmed?.click() })
    const save = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Save')
    await act(async () => {
      save?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 30))
    })
    const patchCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/recruiter-opportunities/9') && init?.method === 'PATCH')
    expect(JSON.parse(String(patchCall?.[1]?.body))).toMatchObject({ employment_type: 'contract', end_client_confirmed: true })
  })

  it('sorts opportunities by the selected resume and shows match reasons', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/settings/resumes')) return jsonResponse([{ id: 7, file_name: 'java-backend.pdf', version: 2, is_enabled: true, is_current: true }])
      if (url.includes('/applications/match?')) {
        return jsonResponse({ items: [{ opportunity, score: 92.4, reasons: ['Strong Java overlap', 'Limited history with this recruiter'] }] })
      }
      if (url.includes('/recruiter-opportunities?')) return jsonResponse({ items: [opportunity], next_cursor: null, has_next: false })
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })
    await act(async () => {
      root.render(
        <OpportunitiesTab
          apiBase="http://localhost:8000"
          mailDate={null}
          refreshToken={0}
          highlightedId={null}
          applicationsEnabled
          onToast={vi.fn()}
          filterValues={{ resume_fit: '7' }}
          sortValue="resume_fit"
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    expect(container.textContent).toContain('92.4% match')
    expect(container.textContent).toContain('Strong Java overlap')
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/applications/match?resume_asset_id=7'))).toBe(true)
  })
})
