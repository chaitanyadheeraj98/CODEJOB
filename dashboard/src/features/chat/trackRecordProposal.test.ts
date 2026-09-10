import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, proposalResultDetail } from './proposals'

const handler = PROPOSAL_HANDLERS.propose_track_record

const labelFields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_track_record',
  record_kind: 'label_thread',
  record_id: null,
  thread_id: 'label-thread',
  record_label: 'RTR | Java Full Stack Developer',
  recruiter: 'Naman <naman@valzosoft.com>',
  labels: ['RTR Requested'],
  resume: 'ChaithanyaDheeraj2026.docx (v6)',
  fields: { resume_asset_id: 1 },
  changes: [{ field: 'tracked', from: false, to: true }],
  count: 1,
  ...overrides,
})

const requirementFields = (overrides: Record<string, unknown> = {}) => labelFields({
  record_kind: 'requirement',
  record_id: 'record-a',
  thread_id: 'thread-a',
  fields: { resume_asset_id: 1, recruiter_email_id: 42, dedupe_key: 'abc' },
  ...overrides,
})

const summaryOf = (values: Record<string, unknown>) => Object.fromEntries(handler.summary(values))

describe('propose_track_record handler', () => {
  it('promotes a label thread through its own endpoint', () => {
    const resolve = handler.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(labelFields())).toBe('/appts/label-threads/label-thread/promote')
    expect(handler.method).toBe('POST')
  })

  // A thread id comes from Gmail, so it cannot be pasted into a path unescaped.
  it('escapes the thread id it puts in the path', () => {
    const resolve = handler.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(labelFields({ thread_id: 'a/b?c' }))).toBe('/appts/label-threads/a%2Fb%3Fc/promote')
  })

  it('creates a parsed requirement through the applications endpoint', () => {
    const resolve = handler.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(requirementFields())).toBe('/appts/applications')
  })

  it('resolves to an empty endpoint for a kind it does not know', () => {
    const resolve = handler.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(labelFields({ record_kind: 'planet' }))).toBe('')
  })

  // The label route derives the recruiter and the dedupe key from the stored
  // thread. Sending them from the card would let the model's copy of them win.
  it('sends only the resume for a label thread', () => {
    expect(handler.buildBody(labelFields())).toEqual({ resume_asset_id: 1 })
  })

  it('sends the stored source ids for a parsed requirement', () => {
    expect(handler.buildBody(requirementFields())).toEqual({
      resume_asset_id: 1,
      recruiter_email_id: 42,
      dedupe_key: 'abc',
    })
  })

  it('shows the recruiter, labels and the exact resume version on the card', () => {
    const summary = summaryOf(labelFields())

    expect(summary.Subject).toBe('RTR | Java Full Stack Developer')
    expect(summary.Recruiter).toBe('Naman <naman@valzosoft.com>')
    expect(summary.Labels).toBe('RTR Requested')
    expect(summary.Resume).toBe('ChaithanyaDheeraj2026.docx (v6)')
  })

  it('joins several labels rather than dropping the extras', () => {
    expect(summaryOf(labelFields({ labels: ['RTR Requested', 'Submissions'] })).Labels)
      .toBe('RTR Requested, Submissions')
  })

  it('says tracking has not started until the resume is confirmed', () => {
    expect(handler.confirmLabel(labelFields())).toBe('Confirm & Track')
    expect(handler.pendingNotice).toContain('only after you confirm')
  })

  // Both routes echo an ApplicationResponse, so the generic `id` branch would
  // otherwise report a tracked RTR thread as a saved contact.
  it('names the tracked application instead of calling it a saved contact', () => {
    const detail = proposalResultDetail({ id: 42, status: 'matched' }, labelFields())

    expect(detail).toContain('application 42')
    expect(detail).not.toContain('contact')
    expect(detail).toContain('not submitted yet')
  })
})
