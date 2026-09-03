import { describe, expect, it } from 'vitest'

import { renderForMessage } from './renderers'
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

    expect(parsed?.data.title).toBe('Top matches')
    expect(parsed?.data.columns).toEqual(['role', 'sender'])
    expect(parsed?.data.rows[0].candidate_id).toBe(7323)
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

    expect(parsed?.data.dropped).toEqual([])
    expect(parsed?.data.truncated).toBe(false)
  })
})
