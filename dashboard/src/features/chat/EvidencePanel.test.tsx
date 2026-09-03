// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import EvidencePanel from './EvidencePanel'
import { asInferenceProvenance, type InferenceProvenanceData } from './renderers'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const entry = (overrides: Record<string, unknown> = {}) => ({
  signal: 'job_title',
  left_value: 'Senior Java Developer',
  right_value: 'Java Developer',
  normalized_to: 'java developer',
  match: 'exact',
  weight: 0.2,
  sub_score: 1,
  source: 'canonical_entity_taxonomy',
  ...overrides,
})

const parse = (overrides: Record<string, unknown> = {}): InferenceProvenanceData => {
  const parsed = asInferenceProvenance({
    metric: 'relationship',
    source: 'relationship_scoring',
    row_count: 2,
    date_range: { from: null, to: null },
    filters: {},
    assumptions: ['This relationship was inferred from the records listed, not recorded by anyone.'],
    confidence: 'likely',
    score: 0.72,
    evidence: [entry()],
    semantic_available: true,
    ...overrides,
  })
  if (!parsed) throw new Error('fixture did not parse')
  return parsed
}

describe('EvidencePanel', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (data: InferenceProvenanceData) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<EvidencePanel data={data} />))
    return container
  }

  // v2 established one caption component shared by every renderer. A second
  // provenance block beside it is the duplication its handoff forbids.
  it('renders the shared provenance caption rather than a second one', () => {
    const el = render(parse())

    expect(el.querySelectorAll('.chatProvenance').length).toBe(1)
    expect(el.textContent).toContain('This relationship was inferred')
  })

  it('is collapsed by default', () => {
    const el = render(parse())

    expect(el.querySelector('details')?.hasAttribute('open')).toBe(false)
  })

  it('shows each compared signal with its contribution', () => {
    const el = render(parse())

    expect(el.textContent).toContain('Job title')
    expect(el.textContent).toContain('exact match')
    expect(el.textContent).toContain('0.20')
  })

  // The control that stops two claims with very different evidentiary bases
  // from looking identical.
  it('groups absent signals separately and says they were not counted against the match', () => {
    const el = render(parse({
      evidence: [entry(), entry({ signal: 'end_client', match: 'absent', sub_score: 0, left_value: '', right_value: '' })],
    }))

    const absent = el.querySelector('.chatEvidenceAbsent')
    expect(absent).not.toBeNull()
    expect(absent?.textContent).toContain('not counted against the match')
    expect(absent?.textContent).toContain('End client')
    expect(el.querySelector('summary')?.textContent).toContain('1 signal compared')
  })

  it('says so when the pair was compared on keywords only', () => {
    expect(render(parse({ semantic_available: false })).textContent).toContain('keywords only')
    act(() => root?.unmount())
    container?.remove()
    expect(render(parse()).textContent).not.toContain('keywords only')
  })
})
