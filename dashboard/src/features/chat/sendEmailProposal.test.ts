import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS } from './proposals'

const send = PROPOSAL_HANDLERS.propose_send_email

const fields = (overrides: Record<string, unknown> = {}) => ({
  action: 'send_email',
  candidate_email_id: 42,
  to: 'sarah@acme-staffing.com',
  cc: '',
  subject: 'Re: Senior Backend Engineer',
  body: 'Attached, thanks.',
  document_ids: [3, 7],
  document_names: ['CD_scan_0412.pdf', '2024_W2.pdf'],
  ...overrides,
})

const summaryOf = (values: Record<string, unknown>) => Object.fromEntries(send.summary(values))
const compose_body = (values: Record<string, unknown>) => send.buildBody(values) as { resume_id: number; document_ids: number[] }

describe('propose_send_email handler', () => {
  it('sends ids and shows names', () => {
    // The user confirms file names; the server re-resolves the ids. Sending the
    // names instead would let a rename between proposal and click attach a
    // different file than the one on the card.
    expect(send.buildBody(fields())).toEqual({
      body: 'Attached, thanks.',
      subject: 'Re: Senior Backend Engineer',
      to: 'sarah@acme-staffing.com',
      cc: '',
      resume_id: 0,
      confirm_new_recipients: false,
      document_ids: [3, 7],
    })
    expect(summaryOf(fields()).Attachments).toBe('CD_scan_0412.pdf, 2024_W2.pdf')
  })

  it('lists the attachments above the body', () => {
    // A long body pushes everything after it out of view, and the files leaving
    // with the mail are the part worth reading before clicking Send.
    const labels = send.summary(fields()).map(([label]) => label)
    expect(labels.indexOf('Attachments')).toBeLessThan(labels.indexOf('Body'))
  })

  it('omits the row entirely when nothing is attached', () => {
    const summary = summaryOf(fields({ document_ids: [], document_names: [] }))
    expect(summary.Attachments).toBeUndefined()
    expect(summary.To).toBe('sarah@acme-staffing.com')
  })

  it('sends an empty list for a proposal from before documents existed', () => {
    // An older tool payload has neither key; the body must still be valid.
    const legacy = fields()
    delete (legacy as Record<string, unknown>).document_ids
    delete (legacy as Record<string, unknown>).document_names
    expect(send.buildBody(legacy)).toEqual({
      body: 'Attached, thanks.',
      subject: 'Re: Senior Backend Engineer',
      to: 'sarah@acme-staffing.com',
      cc: '',
      resume_id: 0,
      confirm_new_recipients: false,
      document_ids: [],
    })
    expect(summaryOf(legacy).Attachments).toBeUndefined()
  })

  it('sends null for a proposal from before the envelope was editable', () => {
    // An older payload carries no to/cc. Null rather than omitted, so the
    // server's "keep the thread's own addresses" branch is the one that runs.
    const legacy = fields()
    delete (legacy as Record<string, unknown>).to
    delete (legacy as Record<string, unknown>).cc
    const body = send.buildBody(legacy) as { to: unknown; cc: unknown }
    expect(body.to).toBeNull()
    expect(body.cc).toBeNull()
  })

  it('warns above the address when the envelope no longer matches the thread', () => {
    // The one value on this card a user cannot check by recognising it: a
    // redirected reply looks exactly like an ordinary one.
    const rows = send.summary(fields({ to: 'hiring@employer.com', to_changed: true }))
    const labels = rows.map(([label]) => label)
    expect(labels.indexOf('Heads up')).toBeLessThan(labels.indexOf('To'))
    expect(summaryOf(fields())['Heads up']).toBeUndefined()
  })

  it('says "none" when a dropped CC would otherwise read as unchanged', () => {
    expect(summaryOf(fields({ cc: '', cc_changed: true })).CC).toBe('none')
  })

  it('drops junk rather than posting it as an id', () => {
    const body = send.buildBody(fields({ document_ids: [3, 'nine', null] })) as { document_ids: number[] }
    expect(body.document_ids).toEqual([3])
  })

  it('still routes to the candidate send endpoint', () => {
    const resolve = send.endpoint as (f: Record<string, unknown>) => string
    expect(resolve(fields())).toBe('/candidates/42/send-chat-reply')
    expect(send.method).toBe('POST')
  })

  it('carries a resume as its own field, beside the documents', () => {
    // A resume id sent in document_ids resolves to nothing on the server, so
    // the two stores stay two fields on a reply exactly as on a new email.
    const withResume = fields({ resume_id: 21, resume_name: 'Resume.pdf' })
    const body = compose_body(withResume)
    expect(body.resume_id).toBe(21)
    expect(body.document_ids).toEqual([3, 7])
    expect(summaryOf(withResume).Attachments).toBe('CD_scan_0412.pdf, 2024_W2.pdf, Resume.pdf')
  })

  it('lists a resume even when nothing else is attached', () => {
    const onlyResume = fields({ document_ids: [], document_names: [], resume_id: 21, resume_name: 'Resume.pdf' })
    expect(summaryOf(onlyResume).Attachments).toBe('Resume.pdf')
  })
})
