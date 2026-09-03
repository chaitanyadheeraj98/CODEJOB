// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useInventory } from './useInventory'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type HookApi = ReturnType<typeof useInventory>

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

async function waitForCalls(fetchMock: ReturnType<typeof vi.fn>, expected: number): Promise<void> {
  for (let i = 0; i < 50; i += 1) {
    if (fetchMock.mock.calls.length >= expected) return
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 20)) })
  }
  throw new Error(`Timed out waiting for ${expected} calls, got ${fetchMock.mock.calls.length}`)
}

function setupHarness() {
  let latest: HookApi | null = null
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root: Root = createRoot(container)

  function Harness() {
    latest = useInventory('http://localhost:8000', 0)
    return null
  }

  act(() => {
    root.render(<Harness />)
  })

  const getHook = () => {
    if (!latest) throw new Error('Hook not ready')
    return latest
  }

  const cleanup = () => {
    act(() => root.unmount())
    container.remove()
  }

  return { getHook, cleanup }
}

describe('useInventory', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    while (cleanups.length) cleanups.pop()?.()
  })

  it('fetches the unified /premium-numbers/inventory endpoint exactly once per filter or sort change', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => jsonResponse({ items: [], total: 0, next_cursor: null, has_next: false }))
    vi.stubGlobal('fetch', fetchMock)

    const { getHook, cleanup } = setupHarness()
    cleanups.push(cleanup)

    await waitForCalls(fetchMock, 1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/premium-numbers/inventory?')

    await act(async () => { getHook().updateFilter('q', 'acme') })
    await waitForCalls(fetchMock, 2)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(String(fetchMock.mock.calls[1][0])).toContain('q=acme')

    await act(async () => { getHook().setSort('oldest') })
    await waitForCalls(fetchMock, 3)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(String(fetchMock.mock.calls[2][0])).toContain('sort=oldest')
  })

  it('resets to page 1 when a filter or sort changes', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ items: [], total: 100, next_cursor: null, has_next: false }))
    vi.stubGlobal('fetch', fetchMock)

    const { getHook, cleanup } = setupHarness()
    cleanups.push(cleanup)
    await waitForCalls(fetchMock, 1)

    await act(async () => { getHook().setPage(4) })
    await waitForCalls(fetchMock, 2)
    expect(getHook().page).toBe(4)

    await act(async () => { getHook().updateFilter('status', ['flagged']) })
    await waitForCalls(fetchMock, 3)
    expect(getHook().page).toBe(1)
  })

  it('maps the response fields straight onto rows/total with no client-side reshaping', async () => {
    const item = {
      key: 'contact:7', kind: 'contact', id: 7, number: '+1 (214) 555-0101', owner: 'Rita',
      company: 'Acme', categories: ['Recruiter'], status: 'Active', score: 92, sourceType: 'gmail',
      lastCheckedAt: '2026-08-18T12:00:00Z', review: null, recruiter: { id: 7 }, employer: null,
    }
    const fetchMock = vi.fn(async () => jsonResponse({ items: [item], total: 1, next_cursor: null, has_next: false }))
    vi.stubGlobal('fetch', fetchMock)

    const { getHook, cleanup } = setupHarness()
    cleanups.push(cleanup)
    await waitForCalls(fetchMock, 1)
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 20)) })

    expect(getHook().rows).toEqual([item])
    expect(getHook().total).toBe(1)
  })
})
