// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DetailPanel from './DetailPanel'
import type { InventoryRow, NumberReviewCard, PremiumNumberVersion, RecruiterNumberCard } from './types'

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
  seen_count: 4,
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
          onPhoneConflict={vi.fn()}
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
          onPhoneConflict={vi.fn()}
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

  it('edits a contact\'s full phone list - primary and secondary - and saves it', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const multiPhoneRecruiter: RecruiterNumberCard = {
      ...recruiter,
      display_phone_number: '(856) 456-1805 ext 1025',
      phones: [
        { phone: '18564561805', extension: '1025', display: '(856) 456-1805 ext 1025', is_primary: true, is_verified: false, label: '' },
        { phone: '18563724625', extension: '', display: '(856) 372-4625', is_primary: false, is_verified: false, label: '' },
      ],
    }
    const multiPhoneRow: InventoryRow = { ...row, recruiter: multiPhoneRecruiter }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/versions')) return jsonResponse(versions)
      if (url.endsWith('/recruiter-numbers/613') && init?.method === 'PATCH') {
        return jsonResponse({ ...multiPhoneRecruiter, ...JSON.parse(String(init.body)) })
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
          row={multiPhoneRow}
          busy={false}
          returnFocusRef={{ current: null }}
          onClose={vi.fn()}
          onAction={vi.fn().mockResolvedValue(undefined)}
          onReload={vi.fn().mockResolvedValue(undefined)}
          onError={vi.fn()}
          onToast={vi.fn()}
          onPhoneConflict={vi.fn()}
        />,
      )
    })
    await act(async () => { await Promise.resolve() })
    const edit = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Edit')
    await act(async () => { edit?.click() })

    const phoneInputs = () => Array.from(container.querySelectorAll<HTMLInputElement>('.phoneListEditorRow input'))
    expect(phoneInputs().map((input) => input.value)).toEqual(['(856) 456-1805 ext 1025', '(856) 372-4625'])

    const addPhone = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === '+ Add phone')
    await act(async () => { addPhone?.click() })
    expect(phoneInputs()).toHaveLength(3)

    const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      inputSetter.call(phoneInputs()[2], '(212) 555-0199')
      phoneInputs()[2].dispatchEvent(new Event('input', { bubbles: true }))
    })

    const save = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Save')
    await act(async () => {
      save?.click()
      await Promise.resolve()
    })

    const patchCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/recruiter-numbers/613') && init?.method === 'PATCH')
    expect(JSON.parse(String(patchCall?.[1]?.body))).toMatchObject({
      phones: ['(856) 456-1805 ext 1025', '(856) 372-4625', '(212) 555-0199'],
    })
  })

  it('edits and saves a contact\'s full email list', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const multiEmailRecruiter: RecruiterNumberCard = {
      ...recruiter,
      emails: [
        { email: 'ravish.k@usgrpinc.com', domain: 'usgrpinc.com', is_primary: true },
        { email: 'ravish@usgrpinc.com', domain: 'usgrpinc.com', is_primary: false },
      ],
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/versions')) return jsonResponse(versions)
      if (url.endsWith('/recruiter-numbers/613') && init?.method === 'PATCH') {
        return jsonResponse({ ...multiEmailRecruiter, ...JSON.parse(String(init.body)) })
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
          row={{ ...row, recruiter: multiEmailRecruiter }}
          busy={false}
          returnFocusRef={{ current: null }}
          onClose={vi.fn()}
          onAction={vi.fn().mockResolvedValue(undefined)}
          onReload={vi.fn().mockResolvedValue(undefined)}
          onError={vi.fn()}
          onToast={vi.fn()}
          onPhoneConflict={vi.fn()}
        />,
      )
      await Promise.resolve()
      await Promise.resolve()
    })
    const edit = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Edit')
    await act(async () => { edit?.click() })
    const emailInputs = () => Array.from(container.querySelectorAll<HTMLInputElement>('.emailListEditorRow input'))
    expect(emailInputs().map((input) => input.value)).toEqual(['ravish.k@usgrpinc.com', 'ravish@usgrpinc.com'])
    const addEmail = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === '+ Add email')
    await act(async () => { addEmail?.click() })
    const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      inputSetter.call(emailInputs()[2], 'rk@usgrpinc.com')
      emailInputs()[2].dispatchEvent(new Event('input', { bubbles: true }))
    })
    const save = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Save')
    await act(async () => {
      save?.click()
      await Promise.resolve()
    })

    const patchCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/recruiter-numbers/613') && init?.method === 'PATCH')
    expect(JSON.parse(String(patchCall?.[1]?.body))).toMatchObject({
      emails: ['ravish.k@usgrpinc.com', 'ravish@usgrpinc.com', 'rk@usgrpinc.com'],
    })
  })

  it('shows seen count and source extraction decisions', async () => {
    const gmailRecruiter = { ...recruiter, source_type: 'gmail' as const, source_id: 42 }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/versions')) return jsonResponse(versions)
      if (url.endsWith('/reputation')) return jsonResponse({
        recruiter_contact_id: 613,
        history_label: 'limited_history',
        outreach_count: 0,
        replies_count: 0,
        median_first_reply_business_days: null,
        submissions_count: 0,
        interviews_after_submission_count: 0,
        offers_count: 0,
        last_active_at: null,
      })
      if (url.includes('/premium-numbers/extraction-audit?')) return jsonResponse({ items: [{
        id: 1,
        source_email_id: 42,
        source_external_opportunity_id: null,
        raw_value: '(614) 495-9222',
        normalized_value: '16144959222',
        status: 'accepted',
        stage: 'accepted',
        reason: 'candidate_accepted',
        created_at: '2026-08-20T10:00:00Z',
      }] })
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
          row={{ ...row, recruiter: gmailRecruiter }}
          busy={false}
          returnFocusRef={{ current: null }}
          onClose={vi.fn()}
          onAction={vi.fn().mockResolvedValue(undefined)}
          onReload={vi.fn().mockResolvedValue(undefined)}
          onError={vi.fn()}
          onToast={vi.fn()}
          onPhoneConflict={vi.fn()}
        />,
      )
      await Promise.resolve()
    })

    expect(container.textContent).toContain('4 times')
    expect(container.textContent).toContain('Extraction audit')
    expect(container.textContent).toContain('candidate_accepted')
  })

  it('resolves a phone/email cross-conflict review by merging into the chosen contact', async () => {
    const review: NumberReviewCard = {
      id: 900,
      source_email_id: 501,
      source_external_opportunity_id: null,
      source_lead_id: null,
      target_contact_id: 562,
      secondary_contact_id: 588,
      normalized_phone_number: '19802650643',
      display_phone_number: '(980) 265-0643',
      owner_name: 'Harshitha Voddepally',
      company: 'Horizons of Tech',
      designation: 'Unknown',
      confidence: 'low',
      purpose: 'Call/Text for application',
      evidence_snippet: 'Call/Text: 980-265-0643',
      email_subject: '#Senior Java Spring Boot Microservices Developer',
      email_sender: 'Harshitha Voddepally <harshitha@horizonsoftech.net>',
      contact_email: 'harshitha@horizonsoftech.net',
      contact_type: 'employer_internal',
      recruiter_relevance_score: 5,
      relevance_reason: 'employer_domain,identity_fields_conflict:company',
      extraction_source: 'ai',
      scored_with: 'current',
      gmail_open_url: 'https://mail.google.test/501',
      state: 'pending',
      role: 'recruiter',
      reason_code: 'phone_email_cross_conflict',
      occurrence_count: 1,
      created_at: '2026-08-29T10:00:00Z',
      updated_at: '2026-08-29T10:00:00Z',
    }
    const reviewRow: InventoryRow = {
      key: 'review:900',
      kind: 'review',
      id: 900,
      number: review.display_phone_number,
      owner: review.owner_name,
      company: review.company,
      categories: ['Employer'],
      status: 'Pending',
      score: review.recruiter_relevance_score,
      sourceType: 'gmail',
      lastCheckedAt: review.updated_at,
      review,
    }
    const target = {
      ...recruiter,
      id: 562,
      recruiter_name: 'Vankayalapati Saicharan',
      company: 'Eversoft IT',
      secondary_company: 'Eversoft Staffing',
      recruiter_email: 'vankayalapati.saicharan@eversoftit.com',
      phones: [
        { phone: '19802650643', extension: '', display: '(980) 265-0643', is_primary: true, is_verified: true, label: '' },
        { phone: '19802650000', extension: '42', display: '(980) 265-0000 ext 42', is_primary: false, is_verified: false, label: 'fax' },
      ],
      emails: [
        { email: 'vankayalapati.saicharan@eversoftit.com', domain: 'eversoftit.com', is_primary: true },
        { email: 'saicharan@eversoftit.com', domain: 'eversoftit.com', is_primary: false },
      ],
    }
    const secondary = {
      ...recruiter,
      id: 588,
      recruiter_name: 'Harshitha Voddepally',
      company: 'Horizons of Tech',
      recruiter_email: '',
      emails: [{ email: 'harshitha@horizonsoftech.net', domain: 'horizonsoftech.net', is_primary: true }],
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/recruiter-numbers/562')) return jsonResponse(target)
      if (url.endsWith('/recruiter-numbers/588')) return jsonResponse(secondary)
      if (url.endsWith('/number-review/900/approve-merge') && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ canonical_contact_id: 588 })
        return jsonResponse({ review_id: 900, contact_id: 588, status: 'resolved' })
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onClose = vi.fn()
    const onReload = vi.fn().mockResolvedValue(undefined)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })

    await act(async () => {
      root.render(
        <DetailPanel
          apiBase="http://localhost:8000"
          row={reviewRow}
          busy={false}
          returnFocusRef={{ current: null }}
          onClose={onClose}
          onAction={vi.fn().mockResolvedValue(undefined)}
          onReload={onReload}
          onError={vi.fn()}
          onToast={vi.fn()}
          onPhoneConflict={vi.fn()}
        />,
      )
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('Resolve identity conflict')
    expect(container.textContent).toContain('Vankayalapati Saicharan')
    expect(container.textContent).toContain('Harshitha Voddepally')
    expect(container.textContent).toContain('Eversoft Staffing')
    expect(container.textContent).toContain('(980) 265-0000 ext 42')
    expect(container.textContent).toContain('fax')
    expect(container.textContent).toContain('saicharan@eversoftit.com')
    expect(container.textContent).toContain('harshitha@horizonsoftech.net')
    expect(container.textContent).toContain('Resolve the identity conflict above before marking this contact.')
    expect(Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === 'Mark as Recruiter')?.disabled).toBe(true)

    const mergeIntoSecondary = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.startsWith('Merge into "Harshitha Voddepally'))
    expect(mergeIntoSecondary).toBeDefined()
    await act(async () => {
      mergeIntoSecondary?.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/number-review/900/approve-merge') && init?.method === 'POST')).toBe(true)
    expect(onClose).toHaveBeenCalled()
    expect(onReload).toHaveBeenCalled()
  })

  it('shows the diff for an automatic contact enrichment and acknowledges it', async () => {
    const review: NumberReviewCard = {
      id: 950,
      source_email_id: null,
      source_external_opportunity_id: 40,
      source_lead_id: null,
      target_contact_id: 580,
      secondary_contact_id: null,
      normalized_phone_number: '18563724625',
      display_phone_number: '(856) 372-4625',
      owner_name: 'Sunitha Sanu',
      company: 'Momentousa',
      designation: 'Unknown',
      confidence: 'high',
      purpose: 'Direct contact',
      evidence_snippet: 'Call: 856-372-4625',
      email_subject: '',
      email_sender: '',
      contact_email: 'sunitha@example.com',
      contact_type: 'recruiter_direct',
      recruiter_relevance_score: 95,
      relevance_reason: 'external_domain',
      extraction_source: 'ai',
      scored_with: 'current',
      gmail_open_url: 'https://nvoids.test/post-40',
      state: 'pending',
      role: 'recruiter',
      reason_code: 'contact_enriched',
      field_changes_json: JSON.stringify([{
        field: 'display_phone_number', label: 'Phone',
        old: '(856) 456-1805 ext 1025', new: '(856) 372-4625',
      }]),
      occurrence_count: 1,
      created_at: '2026-08-30T10:00:00Z',
      updated_at: '2026-08-30T10:00:00Z',
    }
    const reviewRow: InventoryRow = {
      key: 'review:950',
      kind: 'review',
      id: 950,
      number: review.display_phone_number,
      owner: review.owner_name,
      company: review.company,
      categories: ['Recruiter'],
      status: 'Pending',
      score: review.recruiter_relevance_score,
      sourceType: 'nvoids',
      lastCheckedAt: review.updated_at,
      review,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/recruiter-numbers/580')) return jsonResponse({ ...recruiter, id: 580, recruiter_name: 'Sunitha Sanu' })
      if (url.endsWith('/number-review/950/acknowledge') && init?.method === 'POST') {
        return jsonResponse({ review_id: 950, status: 'acknowledged' })
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onClose = vi.fn()
    const onReload = vi.fn().mockResolvedValue(undefined)
    const onToast = vi.fn()
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })

    await act(async () => {
      root.render(
        <DetailPanel
          apiBase="http://localhost:8000"
          row={reviewRow}
          busy={false}
          returnFocusRef={{ current: null }}
          onClose={onClose}
          onAction={vi.fn().mockResolvedValue(undefined)}
          onReload={onReload}
          onError={vi.fn()}
          onToast={onToast}
          onPhoneConflict={vi.fn()}
        />,
      )
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent).toContain("Here's what the AI merged in")
    expect(container.textContent).toContain('(856) 456-1805 ext 1025')
    expect(container.textContent).toContain('(856) 372-4625')
    expect(container.textContent).not.toContain('Resolve identity conflict')
    expect(Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === 'Mark as Recruiter')?.disabled).toBe(false)

    const acknowledgeButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Acknowledge')
    expect(acknowledgeButton).toBeDefined()
    await act(async () => {
      acknowledgeButton?.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/number-review/950/acknowledge') && init?.method === 'POST')).toBe(true)
    expect(onToast).toHaveBeenCalledWith('Acknowledged')
    expect(onClose).toHaveBeenCalled()
    expect(onReload).toHaveBeenCalled()
  })

  it('edits and saves a pending review\'s contact details without classifying its role', async () => {
    const review: NumberReviewCard = {
      id: 700,
      source_email_id: 1200,
      source_external_opportunity_id: null,
      source_lead_id: null,
      target_contact_id: null,
      secondary_contact_id: null,
      normalized_phone_number: '',
      display_phone_number: '',
      owner_name: 'Gunika Sharma',
      company: 'Empower Professionals',
      designation: 'Recruiter',
      confidence: 'medium',
      purpose: 'Recruiter contact',
      evidence_snippet: '',
      email_subject: '',
      email_sender: '',
      contact_email: 'gunika@empowerprofessionals.com',
      contact_type: 'recruiter_direct',
      recruiter_relevance_score: 75,
      relevance_reason: '',
      extraction_source: 'ai',
      scored_with: 'current',
      gmail_open_url: '',
      state: 'pending',
      role: 'recruiter',
      reason_code: 'new_number',
      occurrence_count: 1,
      created_at: '2026-08-30T10:00:00Z',
      updated_at: '2026-08-30T10:00:00Z',
    }
    const reviewRow: InventoryRow = {
      key: 'review:700',
      kind: 'review',
      id: 700,
      number: '(XXX) XXX-XXXX',
      owner: review.owner_name,
      company: review.company,
      categories: ['Recruiter'],
      status: 'Pending',
      score: review.recruiter_relevance_score,
      sourceType: 'gmail',
      lastCheckedAt: review.updated_at,
      review,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/number-review/700') && init?.method === 'PATCH') {
        expect(JSON.parse(String(init.body))).toMatchObject({ display_phone_number: '7323568008 ext 355' })
        return jsonResponse({ ...review, normalized_phone_number: '17323568008', display_phone_number: '(732) 356-8008 ext 355' })
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onToast = vi.fn()
    const onReload = vi.fn().mockResolvedValue(undefined)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })

    await act(async () => {
      root.render(
        <DetailPanel
          apiBase="http://localhost:8000"
          row={reviewRow}
          busy={false}
          returnFocusRef={{ current: null }}
          onClose={vi.fn()}
          onAction={vi.fn().mockResolvedValue(undefined)}
          onReload={onReload}
          onError={vi.fn()}
          onToast={onToast}
          onPhoneConflict={vi.fn()}
        />,
      )
      await Promise.resolve()
    })

    expect(container.textContent).toContain('Contact details')
    expect(container.querySelector('.detailFormGrid')).toBeNull()
    expect(Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === 'Mark as Recruiter')?.disabled).toBe(false)

    const edit = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Edit')
    await act(async () => { edit?.click() })

    const phoneInput = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.startsWith('Phone'))?.querySelector<HTMLInputElement>('input')
    const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      inputSetter.call(phoneInput, '7323568008 ext 355')
      phoneInput?.dispatchEvent(new Event('input', { bubbles: true }))
    })

    const save = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Save')
    await act(async () => {
      save?.click()
      await Promise.resolve()
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/number-review/700') && init?.method === 'PATCH')).toBe(true)
    expect(onToast).toHaveBeenCalledWith('Saved')
    expect(onReload).toHaveBeenCalled()
    expect(container.querySelector('.detailFormGrid')).toBeNull()
  })

  it('shows what Rescore changed and reverts it on Undo', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/versions')) return jsonResponse(versions)
      if (url.endsWith('/recruiter-numbers/613/reputation')) return jsonResponse({
        recruiter_contact_id: 613, history_label: 'limited_history', outreach_count: 0, replies_count: 0,
        median_first_reply_business_days: null, submissions_count: 0, interviews_after_submission_count: 0,
        offers_count: 0, last_active_at: null,
      })
      if (url.endsWith('/recruiter-numbers/613/rescore') && init?.method === 'POST') {
        return jsonResponse({
          id: 613,
          status: 'rescored',
          changes: [{ field: 'company', label: 'Company', old: 'United Software Group Inc', new: 'Renamed Group Inc' }],
        })
      }
      if (url.endsWith('/recruiter-numbers/613') && init?.method === 'PATCH') {
        expect(JSON.parse(String(init.body))).toEqual({ company: 'United Software Group Inc' })
        return jsonResponse({ ...recruiter })
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onToast = vi.fn()
    const onReload = vi.fn().mockResolvedValue(undefined)
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
          onReload={onReload}
          onError={vi.fn()}
          onToast={onToast}
          onPhoneConflict={vi.fn()}
        />,
      )
    })
    await act(async () => { await Promise.resolve() })

    const rescoreButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Rescore')
    await act(async () => {
      rescoreButton?.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(document.querySelector('[aria-label="Rescore results"]')).not.toBeNull()
    expect(document.body.textContent).toContain('United Software Group Inc')
    expect(document.body.textContent).toContain('Renamed Group Inc')

    const undoButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent === 'Undo')
    await act(async () => {
      undoButton?.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/recruiter-numbers/613') && init?.method === 'PATCH')).toBe(true)
    expect(onToast).toHaveBeenCalledWith('Rescore undone')
    expect(onReload).toHaveBeenCalled()
    expect(document.querySelector('[aria-label="Rescore results"]')).toBeNull()
  })
})
