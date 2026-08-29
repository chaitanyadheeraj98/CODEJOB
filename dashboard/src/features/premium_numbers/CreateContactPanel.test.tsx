// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CreateContactPanel from './CreateContactPanel'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setValue(input: HTMLInputElement, value: string) {
  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
  nativeSetter.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('CreateContactPanel', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    vi.restoreAllMocks()
    while (cleanups.length) cleanups.pop()?.()
  })

  function mount(onCreated = vi.fn(), onClose = vi.fn()) {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root: Root = createRoot(container)
    cleanups.push(() => {
      act(() => root.unmount())
      container.remove()
      vi.unstubAllGlobals()
    })
    act(() => {
      root.render(<CreateContactPanel apiBase="http://localhost:8000" onClose={onClose} onCreated={onCreated} />)
    })
    return { container, onCreated, onClose }
  }

  it('blocks submit and shows an error when both phone and email are missing', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { container } = mount()

    act(() => setValue(container.querySelector('input')!, 'Jamie Recruiter'))
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(container.textContent).toContain('Provide a phone number, an email address, or both.')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('submits an email-only contact and reports the resulting status to the caller', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({
        name: 'Jamie Recruiter', title: '', company: '', email: 'jamie@example.com', phone: '', role: 'recruiter',
      })
      return jsonResponse({ id: 9, created: true, status: 'created', review_id: null, phone_display: '', role: 'recruiter' })
    })
    vi.stubGlobal('fetch', fetchMock)
    const onCreated = vi.fn()
    const { container } = mount(onCreated)

    const inputs = Array.from(container.querySelectorAll('input'))
    const nameInput = inputs.find((input) => input.closest('label')?.textContent?.startsWith('Name'))
    const emailInput = container.querySelector<HTMLInputElement>('input[type="email"]')!
    act(() => {
      setValue(nameInput ?? inputs[0], 'Jamie Recruiter')
      setValue(emailInput, 'jamie@example.com')
    })

    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(onCreated).toHaveBeenCalledWith({ id: 9, created: true, status: 'created', review_id: null, phone_display: '', role: 'recruiter' })
  })
})
