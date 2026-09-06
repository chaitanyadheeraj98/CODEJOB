// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ChatProvider from './ChatProvider'
import { useChat } from './chatContext'
import { PROPOSAL_HANDLERS } from './proposals'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const session = { id: 1, title: 'Shared', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }

// Stands in for the floating widget and the Assistant workspace: two
// independent consumers of one provider, which is the whole point of the hoist.
function Surface({ label }: { label: string }) {
  const chat = useChat()
  return (
    <section data-testid={label}>
      <p className="thread">{chat.messages.map((message) => message.content).join('|')}</p>
      <p className="model">{chat.selectedModel}</p>
      <p className="unseen">{chat.unseenCount}</p>
      <p className="tools">{chat.messages.map((message) => message.tool_name ?? '-').join('|')}</p>
      <button type="button" className="send" onClick={() => void chat.sendMessage('hello from ' + label, chat.selectedModel)}>send</button>
      <button type="button" className="pick" onClick={() => chat.selectModel('minimax-m3:cloud')}>pick</button>
      <button type="button" className="focus" onClick={() => chat.focusCandidate(7323)}>focus</button>
      <p className="results">{Object.entries(chat.proposalResults).map(([id, result]) => (
        `${id}:${result === 'cancelled' ? 'cancelled' : `${result.approved}/${result.detail}`}`
      )).join('|')}</p>
      <button
        type="button"
        className="approve"
        onClick={() => void chat.approveProposal(5, {
          handler: PROPOSAL_HANDLERS.propose_profile_update,
          fields: PROFILE_PROPOSAL,
        })}
      >
        approve
      </button>
      <button
        type="button"
        className="cancel"
        onClick={() => chat.cancelProposal(5, 'propose_profile_update')}
      >
        cancel
      </button>
    </section>
  )
}

const PROFILE_PROPOSAL = {
  action: 'propose_profile_update',
  operation: 'append',
  field: 'Notice period',
  value: '2 weeks',
  entry: '- Notice period: 2 weeks',
  existing_profile: '# Me\n',
  resulting_profile: '# Me\n\n## Saved from chat\n- Notice period: 2 weeks\n',
  base_sha256: 'a'.repeat(64),
  provenance: 'assistant_asked',
}

const profileCardRow = {
  id: 5,
  role: 'tool',
  tool_name: 'propose_profile_update',
  content: JSON.stringify(PROFILE_PROPOSAL),
  created_at: '2026-01-01T00:00:01Z',
}

const readOn = (container: HTMLElement, label: string, selector: string) =>
  container.querySelector(`[data-testid="${label}"] ${selector}`)?.textContent

describe('ChatProvider', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null
  let storedRef: Array<Record<string, unknown>> = []
  let outcomes: Array<Record<string, unknown>> = []

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.restoreAllMocks()
    vi.useRealTimers()
    window.localStorage.clear()
    storedRef = []
    outcomes = []
  })

  const stubChatApi = (
    messages: Array<Record<string, unknown>> = [],
    honourSinceId = true,
    failWrite = false,
  ) => {
    const calls: string[] = []
    // Stateful on purpose: sendMessage reconciles against the server once the
    // stream ends, so a stub that kept returning an empty thread would erase
    // the message it had just accepted.
    const stored = [...messages]
    storedRef = stored
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url.replace('http://localhost:8000', '')}`)
      if (url.endsWith('/chat/status')) {
        return new Response(JSON.stringify({
          enabled: true,
          ollama_running: true,
          ollama_last_error: null,
          ollama_last_success_at: null,
          chat_last_error: null,
          mcp_status: 'ok',
          model: 'gemma4:31b-cloud',
          available_models: ['gemma4:31b-cloud', 'minimax-m3:cloud'],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions/1/messages') && init?.method === 'POST') {
        const { text } = JSON.parse(String(init.body)) as { text: string }
        stored.push({ id: stored.length + 1, role: 'user', content: text, tool_name: null, created_at: '2026-01-01T00:00:02Z' })
        stored.push({ id: stored.length + 1, role: 'assistant', content: 'Hi', tool_name: null, created_at: '2026-01-01T00:00:03Z' })
        const encoder = new TextEncoder()
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode('event: message\ndata: {"delta":"Hi"}\n\nevent: done\ndata: {"message_id":9}\n\n'))
            controller.close()
          },
        }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.includes('/chat/sessions/1/events') && init?.method === 'POST') {
        outcomes.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return new Response(JSON.stringify({ id: 99, role: 'event', content: '[System: noted.]', tool_name: null, created_at: '2026-01-01T00:00:04Z' }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/settings/candidate-profile/entries') && init?.method === 'POST') {
        if (failWrite) return new Response(JSON.stringify({ detail: 'The profile changed since this was prepared.' }), { status: 409, headers: { 'Content-Type': 'application/json' } })
        return new Response(JSON.stringify({ filename: 'profile.md', uploaded_at: null, characters: 1204 }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions')) {
        return new Response(JSON.stringify([session]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      const detail = new URL(url)
      if (detail.pathname === '/chat/sessions/1') {
        // Mirrors the server: since_id returns only what is newer, and session
        // metadata comes back either way.
        const sinceId = honourSinceId ? Number(detail.searchParams.get('since_id') ?? 0) : 0
        const messages = sinceId ? stored.filter((row) => Number(row.id) > sinceId) : stored
        return new Response(JSON.stringify({ ...session, messages }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected fetch: ${url}`)
    }))
    return calls
  }

  const flush = async (times = 4) => {
    for (let tick = 0; tick < times; tick += 1) {
      if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0)
      else await new Promise((resolve) => window.setTimeout(resolve, 0))
    }
  }

  const mountSurfaces = async (
    messages: Array<Record<string, unknown>> = [],
    options: {
      honourSinceId?: boolean
      onFocusCandidate?: (id: number) => void
      onProfileChanged?: () => void
      failWrite?: boolean
    } = {},
  ) => {
    const calls = stubChatApi(messages, options.honourSinceId ?? true, options.failWrite ?? false)
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(
        <ChatProvider
          apiBase="http://localhost:8000"
          onFocusCandidate={options.onFocusCandidate}
          onProfileChanged={options.onProfileChanged}
        >
          <Surface label="widget" />
          <Surface label="page" />
        </ChatProvider>,
      )
      await flush()
    })
    return { calls, stored: storedRef }
  }

  it('names the provider when a consumer is mounted without one', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)

    expect(() => act(() => root?.render(<Surface label="orphan" />))).toThrow(/ChatProvider/)
  })

  // Two useChatSession instances would load the history twice and poll twice.
  it('loads the session once no matter how many surfaces are listening', async () => {
    const { calls } = await mountSurfaces()

    expect(calls.filter((call) => call === 'GET /chat/sessions')).toHaveLength(1)
    expect(calls.filter((call) => call === 'GET /chat/sessions/1')).toHaveLength(1)
    expect(calls.filter((call) => call === 'GET /chat/status')).toHaveLength(1)
  })

  it('shows both surfaces the same stored history', async () => {
    await mountSurfaces([
      { id: 1, role: 'user', content: 'Any replies today?', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
      { id: 2, role: 'assistant', content: 'Two recruiters replied.', tool_name: null, created_at: '2026-01-01T00:00:01Z' },
    ])

    const thread = 'Any replies today?|Two recruiters replied.'
    expect(readOn(container!, 'widget', '.thread')).toBe(thread)
    expect(readOn(container!, 'page', '.thread')).toBe(thread)
  })

  // The breakage the hoist exists to prevent: before it, a message sent on one
  // surface stayed invisible on the other until that surface's own 20s poll.
  it('shows a message sent on one surface to the other without waiting for a poll', async () => {
    await mountSurfaces()

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[data-testid="widget"] .send')?.click()
      await flush()
    })

    expect(readOn(container!, 'page', '.thread')).toContain('hello from widget')
    expect(readOn(container!, 'widget', '.thread')).toBe(readOn(container!, 'page', '.thread'))
  })

  // The 20s poll used to re-download the whole thread every time. With
  // render_candidate_table payloads (W6) landing in message.content, that gets
  // expensive fast.
  it('polls for only the messages it has not already got', async () => {
    vi.useFakeTimers()
    const { calls, stored } = await mountSurfaces([
      { id: 4, role: 'user', content: 'Any replies today?', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
      { id: 5, role: 'assistant', content: 'Two recruiters replied.', tool_name: null, created_at: '2026-01-01T00:00:01Z' },
    ])
    const before = calls.length

    stored.push({ id: 6, role: 'assistant', content: 'A third just came in.', tool_name: null, created_at: '2026-01-01T00:01:00Z' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000)
    })

    const polled = calls.slice(before).filter((call) => call.includes('/chat/sessions/1'))
    expect(polled).toEqual(['GET /chat/sessions/1?since_id=5'])
    expect(readOn(container!, 'page', '.thread'))
      .toBe('Any replies today?|Two recruiters replied.|A third just came in.')
    expect(readOn(container!, 'widget', '.unseen')).toBe('1')
  })

  it('asks from the newest id again when a poll brings nothing new', async () => {
    vi.useFakeTimers()
    const { calls } = await mountSurfaces([
      { id: 4, role: 'user', content: 'Any replies today?', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
    ])
    const before = calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(40000)
    })

    // Two empty polls, and neither one walks the high-water mark backwards or
    // re-counts the message already on screen.
    expect(calls.slice(before).filter((call) => call.includes('/chat/sessions/1')))
      .toEqual(['GET /chat/sessions/1?since_id=4', 'GET /chat/sessions/1?since_id=4'])
    expect(readOn(container!, 'page', '.thread')).toBe('Any replies today?')
    expect(readOn(container!, 'widget', '.unseen')).toBe('0')
  })

  // Deployment order is not guaranteed, and neither is every proxy in between.
  // Caught for real: the frontend shipped against a backend container that
  // predated since_id, and the thread quadrupled inside a minute.
  it('does not duplicate the thread when the server ignores since_id', async () => {
    vi.useFakeTimers()
    await mountSurfaces(
      [{ id: 4, role: 'user', content: 'Any replies today?', tool_name: null, created_at: '2026-01-01T00:00:00Z' }],
      { honourSinceId: false },
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000)
    })

    expect(readOn(container!, 'page', '.thread')).toBe('Any replies today?')
    expect(readOn(container!, 'widget', '.unseen')).toBe('0')
  })

  // The seam W6's candidate table navigates through. App owns the navigation
  // state, so the provider only relays.
  it('relays a focus request to the host', async () => {
    const onFocusCandidate = vi.fn()
    await mountSurfaces([], { onFocusCandidate })

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[data-testid="page"] .focus')?.click()
    })

    expect(onFocusCandidate).toHaveBeenCalledWith(7323)
  })

  it('treats focus as a no-op when the host supplies no handler', async () => {
    await mountSurfaces()

    expect(() => {
      act(() => {
        container?.querySelector<HTMLButtonElement>('[data-testid="page"] .focus')?.click()
      })
    }).not.toThrow()
  })

  // visibleMessages drops tool messages nothing knows how to draw. It has to
  // consult both registries: checking only PROPOSAL_HANDLERS deletes rendered
  // tables from history on both surfaces, and the failure is silent - the model
  // reports a table it drew and nothing appears.
  it('keeps rendered tool messages in history', async () => {
    await mountSurfaces([
      { id: 4, role: 'user', content: 'Show me the top matches', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
      {
        id: 5,
        role: 'tool',
        tool_name: 'render_candidate_table',
        content: JSON.stringify({
          action: 'render_candidate_table',
          title: 'Top matches',
          columns: ['role'],
          rows: [{ candidate_id: 11, record_id: 'a', role: 'Backend' }],
          dropped: [],
          truncated: false,
        }),
        created_at: '2026-01-01T00:00:01Z',
      },
    ])

    expect(readOn(container!, 'page', '.tools')).toBe('-|render_candidate_table')
    expect(readOn(container!, 'widget', '.tools')).toBe('-|render_candidate_table')
  })

  it('still drops tool messages nothing can draw', async () => {
    await mountSurfaces([
      { id: 4, role: 'user', content: 'How many replies?', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
      { id: 5, role: 'tool', tool_name: 'search_candidates', content: '{"candidates":[]}', created_at: '2026-01-01T00:00:01Z' },
    ])

    expect(readOn(container!, 'page', '.tools')).toBe('-')
  })

  // A propose_* row from a tool this build has no handler for. It is kept, and
  // the surfaces draw `unsupportedProposalNotice` for it. Dropping it is how
  // propose_nvoids_search became invisible rather than merely broken: the row
  // was deleted before render, so the assistant's "ready to confirm" had
  // nothing beside it to disagree. A build-time check cannot reach a user on a
  // stale bundle; this can.
  it('keeps a proposal it cannot draw, rather than deleting it', async () => {
    await mountSurfaces([
      { id: 4, role: 'user', content: 'search nvoids for Citi', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
      {
        id: 5,
        role: 'tool',
        tool_name: 'propose_something_this_build_has_never_heard_of',
        content: JSON.stringify({ action: 'propose_something_this_build_has_never_heard_of', company: 'Citi' }),
        created_at: '2026-01-01T00:00:01Z',
      },
    ])

    expect(readOn(container!, 'page', '.tools')).toBe('-|propose_something_this_build_has_never_heard_of')
    expect(readOn(container!, 'widget', '.tools')).toBe('-|propose_something_this_build_has_never_heard_of')
  })

  // --- the outcome of a click becomes part of the record --------------

  it('records a confirmed write as an outcome and refreshes the host', async () => {
    const onProfileChanged = vi.fn()
    await mountSurfaces([profileCardRow], { onProfileChanged })

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[data-testid="widget"] .approve')?.click()
      await flush()
    })

    expect(outcomes).toEqual([{
      tool_name: 'propose_profile_update',
      outcome: 'confirmed',
      proposal_message_id: 5,
      characters: 1204,
    }])
    // Without this the Settings panel keeps showing the pre-write filename and
    // character count until something else happens to reload it.
    expect(onProfileChanged).toHaveBeenCalledTimes(1)
    expect(readOn(container!, 'page', '.results')).toContain('true/Profile updated - 1,204 characters.')
  })

  it('records a failed write as failed, and does not refresh the host', async () => {
    const onProfileChanged = vi.fn()
    await mountSurfaces([profileCardRow], { onProfileChanged, failWrite: true })

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[data-testid="widget"] .approve')?.click()
      await flush()
    })

    expect(outcomes).toHaveLength(1)
    expect(outcomes[0].outcome).toBe('failed')
    expect(onProfileChanged).not.toHaveBeenCalled()
    expect(readOn(container!, 'page', '.results')).toContain('false/')
  })

  it('records a cancel, because a later turn cannot tell one from a confirm', async () => {
    await mountSurfaces([profileCardRow])

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[data-testid="page"] .cancel')?.click()
      await flush()
    })

    expect(outcomes).toEqual([{
      tool_name: 'propose_profile_update',
      outcome: 'cancelled',
      proposal_message_id: 5,
    }])
    expect(readOn(container!, 'widget', '.results')).toBe('5:cancelled')
  })

  it('does not turn a successful write into a reported failure when the bookkeeping call fails', async () => {
    await mountSurfaces([profileCardRow])
    const originalFetch = globalThis.fetch as typeof fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/events')) throw new Error('network down')
      return originalFetch(input, init)
    }))

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[data-testid="widget"] .approve')?.click()
      await flush()
    })

    // The write happened. A bookkeeping call that did not must not change that.
    expect(readOn(container!, 'page', '.results')).toContain('true/Profile updated')
  })

  // --- the card knows its own outcome after a reload -------------------

  it('seeds a confirmed card from its persisted event row', async () => {
    await mountSurfaces([
      profileCardRow,
      {
        id: 6,
        role: 'event',
        tool_name: 'propose_profile_update',
        content: '[System: your propose_profile_update card was confirmed by the user.]',
        created_at: '2026-01-01T00:00:02Z',
        proposal_message_id: 5,
        outcome: 'confirmed',
      },
    ])

    // Without this a refresh returns the card to unresolved: Confirm goes live
    // again, and W11's pending line would claim nothing was saved.
    expect(readOn(container!, 'page', '.results')).toBe('5:true/[System: your propose_profile_update card was confirmed by the user.]')
  })

  it('seeds a cancelled card from its persisted event row', async () => {
    await mountSurfaces([
      profileCardRow,
      {
        id: 6,
        role: 'event',
        tool_name: 'propose_profile_update',
        content: '[System: cancelled.]',
        created_at: '2026-01-01T00:00:02Z',
        proposal_message_id: 5,
        outcome: 'cancelled',
      },
    ])

    expect(readOn(container!, 'page', '.results')).toBe('5:cancelled')
  })

  it('leaves a card unresolved when no event row pairs with it', async () => {
    await mountSurfaces([profileCardRow])

    expect(readOn(container!, 'page', '.results')).toBe('')
  })

  it('ignores an event row whose proposal id matches nothing, rather than throwing', async () => {
    await mountSurfaces([
      profileCardRow,
      {
        id: 6,
        role: 'event',
        tool_name: 'propose_profile_update',
        content: '[System: confirmed.]',
        created_at: '2026-01-01T00:00:02Z',
        proposal_message_id: 4242,
        outcome: 'confirmed',
      },
    ])

    expect(readOn(container!, 'page', '.results')).toBe('')
  })

  it('renders an event row in the thread rather than dropping it', async () => {
    await mountSurfaces([
      { id: 4, role: 'user', content: 'Save my notice period', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
      {
        id: 6,
        role: 'event',
        tool_name: 'propose_profile_update',
        content: '[System: cancelled.]',
        created_at: '2026-01-01T00:00:02Z',
        proposal_message_id: 5,
        outcome: 'cancelled',
      },
    ])

    // visibleMessages keeps it - an event row is not role 'tool' - and it falls
    // through to its own bubble class rather than an assistant one.
    expect(readOn(container!, 'page', '.thread')).toBe('Save my notice period|[System: cancelled.]')
  })

  it('shares the model choice across surfaces and remembers it', async () => {
    await mountSurfaces()

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[data-testid="page"] .pick')?.click()
    })

    expect(readOn(container!, 'widget', '.model')).toBe('minimax-m3:cloud')
    expect(window.localStorage.getItem('codejob.chat.model')).toBe('minimax-m3:cloud')
  })
})
