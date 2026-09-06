import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, isProposalToolName, proposalForMessage, proposalResultDetail, unsupportedProposalNotice } from './proposals'
import type { ChatMessage } from './types'

const search = PROPOSAL_HANDLERS.propose_nvoids_search

// The payload `propose_nvoids_search` actually returns, field for field.
const fields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_nvoids_search',
  company: 'Morgan Stanley',
  criteria: {
    end_client: 'Morgan Stanley',
    job_role: 'Java Developer',
    search_location: 'Texas',
    query_mode: 'composed',
    batch_limit: 10,
    generated_query: '(Texas) and Java Developer and morgan and stanley',
  },
  criteria_summary: "end client 'Morgan Stanley', role 'Java Developer', batch limit 10",
  already_stored: 4,
  started: false,
  requires_confirmation: true,
  ...overrides,
})

const summaryOf = (values: Record<string, unknown>) => Object.fromEntries(search.summary(values))

const toolMessage = (content: unknown): ChatMessage => ({
  id: 9,
  role: 'tool',
  tool_name: 'propose_nvoids_search',
  content: JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
} as ChatMessage)

describe('propose_nvoids_search handler', () => {
  it('draws a card at all', () => {
    // The regression this file exists for. Without a handler the row is not
    // merely undrawn: visibleMessages drops it from the session, so the
    // assistant's "ready to confirm" sat beside nothing at all.
    const resolved = proposalForMessage(toolMessage(fields()))
    expect(resolved).not.toBeNull()
    expect(resolved?.handler.endpoint).toBe('/jobs/nvoids-client-search')
  })

  it('sends the criteria and never the query', () => {
    // generated_query is on the payload and shown on the card. Sending it back
    // would turn a display field into the instruction, and the string reaching
    // nvoids would be one nothing on screen had to agree with.
    const body = search.buildBody(fields()) as Record<string, unknown>
    expect(body).toEqual({
      end_client: 'Morgan Stanley',
      job_role: 'Java Developer',
      search_location: 'Texas',
      query_mode: 'composed',
      batch_limit: 10,
    })
    expect(Object.keys(body)).not.toContain('generated_query')
  })

  it('shows the query that will run', () => {
    expect(summaryOf(fields()).Query).toBe('(Texas) and Java Developer and morgan and stanley')
  })

  it('says how much is already stored', () => {
    // A crawl of a company with 400 records already held is usually not what
    // the user wants, and the count is the only thing on the card that says so.
    expect(summaryOf(fields())['Already stored']).toBe('4 record(s) for this company')
  })

  it('hides role and location in end-client-only mode', () => {
    // They are dropped from the query in that mode, so listing them would show
    // two criteria that will not be applied.
    const discovery = fields({
      criteria: { ...fields().criteria, query_mode: 'end_client_only' },
    })
    const summary = summaryOf(discovery)
    expect(summary.Role).toBeUndefined()
    expect(summary.Location).toBeUndefined()
    expect(summary.Mode).toBe('End client only')
  })

  it('falls back to the company when criteria are missing', () => {
    const bare = fields({ criteria: {} })
    expect((search.buildBody(bare) as Record<string, unknown>).end_client).toBe('Morgan Stanley')
    expect((search.buildBody(bare) as Record<string, unknown>).query_mode).toBe('composed')
    expect((search.buildBody(bare) as Record<string, unknown>).batch_limit).toBe(10)
  })

  it('says nothing has run until the click', () => {
    expect(search.pendingNotice).toMatch(/only when you click Confirm/)
  })

  it('names the company on the button', () => {
    expect(search.confirmLabel(fields())).toBe('Search Nvoids for Morgan Stanley')
  })
})

describe('the result of a queued job', () => {
  it('reports it as queued rather than done', () => {
    // Every other branch reports something that already happened. For a crawl
    // just enqueued, "Action completed." is the one sentence on screen implying
    // results exist.
    const detail = proposalResultDetail({ run_key: 'nvoids_client_search:abc', job_id: 'j1', status: 'queued' })
    expect(detail).toMatch(/Queued/)
    expect(detail).not.toBe('Action completed.')
  })
})

describe('a proposal this build cannot draw', () => {
  const row = (tool_name: string, content = '{"action":"x"}'): ChatMessage => ({
    id: 12,
    role: 'tool',
    tool_name,
    content,
    created_at: '2026-01-01T00:00:00Z',
  } as ChatMessage)

  it('says so, naming the tool', () => {
    // The run-time half of the guarantee. test_proposal_card_coverage.py fails
    // CI when a tool ships with no handler; this is what a user sees if one
    // reaches them anyway - a dashboard build older than its backend, or a flag
    // turning on a tool the bundle predates.
    const notice = unsupportedProposalNotice(row('propose_a_thing_from_the_future'))
    expect(notice).toContain('propose_a_thing_from_the_future')
    expect(notice).toContain('nothing has happened')
  })

  it('stays quiet for a tool that does have a card', () => {
    expect(unsupportedProposalNotice(row('propose_nvoids_search'))).toBeNull()
  })

  it('stays quiet for read-only tools', () => {
    // Their output is not a proposal, and announcing "no card" for a search
    // result would be noise on every turn that used one.
    expect(unsupportedProposalNotice(row('search_candidates'))).toBeNull()
    expect(unsupportedProposalNotice(row('get_resume'))).toBeNull()
  })

  it('does not depend on the payload parsing', () => {
    // Whatever the row carries, the app still knows it cannot draw it. Parsing
    // first would put this behind the same silence it exists to break.
    expect(unsupportedProposalNotice(row('propose_a_thing_from_the_future', 'not json'))).not.toBeNull()
  })

  it('recognises a proposal tool by its prefix alone', () => {
    expect(isProposalToolName('propose_send_email')).toBe(true)
    expect(isProposalToolName('list_resumes')).toBe(false)
  })
})
