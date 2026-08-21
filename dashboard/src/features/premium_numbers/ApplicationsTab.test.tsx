// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ApplicationsTab from './ApplicationsTab'
import type { ApplicationCard } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const application: ApplicationCard = {
  id: 41,
  resume_asset_id: 7,
  resume_version_snapshot: 2,
  resume_file_name_snapshot: 'java-backend.pdf',
  recruiter_opportunity_id: 9,
  recruiter_contact_id: 11,
  recruiter_name_snapshot: 'Priya Patel',
  recruiter_company_snapshot: 'ABC Staffing',
  job_title_snapshot: 'Senior Java Developer',
  end_client_snapshot: 'Bank X',
  current_recruiter_name: 'Priya Patel',
  current_recruiter_company: 'ABC Staffing',
  current_recruiter_phone_display: '+1 214 555 1212',
  current_job_title: 'Senior Java Developer',
  current_end_client: 'Bank X',
  status: 'matched',
  status_changed_at: '2026-08-20T12:00:00Z',
  resume_shared_at: null,
  submitted_to_client_at: null,
  next_action_type: null,
  next_action_at: null,
  follow_up_count: 0,
  last_contact_at: null,
  closed_at: null,
  closed_reason: null,
  closed_reason_code: null,
  created_at: '2026-08-20T12:00:00Z',
  updated_at: '2026-08-20T12:00:00Z',
  events: [],
  rtr_history: [],
  interviews: [],
}

describe('ApplicationsTab', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    vi.restoreAllMocks()
    while (cleanups.length) cleanups.pop()?.()
  })

  it('lists applications, changes status, and adds a timeline note', async () => {
    let noteAdded = false
    const createdEvent = {
      id: 1,
      event_type: 'created',
      event_source: 'system',
      note: '',
      linked_recruiter_email_id: null,
      occurred_at: '2026-08-20T12:00:00Z',
    }
    const noteEvent = {
      id: 2,
      event_type: 'note',
      event_source: 'user',
      note: 'Recruiter requested an updated summary.',
      linked_recruiter_email_id: null,
      occurred_at: '2026-08-20T13:00:00Z',
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/applications/dashboard-summary')) {
        return jsonResponse({ due_today: 1, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0, pending_suggestions: 0 })
      }
      if (url.endsWith('/settings/attachments')) return jsonResponse([])
      if (url.includes('/applications/suggestions?')) return jsonResponse({ items: [] })
      if (url.includes('/applications?')) {
        return jsonResponse({ items: [application], next_cursor: null, has_next: false })
      }
      if (url.endsWith('/applications/41/events') && init?.method === 'POST') {
        noteAdded = true
        return jsonResponse(noteEvent, 201)
      }
      if (url.endsWith('/applications/41') && init?.method === 'PATCH') {
        return jsonResponse({ ...application, status: 'contacted' })
      }
      if (url.endsWith('/applications/41')) {
        return jsonResponse({ ...application, events: noteAdded ? [createdEvent, noteEvent] : [createdEvent] })
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
      root.render(<ApplicationsTab apiBase="http://localhost:8000" refreshToken={0} onToast={vi.fn()} />)
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    expect(container.textContent).toContain('Senior Java Developer')
    expect(container.textContent).toContain('Due today')

    const status = container.querySelector<HTMLSelectElement>('select[aria-label^="Application status"]')!
    const selectSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      selectSetter.call(status, 'contacted')
      status.dispatchEvent(new Event('change', { bubbles: true }))
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    const patchCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/applications/41') && init?.method === 'PATCH')
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ status: 'contacted' })

    const timelineButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'View timeline')
    await act(async () => {
      timelineButton?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    expect(container.textContent).toContain('Created')

    const textarea = container.querySelector<HTMLTextAreaElement>('.applicationEventForm textarea')!
    const textareaSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      textareaSetter.call(textarea, 'Recruiter requested an updated summary.')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const addButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Add to timeline')
    await act(async () => {
      addButton?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    })
    expect(container.textContent).toContain('Recruiter requested an updated summary.')
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/applications/41/events') && init?.method === 'POST')).toBe(true)
  })

  it('requests and confirms RTR, adds an interview, and explicitly overrides a duplicate warning', async () => {
    const rtr = {
      id: 71,
      status: 'requested' as const,
      role_scope: 'Senior Java Developer',
      end_client_scope: 'Bank X',
      requested_at: '2026-08-21T12:00:00Z',
      confirmed_at: null,
      expires_at: null,
      proof_attachment_id: null,
      proof_recruiter_email_id: null,
      note: '',
    }
    const interview = {
      id: 81,
      round_type: 'interview_1' as const,
      scheduled_at: null,
      format: '',
      interviewer_names: '',
      feedback: '',
      result: 'scheduled' as const,
      follow_up_task_note: '',
    }
    let detail: ApplicationCard = application
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/settings/attachments')) return jsonResponse([{ id: 5, file_name: 'rtr-proof.pdf', is_enabled: true }])
      if (url.endsWith('/applications/dashboard-summary')) return jsonResponse({ due_today: 0, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0, pending_suggestions: 0 })
      if (url.includes('/applications/suggestions?')) return jsonResponse({ items: [] })
      if (url.includes('/applications?')) return jsonResponse({ items: [detail], next_cursor: null, has_next: false })
      if (url.endsWith('/applications/41/rtr') && init?.method === 'POST') {
        detail = { ...detail, status: 'rtr_requested', rtr_history: [rtr] }
        return jsonResponse(detail, 201)
      }
      if (url.endsWith('/applications/41/rtr/71') && init?.method === 'PATCH') {
        detail = {
          ...detail,
          status: 'rtr_confirmed',
          rtr_history: [{ ...rtr, status: 'confirmed', confirmed_at: '2026-08-21T13:00:00Z', proof_attachment_id: 5 }],
        }
        return jsonResponse(detail)
      }
      if (url.endsWith('/applications/41/interviews') && init?.method === 'POST') {
        detail = { ...detail, status: 'interview_1', interviews: [interview] }
        return jsonResponse(detail, 201)
      }
      if (url.endsWith('/applications/41/submit-to-client') && init?.method === 'POST') {
        const payload = JSON.parse(String(init.body)) as { override_duplicate_warning: boolean }
        if (!payload.override_duplicate_warning) {
          return jsonResponse({
            detail: {
              message: 'Possible duplicate submission to the same end client',
              duplicates: [{ id: 42, job_title_snapshot: 'Java Developer', end_client_snapshot: 'Bank X', status: 'matched', created_at: '2026-08-20T12:00:00Z' }],
            },
          }, 409)
        }
        detail = { ...detail, status: 'submitted_to_client', submitted_to_client_at: '2026-08-21T14:00:00Z' }
        return jsonResponse(detail)
      }
      if (url.endsWith('/applications/41')) return jsonResponse(detail)
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
      root.render(<ApplicationsTab apiBase="http://localhost:8000" refreshToken={0} onToast={vi.fn()} />)
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    const button = (text: string) => Array.from(container.querySelectorAll('button')).find((item) => item.textContent === text)

    await act(async () => {
      button('View timeline')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 30))
    })
    await act(async () => {
      button('Request RTR')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 30))
    })
    expect(container.textContent).toContain('Confirm RTR')

    const attachmentSelect = Array.from(container.querySelectorAll('label')).find((item) => item.textContent?.includes('Attachment proof'))?.querySelector('select')
    const selectSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      selectSetter.call(attachmentSelect, '5')
      attachmentSelect?.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => {
      button('Confirm RTR')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 30))
    })
    expect(detail.rtr_history[0].status).toBe('confirmed')

    await act(async () => {
      button('Add interview')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 30))
    })
    expect(container.textContent).toContain('Interview 1')

    await act(async () => {
      button('Submit to client')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 30))
    })
    expect(container.textContent).toContain('Possible duplicate submission')
    expect(container.textContent).toContain('#42')
    await act(async () => {
      button('Submit anyway')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 30))
    })
    expect(detail.status).toBe('submitted_to_client')
    const submitCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/applications/41/submit-to-client'))
    expect(submitCalls.map(([, init]) => JSON.parse(String(init?.body)).override_duplicate_warning)).toEqual([false, true])
  })

  it('accepts and dismisses pending suggestions without automatic mutation', async () => {
    const suggestions = [
      {
        id: 91,
        application_id: 41,
        suggestion_type: 'status_change',
        status: 'pending',
        confidence: 'high',
        recruiter_email_id: 77,
        suggested_status: 'client_reviewing',
        suggested_next_action_type: null,
        suggested_next_action_at: null,
        reason: "Matched phrase: 'submitted your resume'",
        created_at: '2026-08-21T12:00:00Z',
        resolved_at: null,
      },
      {
        id: 92,
        application_id: 41,
        suggestion_type: 'stale_prompt',
        status: 'pending',
        confidence: 'high',
        recruiter_email_id: null,
        suggested_status: null,
        suggested_next_action_type: null,
        suggested_next_action_at: null,
        reason: 'No activity in 3 weeks - close or continue?',
        created_at: '2026-08-21T11:00:00Z',
        resolved_at: null,
      },
    ]
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/settings/attachments')) return jsonResponse([])
      if (url.endsWith('/applications/dashboard-summary')) return jsonResponse({ due_today: 0, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0, pending_suggestions: 2 })
      if (url.includes('/applications/suggestions?')) return jsonResponse({ items: suggestions })
      if (url.includes('/applications?')) return jsonResponse({ items: [application], next_cursor: null, has_next: false })
      if (url.endsWith('/applications/suggestions/91/accept') && init?.method === 'POST') {
        return jsonResponse({ ...application, status: 'client_reviewing' })
      }
      if (url.endsWith('/applications/suggestions/92/dismiss') && init?.method === 'POST') {
        return jsonResponse({ ...suggestions[1], status: 'dismissed' })
      }
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
      root.render(<ApplicationsTab apiBase="http://localhost:8000" refreshToken={0} onToast={vi.fn()} />)
    })
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 300)) })
    expect(container.textContent).toContain("Matched phrase: 'submitted your resume'")
    expect(container.textContent).toContain('Source email #77')
    const buttons = (text: string) => Array.from(container.querySelectorAll('button')).filter((button) => button.textContent === text)
    await act(async () => {
      buttons('Accept')[0]?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 20))
    })
    expect(container.textContent).not.toContain("Matched phrase: 'submitted your resume'")
    await act(async () => {
      buttons('Dismiss')[0]?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 20))
    })
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/applications/suggestions/91/accept') && init?.method === 'POST')).toBe(true)
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/applications/suggestions/92/dismiss') && init?.method === 'POST')).toBe(true)
  })
})
