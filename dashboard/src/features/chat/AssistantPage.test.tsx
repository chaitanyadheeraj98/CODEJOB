// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AssistantPage from './AssistantPage'
import ChatProvider from './ChatProvider'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

const session = { id: 1, title: 'Java roles', created_at: '2026-01-01T00:00:00Z', updated_at: new Date().toISOString() }

const statusBody = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  ollama_running: true,
  ollama_last_error: null,
  ollama_last_success_at: null,
  chat_last_error: null,
  mcp_status: 'ok',
  model: 'gemma4:31b-cloud',
  available_models: ['gemma4:31b-cloud'],
  ...overrides,
})

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

describe('AssistantPage', () => {
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

  const mount = async (options: {
    status?: Record<string, unknown>
    messages?: Array<Record<string, unknown>>
  } = {}) => {
    const sent: Array<{ text: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/chat/status')) return json(statusBody(options.status))
      if (url.endsWith('/chat/sessions/1/messages') && init?.method === 'POST') {
        sent.push(JSON.parse(String(init.body)))
        const encoder = new TextEncoder()
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode('event: message\ndata: {"delta":"Hi"}\n\nevent: done\ndata: {"message_id":9}\n\n'))
            controller.close()
          },
        }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/chat/sessions')) return json([session])
      if (url.endsWith('/chat/sessions/1')) return json({ ...session, messages: options.messages ?? [] })
      throw new Error(`Unexpected fetch: ${url}`)
    }))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root?.render(
        <ChatProvider apiBase="http://localhost:8000">
          <AssistantPage />
        </ChatProvider>,
      )
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    return sent
  }

  const composer = () => container!.querySelector<HTMLTextAreaElement>('#assistant-message')!

  const type = async (value: string) => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(composer(), value)
      composer().dispatchEvent(new Event('input', { bubbles: true }))
    })
  }

  it('renders the conversation list and thread', async () => {
    await mount({
      messages: [
        { id: 1, role: 'user', content: 'Any replies today?', tool_name: null, created_at: '2026-01-01T00:00:00Z' },
        { id: 2, role: 'assistant', content: 'Two recruiters replied.', tool_name: null, created_at: '2026-01-01T00:00:01Z' },
      ],
    })

    expect(container?.textContent).toContain('Java roles')
    expect(container?.textContent).toContain('Any replies today?')
    expect(container?.textContent).toContain('Two recruiters replied.')
  })

  it('offers starter prompts on an empty thread and loads one into the composer', async () => {
    await mount()

    const starter = Array.from(container!.querySelectorAll<HTMLButtonElement>('.assistantStarters button'))
      .find((button) => button.textContent?.includes('Needs Review'))
    expect(starter).toBeTruthy()

    await act(async () => starter?.click())
    expect(composer().value).toContain('Needs Review')
  })

  it('sends on Enter and inserts a newline on Shift+Enter', async () => {
    const sent = await mount()

    await type('Who replied?')
    await act(async () => {
      composer().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }))
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    expect(sent).toHaveLength(0)

    await act(async () => {
      composer().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
    expect(sent.map((body) => body.text)).toEqual(['Who replied?'])
  })

  // The sidebar entry stays visible when chat is off, so the page has to explain
  // itself rather than render an empty shell.
  it('explains a disabled backend instead of rendering an empty workspace', async () => {
    await mount({ status: { enabled: false, ollama_running: false } })

    expect(container?.textContent).toContain('Chat is disabled')
    expect(container?.textContent).toContain('FEATURE_CHAT_ENABLED=true')
    expect(container?.querySelector('#assistant-message')).toBeNull()
  })

  it('explains a disconnected Ollama and offers a retry', async () => {
    await mount({ status: { ollama_running: false } })

    expect(container?.textContent).toContain('Ollama is not connected')
    const retry = Array.from(container!.querySelectorAll('button')).find((button) => button.textContent === 'Check again')
    expect(retry).toBeTruthy()
  })

  it('renames the current conversation inline', async () => {
    const renames: string[] = []
    await mount()
    const originalFetch = globalThis.fetch as typeof fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        renames.push((JSON.parse(String(init.body)) as { title: string }).title)
        return json({ ...session, title: 'Backend roles' })
      }
      return originalFetch(input, init)
    }))

    const title = container!.querySelector<HTMLButtonElement>('.assistantTitle')!
    await act(async () => title.click())

    const input = container!.querySelector<HTMLInputElement>('.assistantTitleInput')!
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(input, 'Backend roles')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      for (let tick = 0; tick < 3; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })

    expect(renames).toEqual(['Backend roles'])
  })
})
