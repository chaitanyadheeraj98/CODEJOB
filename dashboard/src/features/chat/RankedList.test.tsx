// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ComparisonTable from './ComparisonTable'
import RankedList from './RankedList'
import { ChatContext, type ChatContextValue } from './chatContext'
import { renderForMessage, type ComparisonData, type RankedListData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const provenance = {
  metric: 'Opportunity match score',
  source: 'rank_opportunities',
  row_count: 2,
  date_range: { from: null, to: null },
  filters: { resume_asset_id: '1' },
  assumptions: ['Opportunities with status Closed or Not Interested are excluded.'],
}

const rankedPayload = {
  action: 'render_ranked_list',
  title: 'Best-matching opportunities',
  measure: 'Match score',
  rows: [
    {
      rank: 1, record_id: 11, label: 'Java Developer', detail: 'BigCo · Dallas, TX',
      score: 82.5, reasons: ['Skill overlap: Java, Spring'],
      drill_to: { page: 'premium_numbers', tab: 'opportunities', filters: { q: 'Java Developer' } },
    },
    { rank: 2, record_id: 12, label: 'Python Developer', detail: '', score: 61, reasons: [], drill_to: null },
  ],
  dropped: [],
  provenance,
}

const comparisonPayload = {
  action: 'render_comparison',
  title: 'Recruiter activity',
  measures: [
    { key: 'outreach_count', label: 'Outreach' },
    { key: 'median_first_reply_business_days', label: 'Median first reply' },
  ],
  columns: [
    { record_id: 1, label: 'Sarah', values: { outreach_count: 4, median_first_reply_business_days: 1.5 } },
    { record_id: 2, label: 'Priya', values: { outreach_count: 0, median_first_reply_business_days: null } },
  ],
  dropped: [{ key: '9', reason: 'not_found' }],
  provenance: { ...provenance, metric: 'Recruiter activity', source: 'compare_records' },
}

const toolMessage = (content: unknown, tool_name: string): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name,
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

const rankedData = (): RankedListData => {
  const parsed = renderForMessage(toolMessage(rankedPayload, 'rank_opportunities'))
  if (parsed?.kind !== 'ranked_list') throw new Error('fixture did not parse')
  return parsed.data
}

const comparisonData = (): ComparisonData => {
  const parsed = renderForMessage(toolMessage(comparisonPayload, 'compare_records'))
  if (parsed?.kind !== 'comparison') throw new Error('fixture did not parse')
  return parsed.data
}

describe('RankedList and ComparisonTable', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (node: React.ReactNode, chat: Partial<ChatContextValue> = {}) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chat as ChatContextValue}>{node}</ChatContext.Provider>,
    ))
    return container
  }

  it('renders ranks, scores, reasons and provenance', () => {
    const el = render(<RankedList data={rankedData()} surface="page" />, { navigateToQueue: vi.fn() })

    expect(el.textContent).toContain('Java Developer')
    expect(el.textContent).toContain('82.5')
    expect(el.textContent).toContain('Skill overlap: Java, Spring')
    expect(el.textContent).toContain('Opportunities with status Closed')
  })

  it('drills through from a ranked row', () => {
    const navigateToQueue = vi.fn()
    const el = render(<RankedList data={rankedData()} surface="page" />, { navigateToQueue })

    act(() => { el.querySelector('button')?.click() })

    expect(navigateToQueue).toHaveBeenCalledWith({
      page: 'premium_numbers', tab: 'opportunities', filters: { q: 'Java Developer' },
    })
  })

  it('hides reasons and row buttons on the compact surface', () => {
    const el = render(<RankedList data={rankedData()} surface="compact" />, { navigateToQueue: vi.fn() })

    expect(el.querySelectorAll('button').length).toBe(0)
    expect(el.textContent).not.toContain('Skill overlap')
  })

  // "Never replied" and "no record of this recruiter" are different claims.
  it('renders a missing measure as an em dash, never as zero', () => {
    const el = render(<ComparisonTable data={comparisonData()} surface="page" />)

    const rows = el.querySelectorAll('tbody tr')
    expect(rows[1].textContent).toContain('1.5')
    expect(rows[1].textContent).toContain('—')
    // The genuine zero is still shown as 0, not conflated with unknown.
    expect(rows[0].textContent).toContain('0')
  })

  it('reports records it could not compare', () => {
    const el = render(<ComparisonTable data={comparisonData()} surface="page" />)

    expect(el.textContent).toContain('Not compared: 9')
  })
})

describe('analysis payload parsing', () => {
  it.each([
    ['ranked list', 'rank_opportunities', rankedPayload],
    ['comparison', 'compare_records', comparisonPayload],
  ])('renders nothing for a %s with no provenance', (_label, tool, payload) => {
    const withoutProvenance: Record<string, unknown> = { ...payload }
    delete withoutProvenance.provenance

    expect(renderForMessage(toolMessage(withoutProvenance, tool))).toBeNull()
  })

  it.each([
    ['rows that are not an array', 'rank_opportunities', { ...rankedPayload, rows: 'three' }],
    ['a row with no score', 'rank_opportunities', { ...rankedPayload, rows: [{ record_id: 1, label: 'x' }] }],
    ['columns that are not an array', 'compare_records', { ...comparisonPayload, columns: {} }],
    ['no measures', 'compare_records', { ...comparisonPayload, measures: [] }],
    ['not JSON at all', 'rank_opportunities', 'could not rank'],
  ])('returns null for %s', (_label, tool, content) => {
    expect(renderForMessage(toolMessage(content, tool))).toBeNull()
  })

  it('coerces an unexpected measure value to null rather than displaying it', () => {
    const parsed = renderForMessage(toolMessage({
      ...comparisonPayload,
      columns: [{ record_id: 1, label: 'Sarah', values: { outreach_count: { sneaky: true } } }],
    }, 'compare_records'))

    if (parsed?.kind !== 'comparison') throw new Error('expected a comparison')
    expect(parsed.data.columns[0].values.outreach_count).toBeNull()
  })
})
