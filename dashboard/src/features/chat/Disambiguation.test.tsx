// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Disambiguation from './Disambiguation'
import { ChatContext, type ChatContextValue } from './chatContext'
import { renderForMessage, type DisambiguationData } from './renderers'
import type { ChatMessage } from './types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const payload = {
  action: 'render_disambiguation',
  kind: 'contact',
  query: 'Sarah',
  options: [
    { id: 1, label: 'Sarah Chen', detail: 'Acme Staffing · +1 214 555 1211' },
    { id: 2, label: 'Sarah Okonkwo', detail: 'BigCo Talent · +1 214 555 1212' },
  ],
  truncated: false,
}

const toolMessage = (content: unknown): ChatMessage => ({
  id: 1,
  role: 'tool',
  tool_name: 'resolve_record_reference',
  content: typeof content === 'string' ? content : JSON.stringify(content),
  created_at: '2026-01-01T00:00:00Z',
})

const parse = (raw: unknown): DisambiguationData => {
  const parsed = renderForMessage(toolMessage(raw))
  if (parsed?.kind !== 'disambiguation') throw new Error('fixture did not parse')
  return parsed.data
}

describe('Disambiguation', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (data: DisambiguationData, surface: 'page' | 'compact', chat: Partial<ChatContextValue> = {}) => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={{ navigateToQueue: vi.fn(), focusCandidate: vi.fn(), ...chat } as ChatContextValue}>
        <Disambiguation data={data} surface={surface} />
      </ChatContext.Provider>,
    ))
    return container
  }

  it('shows every option with its distinguishing detail', () => {
    const el = render(parse(payload), 'page')

    expect(el.textContent).toContain('Sarah Chen')
    expect(el.textContent).toContain('Acme Staffing')
    expect(el.textContent).toContain('Sarah Okonkwo')
    expect(el.textContent).toContain('BigCo Talent')
  })

  it('opens the chosen contact rather than sending a message', () => {
    const navigateToQueue = vi.fn()
    const el = render(parse(payload), 'page', { navigateToQueue })

    act(() => { el.querySelectorAll('button')[0].click() })

    expect(navigateToQueue).toHaveBeenCalledWith({
      page: 'premium_numbers', tab: 'inventory', filters: { q: 'Sarah Chen' },
    })
    expect(el.textContent).toContain('Opened Sarah Chen')
  })

  it('focuses a candidate rather than navigating a queue', () => {
    const focusCandidate = vi.fn()
    const el = render(parse({ ...payload, kind: 'candidate' }), 'page', { focusCandidate })

    act(() => { el.querySelectorAll('button')[1].click() })

    expect(focusCandidate).toHaveBeenCalledWith(2)
  })

  it('has no side effect until an option is chosen', () => {
    const navigateToQueue = vi.fn()
    const el = render(parse(payload), 'page', { navigateToQueue })

    expect(navigateToQueue).not.toHaveBeenCalled()
    expect(el.textContent).not.toContain('Opened')
  })

  // The widget is narrow and a mis-tap picks the wrong person.
  it('offers no choice on the compact surface', () => {
    const el = render(parse(payload), 'compact')

    expect(el.querySelectorAll('button').length).toBe(0)
    expect(el.textContent).toContain('Sarah Chen')
    expect(el.textContent).toContain('Open the Assistant page to choose')
  })

  it('says so when more matched than are shown', () => {
    const el = render(parse({ ...payload, truncated: true }), 'page')

    expect(el.textContent).toContain('narrow the name')
  })
})

describe('resolve_record_reference payload parsing', () => {
  // A single confident match resolves silently - there is nothing to draw.
  it('renders nothing for a resolved match', () => {
    expect(renderForMessage(toolMessage({ action: 'resolved', kind: 'contact', id: 1, label: 'Sarah' }))).toBeNull()
  })

  it('renders nothing for a no-match error', () => {
    expect(renderForMessage(toolMessage({ error: 'No contact found', query: 'x', searched: [] }))).toBeNull()
  })

  it.each([
    ['a single option', { ...payload, options: [payload.options[0]] }],
    ['no options', { ...payload, options: [] }],
    ['options that are not an array', { ...payload, options: 'two' }],
    ['an option with no numeric id', { ...payload, options: [{ id: 'a', label: 'x' }, { id: 2, label: 'y' }] }],
    ['no kind', { ...payload, kind: '' }],
    ['not JSON at all', 'could not resolve'],
  ])('returns null for %s', (_label, content) => {
    expect(renderForMessage(toolMessage(content))).toBeNull()
  })

  it('tolerates an option with no detail', () => {
    const data = parse({ ...payload, options: [{ id: 1, label: 'Sarah Chen' }, payload.options[1]] })

    expect(data.options[0].detail).toBe('')
  })
})
