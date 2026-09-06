import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, proposalResultDetail } from './proposals'

const handler = PROPOSAL_HANDLERS.propose_manual_requirement
const fields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_manual_requirement',
  source: 'chat_message',
  source_label: 'the message you sent',
  attachment_id: null,
  message_id: 42,
  characters: 25,
  jd_text: 'Role: Platform Engineer',
  duplicate_of: null,
  ...overrides,
})
const summaryOf = (values: Record<string, unknown>) => Object.fromEntries(handler.summary(values))

describe('propose_manual_requirement handler', () => {
  it('posts ids and never the displayed JD text', () => {
    const body = handler.buildBody(fields()) as Record<string, unknown>
    expect(body).toEqual({
      attachment_id: null,
      message_id: 42,
      acknowledged_duplicate_of: null,
    })
    expect(body).not.toHaveProperty('jd_text')
  })

  it('forwards the duplicate acknowledgement id', () => {
    const body = handler.buildBody(fields({ duplicate_of: { id: 87 } })) as Record<string, unknown>
    expect(body.acknowledged_duplicate_of).toBe(87)
  })

  it('shows the complete requirement unchanged', () => {
    const jd = 'Role: Platform Engineer\nRate: $88/hr\nID: ORIGINAL-42'
    expect(summaryOf(fields({ jd_text: jd }))['Complete requirement that will be ingested']).toBe(jd)
  })

  it('does not truncate a document-sized requirement', () => {
    const jd = 'x'.repeat(4_000)
    expect(summaryOf(fields({ jd_text: jd }))['Complete requirement that will be ingested']).toBe(jd)
  })

  it('shows the non-blocking duplicate warning only when present', () => {
    expect(summaryOf(fields())['Possible duplicate']).toBeUndefined()
    const summary = summaryOf(fields({
      duplicate_of: {
        id: 87,
        role: 'Platform Engineer',
        client: 'Acme',
        created_at: '2026-09-06T12:00:00Z',
      },
    }))
    expect(summary['Possible duplicate']).toContain('#87')
    expect(summary['Possible duplicate']).toContain('It will still be created.')
  })

  it('states that nothing exists before confirmation', () => {
    expect(handler.pendingNotice).toContain('Nothing has been created yet')
  })

  it('names Needs Review after the job is queued', () => {
    const detail = proposalResultDetail(
      { run_key: 'manual_intake:42', status: 'queued' },
      fields(),
    )
    expect(detail).toContain('Needs Review')
    expect(detail).not.toBe('Queued. It runs in the background - ask me for the result.')
  })

  it('uses the fixed from-chat endpoint', () => {
    expect(handler.endpoint).toBe('/manual-requirements/from-chat')
  })
})
