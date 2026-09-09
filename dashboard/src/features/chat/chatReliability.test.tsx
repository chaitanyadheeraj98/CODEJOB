// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'

import AssistantPage from './AssistantPage'
import ChatProvider from './ChatProvider'
import ChatWidget from './ChatWidget'

// jsdom's Response cannot hold an SSE stream open - it ends the moment the
// handler yields, which reaches the client as a dropped connection. So the one
// test that needs a turn still running drives the callback directly and leaves
// every other test on the real implementation.
let sendOverride: ((...args: never[]) => Promise<void>) | null = null
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    sendChatMessage: (...args: never[]) => (sendOverride ?? actual.sendChatMessage)(...args),
  }
})

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

it.each(['page', 'widget'])('retries the same failed message on the %s and clears the error', async (surface) => {
  const sent: string[] = []
  const session = { id: 1, title: 'Chat', created_at: '2026-01-01', updated_at: '2026-01-01' }
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/status')) return json({ enabled: true, ollama_running: true, available_models: [], model: 'test' })
    if (url.endsWith('/attachments')) return json([])
    if (url.endsWith('/sessions')) return json([session])
    if (url.endsWith('/sessions/1')) return json({ ...session, messages: [] })
    if (url.endsWith('/messages')) {
      sent.push(JSON.parse(String(init?.body)).text)
      const body = sent.length === 1
        ? 'event: error\ndata: {"code":"ollama_rate_limited","message":"Try again in a moment."}\n\nevent: done\ndata: {"message_id":2}\n\n'
        : 'event: message\ndata: {"delta":"Done"}\n\nevent: done\ndata: {"message_id":3}\n\n'
      return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
    }
    throw new Error(url)
  }))
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const button = (name: string) => Array.from(container.querySelectorAll('button')).find((item) => item.textContent === name)
  try {
    await act(async () => {
      root.render(<ChatProvider apiBase="http://test">{surface === 'page' ? <AssistantPage /> : <ChatWidget />}</ChatProvider>)
    })
    if (surface === 'widget') await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click())
    expect(button('Retry')).toBeUndefined()
    const input = container.querySelector('textarea')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Who replied?')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => input.form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(button('Retry')).toBeDefined()
    await act(async () => button('Retry')!.click())
    expect(sent).toEqual(['Who replied?', 'Who replied?'])
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(button('Retry')).toBeUndefined()
  } finally {
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  }
})

it('names the answering model only where the turn failed over', async () => {
  const session = { id: 1, title: 'Chat', created_at: '2026-01-01', updated_at: '2026-01-01' }
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/status')) return json({ enabled: true, ollama_running: true, available_models: [], model: 'test' })
    if (url.endsWith('/attachments')) return json([])
    if (url.endsWith('/sessions')) return json([session])
    if (url.endsWith('/sessions/1')) {
      return json({ ...session, messages: [
        { id: 2, role: 'assistant', content: 'First', tool_name: null, created_at: '2026-01-01', answered_by: null },
        { id: 3, role: 'assistant', content: 'Second', tool_name: null, created_at: '2026-01-01', answered_by: 'minimax-m3:cloud' },
      ] })
    }
    throw new Error(url)
  }))
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  try {
    await act(async () => {
      root.render(<ChatProvider apiBase="http://test"><AssistantPage /></ChatProvider>)
    })
    const notes = Array.from(container.querySelectorAll('.chatAnsweredBy'))
    expect(notes).toHaveLength(1)
    expect(notes[0].textContent).toContain('minimax-m3:cloud')
  } finally {
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  }
})

it.each(['page', 'widget'])('stops a running turn from the %s and keeps the partial answer', async (surface) => {
  const session = { id: 1, title: 'Chat', created_at: '2026-01-01', updated_at: '2026-01-01' }
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  const deleted: string[] = []
  let finished = false
  let releaseTurn: () => void = () => {}
  const turnEnded = new Promise<void>((resolve) => { releaseTurn = resolve })
  // Emits the events a real turn emits, in order, and then waits - so the Stop
  // button is under test while the turn is genuinely still running.
  sendOverride = (async (
    _apiBase: string, _sessionId: number, _text: string,
    onEvent: (event: { event: string; data: Record<string, unknown> }) => void,
  ) => {
    onEvent({ event: 'start', data: { message_id: 2, turn_id: 't-1' } })
    onEvent({ event: 'message', data: { delta: 'Partial' } })
    await turnEnded
    onEvent({ event: 'done', data: { message_id: 2, cancelled: true } })
  }) as never
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/status')) return json({ enabled: true, ollama_running: true, available_models: [], model: 'test' })
    if (url.endsWith('/attachments')) return json([])
    if (url.endsWith('/sessions')) return json([session])
    if (url.includes('/turns/')) {
      deleted.push(url)
      // The server writes the partial transcript and then ends the stream. Doing
      // it in the DELETE keeps this test in the real order.
      finished = true
      releaseTurn()
      return json({ cancelled: true })
    }
    if (url.startsWith('http://test/chat/sessions/1')) {
      return json({ ...session, messages: finished
        ? [{ id: 2, role: 'assistant', content: 'Partial', tool_name: null, created_at: '2026-01-01' },
           { id: 3, role: 'event', content: '[System: the user stopped the assistant turn.]', tool_name: null, created_at: '2026-01-01' }]
        : [] })
    }
    throw new Error(url)
  }))
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const button = (name: string) => Array.from(container.querySelectorAll('button')).find((item) => item.textContent === name)
  const settle = async (times = 4) => {
    for (let i = 0; i < times; i += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
  }
  try {
    await act(async () => {
      root.render(<ChatProvider apiBase="http://test">{surface === 'page' ? <AssistantPage /> : <ChatWidget />}</ChatProvider>)
    })
    if (surface === 'widget') await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Open CodeJob assistant chat"]')?.click())
    expect(button('Stop')).toBeUndefined()
    const input = container.querySelector('textarea')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Long question')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    // Not awaited: awaiting the submit would wait on the turn this interrupts.
    input.form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()
    expect(button('Stop')).toBeDefined()
    await act(async () => button('Stop')!.click())
    await settle()
    expect(deleted).toEqual(['http://test/chat/sessions/1/turns/t-1'])
    // Stop must not clear the thread: the server already wrote this much.
    expect(container.textContent).toContain('Partial')
    expect(button('Stop')).toBeUndefined()
    expect(button('Send')).toBeDefined()
    expect(container.querySelector('[role="alert"]')).toBeNull()
  } finally {
    releaseTurn()
    await settle(2)
    sendOverride = null
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  }
})

it('keeps each finished tool on screen with what it did and how long it took', async () => {
  const session = { id: 1, title: 'Chat', created_at: '2026-01-01', updated_at: '2026-01-01' }
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/status')) return json({ enabled: true, ollama_running: true, available_models: [], model: 'test' })
    if (url.endsWith('/attachments')) return json([])
    if (url.endsWith('/sessions')) return json([session])
    if (url.startsWith('http://test/chat/sessions/1')) return json({ ...session, messages: [] })
    throw new Error(url)
  }))
  let releaseTurn: () => void = () => {}
  const turnEnded = new Promise<void>((resolve) => { releaseTurn = resolve })
  sendOverride = (async (
    _apiBase: string, _sessionId: number, _text: string,
    onEvent: (event: { event: string; data: Record<string, unknown> }) => void,
  ) => {
    onEvent({ event: 'start', data: { message_id: 2, turn_id: 't-1' } })
    onEvent({ event: 'tool', data: { name: 'search_candidates' } })
    onEvent({ event: 'tool_done', data: { name: 'search_candidates', duration_ms: 1500, status: 'success' } })
    onEvent({ event: 'tool', data: { name: 'get_metrics' } })
    onEvent({ event: 'tool_done', data: { name: 'get_metrics', duration_ms: 400, status: 'error' } })
    await turnEnded
    onEvent({ event: 'done', data: { message_id: 2 } })
  }) as never
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const settle = async (times = 4) => {
    for (let i = 0; i < times; i += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
  }
  try {
    await act(async () => {
      root.render(<ChatProvider apiBase="http://test"><AssistantPage /></ChatProvider>)
    })
    const input = container.querySelector('textarea')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'How many?')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    input.form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()
    const done = Array.from(container.querySelectorAll('.toolProgressDone li')).map((node) => node.textContent)
    // Both steps stay listed, in the order they ran, with the failure named.
    expect(done).toHaveLength(2)
    expect(done[0]).toContain('Searching your candidates')
    expect(done[0]).toContain('1.5s')
    expect(done[1]).toContain('failed')
    expect(done[1]).toContain('0.4s')
  } finally {
    releaseTurn()
    await settle(2)
    sendOverride = null
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  }
})
