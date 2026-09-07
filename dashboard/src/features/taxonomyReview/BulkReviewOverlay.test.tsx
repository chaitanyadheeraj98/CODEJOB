// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BulkReviewOverlay } from './BulkReviewOverlay'
import type { BulkReviewRecommendation } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const RECOMMENDATIONS: BulkReviewRecommendation[] = [
  {
    key: 'temporal workflow',
    display_name: 'Temporal Workflow',
    occurrence_count: 6,
    candidate_ids: [44, 12],
    bucket: 'approve',
    reason: 'safe_repeated',
    source: 'rules',
    locked: false,
  },
  {
    key: 'and innovation initiatives',
    display_name: 'and innovation initiatives',
    occurrence_count: 8,
    candidate_ids: [8700],
    bucket: 'dismiss',
    reason: 'malformed_or_recoverable',
    source: 'rules',
    locked: true,
  },
  {
    key: 'agent studio',
    display_name: 'Agent Studio',
    occurrence_count: 1,
    candidate_ids: [91],
    bucket: 'review',
    reason: 'safe_singleton',
    source: 'model',
    locked: false,
  },
]

function classifyResponse(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    json: async () => ({
      scope: 'skill',
      total_pending: RECOMMENDATIONS.length,
      counts: { approve: 1, dismiss: 1, review: 1 },
      model_used: 'deepseek-v4-flash',
      model_error: null,
      recommendations: RECOMMENDATIONS,
      ...overrides,
    }),
  }
}

describe('BulkReviewOverlay', () => {
  const cleanups: Array<() => void> = []
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async (url: string) => {
      if (String(url).endsWith('/classify')) return classifyResponse()
      return {
        ok: true,
        json: async () => ({
          scope: 'skill',
          action: 'approve',
          applied_count: 1,
          applied_names: ['Temporal Workflow'],
          skipped: [],
        }),
      }
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.unstubAllGlobals()
  })

  const render = async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    await act(async () => {
      root.render(
        <BulkReviewOverlay
          scope="skill"
          title="Upgrade Skills"
          apiBase="http://api.test"
          onClose={() => {}}
          onApplied={() => {}}
        />,
      )
    })
    return container
  }

  const buttons = (container: HTMLElement) => Array.from(container.querySelectorAll('button'))

  const clickButton = async (container: HTMLElement, label: string, index = 0) => {
    const matches = buttons(container).filter((button) => (button.textContent ?? '').trim() === label)
    expect(matches.length).toBeGreaterThan(index)
    await act(async () => {
      matches[index].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
  }

  const applyCalls = () =>
    fetchMock.mock.calls.filter((call) => String(call[0]).endsWith('/apply'))

  it('renders the three groups with their counts', async () => {
    const container = await render()
    const text = container.textContent ?? ''
    expect(text).toContain('Approve (1)')
    expect(text).toContain('Dismiss (1)')
    expect(text).toContain('Needs Review (1)')
    expect(text).toContain('Temporal Workflow')
    expect(text).toContain('refined by deepseek-v4-flash')
  })

  it('starts Needs Review rows unselected and counts them as left behind', async () => {
    const container = await render()
    expect(container.textContent ?? '').toContain('Approve 1 · Dismiss 1 · Leave 1')
  })

  it('moving a row updates the footer counts', async () => {
    const container = await render()
    // The Approve control on the third row - the one sitting in Needs Review.
    await clickButton(container, 'Approve', 2)
    expect(container.textContent ?? '').toContain('Approve 2 · Dismiss 1 · Leave 0')
  })

  it('does not let a locked row be approved', async () => {
    const container = await render()
    const dismissGroupApprove = buttons(container).filter(
      (button) => (button.textContent ?? '').trim() === 'Approve',
    )[1]
    expect(dismissGroupApprove.hasAttribute('disabled')).toBe(true)
    expect(container.textContent ?? '').toContain('Approve is disabled')
  })

  it('previewing writes nothing; only Apply calls the endpoint', async () => {
    const container = await render()
    await clickButton(container, 'Preview & confirm')
    expect(applyCalls()).toHaveLength(0)
    const text = container.textContent ?? ''
    expect(text).toContain('Nothing has been changed yet')
    expect(text).toContain('Approve 1')
    expect(text).toContain('Dismiss 1')

    await clickButton(container, 'Apply — approve 1, dismiss 1')
    expect(applyCalls()).toHaveLength(2)
  })

  it('sends the exact keys and a matching expected_count', async () => {
    const container = await render()
    await clickButton(container, 'Preview & confirm')
    await clickButton(container, 'Apply — approve 1, dismiss 1')

    const bodies = applyCalls().map((call) => JSON.parse(String((call[1] as RequestInit).body)))
    expect(bodies[0]).toEqual({
      scope: 'skill',
      action: 'approve',
      keys: ['temporal workflow'],
      expected_count: 1,
    })
    expect(bodies[1]).toEqual({
      scope: 'skill',
      action: 'dismiss',
      keys: ['and innovation initiatives'],
      expected_count: 1,
    })
  })

  it('reflects a moved row in the applied keys', async () => {
    const container = await render()
    await clickButton(container, 'Approve', 2)
    await clickButton(container, 'Preview & confirm')
    await clickButton(container, 'Apply — approve 2, dismiss 1')

    const body = JSON.parse(String((applyCalls()[0][1] as RequestInit).body))
    expect(body.keys).toEqual(['temporal workflow', 'agent studio'])
    expect(body.expected_count).toBe(2)
  })

  it('surfaces a model failure but still shows the rules buckets', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/classify')) {
        return classifyResponse({ model_used: null, model_error: 'RuntimeError: provider down' })
      }
      return { ok: true, json: async () => ({}) }
    })
    const container = await render()
    const text = container.textContent ?? ''
    expect(text).toContain('Model refinement unavailable')
    expect(text).toContain('provider down')
    expect(text).toContain('Approve (1)')
  })

  it('does not claim nothing was written when the first request already landed', async () => {
    let applyCallCount = 0
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/classify')) return classifyResponse()
      applyCallCount += 1
      if (applyCallCount === 1) {
        return {
          ok: true,
          json: async () => ({
            scope: 'skill',
            action: 'approve',
            applied_count: 1,
            applied_names: ['Temporal Workflow'],
            skipped: [],
          }),
        }
      }
      return { ok: false, json: async () => ({ detail: 'A bulk review apply is already running' }) }
    })
    const container = await render()
    await clickButton(container, 'Preview & confirm')
    await clickButton(container, 'Apply — approve 1, dismiss 1')
    const text = container.textContent ?? ''
    expect(text).toContain('A bulk review apply is already running')
    expect(text).toContain('Applied 1 record')
    expect(text).not.toContain('Nothing has been changed yet')
  })

  it('reports a rejected apply without claiming anything was written', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/classify')) return classifyResponse()
      return { ok: false, json: async () => ({ detail: 'Confirmed 1 records but received 2 unique keys' }) }
    })
    const container = await render()
    await clickButton(container, 'Preview & confirm')
    await clickButton(container, 'Apply — approve 1, dismiss 1')
    const text = container.textContent ?? ''
    expect(text).toContain('Confirmed 1 records but received 2 unique keys')
    expect(text).toContain('Nothing has been changed yet')
  })
})
