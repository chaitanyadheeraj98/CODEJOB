// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ResumeTrackingPage from './ResumeTrackingPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('ResumeTrackingPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('switches between the Resumes and Submissions views', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.endsWith('/settings/resumes') ? [] : { items: [], next_cursor: null, has_next: false }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResumeTrackingPage apiBase="http://localhost:8000" />); await new Promise((resolve) => setTimeout(resolve, 20)) })
    const submissions = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'Submissions')
    await act(async () => { submissions?.click(); await new Promise((resolve) => setTimeout(resolve, 150)) })
    expect(submissions?.getAttribute('aria-selected')).toBe('true')
    expect(container.textContent).toContain('Log submission')
    act(() => root.unmount())
    container.remove()
  })

  it('hands a resume from the Resumes tab to the Manage tab without leaving the page', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const incomplete = { id: 9, file_name: 'python.pdf', version: 1, skills_text: 'Python', primary_role: '', structured_skills: ['Python'], variant_label: '', variant_code: 'R09', is_enabled: true, is_current: false, created_at: '2026-08-12T10:00:00Z', updated_at: '2026-08-12T10:00:00Z' }
      const body = url.includes('/settings/resumes')
        ? [incomplete]
        : url.includes('/performance-summary')
          ? { items: [{ resume: incomplete, submission_count: 0, acceptance_rate: 0 }] }
          : { items: [], next_cursor: null, has_next: false }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResumeTrackingPage apiBase="http://localhost:8000" />); await new Promise((resolve) => setTimeout(resolve, 20)) })

    const resumes = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'Resumes')
    await act(async () => { resumes?.click(); await new Promise((resolve) => setTimeout(resolve, 20)) })
    const fix = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'Add role & label')
    await act(async () => { fix?.click(); await new Promise((resolve) => setTimeout(resolve, 20)) })

    const manage = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'Manage')
    expect(manage?.getAttribute('aria-selected')).toBe('true')
    // It lands on that resume's open editor, not on a list to search again.
    expect(container.querySelector('.resumeLibraryEditor')).not.toBeNull()
    expect(container.querySelector('.resumeLibraryCode')?.textContent).toBe('R09')
    act(() => root.unmount())
    container.remove()
  })
})
