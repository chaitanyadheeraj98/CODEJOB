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
    review: {
      id: 1,
      source_email_id: 10,
      source_external_opportunity_id: null,
      source_lead_id: 20,
      target_contact_id: 3,
      normalized_phone_number: '15550192834',
      display_phone_number: '+1 (555) 019-2834',
      owner_name: 'TechFlow Inc.',
      company: 'TechFlow Inc.',
      designation: 'Recruiter',
      confidence: 'high',
      purpose: 'Contact',
      evidence_snippet: 'Call me',
      email_subject: 'Role',
      email_sender: 'sender@example.com',
      contact_email: 'sender@example.com',
      contact_type: 'recruiter',
      recruiter_relevance_score: 98,
      relevance_reason: 'external_domain',
      extraction_source: 'ai',
      scored_with: 'current',
      gmail_open_url: '',
      state: 'pending',
      role: 'recruiter',
      reason_code: 'identity_conflict',
      occurrence_count: 2,
      created_at: '2026-08-18T10:00:00Z',
      updated_at: '2026-08-18T12:00:00Z',
    },
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
          onToggleFavorite={vi.fn()}
        />,
      )
    })

    expect(container.textContent).toContain('Number')
    expect(container.textContent).toContain('Global Talent Ltd')
    expect(container.textContent).toContain('Recruiter')
    expect(container.textContent).toContain('Employer')
    expect(container.textContent).toContain('Identity Conflict')
    expect(container.textContent).toContain('Showing 1 to 10 of 12 loaded entries')

    const conflictMenuButtons = Array.from(container.querySelectorAll<HTMLButtonElement>('tbody tr:first-child .inventoryMenuPopover button'))
    expect(conflictMenuButtons.find((button) => button.textContent === 'Mark as Recruiter')?.disabled).toBe(true)
    expect(conflictMenuButtons.find((button) => button.textContent === 'Mark as Employer')?.disabled).toBe(true)
    const normalMenuButtons = Array.from(container.querySelectorAll<HTMLButtonElement>('tbody tr:nth-child(2) .inventoryMenuPopover button'))
    expect(normalMenuButtons.find((button) => button.textContent === 'Mark as Recruiter')?.disabled).toBe(false)

    const contactCheckbox = container.querySelector<HTMLInputElement>('input[aria-label="Select +44 20 7946 0958"]')
    act(() => contactCheckbox?.click())
    expect(onToggle).toHaveBeenCalledWith('contact:2')
  })
})
