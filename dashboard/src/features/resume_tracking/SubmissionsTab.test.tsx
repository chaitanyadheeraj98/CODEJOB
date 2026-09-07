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

  it('resolves a pasted variant marker to the send it came from', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url.includes('/resumes/variant-lookup')) {
        return new Response(JSON.stringify({
          email_id: 8919,
          variant_code: 'R13',
          variant_label: 'banking, payments',
          resume_file_name: 'resume.docx',
          role: 'Java Developer',
          subject: 'Java Developer',
          recruiter_email: 'amir@example.com',
          sent_at: '2026-09-04T22:57:39Z',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      const body = url.includes('/applications/suggestions') ? { items: [] } : { items: [], next_cursor: null, has_next: false }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => { act(() => root.unmount()); container.remove() })
    await act(async () => {
      root.render(<SubmissionsTab apiBase="http://localhost:8000" resumes={[]} />)
      await new Promise((resolve) => setTimeout(resolve, 150))
    })

    const input = container.querySelector<HTMLInputElement>('#variantLookupInput')!
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'CJ-R13-8919')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      container.querySelector<HTMLFormElement>('.variantLookup')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 100))
    })

    expect(calls.some((url) => url.includes('variant-lookup') && url.includes('CJ-R13-8919'))).toBe(true)
    const result = container.querySelector('.variantLookupResult')?.textContent ?? ''
    expect(result).toContain('R13')
    expect(result).toContain('Java Developer')
    expect(result).toContain('amir@example.com')
  })
})
