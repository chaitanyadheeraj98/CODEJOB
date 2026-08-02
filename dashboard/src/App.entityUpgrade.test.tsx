// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EntityUpgradeSection } from './App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('EntityUpgradeSection', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
  })

  const render = (children: React.ReactNode) => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    act(() => {
      root.render(children)
    })
    return container
  }

  it('hides Approve all when nothing meets the bulk-approval threshold', () => {
    const approveAll = vi.fn()
    const container = render(
      <EntityUpgradeSection
        title="Upgrade Companies"
        pendingEntities={[
          { entity_type: 'company', display_name: 'Alltech Consulting Services', normalized_name: 'alltech consulting services', occurrence_count: 1, candidate_ids: [4501] },
          { entity_type: 'company', display_name: 'Amber IT Staffing', normalized_name: 'amber it staffing', occurrence_count: 1, candidate_ids: [4256] },
        ]}
        loading={false}
        busyKey={null}
        approveAll={approveAll}
        approve={vi.fn()}
        dismiss={vi.fn()}
      />,
    )

    const buttons = Array.from(container.querySelectorAll('button'))
    expect(buttons.find((button) => button.textContent?.startsWith('Approve all'))).toBeUndefined()
  })

  it('shows Approve all with the actionable count when some entities qualify', () => {
    const approveAll = vi.fn()
    const container = render(
      <EntityUpgradeSection
        title="Upgrade Companies"
        pendingEntities={[
          { entity_type: 'company', display_name: 'Alltech Consulting Services', normalized_name: 'alltech consulting services', occurrence_count: 1, candidate_ids: [4501] },
          { entity_type: 'company', display_name: 'JPMorgan Chase', normalized_name: 'jpmorgan chase', occurrence_count: 3, candidate_ids: [1, 2, 3] },
        ]}
        loading={false}
        busyKey={null}
        approveAll={approveAll}
        approve={vi.fn()}
        dismiss={vi.fn()}
      />,
    )

    expect(container.textContent ?? '').toContain('Approve all (1)')
    const button = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent === 'Approve all (1)')
    button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(approveAll).toHaveBeenCalledTimes(1)
  })
})
