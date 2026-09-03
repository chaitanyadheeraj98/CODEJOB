// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MetricCards from './MetricCards'
import { ChatContext, type ChatContextValue } from './chatContext'
import { renderForMessage, type MetricCardsData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const payload = {
  action: 'render_metric_cards',
  title: 'Application pipeline',
  cards: [
    { label: 'Due today', value: 4, unit: '', delta: null, drill_to: { page: 'application_tracking', tab: 'tracked', filters: {} } },
    { label: 'Interviews', value: 2, unit: '', delta: null, drill_to: null },
  ],
  provenance: {
    metric: 'Application pipeline',
    source: 'get_metrics/pipeline_summary',
    row_count: 12,
    date_range: { from: '2026-09-01', to: '2026-09-03' },
    filters: { range: 'current_month' },
    assumptions: ['Deleted applications are excluded.'],
  },
}

const data = (): MetricCardsData => {
  const parsed = renderForMessage(toolMessage(payload))
  if (parsed?.kind !== 'metric_cards') throw new Error('fixture did not parse')
  return parsed.data
}

const toolMessage = (content: unknown): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name: 'get_metrics',
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

describe('MetricCards', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (chat: Partial<ChatContextValue>, surface: 'page' | 'compact' = 'page') => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chat as ChatContextValue}>
        <MetricCards data={data()} surface={surface} />
      </ChatContext.Provider>,
    ))
    return container
  }

  it('renders the cards and the provenance caption', () => {
    const el = render({ navigateToQueue: vi.fn() })

    expect(el.textContent).toContain('Due today')
    expect(el.textContent).toContain('4')
    expect(el.textContent).toContain('2026-09-01 to 2026-09-03')
    expect(el.textContent).toContain('12 rows read')
    expect(el.textContent).toContain('Deleted applications are excluded.')
  })

  it('drills through from a card that carries a target', () => {
    const navigateToQueue = vi.fn()
    const el = render({ navigateToQueue })

    const buttons = el.querySelectorAll('button')
    expect(buttons.length).toBe(1)
    act(() => { buttons[0].click() })

    expect(navigateToQueue).toHaveBeenCalledWith({ page: 'application_tracking', tab: 'tracked', filters: {} })
  })

  it('renders no drill-through buttons on the compact surface', () => {
    const el = render({ navigateToQueue: vi.fn() }, 'compact')

    expect(el.querySelectorAll('button').length).toBe(0)
    expect(el.textContent).toContain('Due today')
  })
})

describe('get_metrics payload parsing', () => {
  // The headline test of this work item. A chart with a caveat is still a
  // chart, so a payload without provenance has to render nothing at all.
  it('renders nothing when the provenance block is missing', () => {
    const withoutProvenance: Record<string, unknown> = { ...payload }
    delete withoutProvenance.provenance

    expect(renderForMessage(toolMessage(withoutProvenance))).toBeNull()
  })

  it.each([
    ['an empty metric name', { ...payload.provenance, metric: '' }],
    ['a non-numeric row_count', { ...payload.provenance, row_count: 'lots' }],
    ['a null block', null],
    ['an array', []],
  ])('renders nothing for provenance with %s', (_label, provenance) => {
    expect(renderForMessage(toolMessage({ ...payload, provenance }))).toBeNull()
  })

  it.each([
    ['not JSON at all', 'no metrics for you'],
    ['a different action', { ...payload, action: 'navigate_to_queue' }],
    ['cards that are not an array', { ...payload, cards: 'four' }],
    ['a card with no label', { ...payload, cards: [{ value: 4 }] }],
    ['a card whose value is an object', { ...payload, cards: [{ label: 'Due', value: {} }] }],
  ])('renders nothing for %s', (_label, content) => {
    expect(renderForMessage(toolMessage(content))).toBeNull()
  })

  it('tolerates a missing assumptions list and an open date range', () => {
    const parsed = renderForMessage(toolMessage({
      ...payload,
      provenance: { metric: 'Stale', source: 's', row_count: 0, date_range: {}, filters: {}, assumptions: undefined },
    }))

    if (parsed?.kind !== 'metric_cards') throw new Error('expected metric cards')
    expect(parsed.data.provenance.assumptions).toEqual([])
    expect(parsed.data.provenance.date_range).toEqual({ from: null, to: null })
  })

  it('drops a drill target with no page rather than passing it through', () => {
    const parsed = renderForMessage(toolMessage({
      ...payload,
      cards: [{ label: 'Due today', value: 4, unit: '', delta: null, drill_to: { filters: {} } }],
    }))

    if (parsed?.kind !== 'metric_cards') throw new Error('expected metric cards')
    expect(parsed.data.cards[0].drill_to).toBeNull()
  })
})
