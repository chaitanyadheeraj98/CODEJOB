// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Chart from './Chart'
import { ChatContext, type ChatContextValue } from './chatContext'
import { renderForMessage, type ChartData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const provenance = {
  metric: 'Approved sends',
  source: 'get_chart/activity_trend',
  row_count: 3,
  date_range: { from: '2026-09-01', to: '2026-09-03' },
  filters: { range: 'current_month', bucket: 'day' },
  assumptions: ['Empty buckets are shown as zero rather than omitted.'],
}

const payload = {
  action: 'render_chart',
  chart_type: 'activity_trend',
  title: 'Approved sends',
  max_value: 8,
  series: [
    { label: '2026-09-01', value: 8, rate_of_previous: null, drill_to: { page: 'sent_items', tab: null, filters: {} } },
    { label: '2026-09-02', value: 4, rate_of_previous: null, drill_to: null },
    { label: '2026-09-03', value: 0, rate_of_previous: null, drill_to: null },
  ],
  provenance,
}

const funnelPayload = {
  ...payload,
  chart_type: 'resume_funnel',
  title: 'Resume funnel',
  max_value: 10,
  series: [
    { label: 'Submitted', value: 10, rate_of_previous: null, drill_to: null },
    { label: 'Viewed', value: 5, rate_of_previous: 50, drill_to: null },
  ],
}

const toolMessage = (content: unknown): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name: 'get_chart',
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

const parse = (raw: unknown): ChartData => {
  const parsed = renderForMessage(toolMessage(raw))
  if (parsed?.kind !== 'chart') throw new Error('fixture did not parse')
  return parsed.data
}

describe('Chart', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (data: ChartData, surface: 'page' | 'compact' = 'page', chat: Partial<ChatContextValue> = {}) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={{ navigateToQueue: vi.fn(), ...chat } as ChatContextValue}>
        <Chart data={data} surface={surface} />
      </ChatContext.Provider>,
    ))
    return container
  }

  it('draws one bar per point, sized against max_value', () => {
    const el = render(parse(payload))

    const fills = Array.from(el.querySelectorAll('.chatChartFill')) as HTMLElement[]
    expect(fills.map((fill) => fill.style.width)).toEqual(['100%', '50%', '0%'])
  })

  it('shows a text value beside every bar', () => {
    const el = render(parse(payload))

    // The bars are decorative divs; the numbers have to be readable text.
    expect(Array.from(el.querySelectorAll('.chatChartValue')).map((n) => n.textContent)).toEqual(['8', '4', '0'])
    expect(el.querySelector('[role="group"]')?.getAttribute('aria-label')).toContain('Approved sends')
  })

  it('does not divide by zero when max_value is 0', () => {
    const el = render(parse({ ...payload, max_value: 0, series: payload.series.map((p) => ({ ...p, value: 0 })) }))

    const fills = Array.from(el.querySelectorAll('.chatChartFill')) as HTMLElement[]
    expect(fills.every((fill) => fill.style.width === '0%')).toBe(true)
  })

  it('renders an explicit empty state for an empty series', () => {
    const el = render(parse({ ...payload, series: [] }))

    expect(el.textContent).toContain('no data in this range')
    expect(el.querySelectorAll('.chatChartFill').length).toBe(0)
    // The provenance caption still names the range.
    expect(el.textContent).toContain('2026-09-01 to 2026-09-03')
  })

  it('renders values as a labelled list with no bars on the compact surface', () => {
    const el = render(parse(payload), 'compact')

    expect(el.querySelectorAll('.chatChartFill').length).toBe(0)
    expect(el.textContent).toContain('2026-09-01')
    expect(el.textContent).toContain('8')
  })

  it('shows the drop-off percentage on a funnel', () => {
    const el = render(parse(funnelPayload))

    expect(el.textContent).toContain('(50%)')
  })

  it('drills through from a bar that carries a target', () => {
    const navigateToQueue = vi.fn()
    const el = render(parse(payload), 'page', { navigateToQueue })

    const buttons = el.querySelectorAll('button')
    expect(buttons.length).toBe(1)
    act(() => { buttons[0].click() })

    expect(navigateToQueue).toHaveBeenCalledWith({ page: 'sent_items', tab: null, filters: {} })
  })
})

describe('get_chart payload parsing', () => {
  it('renders nothing without provenance', () => {
    const withoutProvenance: Record<string, unknown> = { ...payload }
    delete withoutProvenance.provenance

    expect(renderForMessage(toolMessage(withoutProvenance))).toBeNull()
  })

  it.each([
    ['a missing chart_type', { ...payload, chart_type: '' }],
    ['a series that is not an array', { ...payload, series: 'lots' }],
    ['a point with no numeric value', { ...payload, series: [{ label: 'a', value: 'many' }] }],
    ['not JSON at all', 'could not chart'],
    ['a different action', { ...payload, action: 'render_metric_cards' }],
  ])('returns null for %s', (_label, content) => {
    expect(renderForMessage(toolMessage(content))).toBeNull()
  })

  it('falls back to the series maximum when max_value is absent', () => {
    const withoutMax: Record<string, unknown> = { ...payload }
    delete withoutMax.max_value

    expect(parse(withoutMax).max_value).toBe(8)
  })
})
