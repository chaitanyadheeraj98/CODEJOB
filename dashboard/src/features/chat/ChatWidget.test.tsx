// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ChatWidget from './ChatWidget'
import { consumeSseStream } from './api'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('ChatWidget', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    if (root) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
    vi.restoreAllMocks()
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
      root?.render(<ChatWidget apiBase="http://localhost:8000" />)
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant"]')?.click()
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
      root?.render(<ChatWidget apiBase="http://localhost:8000" />)
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant"]')?.click()
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Rename current chat"]')?.click()
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })

    expect(window.prompt).toHaveBeenCalledWith('Rename chat', 'Old title')
    const options = container.querySelectorAll('option')
    expect(Array.from(options).map((option) => option.textContent)).toContain('Gmail Integration Testing')
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
      response: { approved_count: 2, failed: [] },
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
      body: { body: 'Thanks', subject: 'Re: Role' },
      response: { sent: true, message_id: 'gmail-1' },
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
      root?.render(<ChatWidget apiBase="http://localhost:8000" />)
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant"]')?.click()
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
})
