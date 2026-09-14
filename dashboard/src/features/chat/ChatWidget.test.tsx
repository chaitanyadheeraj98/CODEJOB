// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ChatProvider from './ChatProvider'
import ChatWidget from './ChatWidget'
import { consumeSseStream } from './api'

// ChatWidget reads its session, status, model, and proposal state from
// ChatProvider, so every mount needs one. The provider issues exactly the
// requests the widget used to, which is why the fetch stubs below are
// unchanged from before the hoist.
const renderWithChat = (node: React.ReactNode) => (
  <ChatProvider apiBase="http://localhost:8000">{node}</ChatProvider>
)

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
// jsdom doesn't implement scrollIntoView; ChatWidget calls it when new messages arrive.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

describe('ChatWidget', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('shows why chat is disabled instead of hiding the widget', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      enabled: false,
      ollama_running: false,
      ollama_last_error: null,
      ollama_last_success_at: null,
      chat_last_error: null,
      mcp_status: 'disabled',
      model: 'gemma4:31b-cloud',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(renderWithChat(<ChatWidget />))
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click()
    })

    expect(container.textContent).toContain('Chat is disabled')
    expect(container.textContent).toContain('FEATURE_CHAT_ENABLED=true')
  })

  it('renames the current chat via the header prompt and shows the new title', async () => {
    const session = { id: 1, title: 'Old title', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/chat/status')) {
        return new Response(JSON.stringify({
          enabled: true,
          ollama_running: true,
          ollama_last_error: null,
          ollama_last_success_at: null,
          chat_last_error: null,
          mcp_status: 'ok',
          model: 'gemma4:31b-cloud',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions') && (!init || init.method === undefined)) {
        return new Response(JSON.stringify([session]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions/1') && init?.method === 'PATCH') {
        session.title = (JSON.parse(String(init.body)) as { title: string }).title
        return new Response(JSON.stringify(session), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions/1')) {
        return new Response(JSON.stringify({ ...session, messages: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected fetch: ${url}`)
    }))
    vi.spyOn(window, 'prompt').mockReturnValue('Gmail Integration Testing')
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(renderWithChat(<ChatWidget />))
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click()
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Rename current chat"]')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })

    expect(window.prompt).toHaveBeenCalledWith('Rename chat', 'Old title')
    const options = container.querySelectorAll('option')
    expect(Array.from(options).map((option) => option.textContent)).toContain('Gmail Integration Testing')
  })

  // The same stored message the Assistant page renders interactively. Both
  // surfaces must show something, or a shared session has blank spots depending
  // on where it is opened.
  it('renders a stored candidate table read-only', async () => {
    const session = { id: 1, title: 'Matches', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/chat/status')) {
        return new Response(JSON.stringify({
          enabled: true,
          ollama_running: true,
          ollama_last_error: null,
          ollama_last_success_at: null,
          chat_last_error: null,
          mcp_status: 'ok',
          model: 'gemma4:31b-cloud',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions')) {
        return new Response(JSON.stringify([session]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify({
        ...session,
        messages: [{
          id: 5,
          role: 'tool',
          tool_name: 'render_candidate_table',
          content: JSON.stringify({
            action: 'render_candidate_table',
            title: 'Top matches',
            columns: ['role', 'ats_score', 'state'],
            rows: [{ candidate_id: 11, record_id: 'a', role: 'Backend Engineer', ats_score: 61.5, state: 'needs_review' }],
            dropped: [],
            truncated: false,
          }),
          created_at: '2026-01-01T00:00:01Z',
        }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(renderWithChat(<ChatWidget />))
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click()
    })

    expect(container.textContent).toContain('Top matches')
    expect(container.textContent).toContain('Backend Engineer')
    expect(container.querySelectorAll('.chatMessages input[type="checkbox"]')).toHaveLength(0)
    expect(container.querySelectorAll('.chatMessages table')).toHaveLength(1)
  })

  it('parses SSE events split across arbitrary response chunks', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: message\ndata: {"del'))
        controller.enqueue(encoder.encode('ta":"Hel"}\n\nevent: message\r\ndata: {"delta":"lo"}\r\n\r\n'))
        controller.enqueue(encoder.encode('event: done\ndata: {"message_id":7}\n\n'))
        controller.close()
      },
    })
    const events: Array<{ event: string; data: Record<string, unknown> }> = []
    await consumeSseStream(stream, (event) => events.push(event))

    expect(events.map((event) => event.event)).toEqual(['message', 'message', 'done'])
    expect(events[0].data.delta).toBe('Hel')
    expect(events[1].data.delta).toBe('lo')
    expect(events[2].data.message_id).toBe(7)
  })

  it.each([
    {
      name: 'bulk approval',
      toolName: 'propose_bulk_approve_candidates',
      fields: { action: 'approve_candidates', candidate_ids: [11, 12], count: 2 },
      label: 'Approve 2 Emails',
      endpoint: '/candidates/approve-bulk',
      body: { ids: [11, 12] },
      response: { succeeded_ids: [1, 2], failed: [] },
      outcome: '2 approved.',
    },
    {
      name: 'contact creation',
      toolName: 'propose_create_premium_contact',
      fields: {
        action: 'create_premium_contact',
        fields: { name: 'Pat', title: 'Recruiter', company: 'Acme', email: 'pat@example.com', phone: '2145551212', role: 'recruiter' },
      },
      label: 'Save Contact',
      endpoint: '/premium-numbers/contacts',
      body: { name: 'Pat', title: 'Recruiter', company: 'Acme', email: 'pat@example.com', phone: '2145551212', role: 'recruiter' },
      response: { id: 9, created: true },
      outcome: 'Saved as contact 9.',
    },
    {
      name: 'email send',
      toolName: 'propose_send_email',
      fields: { action: 'send_email', candidate_email_id: 44, to: 'to@example.com', cc: '', subject: 'Re: Role', body: 'Thanks' },
      label: 'Send Email',
      endpoint: '/candidates/44/send-chat-reply',
      // A proposal with no documents still sends the key, so the server sees an
      // explicit empty list rather than a missing field. The envelope goes with
      // it for the same reason: what the card showed is what leaves.
      body: { body: 'Thanks', subject: 'Re: Role', to: 'to@example.com', cc: '', resume_id: 0, confirm_new_recipients: false, document_ids: [] },
      response: { sent: true, message_id: 'gmail-1', attached_documents: [] },
      outcome: 'Email sent.',
    },
  ])('renders and executes the $name proposal only after approval', async ({
    toolName, fields, label, endpoint, body, response, outcome,
  }) => {
    const actionCalls: Array<{ url: string; body: unknown }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/chat/status')) {
        return new Response(JSON.stringify({
          enabled: true,
          ollama_running: true,
          ollama_last_error: null,
          ollama_last_success_at: null,
          chat_last_error: null,
          mcp_status: 'ok',
          model: 'gemma4:31b-cloud',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions')) {
        return new Response(JSON.stringify([{ id: 1, title: 'Actions', created_at: '2026-01-01', updated_at: '2026-01-01' }]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/chat/sessions/1')) {
        return new Response(JSON.stringify({
          id: 1,
          title: 'Actions',
          created_at: '2026-01-01',
          updated_at: '2026-01-01',
          messages: [{ id: 7, role: 'tool', tool_name: toolName, content: JSON.stringify(fields), created_at: '2026-01-01' }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith(endpoint) && init?.method === 'POST') {
        actionCalls.push({ url, body: JSON.parse(String(init.body)) })
        return new Response(JSON.stringify(response), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected fetch: ${url}`)
    }))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(renderWithChat(<ChatWidget />))
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click()
    })

    expect(actionCalls).toEqual([])
    const approve = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === label)
    await act(async () => {
      approve?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })

    expect(actionCalls).toEqual([{ url: `http://localhost:8000${endpoint}`, body }])
    expect(container.textContent).toContain(outcome)
  })

  it('renders a markdown table with inline code spans instead of raw pipes and backticks', async () => {
    const tableMarkdown = [
      'Here are the Java-based roles with an ATS score greater than 60:',
      '',
      '| Record ID | Role | ATS Score |',
      '| :--- | :--- | :--- |',
      '| `cdbd9c9b-d38b-474a-8df6-68e848fecc6c` | Senior Full Stack Engineer | 72.08 |',
      '| `3857a0a1-e127-4b41-b01e-40fbf1de5ed0` | Senior Java/Kotlin Backend Developer | 66.82 |',
    ].join('\n')
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/chat/status')) {
        return new Response(JSON.stringify({
          enabled: true,
          ollama_running: true,
          ollama_last_error: null,
          ollama_last_success_at: null,
          chat_last_error: null,
          mcp_status: 'ok',
          model: 'gemma4:31b-cloud',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions')) {
        return new Response(JSON.stringify([{ id: 1, title: 'Java roles', created_at: '2026-01-01', updated_at: '2026-01-01' }]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/chat/sessions/1')) {
        return new Response(JSON.stringify({
          id: 1,
          title: 'Java roles',
          created_at: '2026-01-01',
          updated_at: '2026-01-01',
          messages: [{ id: 7, role: 'assistant', tool_name: null, content: tableMarkdown, created_at: '2026-01-01' }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected fetch: ${url}`)
    }))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(renderWithChat(<ChatWidget />))
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click()
    })

    const table = container.querySelector('.chatBubble.assistant table')
    expect(table).not.toBeNull()
    expect(Array.from(table!.querySelectorAll('thead th')).map((th) => th.textContent)).toEqual([
      'Record ID', 'Role', 'ATS Score',
    ])
    expect(table!.querySelectorAll('tbody tr')).toHaveLength(2)
    const firstIdCell = table!.querySelector('tbody tr td code')
    expect(firstIdCell?.textContent).toBe('cdbd9c9b-d38b-474a-8df6-68e848fecc6c')
    expect(container.querySelector('.chatBubble.assistant')?.textContent).not.toContain('|')
    expect(container.querySelector('.chatBubble.assistant')?.textContent).not.toContain('`')
  })

  it('renders numbered lists as an ordered list, not flattened paragraph text', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/chat/status')) {
        return new Response(JSON.stringify({
          enabled: true,
          ollama_running: true,
          ollama_last_error: null,
          ollama_last_success_at: null,
          chat_last_error: null,
          mcp_status: 'ok',
          model: 'gemma4:31b-cloud',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions')) {
        return new Response(JSON.stringify([{ id: 1, title: 'Steps', created_at: '2026-01-01', updated_at: '2026-01-01' }]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/chat/sessions/1')) {
        return new Response(JSON.stringify({
          id: 1,
          title: 'Steps',
          created_at: '2026-01-01',
          updated_at: '2026-01-01',
          messages: [{ id: 7, role: 'assistant', tool_name: null, content: '1. First step\n2. Second step\n3. Third step', created_at: '2026-01-01' }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected fetch: ${url}`)
    }))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(renderWithChat(<ChatWidget />))
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click()
    })

    const list = container.querySelector('.chatBubble.assistant ol')
    expect(list).not.toBeNull()
    expect(Array.from(list!.querySelectorAll('li')).map((li) => li.textContent)).toEqual([
      'First step', 'Second step', 'Third step',
    ])
  })

  it('lists the configured models plus Auto, and sends the manually picked model with the message', async () => {
    const session = { id: 1, title: 'Models', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
    const sentBodies: Array<{ text: string; model?: string; attachment_ids?: number[] }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/chat/status')) {
        return new Response(JSON.stringify({
          enabled: true,
          ollama_running: true,
          ollama_last_error: null,
          ollama_last_success_at: null,
          chat_last_error: null,
          mcp_status: 'ok',
          model: 'gemma4:31b-cloud',
          available_models: ['gemma4:31b-cloud', 'minimax-m3:cloud', 'nemotron-3-nano:30b-cloud'],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions') && (!init || init.method === undefined)) {
        return new Response(JSON.stringify([session]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/chat/sessions/1/messages') && init?.method === 'POST') {
        sentBodies.push(JSON.parse(String(init.body)))
        const encoder = new TextEncoder()
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode('event: message\ndata: {"delta":"Hi"}\n\nevent: done\ndata: {"message_id":9}\n\n'))
            controller.close()
          },
        }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/chat/sessions/1')) {
        return new Response(JSON.stringify({ ...session, messages: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected fetch: ${url}`)
    }))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(renderWithChat(<ChatWidget />))
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click()
    })

    const modelSelect = container.querySelector<HTMLSelectElement>('[aria-label="Chat model"]')!
    expect(Array.from(modelSelect.options).map((option) => option.value)).toEqual([
      'auto', 'gemma4:31b-cloud', 'minimax-m3:cloud', 'nemotron-3-nano:30b-cloud',
    ])

    const selectSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      selectSetter.call(modelSelect, 'nemotron-3-nano:30b-cloud')
      modelSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const textarea = container.querySelector<HTMLTextAreaElement>('#chat-message')!
    const textareaSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      textareaSetter.call(textarea, 'Which model is this?')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const sendButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Send')
    await act(async () => {
      sendButton?.click()
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })

    // attachment_ids is always present now, empty from the widget, which has no
    // upload control by design.
    expect(sentBodies).toEqual([
      { text: 'Which model is this?', model: 'nemotron-3-nano:30b-cloud', attachment_ids: [] },
    ])
  })
})
