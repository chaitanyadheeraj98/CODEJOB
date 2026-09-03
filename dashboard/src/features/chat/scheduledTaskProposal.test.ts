import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS } from './proposals'

const handler = PROPOSAL_HANDLERS.propose_scheduled_task

const fields = (overrides: Record<string, unknown> = {}) => ({
  operation: 'create',
  task_id: 0,
  title: 'Morning digest',
  kind: 'digest',
  when_phrase: 'every weekday at 9am',
  note: '',
  trigger: 'Every weekday at 9:00 AM (America/New_York)',
  first_run: '2026-09-04T13:00:00+00:00',
  permitted_actions: 'Summarises pending work and notifies you. Changes nothing.',
  granularity_note: 'Scheduled work is checked every few minutes, so a run can start up to 5 minutes after its scheduled time.',
  reversible: true,
  reversible_detail: 'Creating a task changes no records.',
  ...overrides,
})

const method = (values: Record<string, unknown>) =>
  typeof handler.method === 'function' ? handler.method(values) : handler.method

const endpoint = (values: Record<string, unknown>) =>
  typeof handler.endpoint === 'function' ? handler.endpoint(values) : handler.endpoint

describe('the propose_scheduled_task handler', () => {
  it('creates through POST /scheduled-tasks', () => {
    expect(endpoint(fields())).toBe('/scheduled-tasks')
    expect(method(fields())).toBe('POST')
  })

  it('routes pause, resume and edit to PATCH on the task', () => {
    for (const operation of ['pause', 'resume', 'edit']) {
      const values = fields({ operation, task_id: 7 })
      expect(endpoint(values)).toBe('/scheduled-tasks/7')
      expect(method(values)).toBe('PATCH')
    }
  })

  it('routes delete to DELETE on the task', () => {
    const values = fields({ operation: 'delete', task_id: 7 })

    expect(endpoint(values)).toBe('/scheduled-tasks/7')
    expect(method(values)).toBe('DELETE')
  })

  // A malformed payload must not be able to aim a request anywhere: the
  // endpoint table is the client's, and an unknown operation resolves to ''
  // which runProposalAction refuses.
  it('resolves an unknown operation to no endpoint at all', () => {
    expect(endpoint(fields({ operation: 'exfiltrate' }))).toBe('')
  })

  it('sends the phrase, not a cron expression the client composed', () => {
    const body = handler.buildBody(fields()) as Record<string, unknown>

    expect(body.when).toBe('every weekday at 9am')
    expect(body).not.toHaveProperty('cron_expression')
  })

  it('sends only the operation for a lifecycle change', () => {
    expect(handler.buildBody(fields({ operation: 'pause', task_id: 7 })))
      .toEqual({ operation: 'pause' })
  })

  // The card is the last thing the user reads before the task exists, and what
  // it must show is the system's reading of the schedule - not their words.
  it('summarises the trigger, first run, permitted actions and timing caveat', () => {
    const rows = Object.fromEntries(handler.summary(fields()))

    expect(rows.Runs).toBe('Every weekday at 9:00 AM (America/New_York)')
    expect(rows['May do']).toContain('Changes nothing')
    expect(rows.Timing).toContain('5 minutes')
    expect(rows['First run']).toBeTruthy()
  })

  it('states reversibility from the field the tool set, never from prose', () => {
    expect(Object.fromEntries(handler.summary(fields())).Reversible).toBe('Yes')
    expect(Object.fromEntries(handler.summary(fields({ reversible: false }))).Reversible).toBe('No')
  })

  it('labels the confirm button by operation', () => {
    expect(handler.confirmLabel(fields())).toBe('Create Task')
    expect(handler.confirmLabel(fields({ operation: 'delete' }))).toBe('Delete Task')
  })
})
