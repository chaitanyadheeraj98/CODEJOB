// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManualIntakePage from './ManualIntakePage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const API = 'http://api.test'

const PASTE = `Job Title: Senior Full Stack Developer

Thanks & regards
T Mahesh royal
Email: mahesh@fusiongts.com`

function jsonResponse(payload: unknown, ok = true) {
  return { ok, status: ok ? 200 : 400, json: async () => payload } as unknown as Response
}

async function flush(times = 4): Promise<void> {
  for (let index = 0; index < times; index += 1) await Promise.resolve()
}

describe('ManualIntakePage', () => {
  const cleanups: Array<() => void> = []

  afterEach(() => {
    vi.useRealTimers()
    while (cleanups.length) cleanups.pop()?.()
    vi.restoreAllMocks()
  })

  const render = () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
    })
    act(() => root.render(<ManualIntakePage apiBase={API} />))
    return container
  }

  const textarea = (container: HTMLElement) =>
    container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Pasted requirement"]')!

  const button = (container: HTMLElement, label: string) =>
    Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(label))

  const type = (container: HTMLElement, value: string) => {
    const field = textarea(container)
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      'value',
    )!.set!
    act(() => {
      setter.call(field, value)
      field.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }

  it('will not submit an empty paste', () => {
    vi.stubGlobal('fetch', vi.fn())
    const container = render()
    expect(button(container, 'Create Card')?.disabled).toBe(true)
  })

  it('queues the paste and reports progress until the run finishes', async () => {
    const statuses = [
      { run_key: 'manual_intake:abc', status: 'running', detail: '' },
      {
        run_key: 'manual_intake:abc',
        status: 'ok',
        detail: 'Requirement added to Needs Review for mahesh@fusiongts.com.',
      },
    ]
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/manual-requirements/preview')) return jsonResponse({ duplicate_of: null })
      if (url.endsWith('/manual-requirements')) {
        return jsonResponse({ run_key: 'manual_intake:abc', job_id: 'j1', status: 'queued' })
      }
      return jsonResponse(statuses.shift() ?? statuses[0])
    })
    vi.stubGlobal('fetch', fetchMock)
    // Installed before the component mounts: the polling interval is created on
    // submit, and a real interval created first would never be advanced.
    vi.useFakeTimers()

    const container = render()
    type(container, PASTE)
    await act(async () => {
      button(container, 'Create Card')?.click()
      await flush(6)
    })

    // A run key, not a card: the work happens on a worker.
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/manual-requirements'))).toBe(true)
    expect(container.textContent).toContain('Reading the requirement')

    for (let tick = 0; tick < 2; tick += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1600)
        await flush(6)
      })
    }
    // The server's own detail, not a phrase the page also prints in its intro.
    expect(container.textContent).toContain('Requirement added to Needs Review for')
    expect(button(container, 'Paste Another')).toBeTruthy()
  })

  it('warns about a duplicate without blocking creation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/manual-requirements/preview')) {
          return jsonResponse({
            duplicate_of: {
              id: 42,
              role: 'Senior Full Stack Developer',
              client: 'TECH M',
              created_at: '2026-09-05T00:00:00Z',
            },
          })
        }
        return jsonResponse({ run_key: 'manual_intake:abc', job_id: 'j1', status: 'queued' })
      }),
    )
    vi.useFakeTimers()
    const container = render()
    type(container, PASTE)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700)
      await flush(6)
    })

    expect(container.textContent).toContain('already pasted this requirement')
    expect(container.textContent).toContain('Senior Full Stack Developer')
    expect(container.textContent).toContain('warning, not a block')
    // The point of the whole rule: the button stays live.
    expect(button(container, 'Create Card')?.disabled).toBe(false)
  })

  it('keeps the paste usable when the duplicate check itself fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).endsWith('/manual-requirements/preview')
          ? jsonResponse({ detail: 'boom' }, false)
          : jsonResponse({ run_key: 'k', job_id: 'j', status: 'queued' }),
      ),
    )
    vi.useFakeTimers()
    const container = render()
    type(container, PASTE)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700)
      await flush(6)
    })
    // Losing the warning is better than losing the requirement.
    expect(container.textContent).not.toContain('already pasted')
    expect(button(container, 'Create Card')?.disabled).toBe(false)
  })

  it('surfaces the server detail when queueing is refused', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).endsWith('/manual-requirements/preview')
          ? jsonResponse({ duplicate_of: null })
          : jsonResponse({ detail: { code: 'another_job_in_progress' } }, false),
      ),
    )
    const container = render()
    type(container, PASTE)
    await act(async () => {
      button(container, 'Create Card')?.click()
      await flush(6)
    })
    expect(container.textContent).toContain('another_job_in_progress')
    expect(button(container, 'Create Card')?.disabled).toBe(false)
  })

  it('refuses a paste over the character limit before sending it', () => {
    vi.stubGlobal('fetch', vi.fn())
    const container = render()
    type(container, 'x'.repeat(20001))
    expect(container.textContent).toContain('too long to accept')
    expect(button(container, 'Create Card')?.disabled).toBe(true)
  })
})
