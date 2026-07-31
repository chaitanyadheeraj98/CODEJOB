// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { JobIntentLearningSection, SkillUpgradeSection } from './App'

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

  it('disables approve for suspicious skill blobs while keeping dismiss available', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    const approveSkill = vi.fn()
    const dismissSkill = vi.fn()

    act(() => {
      root.render(
        <SkillUpgradeSection
          pendingSkills={[
            {
              skill_name:
                'javascript typescript java sql react react js angular angularjs next js jquery redux bootstrap material ui sass html node js express express js spring spring boot postgresql mysql mongodb redis',
              normalized_name: 'javascript typescript java sql react react js angular angularjs next js jquery redux bootstrap material ui sass html node js express express js spring spring boot postgresql mysql mongodb redis',
              occurrence_count: 2,
              candidate_ids: [4045, 4044],
            },
          ]}
          loading={false}
          busySkillKey={null}
          approveAllSkills={() => {}}
          approveSkill={approveSkill}
          dismissSkill={dismissSkill}
        />,
      )
    })

    expect(container.textContent ?? '').toContain('Approve is disabled')
    expect(container.textContent ?? '').not.toContain('Approve all')

    const buttons = Array.from(container.querySelectorAll('button'))
    const approveButton = buttons.find((button) => button.textContent === 'Approve') as HTMLButtonElement | undefined
    const dismissButton = buttons.find((button) => button.textContent === 'Dismiss') as HTMLButtonElement | undefined

    expect(approveButton?.disabled).toBe(true)
    expect(dismissButton?.disabled).toBe(false)

    act(() => {
      approveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      dismissButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(approveSkill).not.toHaveBeenCalled()
    expect(dismissSkill).toHaveBeenCalledTimes(1)
  })

  it('renders pending skills in small batches instead of mounting the entire queue', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    const pendingSkills = Array.from({ length: 55 }, (_, index) => ({
      skill_name: `Skill ${index + 1}`,
      normalized_name: `skill ${index + 1}`,
      occurrence_count: 1,
      candidate_ids: [index + 1],
    }))

    act(() => {
      root.render(
        <SkillUpgradeSection
          pendingSkills={pendingSkills}
          loading={false}
          busySkillKey={null}
          approveAllSkills={() => {}}
          approveSkill={() => {}}
          dismissSkill={() => {}}
        />,
      )
    })

    expect(container.querySelectorAll('article.skillUpgradeItem')).toHaveLength(50)
    const showMore = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent === 'Show 5 more',
    )
    expect(showMore).toBeDefined()
    act(() => showMore?.click())
    expect(container.querySelectorAll('article.skillUpgradeItem')).toHaveLength(55)
  })

  it('renders pending and approved intent signals in small batches', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    const signals = Array.from({ length: 55 }, (_, index) => ({
      id: index + 1,
      owner_id: 'default-owner',
      phrase: `Signal ${index + 1}`,
      normalized_phrase: `signal ${index + 1}`,
      polarity: 'positive',
      source_examples_count: 1,
      sample_evidence: [],
      confidence_aggregate: 0.8,
      last_intent_type: 'job_title',
      status: 'pending',
      created_at: '2026-07-31T00:00:00Z',
      updated_at: '2026-07-31T00:00:00Z',
    }))

    act(() => {
      root.render(
        <JobIntentLearningSection
          pendingSignals={signals}
          approvedSignals={signals.map((signal) => ({ ...signal, status: 'approved' }))}
          loading={false}
          busySignalKey={null}
          approveAllSignals={() => {}}
          approveSignal={() => {}}
          dismissSignal={() => {}}
        />,
      )
    })

    expect(container.querySelectorAll('article.skillUpgradeItem')).toHaveLength(100)
    expect(container.textContent ?? '').toContain('Show 5 more pending signals')
    expect(container.textContent ?? '').toContain('Show 5 more approved signals')
  })
})
