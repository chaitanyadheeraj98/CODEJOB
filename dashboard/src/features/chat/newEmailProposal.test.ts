import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS } from './proposals'

const compose = PROPOSAL_HANDLERS.propose_new_email

const fields = (overrides: Record<string, unknown> = {}) => ({
  action: 'send_new_email',
  to: 'xyz@example.com',
  cc: '',
  subject: 'Contract rate',
  body: 'Can we discuss?',
  document_ids: [],
  document_names: [],
  unknown_recipients: ['xyz@example.com'],
  requires_recipient_confirmation: true,
  ...overrides,
})

const summaryOf = (values: Record<string, unknown>) => Object.fromEntries(compose.summary(values))

describe('propose_new_email handler', () => {
  it('posts to the composing route, which names no candidate', () => {
    // A fixed string, not a template: this payload has no id, so there is
    // nothing in the URL a malformed one could aim at.
    expect(compose.endpoint).toBe('/chat/new-email')
    expect(compose.method).toBe('POST')
  })

  it('sends the whole envelope, since nothing on the server can supply it', () => {
    expect(compose.buildBody(fields())).toEqual({
      to: 'xyz@example.com',
      cc: '',
      subject: 'Contract rate',
      body: 'Can we discuss?',
      document_ids: [],
      resume_id: 0,
      confirm_new_recipients: true,
    })
  })

  it('carries a resume as its own field, not as a document id', () => {
    // The failure this prevents: a resume id sent in document_ids resolves to
    // nothing, the tool refuses the call, and the model retries it unchanged.
    const withResume = fields({ resume_id: 21, resume_name: 'Chaithanya_Java.pdf' })
    const body = withResume ? compose.buildBody(withResume) as { resume_id: number; document_ids: number[] } : null
    expect(body?.resume_id).toBe(21)
    expect(body?.document_ids).toEqual([])
    expect(summaryOf(withResume).Attachments).toBe('Chaithanya_Java.pdf')
  })

  it('lists a resume alongside the documents', () => {
    const both = fields({ document_ids: [3], document_names: ['W2.pdf'], resume_id: 21, resume_name: 'Resume.pdf' })
    expect(summaryOf(both).Attachments).toBe('W2.pdf, Resume.pdf')
  })

  it('says it starts a thread before anything else', () => {
    // A reply lands under something the reader recognises; this does not, and
    // a warning below the body is a warning after the decision.
    const labels = compose.summary(fields()).map(([label]) => label)
    expect(labels[0]).toBe('Heads up')
    expect(summaryOf(fields())['Heads up']).toContain('new email thread')
  })

  it('names an unknown recipient above the address itself', () => {
    const labels = compose.summary(fields()).map(([label]) => label)
    expect(labels.indexOf('Not in your records')).toBeLessThan(labels.indexOf('To'))
    expect(summaryOf(fields())['Not in your records']).toBe('xyz@example.com')
  })

  it('asks for nothing extra when the recipient is already known', () => {
    const known = fields({ unknown_recipients: [], requires_recipient_confirmation: false })
    expect(summaryOf(known)['Not in your records']).toBeUndefined()
    expect((compose.buildBody(known) as { confirm_new_recipients: boolean }).confirm_new_recipients).toBe(false)
  })

  it('lists attachments above the body', () => {
    const withFiles = fields({ document_ids: [3], document_names: ['Resume.pdf'] })
    const labels = compose.summary(withFiles).map(([label]) => label)
    expect(labels.indexOf('Attachments')).toBeLessThan(labels.indexOf('Body'))
    expect(summaryOf(withFiles).Attachments).toBe('Resume.pdf')
  })

  it('drops junk rather than posting it as a document id', () => {
    const body = compose.buildBody(fields({ document_ids: [3, 'nine', null] })) as { document_ids: number[] }
    expect(body.document_ids).toEqual([3])
  })

  it('says plainly that nothing has gone yet', () => {
    expect(compose.pendingNotice).toContain('Nothing has been sent yet')
  })
})
