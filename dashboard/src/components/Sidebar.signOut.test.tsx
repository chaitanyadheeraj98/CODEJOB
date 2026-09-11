// @vitest-environment jsdom
/**
 * The rail's account block: who is signed in, and the way out.
 *
 * Reported by the product owner - sign-in worked and there was no way to end
 * the session. The backend route, the API client and `signOutAndReload` all
 * existed; nothing rendered a control that called them.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { act } from 'react'
import Sidebar, { type SidebarProps } from './Sidebar'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const baseProps: SidebarProps = {
  running: false,
  queueCount: 0,
  failedCount: 0,
  runCount: 0,
  sentCount: 0,
  premiumCount: 0,
  resumeTrackingEnabled: false,
  activePage: 'run_queue',
  onNavigate: () => {},
}

let root: Root | null = null
let host: HTMLDivElement | null = null

function render(props: Partial<SidebarProps>) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root!.render(<Sidebar {...baseProps} {...props} />)
  })
  return host
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

const signOutButton = (el: HTMLElement) =>
  [...el.querySelectorAll('button')].find((b) => /sign(ing)? out/i.test(b.textContent ?? ''))

describe('Sidebar account block', () => {
  it('shows the signed-in address', () => {
    const el = render({ account: { email: 'someone@example.com', isAdmin: false } })
    expect(el.textContent).toContain('someone@example.com')
  })

  it('offers a sign out control', () => {
    const el = render({ account: { email: 'someone@example.com', isAdmin: false } })
    expect(signOutButton(el)).toBeTruthy()
  })

  it('calls back when sign out is clicked', () => {
    const onSignOut = vi.fn()
    const el = render({ account: { email: 'someone@example.com', isAdmin: false }, onSignOut })
    act(() => {
      signOutButton(el)!.click()
    })
    expect(onSignOut).toHaveBeenCalledTimes(1)
  })

  // Sign-in off means there is no session to end. The rail must look exactly
  // as it did before any of this existed.
  it('renders nothing about accounts when sign-in is disabled', () => {
    const el = render({})
    expect(signOutButton(el)).toBeUndefined()
    expect(el.querySelector('.accountBlock')).toBeNull()
  })

  it('marks an admin, and does not mark anyone else', () => {
    const admin = render({ account: { email: 'boss@example.com', isAdmin: true } })
    expect(admin.textContent).toContain('Admin')
    act(() => root?.unmount())
    host?.remove()

    const member = render({ account: { email: 'member@example.com', isAdmin: false } })
    expect(member.textContent).not.toContain('Admin')
  })

  it('disables the button while signing out, so a second click cannot fire', () => {
    const onSignOut = vi.fn()
    const el = render({
      account: { email: 'someone@example.com', isAdmin: false },
      onSignOut,
      signingOut: true,
    })
    const button = signOutButton(el)!
    expect(button.disabled).toBe(true)
    expect(button.textContent).toMatch(/signing out/i)
    act(() => button.click())
    expect(onSignOut).not.toHaveBeenCalled()
  })

  it('keeps the full address available when it is too long to show', () => {
    const email = 'a-very-long-address-that-will-not-fit@some-long-domain.example.com'
    const el = render({ account: { email, isAdmin: false } })
    expect(el.querySelector('.accountEmail')?.getAttribute('title')).toBe(email)
  })
})
