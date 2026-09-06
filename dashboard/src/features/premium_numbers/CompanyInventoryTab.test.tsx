// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PremiumNumbersPage from './PremiumNumbersPage'
import type { CompanyCard, EmployerNumberCard, RecruiterNumberCard } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const contactBase = {
  normalized_phone_number: '12104856386',
  display_phone_number: '(210) 485-6386',
  company: 'Fusion Global Technologies',
  source_type: 'gmail' as const,
  source_id: 1,
  source_link_url: null,
  active_lead_id: null,
  version_count: 1,
  seen_count: 1,
  is_recruiter: true,
  is_employer: false,
  recruiter_relevance_score: 40,
  status: 'Active' as const,
  flagged: false,
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
  linkedin_url: '',
  recruiter_verification_level: 'unverified',
  do_not_work_again: false,
  do_not_work_again_reason: '',
}

const mahesh: RecruiterNumberCard = {
  ...contactBase,
  id: 41,
  recruiter_name: 'T Mahesh Royal',
  designation: 'Recruiter',
  recruiter_email: 'mahesh@fusiongts.com',
  first_detected_email_id: null,
  total_opportunity_count: 0,
  last_email_received_at: null,
}

const ravi: EmployerNumberCard = {
  ...contactBase,
  id: 42,
  is_recruiter: false,
  is_employer: true,
  display_phone_number: '(210) 485-6387',
  owner_name: 'Ravi K',
  designation: 'Unknown',
  employer_email: 'ravi@fusiongts.com',
  source_email_id: null,
}

const fusion: CompanyCard = {
  key: 'domain:fusiongts.com',
  name: 'Fusion Global Technologies',
  domain: 'fusiongts.com',
  contact_count: 2,
  recruiter_count: 1,
  employer_count: 1,
  lastCheckedAt: '2026-09-01T10:00:00Z',
  contacts: [
    { key: 'contact:41', kind: 'contact', id: 41, number: mahesh.display_phone_number, owner: 'T Mahesh Royal', company: 'Fusion Global Technologies', categories: ['Recruiter'], status: 'Active', score: 40, sourceType: 'gmail', lastCheckedAt: mahesh.updated_at, recruiter: mahesh },
    { key: 'contact:42', kind: 'contact', id: 42, number: ravi.display_phone_number, owner: 'Ravi K', company: 'Fusion Global Technologies', categories: ['Employer'], status: 'Active', score: null, sourceType: 'gmail', lastCheckedAt: ravi.updated_at, employer: ravi },
  ],
}

const nameless: CompanyCard = {
  key: 'company:horizon softech inc',
  name: 'Horizon Softech Inc',
  domain: '',
  contact_count: 1,
  recruiter_count: 0,
  employer_count: 1,
  lastCheckedAt: '2026-09-01T09:00:00Z',
  contacts: [
    { key: 'contact:43', kind: 'contact', id: 43, number: '(470) 313-6209', owner: 'Pranay Gondela', company: 'Horizon Softech Inc', categories: ['Employer'], status: 'Active', score: 5, sourceType: 'gmail', lastCheckedAt: '2026-09-01T09:00:00Z', employer: { ...ravi, id: 43, owner_name: 'Pranay Gondela', company: 'Horizon Softech Inc', employer_email: '' } },
  ],
}

function stubFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
    if (url.includes('/premium-numbers/companies?')) return jsonResponse({ items: [fusion, nameless], total: 2, next_cursor: null, has_next: false })
    if (url.includes('/premium-numbers/inventory?')) return jsonResponse({ items: [], total: 0, next_cursor: null, has_next: false })
    return jsonResponse({ detail: 'not found' }, 404)
  })
}

describe('CompanyInventoryTab', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    vi.restoreAllMocks()
    while (cleanups.length) cleanups.pop()?.()
  })

  function mount(props: Partial<Parameters<typeof PremiumNumbersPage>[0]> = {}) {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })
    const paint = async (overrides: Partial<Parameters<typeof PremiumNumbersPage>[0]> = {}) => {
      await act(async () => {
        root.render(
          <PremiumNumbersPage
            apiBase="http://localhost:8000"
            mailDate={null}
            emailSearchTarget={null}
            refreshToken={0}
            applicationsEnabled
            onPendingCountChange={vi.fn()}
            {...props}
            {...overrides}
          />,
        )
      })
      await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    }
    return { container, paint }
  }

  async function render(onNavigateToInventory?: (filters: Record<string, string>) => void, sortValue?: string) {
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
          onNavigateToInventory={onNavigateToInventory}
          sortValue={sortValue}
        />,
      )
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    const companiesTab = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find((button) => button.textContent === 'Company Inventory')
    await act(async () => { companiesTab?.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    return container
  }

  it('lists one card per company with its contacts', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const container = await render()

    const cards = Array.from(container.querySelectorAll('.companyCard'))
    expect(cards).toHaveLength(2)
    expect(cards[0].querySelector('h3')?.textContent).toBe('Fusion Global Technologies')
    expect(cards[0].querySelector('.companyDomain')?.textContent).toBe('fusiongts.com')
    expect(cards[0].textContent).toContain('2 contacts')
    expect(cards[0].textContent).toContain('T Mahesh Royal')
    expect(cards[0].textContent).toContain('Ravi K')
    // A company that has no email address yet still gets a card, labelled honestly.
    expect(cards[1].querySelector('.companyDomain')?.textContent).toBe('No email domain')
    expect(container.textContent).toContain('Showing 1 to 2 of 2 companies')
  })

  it('sends a contact link to Number Inventory scoped to its domain, with the detail panel open', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const navigate = vi.fn()
    const container = await render(navigate)

    const link = Array.from(container.querySelectorAll<HTMLButtonElement>('.companyContactLink')).find((button) => button.textContent === 'Ravi K')
    await act(async () => { link?.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    expect(navigate).toHaveBeenCalledWith({ domain: 'fusiongts.com' })
    // The card carries the whole inventory row, so the panel opens without the
    // contact ever having to appear on the loaded page of Number Inventory.
    const panel = document.querySelector('[role="dialog"]')
    expect(panel?.getAttribute('aria-label')).toContain('(210) 485-6387')
    // The employer card the company sent along survives even though the row is
    // absent from the loaded inventory page and the refetch 404s.
    expect(panel?.textContent).toContain('Employer profile')
    expect(panel?.textContent).toContain('Ravi K')
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Number Inventory')
  })

  // Number Inventory's endpoint 422s on this tab's sort vocabulary, so nothing
  // may fetch it while Company Inventory is the tab on screen - it used to, and
  // raised an error toast over a perfectly healthy page.
  it('does not fetch Number Inventory while the companies tab is showing', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    const { paint } = mount({ activeTab: 'inventory', sortValue: 'newest' })
    await paint()
    await paint({ activeTab: 'companies', sortValue: 'most_contacts' })

    const inventoryCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/premium-numbers/inventory?'))
    expect(inventoryCalls.length).toBeGreaterThan(0)
    expect(inventoryCalls.every(([url]) => String(url).includes('sort=newest'))).toBe(true)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/premium-numbers/companies?') && String(url).includes('sort=most_contacts'))).toBe(true)
  })

  it('falls back to a name search for a company with no email domain', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const navigate = vi.fn()
    const container = await render(navigate)

    const view = Array.from(container.querySelectorAll<HTMLButtonElement>('.companyCard button')).filter((button) => button.textContent === 'View in Number Inventory')
    await act(async () => { view[1]?.click() })

    expect(navigate).toHaveBeenCalledWith({ q: 'Horizon Softech Inc' })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
})
