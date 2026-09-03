// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AssistantPage from './AssistantPage'
import ChatProvider from './ChatProvider'
import { formatBytes } from './attachmentDisplay'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

const session = { id: 1, title: 'Java roles', created_at: '2026-01-01T00:00:00Z', updated_at: new Date().toISOString() }

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const attachment = (overrides: Record<string, unknown> = {}) => ({
  id: 12,
  session_id: 1,
  message_id: null,
  file_name: 'backend-jd.pdf',
  mime_type: 'application/pdf',
  byte_size: 2048,
  extraction_error: null,
  created_at: '2026-01-01T00:00:00Z',
  ...overrides,
})

describe('formatBytes', () => {
  it.each([
    [512, '512 B'],
    [2048, '2 KB'],
    [5 * 1024 * 1024, '5.0 MB'],
  ])('renders %i as %s', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected)
  })
})

describe('Assistant attachments', () => {
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
    upload?: () => Response
    attachments?: Array<Record<string, unknown>>
    messages?: Array<Record<string, unknown>>
  } = {}) => {
    const sent: Array<{ text: string; attachment_ids: number[] }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname
      if (path === '/chat/status') {
        return json({
          enabled: true, ollama_running: true, ollama_last_error: null, ollama_last_success_at: null,
          chat_last_error: null, mcp_status: 'ok', model: 'gemma4:31b-cloud', available_models: ['gemma4:31b-cloud'],
        })
      }
      if (path === '/chat/sessions/1/attachments' && init?.method === 'POST') {
        return options.upload ? options.upload() : json(attachment(), 201)
      }
      if (path === '/chat/sessions/1/attachments') return json(options.attachments ?? [])
      if (path === '/chat/sessions/1/messages' && init?.method === 'POST') {
        sent.push(JSON.parse(String(init.body)))
        const encoder = new TextEncoder()
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode('event: message\ndata: {"delta":"Hi"}\n\nevent: done\ndata: {"message_id":9}\n\n'))
            controller.close()
          },
        }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (path === '/chat/sessions') return json([session])
      if (path === '/chat/sessions/1') return json({ ...session, messages: options.messages ?? [] })
      throw new Error(`Unexpected fetch: ${path}`)
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

  const drop = async (files: File[]) => {
    const form = container!.querySelector('form')!
    const dataTransfer = { files } as unknown as DataTransfer
    await act(async () => {
      const event = new Event('drop', { bubbles: true }) as Event & { dataTransfer?: DataTransfer }
      event.dataTransfer = dataTransfer
      form.dispatchEvent(event)
      for (let tick = 0; tick < 4; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
  }

  const typeAndSend = async () => {
    const composer = container!.querySelector<HTMLTextAreaElement>('#assistant-message')!
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(composer, 'What does this need?')
      composer.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      composer.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      for (let tick = 0; tick < 5; tick += 1) await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
  }

  it('uploads a dropped file and sends its id with the message', async () => {
    const sent = await mount()

    await drop([new File(['jd'], 'backend-jd.pdf', { type: 'application/pdf' })])
    expect(container?.textContent).toContain('backend-jd.pdf')

    await typeAndSend()
    expect(sent).toHaveLength(1)
    expect(sent[0].attachment_ids).toEqual([12])
    // The note naming the id is added server-side; the client sends its own text.
    expect(sent[0].text).toBe('What does this need?')
  })

  it('shows an upload failure on the chip and sends nothing for it', async () => {
    const sent = await mount({ upload: () => json({ detail: 'Attachments are limited to 10 MB' }, 413) })

    await drop([new File(['x'], 'huge.pdf', { type: 'application/pdf' })])
    expect(container?.textContent).toContain('Attachments are limited to 10 MB')

    await typeAndSend()
    expect(sent[0].attachment_ids).toEqual([])
  })

  // A file that cannot be parsed is still a file the user attached. Refusing it
  // would lose it; saying nothing would let them think the assistant read it.
  it('warns when a file uploaded but could not be read', async () => {
    await mount({ upload: () => json(attachment({ extraction_error: 'parser exploded' }), 201) })

    await drop([new File(['x'], 'scanned.pdf', { type: 'application/pdf' })])

    expect(container?.textContent).toContain('the text could not be read')
  })

  it('removes a pending attachment before it is sent', async () => {
    const sent = await mount()
    await drop([new File(['jd'], 'backend-jd.pdf', { type: 'application/pdf' })])

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('[aria-label="Remove backend-jd.pdf"]')?.click()
    })
    expect(container?.textContent).not.toContain('backend-jd.pdf')

    await typeAndSend()
    expect(sent[0].attachment_ids).toEqual([])
  })

  it('renders a download link on the message that carried the file', async () => {
    await mount({
      messages: [{ id: 7, role: 'user', content: 'Look at this', tool_name: null, created_at: '2026-01-01T00:00:00Z' }],
      attachments: [attachment({ message_id: 7 })],
    })

    const link = container!.querySelector<HTMLAnchorElement>('.attachmentChip a')
    expect(link?.getAttribute('href')).toBe('http://localhost:8000/chat/attachments/12/download')
    expect(link?.textContent).toBe('backend-jd.pdf')
  })
})
