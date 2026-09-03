// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Sidebar from './Sidebar'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('Sidebar', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    while (cleanups.length) {
      cleanups.pop()?.()
    }
  })

  it('renders Premium Numbers nav item', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => {
        root.unmount()
      })
      container.remove()
    })
    act(() => {
      root.render(
        <Sidebar
          running={false}
          queueCount={1}
          failedCount={2}
          runCount={3}
          sentCount={4}
          inboxCount={6}
          premiumCount={5}
          resumeTrackingEnabled
          activePage="run_queue"
          onNavigate={vi.fn()}
        />,
      )
    })
    expect(container.textContent ?? '').toContain('Premium Contacts')
    expect(container.textContent ?? '').toContain('Inbox6')
  })

  it('navigates to Settings from the footer', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onNavigate = vi.fn()
    cleanups.push(() => {
      act(() => {
        root.unmount()
      })
      container.remove()
    })
    act(() => {
      root.render(
        <Sidebar
          running={false}
          queueCount={0}
          failedCount={0}
          runCount={0}
          sentCount={0}
          premiumCount={0}
          resumeTrackingEnabled={false}
          activePage="run_queue"
          onNavigate={onNavigate}
        />,
      )
    })

    const settingsButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === 'Settings',
    )
    act(() => settingsButton?.click())
    expect(onNavigate).toHaveBeenCalledWith('settings')
  })
})
