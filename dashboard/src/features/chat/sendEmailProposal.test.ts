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

describe('propose_send_email handler', () => {
  it('sends ids and shows names', () => {
    // The user confirms file names; the server re-resolves the ids. Sending the
    // names instead would let a rename between proposal and click attach a
    // different file than the one on the card.
    expect(send.buildBody(fields())).toEqual({
      body: 'Attached, thanks.',
      subject: 'Re: Senior Backend Engineer',
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
      document_ids: [],
    })
    expect(summaryOf(legacy).Attachments).toBeUndefined()
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
})
