// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RelationshipCluster from './RelationshipCluster'
import { ChatContext, type ChatContextValue } from './chatContext'
import { renderForMessage, type RelationshipClusterData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const evidence = [
  {
    signal: 'job_title',
    left_value: 'Senior Java Developer',
    right_value: 'Java Developer',
    normalized_to: 'Java Developer',
    match: 'alias',
    weight: 0.2,
    sub_score: 1,
    source: 'canonical_entity_taxonomy',
  },
  {
    signal: 'end_client',
    left_value: '',
    right_value: '',
    normalized_to: '',
    match: 'absent',
    weight: 0,
    sub_score: 0,
    source: 'recruiter_opportunities',
  },
]

const payload = (overrides: Record<string, unknown> = {}) => ({
  action: 'render_relationship_cluster',
  title: 'Wells Fargo programme',
  claim: '3 requirements appear to belong together.',
  cluster_id: 'cluster-1',
  status: 'proposed',
  members: [
    { opportunity_id: 11, label: 'Java Developer', detail: 'Austin, TX', confidence: 'likely', drill_to: { page: 'premium_numbers', tab: 'opportunities', filters: {} } },
    { opportunity_id: 12, label: 'Java Backend Engineer', detail: 'Dallas, TX', confidence: 'possible', drill_to: null },
  ],
  inferred: { end_client: '', partner: '', domain: '' },
  provenance: {
    metric: 'Related requirements',
    source: 'v3_weighted_v1',
    row_count: 2,
    date_range: { from: null, to: null },
    filters: {},
    assumptions: ['This relationship was inferred from the records listed, not recorded by anyone.'],
    confidence: 'likely',
    score: 0.81,
    evidence,
    semantic_available: true,
  },
  ...overrides,
})

const toolMessage = (content: unknown): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name: 'get_relationships',
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

const parse = (raw: unknown): RelationshipClusterData => {
  const parsed = renderForMessage(toolMessage(raw))
  if (parsed?.kind !== 'relationship_cluster') throw new Error('fixture did not parse')
  return parsed.data
}

const navigateToQueue = vi.fn()

const chat = {
  apiBase: 'http://api.test',
  navigateToQueue,
} as unknown as ChatContextValue

describe('RelationshipCluster', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  beforeEach(() => {
    navigateToQueue.mockClear()
  })

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.unstubAllGlobals()
  })

  const render = (data: RelationshipClusterData, surface: 'page' | 'compact' = 'page') => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chat}><RelationshipCluster data={data} surface={surface} /></ChatContext.Provider>,
    ))
    return container
  }

  // The control: a model can describe a Possible link in confident language,
  // and the badge beside it still says Possible.
  it('renders the badge from the payload field, not from the claim text', () => {
    const el = render(parse(payload({ claim: 'These are definitely the same programme.' })))

    expect(el.querySelector('.chatConfidenceLikely')?.textContent).toBe('Likely')
    expect(el.querySelector('.chatConfidenceConfirmed')).toBeNull()
  })

  it('badges every member with its own level', () => {
    const el = render(parse(payload()))

    const badges = Array.from(el.querySelectorAll('.chatRelationshipMembers .chatConfidence'))
    expect(badges.map((badge) => badge.textContent)).toEqual(['Likely', 'Possible'])
  })

  it('deep-links a member through the existing navigation mechanism', () => {
    const el = render(parse(payload()))

    const button = el.querySelector('.chatRelationshipMembers button') as HTMLButtonElement
    act(() => button.click())

    expect(navigateToQueue).toHaveBeenCalledTimes(1)
  })

  it('collapses the evidence panel by default and shows absent signals inside it', () => {
    const el = render(parse(payload()))

    const details = el.querySelector('.chatEvidenceDetails')
    expect(details?.hasAttribute('open')).toBe(false)
    expect(el.querySelector('.chatEvidenceAbsent')?.textContent).toContain('End client')
  })

  it('hides inferred attributes when they are blank', () => {
    expect(render(parse(payload())).querySelector('.chatRelationshipInferred')).toBeNull()
  })

  // An inferred attribute must never be mistakable for a recorded one.
  it('labels an inferred attribute as inferred when it has one', () => {
    const el = render(parse(payload({ inferred: { end_client: 'Wells Fargo', partner: '', domain: '' } })))

    const inferred = el.querySelector('.chatRelationshipInferred')
    expect(inferred?.textContent).toContain('inferred')
    expect(inferred?.textContent).toContain('Wells Fargo')
  })

  // The widget has no room for a decision, and a mis-click there is a
  // persisted judgment.
  it('renders no controls on the compact surface', () => {
    const el = render(parse(payload()), 'compact')

    expect(el.querySelector('.chatRelationshipActions')).toBeNull()
    expect(el.textContent).toContain('Likely')
  })

  it('posts a judgment and reports the outcome', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'confirmed' }) })
    vi.stubGlobal('fetch', fetchMock)
    const el = render(parse(payload()))

    const confirm = Array.from(el.querySelectorAll('.chatRelationshipActions button'))
      .find((button) => button.textContent?.includes('belong together')) as HTMLButtonElement
    await act(async () => { confirm.click() })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/relationships/clusters/cluster-1/judgment')
    expect(el.querySelector('.chatRelationshipActions')).toBeNull()
    expect(el.textContent).toContain('You confirmed this.')
  })

  it('says a rejected relationship will not be suggested again', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'rejected' }) }))
    const el = render(parse(payload()))

    const reject = Array.from(el.querySelectorAll('.chatRelationshipActions button'))
      .find((button) => button.textContent?.includes('Not related')) as HTMLButtonElement
    await act(async () => { reject.click() })

    expect(el.textContent).toContain('will not be suggested again')
  })

  it('shows an error and keeps the controls when the judgment fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => ({ detail: 'Server said no' }), text: async () => 'Server said no',
    }))
    const el = render(parse(payload()))

    const confirm = el.querySelector('.chatRelationshipActions button') as HTMLButtonElement
    await act(async () => { confirm.click() })

    expect(el.querySelector('.chatRelationshipError')).not.toBeNull()
    expect(el.querySelector('.chatRelationshipActions')).not.toBeNull()
  })
})

describe('get_relationships payload parsing', () => {
  it.each([
    ['no evidence', { provenance: { ...payload().provenance, evidence: [] } }],
    ['no confidence', { provenance: { ...payload().provenance, confidence: undefined } }],
    ['an unknown confidence level', { provenance: { ...payload().provenance, confidence: 'certain' } }],
    ['a score outside zero to one', { provenance: { ...payload().provenance, score: 4 } }],
    ['no members', { members: [] }],
    ['a member with an unreadable confidence', { members: [{ opportunity_id: 1, label: 'x', confidence: 'certain' }] }],
    ['no cluster id', { cluster_id: '' }],
    // A shadow cluster must never reach the DOM even if one arrives.
    ['a shadow status', { status: 'shadow' }],
    ['a status this build cannot read', { status: 'archived' }],
  ])('returns null for %s', (_label, overrides) => {
    expect(renderForMessage(toolMessage(payload(overrides)))).toBeNull()
  })

  it('returns null for content that is not JSON', () => {
    expect(renderForMessage(toolMessage('the cluster failed'))).toBeNull()
  })

  it('parses a well-formed payload into the ninth kind', () => {
    expect(renderForMessage(toolMessage(payload()))?.kind).toBe('relationship_cluster')
  })
})
