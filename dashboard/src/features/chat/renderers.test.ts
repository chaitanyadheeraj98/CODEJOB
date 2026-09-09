import { describe, expect, it } from 'vitest'

import { asInferenceProvenance, asProvenance, recordIndexFromMessages, renderForMessage } from './renderers'
import type { ChatMessage } from './types'

const payload = {
  action: 'render_candidate_table',
  title: 'Top matches',
  columns: ['role', 'sender'],
  rows: [{ candidate_id: 7323, record_id: 'abc', role: 'Backend', sender: 'sarah@example.com' }],
  dropped: [],
  truncated: false,
}

const toolMessage = (content: unknown, tool_name = 'render_candidate_table'): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name,
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

describe('renderForMessage', () => {
  it('parses a well-formed table payload', () => {
    const parsed = renderForMessage(toolMessage(payload))

    // Narrowing on `kind` is the contract now: the union is what lets a second
    // visualization share this registry without being drawn as a table.
    expect(parsed?.kind).toBe('candidate_table')
    if (parsed?.kind !== 'candidate_table') throw new Error('expected a candidate table payload')
    expect(parsed.data.title).toBe('Top matches')
    expect(parsed.data.columns).toEqual(['role', 'sender'])
    expect(parsed.data.rows[0].candidate_id).toBe(7323)
  })

  it('ignores tool messages no handler claims', () => {
    expect(renderForMessage(toolMessage(payload, 'propose_send_email'))).toBeNull()
    expect(renderForMessage(toolMessage(payload, 'search_candidates'))).toBeNull()
  })

  it('ignores user and assistant messages', () => {
    expect(renderForMessage({ ...toolMessage(payload), role: 'assistant' })).toBeNull()
    expect(renderForMessage({ ...toolMessage(payload), role: 'user' })).toBeNull()
  })

  // A malformed payload has to degrade to "nothing rendered". Throwing here
  // would take the whole message list down with it.
  it.each([
    ['not JSON at all', 'sorry, something went wrong'],
    ['a different action', { ...payload, action: 'propose_send_email' }],
    ['no rows array', { ...payload, rows: 'lots' }],
    ['a row with no candidate_id', { ...payload, rows: [{ role: 'Backend' }] }],
    ['a row that is not an object', { ...payload, rows: ['Backend'] }],
    ['no columns', { ...payload, columns: [] }],
    ['columns that are not strings', { ...payload, columns: [1, 2] }],
  ])('returns null for %s', (_label, content) => {
    expect(renderForMessage(toolMessage(content))).toBeNull()
  })

  it('tolerates a missing dropped list and truncated flag', () => {
    const parsed = renderForMessage(toolMessage({ ...payload, dropped: undefined, truncated: undefined }))

    if (parsed?.kind !== 'candidate_table') throw new Error('expected a candidate table payload')
    expect(parsed.data.dropped).toEqual([])
    expect(parsed.data.truncated).toBe(false)
  })
})

describe('asInferenceProvenance', () => {
  const evidence = [{
    signal: 'job_title',
    left_value: 'Senior Java Developer',
    right_value: 'Java Developer',
    normalized_to: 'java developer',
    match: 'exact',
    weight: 0.2,
    sub_score: 1,
    source: 'canonical_entity_taxonomy',
  }]

  const block = (overrides: Record<string, unknown> = {}) => ({
    metric: 'relationship',
    source: 'relationship_scoring',
    row_count: 2,
    date_range: { from: null, to: null },
    filters: {},
    assumptions: ['This relationship was inferred from the records listed, not recorded by anyone.'],
    confidence: 'likely',
    score: 0.72,
    evidence,
    semantic_available: true,
    ...overrides,
  })

  it('accepts a well-formed inference block and keeps every base field', () => {
    const parsed = asInferenceProvenance(block())

    expect(parsed?.metric).toBe('relationship')
    expect(parsed?.row_count).toBe(2)
    expect(parsed?.confidence).toBe('likely')
    expect(parsed?.score).toBe(0.72)
    expect(parsed?.evidence).toHaveLength(1)
    expect(parsed?.semantic_available).toBe(true)
  })

  it('defaults semantic_available to false rather than assuming the richer population', () => {
    expect(asInferenceProvenance(block({ semantic_available: undefined }))?.semantic_available).toBe(false)
  })

  // Each of these is a claim arriving without something that makes it
  // inspectable. A claim that cannot be inspected must not reach the DOM.
  it.each([
    ['no confidence', { confidence: undefined }],
    ['an unknown confidence level', { confidence: 'probable' }],
    ['the scorer "none" verdict, which must never surface', { confidence: 'none' }],
    ['empty evidence', { evidence: [] }],
    ['no evidence key at all', { evidence: undefined }],
    ['evidence that is not an array', { evidence: 'job title matched' }],
    ['a score above one', { score: 1.5 }],
    ['a negative score', { score: -0.2 }],
    ['a NaN score', { score: Number.NaN }],
    ['a non-numeric score', { score: '0.72' }],
    ['an evidence entry with an unknown match kind', { evidence: [{ ...evidence[0], match: 'vibes' }] }],
    ['an evidence entry with no signal', { evidence: [{ ...evidence[0], signal: '' }] }],
    ['an evidence entry with a non-numeric weight', { evidence: [{ ...evidence[0], weight: 'high' }] }],
  ])('returns null for %s', (_label, overrides) => {
    expect(asInferenceProvenance(block(overrides))).toBeNull()
  })

  // The inference gate is a superset of the measured-number gate, not a
  // replacement: a block that fails the base check fails this one too.
  it('returns null when the underlying provenance block is malformed', () => {
    expect(asInferenceProvenance(block({ metric: '' }))).toBeNull()
    expect(asInferenceProvenance(block({ row_count: 'two' }))).toBeNull()
  })

  it('accepts a plain provenance block through asProvenance but not through asInferenceProvenance', () => {
    const measured = { metric: 'pipeline_summary', source: 'dashboard_summary', row_count: 12, date_range: {}, filters: {}, assumptions: [] }

    expect(asProvenance(measured)).not.toBeNull()
    expect(asInferenceProvenance(measured)).toBeNull()
  })
})

describe('recordIndexFromMessages', () => {
  it('indexes record ids from the candidate tables on the transcript', () => {
    const index = recordIndexFromMessages([toolMessage(payload)])

    expect(index.get('abc')).toBe(7323)
    expect(index.size).toBe(1)
  })

  it('ignores rows with no record id and messages nothing can render', () => {
    const withoutRecord = {
      ...payload,
      rows: [{ candidate_id: 11, record_id: null, role: 'Backend', sender: 'x@example.com' }],
    }
    const assistant: ChatMessage = {
      id: 2, role: 'assistant', tool_name: null,
      content: 'record-abc is the one', created_at: '2026-01-01T00:00:00Z',
    }

    // Nothing here can be cited: one row has no permanent id, and prose is not
    // evidence. An empty index is what keeps the assistant's own text inert.
    expect(recordIndexFromMessages([toolMessage(withoutRecord), assistant]).size).toBe(0)
  })
})
