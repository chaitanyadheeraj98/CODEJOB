// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RenderedMessage from './RenderedMessage'
import { ChatContext, type ChatContextValue } from './chatContext'
import type { RenderedPayload } from './renderers'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const tablePayload: RenderedPayload = {
  kind: 'candidate_table',
  data: {
    title: 'Top matches',
    columns: ['role', 'ats_score'],
    rows: [{ candidate_id: 11, record_id: 'a', role: 'Backend Engineer', ats_score: 61.5 }],
    dropped: [],
    truncated: false,
  },
}

const chatStub = () => ({
  proposalResults: {},
  proposalBusyId: null,
  approveProposal: vi.fn(),
  cancelProposal: vi.fn(),
  focusCandidate: vi.fn(),
}) as unknown as ChatContextValue

describe('RenderedMessage', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  const render = (payload: RenderedPayload, surface: 'page' | 'compact') => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(
      <ChatContext.Provider value={chatStub()}>
        <RenderedMessage messageId={99} payload={payload} surface={surface} />
      </ChatContext.Provider>,
    ))
    return container
  }

  it('renders the interactive table on the page surface', () => {
    const el = render(tablePayload, 'page')

    expect(el.textContent).toContain('Backend Engineer')
    expect(el.querySelectorAll('input[type="checkbox"]').length).toBeGreaterThan(0)
  })

  it('renders the read-only table on the compact surface', () => {
    const el = render(tablePayload, 'compact')

    expect(el.textContent).toContain('Backend Engineer')
    expect(el.querySelectorAll('input[type="checkbox"]').length).toBe(0)
  })

  // A payload kind this build does not know about has to degrade to nothing,
  // the same contract every parse() already honours. Throwing here would take
  // the whole message list down.
  it('renders nothing and throws nothing for an unknown kind', () => {
    const unknown = { kind: 'chart', data: {} } as unknown as RenderedPayload

    expect(() => render(unknown, 'page')).not.toThrow()
    expect(container!.textContent).toBe('')
  })
})
