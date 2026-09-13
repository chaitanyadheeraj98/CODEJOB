// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AccountControls from './AccountControls'
import { consumeDeletionIntent, rememberDeletionIntent } from './deletionIntent'

const EMAIL = 'owner@example.com'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement | null = null

// Selected by label rather than by position. There are two buttons now, and a
// positional selector would silently start testing the other one.
function button(text: RegExp): HTMLButtonElement {
  const found = Array.from(host!.querySelectorAll('button'))
    .find((element) => text.test(element.textContent ?? ''))
  if (!found) throw new Error(`no button matching ${text}`)
  return found as HTMLButtonElement
}

function render(onDeactivated = vi.fn()) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(
    <AccountControls apiBase="http://api" accountEmail={EMAIL} onDeactivated={onDeactivated} />,
  ))
  return { onDeactivated }
}

function typeEmail(value: string) {
  const input = host!.querySelector('#confirm-delete-email') as HTMLInputElement
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
  return input
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
  window.sessionStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('AccountControls', () => {
  it('states the credential removal and 30 day recovery window', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render()
    expect(host?.textContent).toMatch(/credentials now/i)
    expect(host?.textContent).toMatch(/30 days/i)
  })

  it('requires confirmation and sends the signed-in request', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const { onDeactivated } = render()

    await act(async () => button(/deactivate account/i).click())

    expect(fetchMock).toHaveBeenCalledWith('http://api/account/deactivate', {
      method: 'POST', credentials: 'include',
    })
    expect(onDeactivated).toHaveBeenCalledOnce()
  })

  it('downloads the export without confirmation and without signing the user out', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const fetchMock = vi.fn(async () => new Response('{"sections":{}}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => {} })
    const clicks: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(this.download)
    })
    const { onDeactivated } = render()

    await act(async () => button(/export my data/i).click())

    expect(fetchMock).toHaveBeenCalledWith('http://api/account/export', {
      method: 'POST', credentials: 'include',
    })
    // Reading your own data is not a destructive act, so it does not ask, and
    // it must not end the session the way deactivation does.
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(onDeactivated).not.toHaveBeenCalled()
    expect(clicks[0]).toMatch(/^codejob-export-\d{4}-\d{2}-\d{2}\.json$/)
  })

  it('surfaces an export failure and leaves the buttons usable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 500 })))
    render()

    await act(async () => button(/export my data/i).click())

    expect(host?.querySelector('[role="alert"]')?.textContent).toMatch(/unable to export/i)
    expect(button(/export my data/i).disabled).toBe(false)
  })

  it('sends the typed confirmation and finishes when the server accepts', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => (
      new Response('{"purge_after":"2026-10-12T00:00:00Z"}', { status: 200 })
    ))
    vi.stubGlobal('fetch', fetchMock)
    const { onDeactivated } = render()

    await act(async () => button(/^delete account$/i).click())
    act(() => { typeEmail(EMAIL) })
    await act(async () => button(/permanently delete/i).click())

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(url).toBe('http://api/account')
    expect(init).toMatchObject({ method: 'DELETE', credentials: 'include' })
    expect(JSON.parse(init!.body as string)).toEqual({ confirm_email: EMAIL })
    expect(onDeactivated).toHaveBeenCalledOnce()
  })

  it('remembers the intent and re-authenticates when the server demands it', async () => {
    const fetchMock = vi.fn(async (url: string) => (
      url.includes('/auth/google/start')
        ? new Response('{"authorization_url":"https://accounts.google.example/x"}', { status: 200 })
        : new Response('{"detail":"reauthentication_required"}', { status: 401 })
    ))
    vi.stubGlobal('fetch', fetchMock)
    const assigned: string[] = []
    vi.stubGlobal('location', { get href() { return '' }, set href(value: string) { assigned.push(value) }, pathname: '/' })
    render()

    await act(async () => button(/^delete account$/i).click())
    act(() => { typeEmail(EMAIL) })
    await act(async () => button(/permanently delete/i).click())

    // reauth=true, or Google accepts the session already in the browser and
    // the round trip proves nothing about who is at the keyboard.
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/auth/google/start?reauth=true'))).toBe(true)
    expect(assigned).toEqual(['https://accounts.google.example/x'])
    expect(consumeDeletionIntent()).toBe(true)
  })

  it('reopens the confirmation screen on the way back, without restoring what was typed', () => {
    rememberDeletionIntent()
    render()
    expect(host?.querySelector('#confirm-delete-email')).not.toBeNull()
    expect((host?.querySelector('#confirm-delete-email') as HTMLInputElement).value).toBe('')
  })

  it('cancelling clears the intent so it cannot resurface later', async () => {
    rememberDeletionIntent()
    render()
    await act(async () => button(/^cancel$/i).click())
    expect(host?.querySelector('#confirm-delete-email')).toBeNull()
    expect(consumeDeletionIntent()).toBe(false)
  })

  it('explains a rejected confirmation instead of silently doing nothing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"nope"}', { status: 400 })))
    render()
    await act(async () => button(/^delete account$/i).click())
    act(() => { typeEmail('someone.else@example.com') })
    await act(async () => button(/permanently delete/i).click())
    expect(host?.querySelector('[role="alert"]')?.textContent).toMatch(/did not match/i)
  })

  it('grades the three zones by how hard the action is to undo', () => {
    // The information architecture, not the pixels. Three identical rows would
    // make "export" and "delete for ever" look like the same kind of decision.
    render()
    const zones = Array.from(host!.querySelectorAll('.accountSection'))
    expect(zones).toHaveLength(3)
    expect(zones[1].className).toMatch(/accountSection--caution/)
    expect(zones[2].className).toMatch(/accountSection--refusal/)
    expect(zones.map((z) => z.querySelector('h3')?.textContent)).toEqual([
      'Export your data', 'Deactivate', 'Delete permanently',
    ])
  })

  it('gives the final irreversible click more weight than the one that opens it', async () => {
    render()
    expect(button(/^delete account$/i).className).toBe('dangerButton')
    await act(async () => button(/^delete account$/i).click())
    expect(button(/permanently delete/i).className).toMatch(/dangerButtonSolid/)
  })

  it('offers the export above deactivation, since deactivating revokes the session it needs', () => {
    render()
    const labels = Array.from(host!.querySelectorAll('button')).map((b) => b.textContent ?? '')
    expect(labels[0]).toMatch(/export my data/i)
    expect(labels[1]).toMatch(/deactivate account/i)
  })
})
