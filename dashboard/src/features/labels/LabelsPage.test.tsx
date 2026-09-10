// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LabelsPage from './LabelsPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const OVERVIEW = {
  items: [
    { external_label_id: 'Label_1', name: 'RTR Requested', color_background: null, color_text: null, thread_count: 2, unread_count: 1, last_message_at: '2026-09-10T12:00:00Z' },
    { external_label_id: 'Label_2', name: 'Submitted', color_background: null, color_text: null, thread_count: 0, unread_count: 0, last_message_at: null },
  ],
  tracked_thread_total: 2,
  untracked_label_count: 3,
}

const THREADS = {
  items: [{
    thread_id: 'thread-1', subject: 'RTR for Java Developer', recruiter: 'Naman',
    recruiter_email: 'naman@valzosoft.com', labels: ['RTR Requested'], last_message_at: '2026-09-10T12:00:00Z',
    message_count: 3, unread_count: 1, conversation_id: 1, gmail_thread_link: null,
    appts_application_id: null, record_id: null,
  }],
  total: 1,
  has_next: false,
  next_cursor: null,
}

const DOSSIER = {
  thread_id: 'thread-1',
  subject: 'RTR for Java Developer',
  labels: ['RTR Requested'],
  last_message_at: '2026-09-10T14:00:00Z',
  conversation_id: 1,
  thread_count: 2,
  unread_count: 1,
  gmail_thread_link: 'https://mail.google.com/mail/u/0/#all/thread-1',
  appts_application_id: null,
  record_id: 'REC-9',
  watches: ['domain:valzosoft.com'],
  contacts: [
    { address: 'naman@valzosoft.com', name: 'Naman', domain: 'valzosoft.com', kind: 'recruiter', message_count: 2, watched: true },
    { address: 'hr@myemployer.com', name: 'HR', domain: 'myemployer.com', kind: 'employer', message_count: 1, watched: false },
    { address: 'me@gmail.com', name: 'Me', domain: 'gmail.com', kind: 'self', message_count: 3, watched: false },
  ],
  messages: [
    { id: 1, conversation_id: 1, external_thread_id: 'thread-1', direction: 'inbound', sender: 'Naman <naman@valzosoft.com>', sender_address: 'naman@valzosoft.com', to_header: 'me@gmail.com', cc_header: null, subject: 'RTR for Java Developer', snippet: 'Sending the RTR', body: 'Sending the RTR', occurred_at: '2026-09-10T12:00:00Z', read_at: null, origin: 'label', gmail_link: null },
    { id: 2, conversation_id: 1, external_thread_id: 'thread-1', direction: 'outbound', sender: 'me@gmail.com', sender_address: 'me@gmail.com', to_header: 'naman@valzosoft.com', cc_header: null, subject: 'RTR for Java Developer', snippet: 'Signed', body: 'Signed and returned', occurred_at: '2026-09-10T13:00:00Z', read_at: '2026-09-10T13:00:00Z', origin: 'label', gmail_link: null },
    { id: 3, conversation_id: 2, external_thread_id: 'thread-2', direction: 'inbound', sender: 'Priya <priya@valzosoft.com>', sender_address: 'priya@valzosoft.com', to_header: 'me@gmail.com', cc_header: null, subject: 'Taking over from Naman', snippet: 'Hello', body: 'Hello, I am taking over', occurred_at: '2026-09-10T14:00:00Z', read_at: null, origin: 'watch', gmail_link: null },
  ],
}

function stubFetch(overrides: { overview?: unknown } = {}) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
    const href = String(url)
    if (href.includes('/labels/overview')) return jsonResponse(overrides.overview ?? OVERVIEW)
    if (href.includes('/dossier')) return jsonResponse(DOSSIER)
    if (href.includes('/appts/label-threads?')) return jsonResponse(THREADS)
    if (href.includes('/resume-assets') || href.includes('/resumes')) return jsonResponse([])
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('LabelsPage', () => {
  const cleanups: Array<() => void> = []

  async function render(props: Partial<Parameters<typeof LabelsPage>[0]> = {}) {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => {
      root.render(<LabelsPage apiBase="http://localhost:8000" refreshToken={0} {...props} />)
    })
    // One more flush: the thread list and the dossier fetch after the overview
    // resolves, so the first act only gets us as far as the rail.
    await act(async () => { await Promise.resolve() })
    await act(async () => { await Promise.resolve() })
    return container
  }

  afterEach(() => {
    cleanups.splice(0).forEach((fn) => fn())
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists tracked labels with their thread and unread counts', async () => {
    stubFetch()

    const container = await render()

    const rail = Array.from(container.querySelectorAll('.labelRailItem')).map((el) => el.textContent)
    expect(rail).toEqual(['RTR Requested12', 'Submitted0'])
  })

  it('opens the busiest label first rather than an empty one', async () => {
    stubFetch()

    const container = await render()

    expect(container.querySelector('.labelRailItem.active')?.textContent).toContain('RTR Requested')
  })

  // The reason the page exists: a reply that started a new Gmail thread has to
  // show up in the same reading column as the labeled thread.
  it('shows follow-ups from a separate thread in one chronology', async () => {
    stubFetch()

    const container = await render()

    const bodies = Array.from(container.querySelectorAll('.labelMessageBody')).map((el) => el.textContent)
    expect(bodies).toEqual(['Sending the RTR', 'Signed and returned', 'Hello, I am taking over'])
    expect(container.querySelector('.labelThreadBreak')?.textContent).toContain('Taking over from Naman')
  })

  it('distinguishes what was sent from what was received', async () => {
    stubFetch()

    const container = await render()

    const directions = Array.from(container.querySelectorAll('.labelMessage')).map((el) => el.className.includes('outbound') ? 'out' : 'in')
    expect(directions).toEqual(['in', 'out', 'in'])
    expect(container.querySelectorAll('.labelMessage.outbound .labelMessageSender')[0]?.textContent).toBe('You')
  })

  it('separates tracked recruiters from the employer, which is not tracked', async () => {
    stubFetch()

    const container = await render()

    const recruiter = container.querySelector('.labelContactChip.recruiter')
    const employer = container.querySelector('.labelContactChip.employer')
    expect(recruiter?.textContent).toContain('naman@valzosoft.com')
    expect(employer?.getAttribute('title')).toContain('deliberately excluded')
    // The owner's own address is not offered as a "participant" to reason about.
    expect(container.querySelector('.labelContactChip.self')).toBeNull()
  })

  it('teaches the feature when no label is tracked yet', async () => {
    stubFetch({ overview: { items: [], tracked_thread_total: 0, untracked_label_count: 3 } })

    const container = await render({ onOpenSettings: () => undefined })

    expect(container.querySelector('.labelsEmpty h3')?.textContent).toBe('Choose which Gmail labels to follow')
    expect(container.querySelector('.labelsEmpty p')?.textContent).toContain('3 labels in Gmail')
  })

  it('gives different instructions when Gmail itself has no labels', async () => {
    stubFetch({ overview: { items: [], tracked_thread_total: 0, untracked_label_count: 0 } })

    const container = await render()

    expect(container.querySelector('.labelsEmpty h3')?.textContent).toBe('No Gmail labels yet')
  })

  it('asks the backend for threads in the selected label only', async () => {
    const fetchMock = stubFetch()

    await render()

    const threadCall = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/appts/label-threads?'))
    expect(threadCall).toContain('label=RTR+Requested')
  })
})
