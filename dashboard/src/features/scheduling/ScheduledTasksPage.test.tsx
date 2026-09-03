// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ScheduledTasksPage from './ScheduledTasksPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const API = 'http://localhost:8000'

const task = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  title: 'Morning digest',
  kind: 'digest',
  status: 'active',
  schedule_kind: 'recurring',
  cron_expression: '0 9 * * 1-5',
  timezone: 'America/New_York',
  trigger: 'Every weekday at 9:00 AM (America/New_York)',
  next_run_at: '2026-09-04T13:00:00+00:00',
  last_run_at: null,
  last_error: '',
  consecutive_failures: 0,
  retention_hours: 48,
  permitted_actions: 'Summarises pending work and notifies you. Changes nothing.',
  subject_type: '',
  subject_id: '',
  run_count: 2,
  ...overrides,
})

const run = (overrides: Record<string, unknown> = {}) => ({
  id: 9,
  task_id: 1,
  started_at: '2026-09-03T13:00:00+00:00',
  finished_at: '2026-09-03T13:00:05+00:00',
  outcome: 'notified',
  item_count: 0,
  approved_count: 0,
  expires_at: null,
  expired_at: null,
  expiry_reason: '',
  error: '',
  items: [],
  ...overrides,
})

function stubFetch(handlers: Record<string, unknown>, status = 200) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input).replace(API, '')
    const key = Object.keys(handlers).find((candidate) => url.startsWith(candidate))
    if (!key) return { ok: false, status: 500, json: async () => ({ detail: 'unstubbed' }) } as Response
    return {
      ok: status < 400,
      status,
      json: async () => handlers[key],
    } as Response
  })
}

describe('ScheduledTasksPage', () => {
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
    await act(async () => {
      root.render(<ScheduledTasksPage apiBase={API} />)
    })
    await act(async () => { await Promise.resolve() })
  }

  it('shows every task with its trigger, next run and permitted actions', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/scheduled-tasks': { tasks: [task()] } }))

    await mount()

    expect(container.textContent).toContain('Morning digest')
    expect(container.textContent).toContain('Every weekday at 9:00 AM (America/New_York)')
    expect(container.textContent).toContain('Changes nothing')
    expect(container.textContent).toContain('48 hours')
  })

  // temp157 §8.1: no task may exist that the user cannot see here.
  it('lists every kind, including ones with no schedule', async () => {
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks': {
        tasks: [
          task({ id: 1, kind: 'reminder', title: 'Reminder' }),
          task({ id: 2, kind: 'checklist', title: 'Checklist', schedule_kind: 'none', next_run_at: null, trigger: 'No schedule - runs only when you open it' }),
          task({ id: 3, kind: 'workflow', title: 'Workflow' }),
        ],
      },
    }))

    await mount()

    for (const title of ['Reminder', 'Checklist', 'Workflow']) {
      expect(container.textContent).toContain(title)
    }
  })

  it('shows a suspended task with its error', async () => {
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks': {
        tasks: [task({ status: 'suspended', last_error: 'Redis unreachable', consecutive_failures: 3 })],
      },
    }))

    await mount()

    expect(container.textContent).toContain('Redis unreachable')
    expect(container.textContent).toContain('3 in a row')
  })

  it('offers Resume rather than Pause for a paused task', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/scheduled-tasks': { tasks: [task({ status: 'paused' })] } }))

    await mount()

    const labels = [...container.querySelectorAll('button')].map((node) => node.textContent)
    expect(labels).toContain('Resume')
    expect(labels).not.toContain('Pause')
  })

  it('opens run history and shows an expired run with its reason and no approve control', async () => {
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/1/runs': {
        runs: [run({ outcome: 'expired', expiry_reason: 'Not reviewed before expiration' })],
        items: [],
      },
      '/scheduled-tasks': { tasks: [task()] },
    }))

    await mount()
    const title = container.querySelector('.scheduling-task-title') as HTMLButtonElement
    await act(async () => { title.click() })
    await act(async () => { await Promise.resolve() })

    expect(container.textContent).toContain('Not reviewed before expiration')
    const labels = [...container.querySelectorAll('button')].map((node) => node.textContent)
    expect(labels).not.toContain('Approve')
  })

  it('shows partial approval as a count of the whole', async () => {
    vi.stubGlobal('fetch', stubFetch({
      '/scheduled-tasks/1/runs': {
        runs: [run({ outcome: 'partially_approved', item_count: 7, approved_count: 3 })],
        items: [],
      },
      '/scheduled-tasks': { tasks: [task()] },
    }))

    await mount()
    const title = container.querySelector('.scheduling-task-title') as HTMLButtonElement
    await act(async () => { title.click() })
    await act(async () => { await Promise.resolve() })

    expect(container.textContent).toContain('3 of 7 approved')
  })

  // Every scheduling route 404s when the feature is off, so the page must say
  // "not enabled here" rather than "you have nothing".
  it('says the feature is off rather than showing an empty list', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/scheduled-tasks': { detail: 'Not found' } }, 404))

    await mount()

    expect(container.textContent).toContain('not enabled')
  })

  it('says so plainly when the feature is on and nothing is scheduled', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/scheduled-tasks': { tasks: [] } }))

    await mount()

    expect(container.textContent).toContain('Nothing is scheduled')
  })
})
