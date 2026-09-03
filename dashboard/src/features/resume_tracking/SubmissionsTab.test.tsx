// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SubmissionsTab from './SubmissionsTab'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('SubmissionsTab', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => { vi.unstubAllGlobals(); while (cleanups.length) cleanups.pop()?.() })

  it('opens a backdate-capable manual submission form with a resume selected', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const body = String(input).includes('/applications/suggestions') ? { items: [] } : { items: [], next_cursor: null, has_next: false }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => {
      root.render(<SubmissionsTab apiBase="http://localhost:8000" resumes={[{ id: 7, file_name: 'java.pdf', version: 3, skills_text: 'Java', primary_role: 'Java Developer', structured_skills: ['Java'], variant_label: 'Banking', is_enabled: true, is_current: true }]} />)
      await new Promise((resolve) => setTimeout(resolve, 150))
    })
    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'Log submission')
    act(() => button?.click())
    expect(container.textContent).toContain('Submission date')
    expect(container.querySelector<HTMLInputElement>('input[type="date"]')?.value).toBe(new Date().toISOString().slice(0, 10))
    expect(container.querySelector('.resumeTrackingForm select')?.textContent).toContain('Banking')
  })
})
