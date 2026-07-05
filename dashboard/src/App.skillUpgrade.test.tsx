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

  it('renders pending skills only and forwards approve/dismiss actions', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    const approveSkill = vi.fn()
    const approveAllSkills = vi.fn()
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
          loading={false}
          busySkillKey={null}
          approveAllSkills={approveAllSkills}
          approveSkill={approveSkill}
          dismissSkill={dismissSkill}
        />,
      )
    })

    expect(container.textContent ?? '').toContain('Upgrade Skills')
    expect(container.textContent ?? '').toContain('Pending Unknown Skills')
    expect(container.textContent ?? '').toContain('Approve all')
    expect(container.textContent ?? '').toContain('Temporal Workflow')
    expect(container.textContent ?? '').toContain('Candidate IDs: 44, 12')
    expect(container.textContent ?? '').toContain('Approve adds them to your custom taxonomy')
    expect(container.textContent ?? '').not.toContain('Approved Custom Skills')

    const buttons = Array.from(container.querySelectorAll('button'))
    const approveAllButton = buttons.find((button) => button.textContent === 'Approve all') as HTMLButtonElement | undefined
    const approveButton = buttons.find((button) => button.textContent === 'Approve') as HTMLButtonElement | undefined
    const dismissButton = buttons.find((button) => button.textContent === 'Dismiss') as HTMLButtonElement | undefined

    act(() => {
      approveAllButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      approveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      dismissButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(approveAllSkills).toHaveBeenCalledTimes(1)
    expect(approveSkill).toHaveBeenCalledWith(pendingSkill)
    expect(dismissSkill).toHaveBeenCalledWith(pendingSkill)
  })

  it('disables approve all while bulk action is running', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    act(() => {
      root.render(
        <SkillUpgradeSection
          pendingSkills={[{ skill_name: 'Temporal Workflow', normalized_name: 'temporal workflow', occurrence_count: 2, candidate_ids: [44] }]}
          loading={false}
          busySkillKey="approve-all-skills"
          approveAllSkills={() => {}}
          approveSkill={() => {}}
          dismissSkill={() => {}}
        />,
      )
    })

    const approveAllButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Approving all...') as HTMLButtonElement | undefined
    expect(approveAllButton).toBeDefined()
    expect(approveAllButton?.disabled).toBe(true)
  })
})
