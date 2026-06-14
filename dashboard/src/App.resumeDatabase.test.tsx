// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ResumeDatabaseSection } from './App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('ResumeDatabaseSection', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    while (cleanups.length) cleanups.pop()?.()
  })

  it('renders contained resume database structure for long resume names', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })

    const longFileName =
      'Chaithanya_Dheeraj_Full_Stack_Java_Engineer_React_AI_Dallas_Very_Long_Resume_Name_For_Overflow_Checking.docx'

    act(() => {
      root.render(
        <ResumeDatabaseSection
          activeResume={{
            id: 1,
            file_name: longFileName,
            mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            sha256: 'abc',
            version: 7,
            skills_text: 'java, spring boot, microservices, react, aws',
            is_enabled: true,
            is_current: true,
            created_at: '2026-06-14T00:00:00Z',
            updated_at: '2026-06-14T00:00:00Z',
          }}
          resumeFile={null}
          resumeSkillsInput="java, spring boot, microservices, react, aws"
          resumeSkillEdits={{ 1: 'java, spring boot, microservices, react, aws' }}
          resumeAssets={[
            {
              id: 1,
              file_name: longFileName,
              mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              sha256: 'abc',
              version: 7,
              skills_text: 'java, spring boot, microservices, react, aws',
              is_enabled: true,
              is_current: true,
              created_at: '2026-06-14T00:00:00Z',
              updated_at: '2026-06-14T00:00:00Z',
            },
          ]}
          setResumeFile={vi.fn()}
          setResumeSkillsInput={vi.fn()}
          setResumeSkillEdits={vi.fn()}
          uploadResume={vi.fn()}
          saveResumeSkills={vi.fn()}
          toggleResumeAsset={vi.fn()}
          deleteResumeAsset={vi.fn()}
        />,
      )
    })

    expect(container.querySelector('.resumeDatabaseCard')).not.toBeNull()
    expect(container.querySelector('.resumeDatabaseFallback')?.textContent ?? '').toContain(longFileName)
    expect(container.querySelector('.resumeDatabaseFileName')?.textContent).toBe(longFileName)
    expect(container.querySelector('.resumeDatabaseList')).not.toBeNull()
    expect(container.querySelector('.resumeDatabaseActions')).not.toBeNull()
    expect(container.querySelector('.resumeDatabaseButtons')?.textContent ?? '').toContain('Save Skills')
    expect(container.querySelector('.resumeDatabaseButtons')?.textContent ?? '').toContain('Delete')
  })
})
