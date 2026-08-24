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
})
