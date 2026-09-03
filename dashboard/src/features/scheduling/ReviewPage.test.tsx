// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewPage from './ReviewPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const API = 'http://localhost:8000'

const pendingRun = (overrides: Record<string, unknown> = {}) => ({
  source: 'scheduled_run',
  source_id: 9,
  task_id: 1,
  title: 'Chase stale applications',
  detail: '2 prepared item(s) awaiting review',
  subject_type: 'application',
  subject_id: '',
  prepared_at: '2026-09-03T09:00:00+00:00',
  expires_at: '2026-09-10T09:00:00+00:00',
  item_count: 2,
  approve_endpoint: 'scheduled_run_approve',
  ...overrides,
})

const suggestion = (overrides: Record<string, unknown> = {}) => ({
  source: 'application_suggestion',
  source_id: 4,
  task_id: 0,
  title: 'Next action: Java Developer at Bank X',
  detail: 'No reply in 3 business days',
  subject_type: 'application',
  subject_id: '12',
  prepared_at: '2026-09-02T09:00:00+00:00',
  expires_at: '2026-09-09T09:00:00+00:00',
  item_count: 1,
  approve_endpoint: 'application_suggestion_accept',
  ...overrides,
})

const preparedItem = (overrides: Record<string, unknown> = {}) => ({
  item_id: 'item-a',
  action: 'propose_record_update',
  record_kind: 'application',
  record_id: 12,
  summary: 'Set next action on Java Developer at Bank X',
  payload: { next_action_type: 'Follow up with recruiter', next_action_at: '2026-09-03T09:00:00+00:00' },
  editable_fields: ['next_action_type'],
  ...overrides,
})

type Call = { url: string; method: string; body: unknown }

function stubFetch(handlers: Record<string, unknown>, calls: Call[] = []) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace(API, '')
    calls.push({
      url,
      method: init?.method ?? 'GET',
      body: init?.body ? JSON.parse(String(init.body)) : null,
    })
    const key = Object.keys(handlers).find((candidate) => url.startsWith(candidate))
    if (!key) return { ok: false, status: 500, json: async () => ({ detail: 'unstubbed' }) } as Response
    return { ok: true, status: 200, json: async () => handlers[key] } as Response
  })
}

describe('ReviewPage', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  })

  const mount = async () => {
    await act(async () => { root.render(<ReviewPage apiBase={API} />) })
    await act(async () => { await Promise.resolve() })
  }

  // React tracks an input's value itself, so assigning `.value` and firing a
  // plain event does not reach onChange. The repo's existing tests use the
  // native setter for the same reason.
  const type = (element: HTMLInputElement, value: string) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    setter?.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  }

  const button = (label: string) =>
    [...container.querySelectorAll('button')].find((node) => node.textContent?.includes(label)) as HTMLButtonElement

  // §4.6: two inboxes is a product defect the user experiences as missed work.
  it('renders prepared runs and application suggestions in one list', async () => {
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/pending-work': { items: [pendingRun(), suggestion()] },
    }))

    await mount()

    expect(container.textContent).toContain('Chase stale applications')
    expect(container.textContent).toContain('Next action: Java Developer at Bank X')
    expect(container.textContent).toContain('Scheduled run')
    expect(container.textContent).toContain('Suggestion')
  })

  it('approves everything when Approve all is clicked', async () => {
    const calls: Call[] = []
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/pending-work': { items: [pendingRun()] },
      '/scheduled-tasks/runs/9/approve': { approved: 2, failed: 0, outcome: 'approved', errors: [] },
    }, calls))

    await mount()
    await act(async () => { button('Approve all').click() })
    await act(async () => { await Promise.resolve() })

    const approve = calls.find((call) => call.url.includes('/approve'))
    expect(approve?.method).toBe('POST')
    // An empty item list means "everything in this run", so the client never
    // has to enumerate ids it did not read from the server.
    expect((approve?.body as { item_ids: string[] }).item_ids).toEqual([])
  })

  it('approves only the selected subset', async () => {
    const calls: Call[] = []
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/pending-work': { items: [pendingRun()] },
      '/scheduled-tasks/1/runs': {
        runs: [{
          id: 9,
          task_id: 1,
          started_at: '2026-09-03T09:00:00+00:00',
          finished_at: null,
          outcome: 'pending',
          item_count: 2,
          approved_count: 0,
          expires_at: '2026-09-10T09:00:00+00:00',
          expired_at: null,
          expiry_reason: '',
          error: '',
          items: [preparedItem(), preparedItem({ item_id: 'item-b', summary: 'Second item' })],
        }],
        items: [],
      },
      '/scheduled-tasks/runs/9/approve': { approved: 1, failed: 0, outcome: 'partially_approved', errors: [] },
    }, calls))

    await mount()
    await act(async () => { button('Review items').click() })
    await act(async () => { await Promise.resolve() })

    const checkboxes = [...container.querySelectorAll('input[type="checkbox"]')] as HTMLInputElement[]
    await act(async () => { checkboxes[1].click() })
    await act(async () => { button('Approve 1 selected').click() })
    await act(async () => { await Promise.resolve() })

    const approve = calls.find((call) => call.url.includes('/approve'))
    expect((approve?.body as { item_ids: string[] }).item_ids).toEqual(['item-a'])
  })

  it('sends the edited payload rather than the prepared one', async () => {
    const calls: Call[] = []
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/pending-work': { items: [pendingRun()] },
      '/scheduled-tasks/1/runs': {
        runs: [{
          id: 9, task_id: 1, started_at: '2026-09-03T09:00:00+00:00', finished_at: null,
          outcome: 'pending', item_count: 1, approved_count: 0,
          expires_at: null, expired_at: null, expiry_reason: '', error: '',
          items: [preparedItem()],
        }],
        items: [],
      },
      '/scheduled-tasks/runs/9/approve': { approved: 1, failed: 0, outcome: 'approved', errors: [] },
    }, calls))

    await mount()
    await act(async () => { button('Review items').click() })
    await act(async () => { await Promise.resolve() })

    const field = container.querySelector('.scheduling-edit input') as HTMLInputElement
    await act(async () => { type(field, 'Call them instead') })
    await act(async () => { button('Approve 1 selected').click() })
    await act(async () => { await Promise.resolve() })

    const approve = calls.find((call) => call.url.includes('/approve'))
    expect((approve?.body as { edits: Record<string, Record<string, string>> }).edits['item-a'])
      .toEqual({ next_action_type: 'Call them instead' })
  })

  it('discards a run without approving anything', async () => {
    const calls: Call[] = []
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/pending-work': { items: [pendingRun()] },
      '/scheduled-tasks/runs/9/discard': { id: 9, outcome: 'discarded' },
    }, calls))

    await mount()
    await act(async () => { button('Discard').click() })
    await act(async () => { await Promise.resolve() })

    expect(calls.some((call) => call.url.includes('/discard'))).toBe(true)
    expect(calls.some((call) => call.url.includes('/approve'))).toBe(false)
  })

  it('reports a partial failure rather than claiming success', async () => {
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/pending-work': { items: [pendingRun()] },
      '/scheduled-tasks/runs/9/approve': {
        approved: 1, failed: 1, outcome: 'partially_approved', errors: ['That record no longer exists.'],
      },
    }))

    await mount()
    await act(async () => { button('Approve all').click() })
    await act(async () => { await Promise.resolve() })

    expect(container.textContent).toContain('1 approved, 1 failed')
    expect(container.textContent).toContain('That record no longer exists.')
  })

  it('says so plainly when nothing is waiting', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/scheduled-tasks/pending-work': { items: [] } }))

    await mount()

    expect(container.textContent).toContain('Nothing is waiting')
  })
})
