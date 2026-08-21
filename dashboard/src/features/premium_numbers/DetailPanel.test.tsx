// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DetailPanel from './DetailPanel'
import type { InventoryRow, PremiumNumberVersion, RecruiterNumberCard } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const recruiter: RecruiterNumberCard = {
  id: 613,
  normalized_phone_number: '16144959222',
  display_phone_number: '(614) 495-9222',
  company: 'United Software Group Inc',
  source_type: 'nvoids',
  source_id: 200,
  source_link_url: null,
  active_lead_id: 2147,
  version_count: 2,
  is_recruiter: true,
  is_employer: false,
  recruiter_relevance_score: 75,
  status: 'Active',
  flagged: false,
  created_at: '2026-08-18T10:00:00Z',
  updated_at: '2026-08-20T22:07:38Z',
  recruiter_name: 'Ravish Khan',
  designation: 'Recruiter',
  recruiter_email: 'ravish.k@usgrpinc.com',
  first_detected_email_id: null,
  linkedin_url: '',
  total_opportunity_count: 3,
  last_email_received_at: null,
}

const row: InventoryRow = {
  key: 'contact:613',
  kind: 'contact',
  id: 613,
  number: '(614) 495-9222',
  owner: 'Ravish Khan',
  company: 'United Software Group Inc',
  categories: ['Recruiter'],
  status: 'Active',
  score: 75,
  sourceType: 'nvoids',
  lastCheckedAt: '2026-08-20T22:07:38Z',
  recruiter,
}

const versions: PremiumNumberVersion[] = [
  {
    id: 2200,
    role: 'recruiter',
    owner_name: 'Unknown',
    company: 'Unknown',
    designation: 'Unknown',
    contact_email: '',
    confidence: 'low',
    extraction_source: 'legacy_snapshot',
    recruiter_email_id: null,
    external_opportunity_id: null,
    source_url: null,
    linkedin_url: '',
    created_at: '2026-08-20T10:00:00Z',
  },
  {
    id: 2147,
    role: 'recruiter',
    owner_name: 'Ravish Khan',
    company: 'United Software Group Inc',
    designation: 'Recruiter',
    contact_email: 'ravish.k@usgrpinc.com',
    confidence: 'high',
    extraction_source: 'ai',
    recruiter_email_id: null,
    external_opportunity_id: null,
    source_url: null,
    linkedin_url: '',
    created_at: '2026-08-20T09:00:00Z',
  },
]

describe('DetailPanel', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    vi.restoreAllMocks()
    while (cleanups.length) cleanups.pop()?.()
  })

  it('closes an open profile edit form after switching versions instead of showing stale data', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/versions')) return jsonResponse(versions)
      if (url.includes('/select-version/')) return jsonResponse({})
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
        <DetailPanel
          apiBase="http://localhost:8000"
          row={row}
          busy={false}
          returnFocusRef={{ current: null }}
          onClose={vi.fn()}
          onAction={vi.fn().mockResolvedValue(undefined)}
          onReload={vi.fn().mockResolvedValue(undefined)}
          onError={vi.fn()}
          onToast={vi.fn()}
        />,
      )
    })
    await act(async () => { await Promise.resolve() })

    const editButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Edit')
    await act(async () => { editButton?.click() })
    expect(container.querySelector<HTMLInputElement>('.detailFormGrid input')?.value).toBe('Ravish Khan')

    const select = container.querySelector<HTMLSelectElement>('select')!
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      nativeSetter.call(select, '2200')
      select.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })

    expect(container.querySelector('.detailFormGrid')).toBeNull()
  })
})
