// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RenderedMessage from './RenderedMessage'
import { ChatContext, type ChatContextValue } from './chatContext'
import type { RenderedPayload } from './renderers'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const tablePayload: RenderedPayload = {
  kind: 'candidate_table',
  data: {
    title: 'Top matches',
    columns: ['role', 'ats_score'],
    rows: [{ candidate_id: 11, record_id: 'a', role: 'Backend Engineer', ats_score: 61.5 }],
    dropped: [],
    truncated: false,
  },
}

const chatStub = () => ({
  proposalResults: {},
  proposalBusyId: null,
  approveProposal: vi.fn(),
  cancelProposal: vi.fn(),
  focusCandidate: vi.fn(),
  navigateToQueue: vi.fn(),
  apiBase: 'http://api.test',
}) as unknown as ChatContextValue

describe('RenderedMessage', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (payload: RenderedPayload, surface: 'page' | 'compact') => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chatStub()}>
        <RenderedMessage messageId={99} payload={payload} surface={surface} />
      </ChatContext.Provider>,
    ))
    return container
  }

  it('renders the interactive table on the page surface', () => {
    const el = render(tablePayload, 'page')

    expect(el.textContent).toContain('Backend Engineer')
    expect(el.querySelectorAll('input[type="checkbox"]').length).toBeGreaterThan(0)
  })

  it('renders the read-only table on the compact surface', () => {
    const el = render(tablePayload, 'compact')

    expect(el.textContent).toContain('Backend Engineer')
    expect(el.querySelectorAll('input[type="checkbox"]').length).toBe(0)
  })

  it('routes the ninth kind to the relationship renderer', () => {
    const el = render({
      kind: 'relationship_cluster',
      data: {
        title: 'Wells Fargo programme',
        claim: 'Two requirements appear to belong together.',
        cluster_id: 'cluster-1',
        status: 'proposed',
        members: [
          { opportunity_id: 11, label: 'Java Developer', detail: '', confidence: 'likely', drill_to: null },
        ],
        inferred: { end_client: '', partner: '', domain: '' },
        provenance: {
          metric: 'Related requirements',
          source: 'v3_weighted_v1',
          row_count: 1,
          date_range: { from: null, to: null },
          filters: {},
          assumptions: ['This relationship was inferred from the records listed, not recorded by anyone.'],
          confidence: 'likely',
          score: 0.81,
          evidence: [{
            signal: 'job_title', left_value: 'a', right_value: 'b', normalized_to: '',
            match: 'exact', weight: 0.2, sub_score: 1, source: 'canonical_entity_taxonomy',
          }],
          semantic_available: true,
        },
      },
    }, 'page')

    expect(el.querySelector('.chatRelationship')).not.toBeNull()
    expect(el.textContent).toContain('Likely')
  })

  // A payload kind this build does not know about has to degrade to nothing,
  // the same contract every parse() already honours. Throwing here would take
  // the whole message list down.
  //
  // The kind here must stay one the union will never gain: an earlier version
  // of this test used 'chart', and it started rendering a real (empty) chart
  // the moment W5 added that member.
  it('renders nothing and throws nothing for an unknown kind', () => {
    const unknown = { kind: 'from_a_future_build', data: {} } as unknown as RenderedPayload

    expect(() => render(unknown, 'page')).not.toThrow()
    expect(container!.textContent).toBe('')
  })
})
