// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import FilterVisibilitySettings from './FilterVisibilitySettings'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('FilterVisibilitySettings', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  function renderSettings(visibleFilters: Record<string, string[]>, onChange = vi.fn()) {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <FilterVisibilitySettings
        visibleFilters={visibleFilters}
        onChange={onChange}
        resumeAssets={[]}
        savingLabel=""
      />,
    ))
    const group = Array.from(container.querySelectorAll('details')).find((details) => details.textContent?.includes('Needs Review'))!
    return { group, onChange }
  }

  it('removes an unchecked field from that dashboard preference', () => {
    const { group, onChange } = renderSettings({})
    const roleLabel = Array.from(group.querySelectorAll('label')).find((label) => label.textContent?.includes('Job title'))!
    act(() => roleLabel.querySelector('input')?.click())
    const next = onChange.mock.calls[0][0] as Record<string, string[]>
    expect(next.needs_review).not.toContain('role')
    expect(next.needs_review).toContain('date')
  })

  it('hide all stores an empty preference while date stays checked and counted', () => {
    const { group, onChange } = renderSettings({ needs_review: [] })
    const dateLabel = Array.from(group.querySelectorAll('label')).find((label) => label.textContent?.includes('Date'))!
    const dateCheckbox = dateLabel.querySelector<HTMLInputElement>('input')!
    expect(dateCheckbox.checked).toBe(true)
    expect(dateCheckbox.disabled).toBe(true)
    expect(dateLabel.textContent).toContain('Always shown')
    // The visible count is terse so eleven rows stay scannable; the full phrasing
    // is the accessible name, which is what assistive tech announces.
    const count = group.querySelector('.filterVisibilityCount')!
    expect(count.textContent).toBe('1/14')
    expect(count.getAttribute('aria-label')).toBe('1 of 14 filters shown')
    expect(count.classList.contains('is-reduced')).toBe(true)

    const hideAll = Array.from(group.querySelectorAll('button')).find((button) => button.textContent === 'Hide all')!
    act(() => hideAll.click())
    expect(onChange).toHaveBeenCalledWith({ needs_review: [] })
  })
})
