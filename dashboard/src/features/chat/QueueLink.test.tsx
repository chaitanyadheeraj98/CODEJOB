// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import QueueLink from './QueueLink'
import { ChatContext, type ChatContextValue } from './chatContext'
import { renderForMessage, type QueueLinkData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const data: QueueLinkData = {
  page: 'needs_review',
  tab: null,
  label: 'Needs Review',
  title: 'Java roles from last week',
  filters: { role: 'Java Developer', date_filter: 'last_7_days' },
  dropped: [{ key: 'unreplied', reason: 'unknown_field' }],
}

const toolMessage = (content: unknown): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name: 'navigate_to_queue',
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

describe('QueueLink', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (chat: Partial<ChatContextValue>, surface: 'page' | 'compact' = 'page') => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chat as ChatContextValue}>
        <QueueLink data={data} surface={surface} />
      </ChatContext.Provider>,
    ))
    return container
  }

  it('navigates with the parsed target on click', () => {
    const navigateToQueue = vi.fn()
    const el = render({ navigateToQueue })

    act(() => { el.querySelector('button')?.click() })

    expect(navigateToQueue).toHaveBeenCalledWith({
      page: 'needs_review',
      tab: null,
      filters: { role: 'Java Developer', date_filter: 'last_7_days' },
    })
  })

  it('names the filters in the user\'s words on the page surface', () => {
    const el = render({ navigateToQueue: vi.fn() })

    expect(el.textContent).toContain('Job title: Java Developer')
    expect(el.textContent).toContain('Date: the last 7 days')
    expect(el.textContent).toContain('Not applied: unreplied')
  })

  it('omits the filter detail on the compact surface', () => {
    const el = render({ navigateToQueue: vi.fn() }, 'compact')

    expect(el.textContent).toContain('Needs Review')
    expect(el.textContent).not.toContain('Job title')
  })

  // A provider mounted without the callback (tests, or any host with no such
  // page) must degrade to a no-op rather than throwing out of the message list.
  it('does not throw when the provider supplies no callback', () => {
    const el = render({ navigateToQueue: () => {} })

    expect(() => act(() => { el.querySelector('button')?.click() })).not.toThrow()
  })
})

describe('navigate_to_queue payload parsing', () => {
  it('parses a valid payload to a queue_link', () => {
    const parsed = renderForMessage(toolMessage({
      action: 'navigate_to_queue',
      page: 'needs_review',
      tab: null,
      label: 'Needs Review',
      title: 'Java roles',
      filters: { role: 'Java' },
      dropped: [],
    }))

    expect(parsed?.kind).toBe('queue_link')
    if (parsed?.kind !== 'queue_link') throw new Error('expected a queue link payload')
    expect(parsed.data.page).toBe('needs_review')
    expect(parsed.data.filters).toEqual({ role: 'Java' })
  })

  it.each([
    ['a missing page', { action: 'navigate_to_queue', label: 'Needs Review' }],
    ['a different action', { action: 'render_candidate_table', page: 'needs_review' }],
    ['not JSON at all', 'could not navigate'],
  ])('returns null for %s', (_label, content) => {
    expect(renderForMessage(toolMessage(content))).toBeNull()
  })

  it('drops non-string filter values rather than passing them through', () => {
    const parsed = renderForMessage(toolMessage({
      action: 'navigate_to_queue',
      page: 'needs_review',
      filters: { role: 'Java', limit: 5 },
    }))

    if (parsed?.kind !== 'queue_link') throw new Error('expected a queue link payload')
    expect(parsed.data.filters).toEqual({ role: 'Java' })
  })
})
