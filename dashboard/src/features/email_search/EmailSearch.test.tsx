// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EmailSearchResponse } from '../../emailSearch'
import EmailSearch from './EmailSearch'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('EmailSearch', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('debounces lookup, renders simultaneous section hits, and navigates a visible section', async () => {
    vi.useFakeTimers()
    const payload: EmailSearchResponse = {
      query: 'recruiter@example.com',
      truncated: true,
      hits: [
        {
          section: 'needs_review',
          recruiter_email_id: 42,
          sender: 'Recruiter <recruiter@example.com>',
          subject: 'Java role',
          state: 'needs_review',
          detail: {},
          occurred_at: '2026-08-13T12:00:00Z',
        },
        {
          section: 'inbox',
          recruiter_email_id: 42,
          sender: 'Recruiter <recruiter@example.com>',
          subject: 'Java role',
          state: 'needs_review',
          detail: { conversation_id: 7 },
          occurred_at: '2026-08-13T12:01:00Z',
        },
        {
          section: 'other',
          recruiter_email_id: 99,
          sender: 'Archived <archived@example.com>',
          subject: 'Archived role',
          state: 'rejected',
          detail: {},
          occurred_at: '2026-08-12T12:00:00Z',
        },
      ],
    }
    const fetchImpl = vi.fn(async () => ({ ok: true, json: async () => payload }) as Response)
    const onNavigate = vi.fn()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)

    await act(async () => {
      root?.render(<EmailSearch apiBase="http://localhost:8000" onNavigate={onNavigate} fetchImpl={fetchImpl} />)
    })
    const input = container.querySelector<HTMLInputElement>('input[placeholder="Find email or Email ID..."]')
    expect(input).not.toBeNull()

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setter?.call(input, 'x')
      input?.dispatchEvent(new Event('input', { bubbles: true }))
      vi.advanceTimersByTime(400)
      await Promise.resolve()
    })
    expect(fetchImpl).not.toHaveBeenCalled()

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setter?.call(input, 'recruiter@example.com')
      input?.dispatchEvent(new Event('input', { bubbles: true }))
      vi.advanceTimersByTime(300)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('Needs Review')
    expect(container.textContent).toContain('Inbox')
    expect(container.textContent).toContain('Other')
    expect(container.textContent).toContain('Showing the first 200 matches')
    expect(container.querySelector('.emailSearchResultStatic')).not.toBeNull()

    const inboxResult = Array.from(container.querySelectorAll<HTMLButtonElement>('.emailSearchResult')).find((button) =>
      button.textContent?.includes('Inbox'),
    )
    await act(async () => inboxResult?.click())
    expect(onNavigate).toHaveBeenCalledWith(payload.hits[1])
  })
})
