// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApplicationCard, ApplicationDuplicateSummary } from '../premium_numbers/types'
import * as api from './api'
import type { JourneyAction } from './journey'
import JourneyPanel from './JourneyPanel'

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return {
    ...actual,
    addApplicationInterview: vi.fn(),
    createApplicationEvent: vi.fn(),
    getApplication: vi.fn(),
    requestApplicationRtr: vi.fn(),
    submitApplicationToClient: vi.fn(),
    updateApplication: vi.fn(),
    updateApplicationInterview: vi.fn(),
    updateApplicationRtr: vi.fn(),
  }
})

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function application(overrides: Partial<ApplicationCard> = {}): ApplicationCard {
  return {
    id: 7,
    status: 'rtr_requested',
    current_job_title: 'Java Engineer',
    job_title_snapshot: 'Java Engineer',
    current_end_client: 'Acme',
    end_client_snapshot: 'Acme',
    interviews: [{ id: 12, round_type: 'interview_1', scheduled_at: '2026-01-02T00:00:00Z', format: 'Teams', interviewer_names: 'Pat', feedback: '', result: 'scheduled', follow_up_task_note: '' }],
    rtr_history: [{ id: 9, status: 'requested', role_scope: 'Java Engineer', end_client_scope: 'Acme', requested_at: '2026-01-01T00:00:00Z', confirmed_at: null, expires_at: null, proof_attachment_id: null, proof_recruiter_email_id: null, note: '' }],
    ...overrides,
  } as ApplicationCard
}

const actions = (kind: JourneyAction['kind']): JourneyAction => ({ kind, label: kind })
const cleanups: Array<() => void> = []

function render(action: JourneyAction, card = application(), props: Partial<Parameters<typeof JourneyPanel>[0]> = {}) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root: Root = createRoot(container)
  const defaults = {
    application: card,
    action,
    duplicateConflicts: [] as ApplicationDuplicateSummary[],
    apiBase: 'http://api.test',
    onUpdated: vi.fn(),
    onDuplicateConflicts: vi.fn(),
    onError: vi.fn(),
    onClose: vi.fn(),
    ...props,
  }
  act(() => root.render(<JourneyPanel {...defaults} />))
  cleanups.push(() => { act(() => root.unmount()); container.remove() })
  return { container, props: defaults }
}

async function submit(container: HTMLElement) {
  await act(async () => {
    container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('JourneyPanel actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    const updated = application({ status: 'contacted' })
    vi.mocked(api.addApplicationInterview).mockResolvedValue(updated)
    vi.mocked(api.createApplicationEvent).mockResolvedValue({ id: 1 } as never)
    vi.mocked(api.getApplication).mockResolvedValue(updated)
    vi.mocked(api.requestApplicationRtr).mockResolvedValue(updated)
    vi.mocked(api.submitApplicationToClient).mockResolvedValue(updated)
    vi.mocked(api.updateApplication).mockResolvedValue(updated)
    vi.mocked(api.updateApplicationInterview).mockResolvedValue(updated)
    vi.mocked(api.updateApplicationRtr).mockResolvedValue(updated)
  })

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
  })

  it.each([
    ['add_note', 'note'],
    ['add_call_note', 'call_note'],
    ['link_email', 'email_linked'],
  ] as const)('dispatches %s through the writable event endpoint and refreshes detail', async (kind, eventType) => {
    const { container, props } = render(actions(kind))
    await submit(container)
    expect(api.createApplicationEvent).toHaveBeenCalledWith('http://api.test', 7, expect.objectContaining({ event_type: eventType }))
    expect(api.getApplication).toHaveBeenCalledWith('http://api.test', 7)
    expect(props.onUpdated).toHaveBeenCalledOnce()
  })

  it('dispatches RTR request and each active-RTR transition', async () => {
    let view = render(actions('request_rtr'))
    await submit(view.container)
    expect(api.requestApplicationRtr).toHaveBeenCalledWith('http://api.test', 7, expect.objectContaining({ role_scope: 'Java Engineer', end_client_scope: 'Acme' }))

    for (const [kind, status] of [['confirm_rtr', 'confirmed'], ['expire_rtr', 'expired'], ['revoke_rtr', 'revoked']] as const) {
      view = render(actions(kind))
      await submit(view.container)
      expect(api.updateApplicationRtr).toHaveBeenLastCalledWith('http://api.test', 7, 9, expect.objectContaining({ status }))
    }
  })

  it('dispatches interview creation and result recording', async () => {
    let view = render(actions('add_interview'))
    await submit(view.container)
    expect(api.addApplicationInterview).toHaveBeenCalledWith('http://api.test', 7, expect.objectContaining({ round_type: 'interview_1', sync_application_status: true }))

    view = render(actions('record_result'))
    await submit(view.container)
    expect(api.updateApplicationInterview).toHaveBeenCalledWith('http://api.test', 7, 12, expect.objectContaining({ result: 'scheduled' }))
  })

  it.each([
    ['recruiter_responded', 'recruiter_responded'],
    ['mark_no_response', 'no_response'],
    ['mark_hired', 'hired'],
    ['close', 'rejected'],
  ] as const)('dispatches %s as a guarded status patch', async (kind, status) => {
    const { container } = render(actions(kind))
    await submit(container)
    expect(api.updateApplication).toHaveBeenCalledWith('http://api.test', 7, expect.objectContaining({ status }))
  })

  it('records a follow-up note and reminder through existing endpoints', async () => {
    const { container } = render(actions('follow_up'))
    await submit(container)
    expect(api.createApplicationEvent).toHaveBeenCalledWith('http://api.test', 7, expect.objectContaining({ event_type: 'note' }))
    expect(api.updateApplication).toHaveBeenCalledWith('http://api.test', 7, expect.objectContaining({ next_action_type: 'follow_up' }))
  })

  it('keeps the current journey when a mutation fails and surfaces the error', async () => {
    vi.mocked(api.createApplicationEvent).mockRejectedValueOnce(new Error('write failed'))
    const { container, props } = render(actions('add_note'))
    await submit(container)
    expect(props.onUpdated).not.toHaveBeenCalled()
    expect(container.textContent).toContain('write failed')
  })

  it('shows duplicate conflicts and succeeds through the explicit override path', async () => {
    const duplicate = { id: 99, job_title_snapshot: 'Java Engineer', end_client_snapshot: 'Acme', status: 'submitted_to_client', created_at: '2026-01-01T00:00:00Z' } as const
    vi.mocked(api.submitApplicationToClient)
      .mockRejectedValueOnce(new api.ApplicationDuplicateConflictError('Possible duplicate', [duplicate]))
      .mockResolvedValueOnce(application({ status: 'submitted_to_client' }))
    const onUpdated = vi.fn()

    function Harness() {
      const [conflicts, setConflicts] = useState<ApplicationDuplicateSummary[]>([])
      return <JourneyPanel application={application({ status: 'rtr_confirmed' })} action={actions('submit_to_client')} duplicateConflicts={conflicts} apiBase="http://api.test" onUpdated={onUpdated} onDuplicateConflicts={setConflicts} onError={vi.fn()} onClose={vi.fn()} />
    }

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    act(() => root.render(<Harness />))
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await submit(container)
    expect(container.textContent).toContain('#99')
    const override = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Submit anyway')
    await act(async () => { override?.click(); await Promise.resolve(); await Promise.resolve() })
    expect(api.submitApplicationToClient).toHaveBeenNthCalledWith(1, 'http://api.test', 7, false)
    expect(api.submitApplicationToClient).toHaveBeenNthCalledWith(2, 'http://api.test', 7, true)
    expect(onUpdated).toHaveBeenCalledOnce()
  })
})
