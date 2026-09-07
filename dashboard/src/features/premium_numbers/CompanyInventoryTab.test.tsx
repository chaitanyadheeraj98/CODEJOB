// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import PremiumNumbersPage from './PremiumNumbersPage'
import type { CompanyCard, CompanyDetail, EmployerNumberCard, RecruiterNumberCard } from './types'

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
  recruiter_verification_level: 'unverified' as const,
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
  active_count: 2,
  flagged_count: 0,
  unscored_count: 0,
  opportunity_count: 7,
  application_count: 12,
  conversation_count: 4,
  replied_count: 3,
  lastCheckedAt: '2026-09-01T10:00:00Z',
}

const nameless: CompanyCard = {
  key: 'company:horizon softech inc',
  name: 'Horizon Softech Inc',
  domain: '',
  contact_count: 1,
  recruiter_count: 0,
  employer_count: 1,
  active_count: 0,
  flagged_count: 1,
  unscored_count: 0,
  opportunity_count: 0,
  application_count: 0,
  conversation_count: 0,
  replied_count: 0,
  lastCheckedAt: '2026-09-01T09:00:00Z',
}

const fusionDetail: CompanyDetail = {
  company: fusion,
  contacts: [
    { key: 'contact:41', kind: 'contact', id: 41, number: mahesh.display_phone_number, owner: 'T Mahesh Royal', company: 'Fusion Global Technologies', categories: ['Recruiter'], status: 'Active', score: 40, sourceType: 'gmail', lastCheckedAt: mahesh.updated_at, recruiter: mahesh },
    { key: 'contact:42', kind: 'contact', id: 42, number: ravi.display_phone_number, owner: 'Ravi K', company: 'Fusion Global Technologies', categories: ['Employer'], status: 'Active', score: null, sourceType: 'gmail', lastCheckedAt: ravi.updated_at, employer: ravi },
  ],
  opportunities: [{ id: 9, job_title: 'Java Developer', end_client: 'Acme', status: 'New', created_at: '2026-09-01T10:00:00Z' }],
  pipeline: {
    applications: { value: 12, tracked: true },
    submissions: { value: 5, tracked: true },
    interviews: { value: 0, tracked: true },
    submitted_to_client: { value: 0, tracked: false },
    rtrs: { value: 0, tracked: false },
  },
  responsiveness: {
    emails_received: 31,
    conversations: 4,
    replied: 3,
    reply_rate: 0.75,
    last_inbound_at: '2026-09-04T10:00:00Z',
    last_reply_at: '2026-09-05T10:00:00Z',
  },
}

function stubFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/number-review/pending-count')) return jsonResponse({ count: 0 })
    if (url.includes('/premium-numbers/companies/detail?')) return jsonResponse(fusionDetail)
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

  async function render(onNavigateToInventory?: (filters: Record<string, string>) => void) {
    const { container, paint } = mount({ onNavigateToInventory })
    await paint()
    const companiesTab = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find((button) => button.textContent === 'Company Inventory')
    await act(async () => { companiesTab?.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    return container
  }

  async function openRow(container: HTMLElement, text: string) {
    const row = Array.from(container.querySelectorAll('tbody tr')).find((element) => element.textContent?.includes(text))
    await act(async () => { row?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    return document.querySelector('[role="dialog"]')
  }

  it('lists one row per company with the counts a domain filter cannot give you', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const container = await render()

    const rows = Array.from(container.querySelectorAll('tbody tr'))
    expect(rows).toHaveLength(2)
    const cells = Array.from(rows[0].querySelectorAll('td')).map((cell) => cell.textContent)
    expect(cells[0]).toContain('Fusion Global Technologies')
    expect(cells[0]).toContain('fusiongts.com')
    expect(cells[1]).toBe('2')
    expect(cells[2]).toContain('2 Active')
    expect(cells[3]).toBe('7')
    expect(cells[4]).toBe('12')
    expect(cells[5]).toBe('3 of 4')
    // A company with no email address still gets a row, labelled honestly.
    expect(rows[1].textContent).toContain('No email domain')
    expect(container.textContent).toContain('Showing 1 to 2 of 2 companies')
  })

  it('opens a side panel with the pipeline, and marks never-recorded stages as untracked', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const container = await render()
    const panel = await openRow(container, 'Fusion Global Technologies')

    expect(panel?.getAttribute('aria-label')).toBe('Company details for Fusion Global Technologies')
    const tiles = Array.from(panel?.querySelectorAll('.applicationSummary > div') ?? [])
      .map((tile) => [tile.querySelector('strong')?.textContent, tile.querySelector('span')?.textContent])
    expect(tiles).toContainEqual(['12', 'Applications'])
    expect(tiles).toContainEqual(['5', 'Submissions'])
    // A stage that is in use but empty here is a real zero about the company.
    expect(tiles).toContainEqual(['0', 'Interviews'])
    // A stage nobody has ever recorded must not claim the company produced none.
    expect(tiles).toContainEqual(['--', 'RTRs · not tracked yet'])
    expect(tiles).toContainEqual(['--', 'Submitted to client · not tracked yet'])
    expect(tiles).toContainEqual(['31', 'Emails received'])
    expect(tiles).toContainEqual(['75%', 'Reply rate'])
    expect(panel?.textContent).toContain('Java Developer')
  })

  it('sends a contact from the panel to Number Inventory scoped to its domain', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const navigate = vi.fn()
    const container = await render(navigate)
    const panel = await openRow(container, 'Fusion Global Technologies')

    const link = Array.from(panel?.querySelectorAll<HTMLButtonElement>('.companyContactLink') ?? []).find((button) => button.textContent === 'Ravi K')
    await act(async () => { link?.click() })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })

    expect(navigate).toHaveBeenCalledWith({ domain: 'fusiongts.com' })
    const contactPanel = document.querySelector('[role="dialog"]')
    expect(contactPanel?.getAttribute('aria-label')).toContain('(210) 485-6387')
    expect(contactPanel?.textContent).toContain('Employer profile')
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Number Inventory')
  })

  it('closes the panel on Escape and returns focus to the row that opened it', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const container = await render()
    const panel = await openRow(container, 'Fusion Global Technologies')

    await act(async () => { panel?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(container.querySelector('tbody tr'))
  })

  it('falls back to a name search for a company with no email domain', async () => {
    vi.stubGlobal('fetch', stubFetch())
    const navigate = vi.fn()
    const container = await render(navigate)
    await openRow(container, 'Horizon Softech Inc')

    const view = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')).find((button) => button.textContent === 'View in Number Inventory')
    await act(async () => { view?.click() })
    expect(navigate).toHaveBeenCalledWith({ q: 'Horizon Softech Inc' })
  })

  // Number Inventory's endpoint 422s on this tab's sort vocabulary, so nothing
  // may fetch it while Company Inventory is the tab on screen.
  it('does not fetch Number Inventory while the companies tab is showing', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    const { paint } = mount({ activeTab: 'inventory', sortValue: 'newest' })
    await paint()
    await paint({ activeTab: 'companies', sortValue: 'most_applications' })

    const inventoryCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/premium-numbers/inventory?'))
    expect(inventoryCalls.length).toBeGreaterThan(0)
    expect(inventoryCalls.every(([url]) => String(url).includes('sort=newest'))).toBe(true)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/premium-numbers/companies?') && String(url).includes('sort=most_applications'))).toBe(true)
  })
})
