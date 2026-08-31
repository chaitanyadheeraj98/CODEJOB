// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EmailSearchHit } from '../../emailSearch'
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
  role: 'recruiter',
  reason_code: 'new_number',
  occurrence_count: 1,
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
  seen_count: 1,
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
  recruiter_verification_level: 'unverified',
  do_not_work_again: false,
  do_not_work_again_reason: '',
  total_opportunity_count: 1,
  last_email_received_at: null,
}

const employer: EmployerNumberCard = {
  ...contactBase,
  owner_name: 'Hiring Desk',
  designation: 'Unknown',
  employer_email: '',
  source_email_id: null,
  linkedin_url: '',
  recruiter_verification_level: 'unverified',
  do_not_work_again: false,
  do_not_work_again_reason: '',
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
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [
        { key: `review:${review.id}`, kind: 'review', id: review.id, number: review.display_phone_number, owner: review.owner_name, company: review.company, categories: ['Recruiter'], status: 'Pending', score: review.recruiter_relevance_score, sourceType: 'gmail', lastCheckedAt: review.updated_at, review },
        { key: `contact:${recruiter.id}`, kind: 'contact', id: recruiter.id, number: recruiter.display_phone_number, owner: recruiter.recruiter_name, company: recruiter.company, categories: ['Recruiter', 'Employer'], status: 'Flagged', score: recruiter.recruiter_relevance_score, sourceType: 'nvoids', lastCheckedAt: recruiter.updated_at, recruiter, employer },
      ], total: 2, next_cursor: null, has_next: false })
      if (url.includes('/number-review?')) return jsonResponse({ items: [review], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-numbers?')) return jsonResponse({ items: url.includes('flagged=true') ? [] : [recruiter], next_cursor: null, has_next: false })
      if (url.includes('/employer-numbers?')) return jsonResponse({ items: url.includes('flagged=true') ? [] : [employer], next_cursor: null, has_next: false })
      if (url.endsWith('/applications/dashboard-summary')) return jsonResponse({ due_today: 0, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0 })
      if (url.includes('/applications?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
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
          applicationsEnabled
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

    const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).map((button) => button.textContent)
    expect(tabs).toEqual(['Number Inventory', 'Recruiter Opportunities', 'Recycle Bin'])
  })

  it('previews and merges two selected duplicate contacts', async () => {
    const duplicateEmployer: EmployerNumberCard = { ...employer, id: 806, owner_name: 'Prashanth Kinnera', company: 'Horizons of Tech', display_phone_number: '(770) 824-0630' }
    const staleEmployer: EmployerNumberCard = { ...employer, id: 823, owner_name: 'Prashanth Kinnera', company: 'Horizons of Tech', display_phone_number: '(972) 756-1212' }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [
        { key: `contact:${duplicateEmployer.id}`, kind: 'contact', id: duplicateEmployer.id, number: duplicateEmployer.display_phone_number, owner: duplicateEmployer.owner_name, company: duplicateEmployer.company, categories: ['Employer'], status: 'Active', score: null, sourceType: 'gmail', lastCheckedAt: duplicateEmployer.updated_at, employer: duplicateEmployer },
        { key: `contact:${staleEmployer.id}`, kind: 'contact', id: staleEmployer.id, number: staleEmployer.display_phone_number, owner: staleEmployer.owner_name, company: staleEmployer.company, categories: ['Employer'], status: 'Active', score: null, sourceType: 'gmail', lastCheckedAt: staleEmployer.updated_at, employer: staleEmployer },
      ], total: 2, next_cursor: null, has_next: false })
      if (url.includes('/number-review?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-numbers?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/employer-numbers?')) return jsonResponse({ items: url.includes('flagged=true') ? [] : [duplicateEmployer, staleEmployer], next_cursor: null, has_next: false })
      if (url.includes('/applications/dashboard-summary')) return jsonResponse({ due_today: 0, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0 })
      if (url.includes('/applications?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/premium-numbers/contacts/merge-preview')) return jsonResponse({
        contact_a: { id: 806, recruiter_name: 'Unknown', owner_name: 'Prashanth Kinnera', company: 'Horizons of Tech', recruiter_email: '', employer_email: 'kprashanth@horizonsoftech.net', normalized_phone_number: '17708240630', display_phone_number: '(770) 824-0630', is_recruiter: false, is_employer: true, lead_count: 1, latest_evidence_at: '2026-08-28T14:00:00Z', leads: [] },
        contact_b: { id: 823, recruiter_name: 'Prashanth Kinnera', owner_name: 'Prashanth Kinnera', company: 'Horizons of Tech', recruiter_email: 'kprashanth@horizonsoftech.net', employer_email: 'kprashanth@horizonsoftech.net', normalized_phone_number: '19727561212', display_phone_number: '(972) 756-1212', is_recruiter: false, is_employer: true, lead_count: 0, latest_evidence_at: null, leads: [] },
      })
      if (url.includes('/premium-numbers/contacts/merge') && init?.method === 'POST') return jsonResponse({ canonical_contact_id: 806, loser_contact_id: 823, status: 'merged' })
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
          applicationsEnabled
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const mergeButton = () => Array.from(container.querySelectorAll<HTMLButtonElement>('.selectionActions button')).find((button) => button.textContent?.includes('Merge'))
    await act(async () => {
      container.querySelector<HTMLInputElement>('input[aria-label="Select (770) 824-0630"]')?.click()
    })
    expect(mergeButton()?.disabled).toBe(true)

    await act(async () => {
      container.querySelector<HTMLInputElement>('input[aria-label="Select (972) 756-1212"]')?.click()
    })
    expect(mergeButton()?.disabled).toBe(false)

    await act(async () => { mergeButton()?.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 50)) })

    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/premium-numbers/contacts/merge-preview?contact_id_a=806&contact_id_b=823'))).toBe(true)
    expect(container.textContent).toContain('Merge Duplicate Contacts')

    const keepButtons = Array.from(container.querySelectorAll<HTMLButtonElement>('.mergePreviewCard button')).filter((button) => button.textContent === 'Keep this one')
    expect(keepButtons).toHaveLength(2)
    await act(async () => { keepButtons[0]?.click() })

    const confirmButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.startsWith('Confirm'))
    await act(async () => {
      confirmButton?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })

    const mergeCall = fetchMock.mock.calls.find(([url, mergeInit]) => String(url).endsWith('/premium-numbers/contacts/merge') && mergeInit?.method === 'POST')
    expect(mergeCall).toBeDefined()
    expect(JSON.parse(String(mergeCall?.[1]?.body))).toEqual({ canonical_contact_id: 806, loser_contact_id: 823 })
    expect(container.textContent).toContain('Contacts merged')
    expect(container.querySelector('[aria-label="Merge contacts"]')).toBeNull()
  })

  it('opens the merge modal directly from a phone-conflict rescore instead of just reporting it', async () => {
    const staleEmployer: EmployerNumberCard = { ...employer, id: 993, owner_name: 'Prashanth Kinnera', company: 'Horizon Soft Tech', display_phone_number: '(512) 352-9739' }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [
        { key: `contact:${staleEmployer.id}`, kind: 'contact', id: staleEmployer.id, number: staleEmployer.display_phone_number, owner: staleEmployer.owner_name, company: staleEmployer.company, categories: ['Employer'], status: 'Active', score: null, sourceType: 'gmail', lastCheckedAt: staleEmployer.updated_at, employer: staleEmployer },
      ], total: 1, next_cursor: null, has_next: false })
      if (url.includes('/number-review?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-numbers?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/employer-numbers?')) return jsonResponse({ items: url.includes('flagged=true') ? [] : [staleEmployer], next_cursor: null, has_next: false })
      if (url.includes('/applications/dashboard-summary')) return jsonResponse({ due_today: 0, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0 })
      if (url.includes('/applications?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/versions')) return jsonResponse([])
      if (/\/employer-numbers\/\d+\/rescore$/.test(url) && init?.method === 'POST') {
        return new Response(JSON.stringify({ detail: { message: '(770) 824-0630 is already linked to a different contact', conflicting_contact_id: 806 } }), { status: 409, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/premium-numbers/contacts/merge-preview')) return jsonResponse({
        contact_a: { id: 993, recruiter_name: 'Unknown', owner_name: 'Prashanth Kinnera', company: 'Horizon Soft Tech', recruiter_email: '', employer_email: 'kprashanth@horizonsoftech.net', normalized_phone_number: '15123529739', display_phone_number: '(512) 352-9739', is_recruiter: false, is_employer: true, lead_count: 1, latest_evidence_at: null, leads: [] },
        contact_b: { id: 806, recruiter_name: 'Unknown', owner_name: 'Prashanth Kinnera', company: 'Horizons of Tech', recruiter_email: '', employer_email: 'kprashanth@horizonsoftech.net', normalized_phone_number: '17708240630', display_phone_number: '(770) 824-0630', is_recruiter: false, is_employer: true, lead_count: 1, latest_evidence_at: null, leads: [] },
      })
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
          applicationsEnabled
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const contactRow = Array.from(container.querySelectorAll('tbody tr')).find((row) => row.textContent?.includes('(512) 352-9739'))
    await act(async () => { contactRow?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(document.querySelector('[role="dialog"]')).not.toBeNull()

    const rescoreButton = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')).find((button) => button.textContent === 'Rescore')
    await act(async () => {
      rescoreButton?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 100))
    })

    // The modal opens right away with an actionable next step - a brief toast explaining
    // why can still show alongside it, that's just context, not a competing dead end.
    expect(container.querySelector('[aria-label="Merge contacts"]')).not.toBeNull()
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 100)) })
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/premium-numbers/contacts/merge-preview?contact_id_a=993&contact_id_b=806'))).toBe(true)
  })

  it('opens the merge modal when marking recomputes a split identity', async () => {
    const splitReview = { ...review, reason_code: 'new_number', target_contact_id: null, secondary_contact_id: null }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 1 })
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [
        { key: `review:${splitReview.id}`, kind: 'review', id: splitReview.id, number: splitReview.display_phone_number, owner: splitReview.owner_name, company: splitReview.company, categories: ['Recruiter'], status: 'Pending', score: splitReview.recruiter_relevance_score, sourceType: 'gmail', lastCheckedAt: splitReview.updated_at, review: splitReview },
      ], total: 1, next_cursor: null, has_next: false })
      if (url.endsWith('/number-review/bulk-mark-recruiter') && init?.method === 'POST') {
        return new Response(JSON.stringify({ detail: {
          message: 'Resolve the identity conflict on this card before marking it.',
          target_contact_id: 562,
          secondary_contact_id: 588,
        } }), { status: 409, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/premium-numbers/contacts/merge-preview')) return jsonResponse({
        contact_a: { id: 562, recruiter_name: 'Phone Owner', owner_name: 'Unknown', company: 'Acme', recruiter_email: '', employer_email: '', normalized_phone_number: '15550192834', display_phone_number: '+1 (555) 019-2834', is_recruiter: true, is_employer: false, lead_count: 1, latest_evidence_at: null, leads: [] },
        contact_b: { id: 588, recruiter_name: 'Email Owner', owner_name: 'Unknown', company: 'Beta', recruiter_email: 'sender@example.com', employer_email: '', normalized_phone_number: '15550190000', display_phone_number: '+1 (555) 019-0000', is_recruiter: true, is_employer: false, lead_count: 1, latest_evidence_at: null, leads: [] },
      })
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
          applicationsEnabled={false}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    const mark = Array.from(container.querySelectorAll<HTMLButtonElement>('tbody .inventoryMenuPopover button')).find((button) => button.textContent === 'Mark as Recruiter')
    expect(mark?.disabled).toBe(false)
    await act(async () => {
      mark?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 300))
    })

    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/number-review/bulk-mark-recruiter'))).toBe(true)
    expect(container.querySelector('[aria-label="Merge contacts"]')).not.toBeNull()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/premium-numbers/contacts/merge-preview?contact_id_a=562&contact_id_b=588'))).toBe(true)
  })

  it('highlights a promoted premium_number_lead hit via its detail.contact_id', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [{ key: `contact:${recruiter.id}`, kind: 'contact', id: recruiter.id, number: recruiter.display_phone_number, owner: recruiter.recruiter_name, company: recruiter.company, categories: ['Recruiter', 'Employer'], status: 'Flagged', score: recruiter.recruiter_relevance_score, sourceType: 'nvoids', lastCheckedAt: recruiter.updated_at, recruiter }], total: 1, next_cursor: null, has_next: false })
      if (url.includes('/number-review?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-numbers?')) return jsonResponse({ items: [recruiter], next_cursor: null, has_next: false })
      if (url.includes('/employer-numbers?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
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

    const leadHit: EmailSearchHit = {
      section: 'premium_numbers',
      recruiter_email_id: 500,
      sender: 'Global Talent Ltd',
      subject: 'Role',
      state: 'approved_sent',
      detail: { premium_number_lead_id: 9, contact_id: recruiter.id },
      occurred_at: '2026-08-18T13:00:00Z',
    }

    await act(async () => {
      root.render(
        <PremiumNumbersPage
          apiBase="http://localhost:8000"
          mailDate={null}
          emailSearchTarget={leadHit}
          refreshToken={0}
          applicationsEnabled={false}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const highlighted = container.querySelector('tr.emailSearchHighlight')
    expect(highlighted?.textContent).toContain('Global Talent Ltd')
  })

  it('recovers the open detail panel by id when a reload pushes its row off the loaded page', async () => {
    const inventoryRow = { key: `contact:${recruiter.id}`, kind: 'contact', id: recruiter.id, number: recruiter.display_phone_number, owner: recruiter.recruiter_name, company: recruiter.company, categories: ['Recruiter'], status: 'Active', score: recruiter.recruiter_relevance_score, sourceType: 'nvoids', lastCheckedAt: recruiter.updated_at, recruiter }
    const versions = [
      { id: 2000, role: 'recruiter', owner_name: recruiter.recruiter_name, company: recruiter.company, designation: 'Recruiter', contact_email: recruiter.recruiter_email, confidence: 'high', extraction_source: 'ai', recruiter_email_id: null, external_opportunity_id: null, source_url: null, linkedin_url: '', created_at: '2026-08-18T13:00:00Z' },
      { id: 2001, role: 'recruiter', owner_name: recruiter.recruiter_name, company: recruiter.company, designation: 'Recruiter', contact_email: 'legacy@example.com', confidence: 'low', extraction_source: 'legacy_snapshot', recruiter_email_id: null, external_opportunity_id: null, source_url: null, linkedin_url: '', created_at: '2026-08-19T05:00:00Z' },
    ]
    const freshRecruiter: RecruiterNumberCard = { ...recruiter, active_lead_id: 2001, recruiter_email: 'legacy@example.com' }
    let inventoryCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) {
        inventoryCalls += 1
        // After the version switch, the row's updated_at moves it off this (still-page-1)
        // response - simulating it sorting onto a different page under sort=newest.
        const items = inventoryCalls === 1 ? [inventoryRow] : []
        return jsonResponse({ items, total: inventoryCalls === 1 ? 1 : 0, next_cursor: null, has_next: false })
      }
      if (url.includes('/recruiter-numbers/2/versions')) return jsonResponse(versions)
      if (url.includes('/recruiter-numbers/2/select-version/2001') && init?.method === 'POST') return jsonResponse({ id: 2, active_lead_id: 2001, status: 'selected' })
      if (url.endsWith('/recruiter-numbers/2')) return jsonResponse(freshRecruiter)
      if (url.includes('/number-review?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/recruiter-numbers?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/employer-numbers?')) return jsonResponse({ items: [], next_cursor: null, has_next: false })
      if (url.includes('/reputation')) return jsonResponse({ history_label: 'limited_history', outreach_count: 0, replies_count: 0, median_first_reply_business_days: null, submissions_count: 0, interviews_after_submission_count: 0, offers_count: 0, last_active_at: null })
      if (url.includes('/extraction-audit')) return jsonResponse({ items: [] })
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
          applicationsEnabled={false}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const contactRow = Array.from(container.querySelectorAll('tbody tr')).find((row) => row.textContent?.includes(recruiter.display_phone_number))
    await act(async () => { contactRow?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 50)) })

    expect(container.querySelector('[role="dialog"]')?.textContent).toContain('talent@example.com')

    const select = document.querySelector<HTMLSelectElement>('[role="dialog"] select')
    expect(select).not.toBeNull()
    await act(async () => {
      select!.value = '2001'
      select!.dispatchEvent(new Event('change', { bubbles: true }))
      await new Promise((resolve) => window.setTimeout(resolve, 300))
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/recruiter-numbers/2/select-version/2001') && init?.method === 'POST')).toBe(true)
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/recruiter-numbers/2'))).toBe(true)
    expect(container.querySelector('[role="dialog"]')?.textContent).toContain('legacy@example.com')
  })

  it('shows an inventory load failure as a dismissable error toast, not a persistent page banner', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) return new Response('Internal Server Error', { status: 500 })
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
          applicationsEnabled={false}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    expect(container.querySelector('.errorBanner')).toBeNull()
    const toast = container.querySelector('.actionToast')
    expect(toast).not.toBeNull()
    expect(toast?.className).toContain('actionToast--error')
    expect(toast?.textContent).toContain('Failed to load premium number inventory')
  })

  it('merges duplicate contacts from the Number Inventory toolbar', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [], total: 0, next_cursor: null, has_next: false })
      if (url.endsWith('/premium-numbers/contacts/backfill-duplicates') && init?.method === 'POST') {
        return jsonResponse({ groups_merged: 23, contacts_merged: 26 })
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
        <PremiumNumbersPage
          apiBase="http://localhost:8000"
          mailDate={null}
          emailSearchTarget={null}
          refreshToken={0}
          applicationsEnabled={false}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const mergeButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Merge Duplicate Contacts')
    expect(mergeButton).toBeDefined()
    await act(async () => {
      mergeButton?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 300))
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/premium-numbers/contacts/backfill-duplicates') && init?.method === 'POST')).toBe(true)
    expect(container.querySelector('.actionToast')?.textContent).toContain('Merged 26 duplicate contacts into 23 contacts')
  })

  it('lists deleted contacts in the Recycle Bin tab and restores one', async () => {
    const deletedRow = {
      key: 'contact:9', kind: 'contact', id: 9, number: '(214) 555-0909', owner: 'Deleted Recruiter', company: 'Old Agency',
      categories: ['Recruiter'], status: 'Active', score: 70, sourceType: 'gmail', lastCheckedAt: '2026-08-20T10:00:00Z',
      recruiter: { ...recruiter, id: 9, recruiter_name: 'Deleted Recruiter', company: 'Old Agency' },
    }
    let deleted = true
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [], total: 0, next_cursor: null, has_next: false })
      if (url.includes('/premium-numbers/deleted-contacts?')) return jsonResponse({ items: deleted ? [deletedRow] : [], total: deleted ? 1 : 0, next_cursor: null, has_next: false })
      if (url.endsWith('/premium-numbers/contacts/9/restore') && init?.method === 'POST') { deleted = false; return jsonResponse({ id: 9, status: 'restored' }) }
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
          applicationsEnabled={false}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]'))
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Number Inventory', 'Recruiter Opportunities', 'Recycle Bin'])
    const recycleBinTab = tabs.find((tab) => tab.textContent === 'Recycle Bin')
    await act(async () => { recycleBinTab?.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    expect(container.textContent).toContain('Deleted Recruiter')
    expect(container.textContent).toContain('Old Agency')

    const restoreButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Restore')
    expect(restoreButton).toBeDefined()
    await act(async () => {
      restoreButton?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 300))
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/premium-numbers/contacts/9/restore') && init?.method === 'POST')).toBe(true)
    expect(container.querySelector('.actionToast')?.textContent).toContain('Restored')
    expect(container.textContent).toContain('Recycle Bin is empty.')
  })

  it('opens a deleted contact\'s details on click and permanently deletes it from the detail panel', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const deletedRow = {
      key: 'contact:9', kind: 'contact', id: 9, number: '(214) 555-0909', owner: 'Deleted Recruiter', company: 'Old Agency',
      categories: ['Recruiter'], status: 'Active', score: 70, sourceType: 'gmail', lastCheckedAt: '2026-08-20T10:00:00Z',
      recruiter: { ...recruiter, id: 9, recruiter_name: 'Deleted Recruiter', company: 'Old Agency' },
    }
    let deleted = true
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
      if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [], total: 0, next_cursor: null, has_next: false })
      if (url.includes('/premium-numbers/deleted-contacts?')) return jsonResponse({ items: deleted ? [deletedRow] : [], total: deleted ? 1 : 0, next_cursor: null, has_next: false })
      if (url.endsWith('/premium-numbers/contacts/9/purge') && init?.method === 'POST') { deleted = false; return jsonResponse({ id: 9, status: 'purged' }) }
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
          applicationsEnabled={false}
          onPendingCountChange={vi.fn()}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const recycleBinTab = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find((tab) => tab.textContent === 'Recycle Bin')
    await act(async () => { recycleBinTab?.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    const row = Array.from(container.querySelectorAll('tbody tr')).find((tr) => tr.textContent?.includes('Deleted Recruiter'))
    await act(async () => { row?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

    const dialog = container.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.textContent).toContain('Deleted Recruiter')
    expect(dialog?.textContent).toContain('Old Agency')

    const deleteForeverButton = Array.from(dialog?.querySelectorAll('button') ?? []).find((button) => button.textContent === 'Delete Forever')
    expect(deleteForeverButton).toBeDefined()
    await act(async () => {
      deleteForeverButton?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 300))
    })

    expect(window.confirm).toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/premium-numbers/contacts/9/purge') && init?.method === 'POST')).toBe(true)
    expect(container.querySelector('.actionToast')?.textContent).toContain('Deleted forever')
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    expect(container.textContent).toContain('Recycle Bin is empty.')
  })
})
