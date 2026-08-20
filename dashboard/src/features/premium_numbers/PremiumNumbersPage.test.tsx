// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PremiumNumbersPage from './PremiumNumbersPage'
import type { EmployerNumberCard, NumberReviewCard, RecruiterNumberCard } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const review: NumberReviewCard = {
  id: 1,
  source_email_id: 100,
  source_external_opportunity_id: null,
  source_lead_id: 1000,
  normalized_phone_number: '15550192834',
  display_phone_number: '+1 (555) 019-2834',
  owner_name: 'TechFlow Inc.',
  company: 'TechFlow Inc.',
  designation: 'Unknown',
  confidence: 'medium',
  purpose: 'Contact number',
  evidence_snippet: 'Call me',
  email_subject: 'Role',
  email_sender: 'sender@example.com',
  contact_email: 'sender@example.com',
  contact_type: 'unknown',
  recruiter_relevance_score: 72,
  relevance_reason: 'possible recruiter',
  extraction_source: 'ai',
  scored_with: 'current',
  gmail_open_url: 'https://mail.google.test/100',
  state: 'pending',
  created_at: '2026-08-18T10:00:00Z',
  updated_at: '2026-08-18T12:00:00Z',
}

const contactBase = {
  id: 2,
  normalized_phone_number: '442079460958',
  display_phone_number: '+44 20 7946 0958',
  company: 'Global Talent Ltd',
  source_type: 'nvoids' as const,
  source_id: 200,
  source_link_url: 'https://nvoids.test/200',
  active_lead_id: 2000,
  version_count: 1,
  is_recruiter: true,
  is_employer: true,
  recruiter_relevance_score: 92,
  status: 'Active' as const,
  flagged: false,
  created_at: '2026-08-18T10:00:00Z',
  updated_at: '2026-08-18T13:00:00Z',
}

const recruiter: RecruiterNumberCard = {
  ...contactBase,
  recruiter_name: 'Global Talent Ltd',
  designation: 'Recruiter',
  recruiter_email: 'talent@example.com',
  first_detected_email_id: null,
  linkedin_url: '',
  total_opportunity_count: 1,
  last_email_received_at: null,
}

const employer: EmployerNumberCard = {
  ...contactBase,
  owner_name: 'Hiring Desk',
  source_email_id: null,
}

describe('PremiumNumbersPage', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    vi.restoreAllMocks()
    while (cleanups.length) cleanups.pop()?.()
  })

  it('loads a unified inventory, opens an accessible detail panel, and dispatches a mixed rescore', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 1 })
      if (url.includes('/number-review?')) return jsonResponse({ items: [review], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-numbers?')) return jsonResponse({ items: url.includes('flagged=true') ? [] : [recruiter], next_cursor: null, has_next: false })
      if (url.includes('/employer-numbers?')) return jsonResponse({ items: url.includes('flagged=true') ? [] : [employer], next_cursor: null, has_next: false })
      if (init?.method === 'POST') return jsonResponse({ results: [] })
      if (url.includes('/versions')) return jsonResponse([])
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
        <PremiumNumbersPage
          apiBase="http://localhost:8000"
          mailDate={null}
          emailSearchTarget={null}
          refreshToken={0}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    expect(container.textContent).toContain('TechFlow Inc.')
    expect(container.textContent).toContain('Global Talent Ltd')
    expect(container.textContent).toContain('Recruiter')
    expect(container.textContent).toContain('Employer')

    const contactRow = Array.from(container.querySelectorAll('tbody tr')).find((row) => row.textContent?.includes('+44 20 7946 0958'))
    await act(async () => { contactRow?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(document.querySelector('[role="dialog"]')).not.toBeNull()
    await act(async () => { document.querySelector('[role="dialog"]')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
    expect(document.querySelector('[role="dialog"]')).toBeNull()

    await act(async () => {
      container.querySelector<HTMLInputElement>('input[aria-label="Select +1 (555) 019-2834"]')?.click()
      container.querySelector<HTMLInputElement>('input[aria-label="Select +44 20 7946 0958"]')?.click()
    })
    expect(container.textContent).toContain('2 Selected')
    const rescore = Array.from(container.querySelectorAll<HTMLButtonElement>('.selectionActions button')).find((button) => button.textContent === 'Rescore')
    await act(async () => {
      rescore?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/number-review/bulk-rescore') && init?.method === 'POST')).toBe(true)
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/recruiter-numbers/bulk-rescore') && init?.method === 'POST')).toBe(true)
    expect(container.textContent).toContain('Rescored')
  })
})
