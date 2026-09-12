// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AccountControls from './AccountControls'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement | null = null

function render(onDeactivated = vi.fn()) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(<AccountControls apiBase="http://api" onDeactivated={onDeactivated} />))
  return { button: host.querySelector('button')!, onDeactivated }
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
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
    const { button, onDeactivated } = render()

    await act(async () => button.click())

    expect(fetchMock).toHaveBeenCalledWith('http://api/account/deactivate', {
      method: 'POST', credentials: 'include',
    })
    expect(onDeactivated).toHaveBeenCalledOnce()
  })
})
