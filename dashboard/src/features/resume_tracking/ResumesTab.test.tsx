// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ResumesTab from './ResumesTab'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('ResumesTab', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => { vi.unstubAllGlobals(); while (cleanups.length) cleanups.pop()?.() })

  it('renders the variant code, role, skills, and counts as table cells', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [{
      resume: { id: 7, file_name: 'java.pdf', version: 3, skills_text: 'Java', primary_role: 'Java Developer', structured_skills: ['Java', 'AWS'], variant_label: 'Banking', is_enabled: true, is_current: true },
      submission_count: 4,
      acceptance_rate: 0.5,
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => { root.render(<ResumesTab apiBase="http://localhost:8000" />); await new Promise((resolve) => setTimeout(resolve, 20)) })
    expect(container.textContent).toContain('Banking')
    expect(container.textContent).toContain('Java Developer')
    expect(container.textContent).toContain('50%')
    expect(container.textContent).toContain('AWS')
    // The variant code is the primary identity: variant labels collide across
    // near-identical domain strings, the code never does.
    expect(container.querySelector('.resumeCodeCell button')?.textContent).toBe('R07')
    const cells = Array.from(container.querySelectorAll('tbody .numeric')).map((cell) => cell.textContent)
    expect(cells).toEqual(['4', '50%'])
  })

  it('hides the acceptance column entirely when no outcome has ever been logged', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [{
      resume: { id: 3, file_name: 'a.pdf', version: 1, skills_text: 'Java', primary_role: 'Java Developer', structured_skills: ['Java'], variant_label: 'Banking', is_enabled: true, is_current: true },
      submission_count: 12,
      acceptance_rate: 0,
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => { root.render(<ResumesTab apiBase="http://localhost:8000" />); await new Promise((resolve) => setTimeout(resolve, 20)) })
    // A wall of 0% reads as "these resumes fail". It actually means nothing was
    // logged, so the column is withheld rather than shown as a measurement.
    expect(container.textContent).not.toContain('Acceptance')
    expect(container.textContent).not.toContain('0%')
  })

  it('passes the clicked resume id to onNavigateToSettings when details are missing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [{
      resume: { id: 9, file_name: 'python.pdf', version: 1, skills_text: 'Python', primary_role: '', structured_skills: ['Python'], variant_label: '', is_enabled: true, is_current: false },
      submission_count: 0,
      acceptance_rate: 0,
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    const onNavigateToSettings = vi.fn()
    await act(async () => { root.render(<ResumesTab apiBase="http://localhost:8000" onNavigateToSettings={onNavigateToSettings} />); await new Promise((resolve) => setTimeout(resolve, 20)) })
    const link = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === 'Add role & label')
    await act(async () => { link?.click() })
    expect(onNavigateToSettings).toHaveBeenCalledWith(9)
  })
})
