// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ToolProgress } from './ToolProgress'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('ToolProgress', () => {
  const cleanups: Array<() => void> = []

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-07T00:00:00Z'))
  })

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
    vi.useRealTimers()
  })

  const render = (name: string, startedAt = Date.now()) => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    act(() => {
      root.render(<ToolProgress tool={{ name, startedAt }} />)
    })
    return container
  }

  const fillWidth = (container: HTMLElement) =>
    parseFloat((container.querySelector('.toolProgressFill') as HTMLElement).style.width)

  it('names what the tool is actually doing', () => {
    const container = render('propose_taxonomy_bulk_review')
    expect(container.textContent ?? '').toContain('Reviewing pending taxonomy values with DeepSeek')
  })

  it('falls back to the tool name when there is no label', () => {
    const container = render('some_future_tool')
    expect(container.textContent ?? '').toContain('some_future_tool')
  })

  it('counts elapsed seconds so a slow call still looks alive', () => {
    const container = render('propose_taxonomy_bulk_review')
    expect(container.textContent ?? '').toContain('0s')
    act(() => {
      vi.advanceTimersByTime(5_000)
    })
    expect(container.textContent ?? '').toContain('5s')
  })

  it('advances the bar as time passes', () => {
    const container = render('propose_taxonomy_bulk_review')
    const before = fillWidth(container)
    act(() => {
      vi.advanceTimersByTime(10_000)
    })
    expect(fillWidth(container)).toBeGreaterThan(before)
  })

  it('never reaches 100% however long it runs', () => {
    const container = render('propose_taxonomy_bulk_review')
    act(() => {
      vi.advanceTimersByTime(10 * 60 * 1000)
    })
    // A bar that fills and then sits there claims a completion that has not
    // happened; it must stay short of the end until the turn actually finishes.
    expect(fillWidth(container)).toBeLessThanOrEqual(90)
  })

  it('reassures after 20 seconds without claiming anything was written', () => {
    const container = render('propose_taxonomy_bulk_review')
    expect(container.textContent ?? '').not.toContain('Still running')
    act(() => {
      vi.advanceTimersByTime(21_000)
    })
    const text = container.textContent ?? ''
    expect(text).toContain('Still running')
    expect(text).toContain('nothing has been changed yet')
  })

  it('exposes progress to assistive tech', () => {
    const container = render('propose_taxonomy_bulk_review')
    const bar = container.querySelector('[role="progressbar"]') as HTMLElement
    expect(bar).not.toBeNull()
    expect(bar.getAttribute('aria-valuetext')).toContain('seconds elapsed')
    expect(container.querySelector('[role="status"]')).not.toBeNull()
  })
})
