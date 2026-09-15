import { describe, expect, it } from 'vitest'

import type { ApplicationCard } from '../premium_numbers/types'
import { APPLICATION_STATUSES, legalActions, toJourney } from './journey'

function application(overrides: Partial<ApplicationCard> = {}): ApplicationCard {
  return {
    id: 1,
    status: 'matched',
    status_changed_at: '2026-01-01T00:00:00Z',
    created_at: '2026-01-01T00:00:00Z',
    events: [],
    rtr_history: [],
    interviews: [],
    milestones_reached: {},
    rejection_detail_tags: [],
    ...overrides,
  } as ApplicationCard
}

describe('toJourney', () => {
  it('sorts out-of-order status events by timestamp', () => {
    const nodes = toJourney(application({
      status: 'resume_shared',
      events: [
        { id: 2, event_type: 'status_changed', event_source: 'user', note: '', linked_recruiter_email_id: null, metadata_json: '{"from":"contacted","to":"resume_shared"}', occurred_at: '2026-01-03T00:00:00Z' },
        { id: 1, event_type: 'status_changed', event_source: 'user', note: '', linked_recruiter_email_id: null, metadata_json: '{"from":"matched","to":"contacted"}', occurred_at: '2026-01-02T00:00:00Z' },
      ],
    }))
    expect(nodes.map((node) => node.title)).toEqual(['Contacted', 'Resume Shared'])
  })

  it('re-sorts newest-first RTR history into ascending time', () => {
    const nodes = toJourney(application({
      rtr_history: [
        { id: 2, status: 'confirmed', role_scope: 'B', end_client_scope: 'Client', requested_at: '2026-01-03T00:00:00Z', confirmed_at: '2026-01-04T00:00:00Z', expires_at: null, proof_attachment_id: null, proof_recruiter_email_id: 1, note: '' },
        { id: 1, status: 'revoked', role_scope: 'A', end_client_scope: 'Client', requested_at: '2026-01-02T00:00:00Z', confirmed_at: null, expires_at: null, proof_attachment_id: null, proof_recruiter_email_id: null, note: '' },
      ],
    }))
    expect(nodes.filter((node) => node.kind === 'rtr').map((node) => node.id)).toEqual(['rtr:1', 'rtr:2'])
  })

  it('synthesizes the current head when status history is empty', () => {
    const nodes = toJourney(application({ status: 'rtr_confirmed' }))
    expect(nodes).toContainEqual(expect.objectContaining({ id: 'status:rtr_confirmed', title: 'Rtr Confirmed' }))
  })

  it('sorts an unscheduled interview last', () => {
    const nodes = toJourney(application({
      interviews: [
        { id: 1, round_type: 'interview_1', scheduled_at: null, format: '', interviewer_names: '', feedback: '', result: 'scheduled', follow_up_task_note: '' },
        { id: 2, round_type: 'recruiter_screen', scheduled_at: '2026-01-02T00:00:00Z', format: 'phone', interviewer_names: '', feedback: '', result: 'completed', follow_up_task_note: '' },
      ],
    }))
    expect(nodes.at(-1)?.id).toBe('interview:1')
  })

  it('does not derive nodes from milestones', () => {
    const withoutMilestones = toJourney(application({ status: 'resume_shared' }))
    const withMilestones = toJourney(application({ status: 'resume_shared', milestones_reached: { hired: '2026-01-05T00:00:00Z' } }))
    expect(withMilestones.map((node) => node.id)).toEqual(withoutMilestones.map((node) => node.id))
  })

  it('ends an interview journey on a terminal rejection node', () => {
    const nodes = toJourney(application({
      status: 'rejected',
      status_changed_at: '2026-01-03T00:00:00Z',
      closed_reason_code: 'skills_gap',
      rejection_detail_tags: [{ category: 'skill', value: 'Kubernetes', source: 'ai', confirmed_at: null }],
      events: [{ id: 2, event_type: 'status_changed', event_source: 'user', note: '', linked_recruiter_email_id: null, metadata_json: '{"from":"interview_1","to":"rejected"}', occurred_at: '2026-01-03T00:00:00Z' }],
      interviews: [{ id: 1, round_type: 'interview_1', scheduled_at: '2026-01-02T00:00:00Z', format: 'Teams', interviewer_names: '', feedback: '', result: 'failed', follow_up_task_note: '' }],
    }))
    expect(nodes.at(-1)).toEqual(expect.objectContaining({ kind: 'terminal', title: 'Rejected', state: 'failed', detail: expect.objectContaining({ closed_reason_code: 'skills_gap', rejection_tags: 'Kubernetes' }) }))
  })

  it('keeps the trunk after a revoked RTR', () => {
    const nodes = toJourney(application({
      status: 'client_reviewing',
      events: [{ id: 2, event_type: 'status_changed', event_source: 'user', note: '', linked_recruiter_email_id: null, metadata_json: '{"from":"rtr_confirmed","to":"client_reviewing"}', occurred_at: '2026-01-03T00:00:00Z' }],
      rtr_history: [{ id: 1, status: 'revoked', role_scope: 'Java', end_client_scope: 'Acme', requested_at: '2026-01-02T00:00:00Z', confirmed_at: null, expires_at: null, proof_attachment_id: null, proof_recruiter_email_id: null, note: '' }],
    }))
    expect(nodes.find((node) => node.id === 'rtr:1')?.state).toBe('failed')
    expect(nodes.at(-1)?.id).toBe('status:client_reviewing')
  })

  it('keeps both status events and marks a backwards move corrected when the server omits the trigger', () => {
    const nodes = toJourney(application({
      status: 'contacted',
      events: [
        { id: 1, event_type: 'status_changed', event_source: 'user', note: '', linked_recruiter_email_id: null, metadata_json: '{"from":"contacted","to":"resume_shared"}', occurred_at: '2026-01-02T00:00:00Z' },
        { id: 2, event_type: 'status_changed', event_source: 'user', note: '', linked_recruiter_email_id: null, metadata_json: '{"from":"resume_shared","to":"contacted"}', occurred_at: '2026-01-03T00:00:00Z' },
      ],
    }))
    const statuses = nodes.filter((node) => node.kind === 'status')
    expect(statuses).toHaveLength(2)
    expect(statuses.at(-1)).toEqual(expect.objectContaining({ title: 'Contacted', state: 'corrected', detail: expect.objectContaining({ trigger: 'user_correction' }) }))
  })

  it('degrades malformed status metadata to a plain event node', () => {
    const nodes = toJourney(application({
      status: 'contacted',
      events: [{ id: 1, event_type: 'status_changed', event_source: 'user', note: 'bad metadata', linked_recruiter_email_id: null, metadata_json: '{bad', occurred_at: '2026-01-02T00:00:00Z' }],
    }))
    expect(nodes).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'event:1', kind: 'note', state: 'done' }),
      expect.objectContaining({ id: 'status:contacted', kind: 'status' }),
    ]))
  })
})

describe('legalActions', () => {
  it('returns a non-throwing action list for every status', () => {
    for (const status of APPLICATION_STATUSES) expect(legalActions(application({ status })).length).toBeGreaterThan(0)
  })

  it('never offers submitted_to_client as a plain status action', () => {
    for (const status of APPLICATION_STATUSES) {
      expect(legalActions(application({ status })).some((candidate) => candidate.label === 'Submitted to Client')).toBe(false)
    }
  })

  it.each(['hired', 'rejected', 'withdrawn', 'no_response', 'position_closed', 'duplicate'] as const)('%s offers Add Note only', (status) => {
    expect(legalActions(application({ status })).map((candidate) => candidate.kind)).toEqual(['add_note'])
  })

  it('offers Submit to Client only after RTR confirmation', () => {
    expect(legalActions(application({ status: 'rtr_confirmed' })).map((candidate) => candidate.kind)).toContain('submit_to_client')
    expect(legalActions(application({ status: 'rtr_requested' })).map((candidate) => candidate.kind)).not.toContain('submit_to_client')
  })
})
