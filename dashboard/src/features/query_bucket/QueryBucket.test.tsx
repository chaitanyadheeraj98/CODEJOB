// @vitest-environment jsdom
import React from 'react'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import QueryBucket from './QueryBucket'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('QueryBucket', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
  })

  function mount(
    queryValue = 'is:unread',
    savedQueries = ['is:unread', 'remote is:unread', 'tx is:unread'],
  ): { container: HTMLDivElement; onQueryChange: ReturnType<typeof vi.fn>; onQuerySelect: ReturnType<typeof vi.fn>; onSavedQueriesChange: ReturnType<typeof vi.fn> } {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onQueryChange = vi.fn()
    const onQuerySelect = vi.fn()
    const onSavedQueriesChange = vi.fn(async () => {})
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    act(() => {
      root.render(
        <QueryBucket
          queryValue={queryValue}
          savedQueries={savedQueries}
          onQueryChange={onQueryChange}
          onQuerySelect={onQuerySelect}
          onSavedQueriesChange={onSavedQueriesChange}
        />,
      )
    })
    return { container, onQueryChange, onQuerySelect, onSavedQueriesChange }
  }

  it('shows filtered suggestions on focus', () => {
    const { container } = mount('remote')
    const input = container.querySelector('input')!
    act(() => input.focus())
    const list = container.querySelector('.querySuggestionList')
    expect(list?.textContent ?? '').toContain('remote is:unread')
  })

  it('selects highlighted suggestion with Enter', () => {
    const { container, onQuerySelect } = mount('remote')
    const input = container.querySelector('input')!
    act(() => input.dispatchEvent(new FocusEvent('focus', { bubbles: true })))
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })))
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    expect(onQuerySelect).toHaveBeenCalled()
  })

  it('closes dropdown on Escape', () => {
    const { container } = mount('remote')
    const input = container.querySelector('input')!
    act(() => input.dispatchEvent(new FocusEvent('focus', { bubbles: true })))
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(container.querySelector('.querySuggestionList')).toBeNull()
  })
})
