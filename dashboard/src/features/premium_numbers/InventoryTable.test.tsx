// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import InventoryTable from './InventoryTable'
import type { InventoryRow } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const rows: InventoryRow[] = [
  {
    key: 'review:1',
    kind: 'review',
    id: 1,
    number: '+1 (555) 019-2834',
    owner: 'TechFlow Inc.',
    company: 'TechFlow Inc.',
    categories: [],
    status: 'Pending',
    score: 98,
    sourceType: 'gmail',
    lastCheckedAt: new Date().toISOString(),
  },
  {
    key: 'contact:2',
    kind: 'contact',
    id: 2,
    number: '+44 20 7946 0958',
    owner: 'Global Talent Ltd',
    company: 'Global Talent Ltd',
    categories: ['Recruiter', 'Employer'],
    status: 'Active',
    score: 92,
    sourceType: 'nvoids',
    lastCheckedAt: new Date().toISOString(),
  },
]

describe('InventoryTable', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
  })

  it('renders the summary columns, multi-role badges, selection, and pagination copy', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    const onToggle = vi.fn()
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    act(() => {
      root.render(
        <InventoryTable
          rows={rows}
          allRowsCount={12}
          selected={new Set(['review:1'])}
          busy={false}
          page={1}
          pageSize={10}
          totalPages={2}
          highlightedKey="contact:2"
          onToggle={onToggle}
          onSelectVisible={vi.fn()}
          onPageChange={vi.fn()}
          onOpen={vi.fn()}
          onAction={vi.fn()}
        />,
      )
    })

    expect(container.textContent).toContain('Number')
    expect(container.textContent).toContain('Global Talent Ltd')
    expect(container.textContent).toContain('Recruiter')
    expect(container.textContent).toContain('Employer')
    expect(container.textContent).toContain('Showing 1 to 10 of 12 loaded entries')

    const contactCheckbox = container.querySelector<HTMLInputElement>('input[aria-label="Select +44 20 7946 0958"]')
    act(() => contactCheckbox?.click())
    expect(onToggle).toHaveBeenCalledWith('contact:2')
  })
})
