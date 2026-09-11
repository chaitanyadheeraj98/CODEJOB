import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchAuthState, loginErrorMessage, startGoogleLogin } from './api'

const API = 'http://localhost:8000'

function respond(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchAuthState', () => {
  // The distinction that matters most. A 404 means the server has sign-in
  // switched off, and the app must run exactly as it did before sign-in
  // existed - not show a login wall nobody can get past.
  it('treats 404 as sign-in being disabled, not as signed out', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(404)))

    expect(await fetchAuthState(API)).toEqual({ status: 'disabled' })
  })

  it('treats 401 as signed out', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(401)))

    expect(await fetchAuthState(API)).toEqual({ status: 'anonymous' })
  })

  it('returns the user when signed in', async () => {
    const user = { email: 'a@example.com', display_name: 'A', is_admin: false, owner_id: 'usr_1' }
    vi.stubGlobal('fetch', vi.fn(async () => respond(200, user)))

    expect(await fetchAuthState(API)).toEqual({ status: 'signed_in', user })
  })

  // A restart would otherwise replace every page with a login screen.
  it('does not show a login wall when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('failed to fetch') }))

    expect(await fetchAuthState(API)).toEqual({ status: 'disabled' })
  })

  it('sends the cookie', async () => {
    const fetchMock = vi.fn(async () => respond(401))
    vi.stubGlobal('fetch', fetchMock)

    await fetchAuthState(API)

    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: 'include' })
  })
})

describe('startGoogleLogin', () => {
  it('returns the consent url', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(200, { authorization_url: 'https://accounts.google.com/x' })))

    expect(await startGoogleLogin(API)).toBe('https://accounts.google.com/x')
  })

  it('explains a 503 as a server configuration problem', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(503)))

    await expect(startGoogleLogin(API)).rejects.toThrow(/not configured/i)
  })
})

describe('loginErrorMessage', () => {
  it('is empty with no error', () => {
    expect(loginErrorMessage(null)).toBe('')
  })

  // The mismatch case is the one worth wording carefully: the user needs to
  // know nothing was saved, or they will assume the wrong mailbox is attached.
  it('says nothing was saved on an identity mismatch', () => {
    expect(loginErrorMessage('identity_unverified')).toMatch(/nothing was saved/i)
  })

  it('has wording for every reason the callback can redirect with', () => {
    for (const code of ['declined', 'no_code', 'state_mismatch', 'identity_unverified', 'not_permitted', 'unexpected']) {
      expect(loginErrorMessage(code)).not.toBe('')
    }
  })

  it('falls back rather than rendering an unknown code at the user', () => {
    expect(loginErrorMessage('something_new')).toBe(loginErrorMessage('unexpected'))
  })
})
