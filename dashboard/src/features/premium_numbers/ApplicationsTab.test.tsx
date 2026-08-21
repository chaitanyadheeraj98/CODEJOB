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
  created_at: '2026-08-20T12:00:00Z',
  updated_at: '2026-08-20T12:00:00Z',
  events: [],
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
        return jsonResponse({ due_today: 1, waiting_on_recruiter: 0, interviews: 0, closed_recent: 0 })
      }
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
})
