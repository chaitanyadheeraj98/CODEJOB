import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, isProposalToolName, proposalForMessage } from './proposals'
import type { ChatMessage } from './types'

const handler = PROPOSAL_HANDLERS.propose_taxonomy_bulk_review

// The payload `propose_taxonomy_bulk_review` actually returns, field for field.
const fields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_taxonomy_bulk_review',
  scope: 'skill',
  taxonomy_action: 'approve',
  label: 'Approve',
  keys: ['temporal workflow', 'agent studio'],
  count: 2,
  sample_names: ['Temporal Workflow', 'Agent Studio'],
  total_in_bucket: 2,
  remaining_after_batch: 0,
  needs_human_count: 0,
  reversible: true,
  reversible_detail: 'Reversible - an approved value can be dismissed later from Settings.',
  model_error: null,
  ...overrides,
})

const summaryOf = (values: Record<string, unknown>) => Object.fromEntries(handler.summary(values))

const bodyOf = (values: Record<string, unknown>) =>
  handler.buildBody(values) as Record<string, unknown>

describe('propose_taxonomy_bulk_review proposal', () => {
  it('is a recognised proposal tool with a card', () => {
    expect(isProposalToolName('propose_taxonomy_bulk_review')).toBe(true)
    const message = {
      id: 4,
      role: 'tool',
      tool_name: 'propose_taxonomy_bulk_review',
      content: JSON.stringify(fields()),
      created_at: '2026-01-01T00:00:00Z',
    } as ChatMessage
    expect(proposalForMessage(message)).not.toBeNull()
  })

  it('posts to the bulk-review apply endpoint', () => {
    expect(handler.endpoint).toBe('/settings/taxonomy/bulk-review/apply')
    expect(handler.method).toBe('POST')
  })

  it('sends the exact keys with a count derived from them', () => {
    expect(bodyOf(fields())).toEqual({
      scope: 'skill',
      action: 'approve',
      keys: ['temporal workflow', 'agent studio'],
      expected_count: 2,
    })
  })

  it('derives expected_count from the keys, not the tool count field', () => {
    // If the two ever disagree the server answers 409 and writes nothing, so the
    // number the request carries must come from the list it actually sends.
    const body = bodyOf(fields({ count: 99 }))
    expect(body.expected_count).toBe(2)
  })

  it('keeps expected_count in step with the keys even on a malformed payload', () => {
    // `strings()` drops empties and coerces the rest, so a junk entry can survive
    // as text. That is safe here in a way it would not be for a numeric id: an
    // unknown key is re-checked server-side and comes back as `no_longer_pending`
    // rather than writing anything. What must hold is that the count never drifts
    // from the list, because that pair is what the 409 guard compares.
    const body = bodyOf(fields({ keys: ['good one', 7, null, '', 'other one'] }))
    expect(body.keys).toEqual(['good one', '7', 'other one'])
    expect(body.expected_count).toBe(3)
  })

  it('sends nothing when the payload has no keys at all', () => {
    const body = bodyOf(fields({ keys: undefined }))
    expect(body.keys).toEqual([])
    expect(body.expected_count).toBe(0)
  })

  it('labels the confirm button with the count and scope', () => {
    expect(handler.confirmLabel(fields())).toBe('Approve 2 skill value(s)')
    expect(handler.confirmLabel(fields({ label: 'Dismiss', taxonomy_action: 'dismiss', scope: 'role' })))
      .toBe('Dismiss 2 role value(s)')
  })

  it('states the action, count, sample and reversibility', () => {
    const summary = summaryOf(fields())
    expect(summary.Action).toBe('Approve pending skill values')
    expect(summary.Values).toBe('2')
    expect(summary['For example']).toBe('Temporal Workflow, Agent Studio')
    expect(summary.Reversible).toBe('Yes')
  })

  it('says when a batch leaves more behind', () => {
    const summary = summaryOf(fields({ remaining_after_batch: 212 }))
    expect(summary['Not in this batch']).toContain('212 more')
  })

  it('says when records still need the human', () => {
    const summary = summaryOf(fields({ needs_human_count: 197 }))
    expect(summary['Left for you']).toContain('197 undecided')
  })

  it('omits the optional rows when there is nothing to report', () => {
    const summary = summaryOf(fields())
    expect(summary['Not in this batch']).toBeUndefined()
    expect(summary['Left for you']).toBeUndefined()
    expect(summary.Model).toBeUndefined()
  })

  it('surfaces a model error on the card', () => {
    const summary = summaryOf(fields({ model_error: 'DeepSeek API key is missing' }))
    expect(summary.Model).toContain('DeepSeek API key is missing')
  })

  it('warns that nothing is written until Confirm', () => {
    expect(handler.pendingNotice).toContain('Nothing has been approved or dismissed yet')
  })
})
