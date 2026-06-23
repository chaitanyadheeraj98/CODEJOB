// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SkillUpgradeSection } from './App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('SkillUpgradeSection', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
  })

  it('renders pending and approved skills and forwards approve/dismiss actions', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    const approveSkill = vi.fn()
    const dismissSkill = vi.fn()
    const pendingSkill = {
      skill_name: 'Temporal Workflow',
      normalized_name: 'temporal workflow',
      occurrence_count: 2,
      candidate_ids: [44, 12],
    }

    act(() => {
      root.render(
        <SkillUpgradeSection
          pendingSkills={[pendingSkill]}
          approvedSkills={[
            {
              id: 1,
              owner_id: 'default-owner',
              canonical_name: 'Agent Studio',
              aliases: ['Agentic Studio'],
              category: 'custom',
              cluster_hint: 'custom_ai',
              status: 'approved',
              created_at: '2026-06-22T00:00:00Z',
              updated_at: '2026-06-22T00:00:00Z',
            },
          ]}
          loading={false}
          busySkillKey={null}
          approveSkill={approveSkill}
          dismissSkill={dismissSkill}
        />,
      )
    })

    expect(container.textContent ?? '').toContain('Upgrade Skills')
    expect(container.textContent ?? '').toContain('Pending Unknown Skills')
    expect(container.textContent ?? '').toContain('Temporal Workflow')
    expect(container.textContent ?? '').toContain('Candidate IDs: 44, 12')
    expect(container.textContent ?? '').toContain('Approved Custom Skills')
    expect(container.textContent ?? '').toContain('Agent Studio')
    expect(container.textContent ?? '').toContain('Aliases: Agentic Studio')

    const buttons = Array.from(container.querySelectorAll('button'))
    const approveButton = buttons.find((button) => button.textContent === 'Approve') as HTMLButtonElement | undefined
    const dismissButton = buttons.find((button) => button.textContent === 'Dismiss') as HTMLButtonElement | undefined

    act(() => {
      approveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      dismissButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(approveSkill).toHaveBeenCalledWith(pendingSkill)
    expect(dismissSkill).toHaveBeenCalledWith(pendingSkill)
  })
})
