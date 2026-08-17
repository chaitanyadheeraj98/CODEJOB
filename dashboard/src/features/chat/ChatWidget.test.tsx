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
})
