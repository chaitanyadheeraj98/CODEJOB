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
  recruiter_verification_level: 'unverified',
  do_not_work_again: false,
  do_not_work_again_reason: '',
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
      if (url.endsWith('/recruiter-numbers/613/reputation')) return jsonResponse({
        recruiter_contact_id: 613,
        history_label: 'limited_history',
        outreach_count: 2,
        replies_count: 1,
        median_first_reply_business_days: 1.5,
        submissions_count: 1,
        interviews_after_submission_count: 0,
        offers_count: 0,
        last_active_at: '2026-08-20T12:00:00Z',
      })
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
    expect(container.textContent).toContain('Limited history')
    expect(container.textContent).toContain('1.5 business days')

    const editButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Edit')
    await act(async () => { editButton?.click() })
    expect(container.querySelector<HTMLInputElement>('.detailFormGrid input')?.value).toBe('Ravish Khan')

    const select = Array.from(container.querySelectorAll('label'))
      .find((item) => item.textContent?.includes('Active recruiter version'))
      ?.querySelector<HTMLSelectElement>('select')
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      nativeSetter.call(select, '2200')
      select?.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })

    expect(container.querySelector('.detailFormGrid')).toBeNull()
  })

  it('saves recruiter verification and do-not-work-again fields', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/versions')) return jsonResponse(versions)
      if (url.endsWith('/recruiter-numbers/613') && init?.method === 'PATCH') {
        return jsonResponse({ ...recruiter, ...JSON.parse(String(init.body)) })
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
    const edit = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Edit')
    await act(async () => { edit?.click() })

    const verification = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.includes('Verification'))?.querySelector('select')
    const selectSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      selectSetter.call(verification, 'trusted')
      verification?.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const doNotWork = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.includes('Do not work with again'))?.querySelector<HTMLInputElement>('input')
    await act(async () => { doNotWork?.click() })
    const reason = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.startsWith('Reason'))?.querySelector('input')
    const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      inputSetter.call(reason, 'Duplicate submissions')
      reason?.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const save = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Save')
    await act(async () => {
      save?.click()
      await Promise.resolve()
    })
    const patchCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/recruiter-numbers/613') && init?.method === 'PATCH')
    expect(JSON.parse(String(patchCall?.[1]?.body))).toMatchObject({
      recruiter_verification_level: 'trusted',
      do_not_work_again: true,
      do_not_work_again_reason: 'Duplicate submissions',
    })
  })
})
