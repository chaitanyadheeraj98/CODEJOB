// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ScheduledTasks from './ScheduledTasks'
import { renderForMessage } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const task = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  title: 'Morning digest',
  kind: 'digest',
  status: 'active',
  trigger: 'Every weekday at 9:00 AM (America/New_York)',
  next_run_at: '2026-09-04T13:00:00+00:00',
  permitted_actions: 'Summarises pending work and notifies you. Changes nothing.',
  last_error: '',
  ...overrides,
})

const payload = (overrides: Record<string, unknown> = {}) => ({
  action: 'render_scheduled_tasks',
  tasks: [task()],
  truncated: false,
  granularity_note: 'Scheduled work is checked every few minutes, so a run can start up to 5 minutes after its scheduled time.',
  provenance: {
    metric: 'Scheduled tasks',
    source: 'list_scheduled_tasks',
    row_count: 1,
    date_range: { from: null, to: null },
    filters: { status: 'all' },
    assumptions: ['Deleted tasks are excluded unless you ask for them by status.'],
  },
  ...overrides,
})

const message = (content: unknown): ChatMessage => ({
  id: 1,
  role: 'tool',
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-09-03T00:00:00Z',
  tool_name: 'list_scheduled_tasks',
} as ChatMessage)

describe('the scheduled_tasks render kind', () => {
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
  })

  const render = (data: ReturnType<typeof payload>) => {
    const parsed = renderForMessage(message(data))
    expect(parsed?.kind).toBe('scheduled_tasks')
    act(() => {
      root.render(
        <ScheduledTasks
          data={(parsed as { kind: 'scheduled_tasks'; data: never }).data}
          surface="page"
        />,
      )
    })
  }

  it('shows the trigger, the next run and what the task may do', () => {
    render(payload())

    expect(container.textContent).toContain('Every weekday at 9:00 AM (America/New_York)')
    expect(container.textContent).toContain('Changes nothing')
    expect(container.textContent).toContain('Next run:')
  })

  // The user should read the granularity caveat rather than discover it by
  // being five minutes late.
  it('states the timing caveat', () => {
    render(payload())

    expect(container.textContent).toContain('5 minutes')
  })

  it('surfaces the last error of a failing task', () => {
    render(payload({ tasks: [task({ status: 'suspended', last_error: 'Redis unreachable' })] }))

    expect(container.textContent).toContain('Redis unreachable')
    expect(container.textContent).toContain('suspended')
  })

  it('says so plainly when nothing is scheduled', () => {
    render(payload({ tasks: [] }))

    expect(container.textContent).toContain('Nothing is scheduled')
  })
})

describe('the scheduled_tasks parser', () => {
  const parse = (data: unknown) => renderForMessage(message(data))

  it('drops a payload whose provenance is missing', () => {
    expect(parse(payload({ provenance: undefined }))).toBeNull()
  })

  // A row without a trigger sentence is a task the user cannot verify, so the
  // whole payload is dropped rather than rendering a row that says less.
  it('drops a payload whose task has no trigger', () => {
    expect(parse(payload({ tasks: [task({ trigger: '' })] }))).toBeNull()
  })

  it('drops a payload whose task does not say what it may do', () => {
    expect(parse(payload({ tasks: [task({ permitted_actions: '' })] }))).toBeNull()
  })

  it('drops a payload whose tasks are not a list', () => {
    expect(parse(payload({ tasks: 'lots' }))).toBeNull()
  })

  it('drops a payload with the wrong action', () => {
    expect(parse(payload({ action: 'render_something_else' }))).toBeNull()
  })

  it('drops malformed json rather than throwing', () => {
    expect(parse('{ not json')).toBeNull()
  })

  it('keeps a task whose next run is absent', () => {
    const parsed = parse(payload({ tasks: [task({ next_run_at: null })] }))

    expect(parsed?.kind).toBe('scheduled_tasks')
  })
})
