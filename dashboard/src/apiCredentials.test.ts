import { describe, it, expect, vi } from 'vitest'
import { installApiCredentials } from './apiCredentials'

const API = 'http://localhost:8000'
const PAGE = 'http://localhost:5173/'

type Call = [unknown, RequestInit | undefined]

function harness(base = API, href = PAGE) {
  const calls: Call[] = []
  const original = vi.fn((input: unknown, init?: RequestInit) => {
    calls.push([input, init])
    return Promise.resolve(new Response('{}'))
  })
  const target = { fetch: original as unknown as typeof fetch, location: { href } }
  const restore = installApiCredentials(target as never, base)
  return { calls, target, restore, original }
}

describe('installApiCredentials', () => {
  it('sends credentials to the API on another origin', async () => {
    const { calls, target } = harness()
    await target.fetch(`${API}/candidates?limit=1`)
    expect(calls[0][1]?.credentials).toBe('include')
  })

  it('keeps the caller method and body intact', async () => {
    const { calls, target } = harness()
    await target.fetch(`${API}/inbox/conversations/1/read`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{"read":true}',
    })
    const init = calls[0][1]
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe('{"read":true}')
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(init?.credentials).toBe('include')
  })

  // The whole point: cookies must not leak to anyone but our own API.
  it('leaves a third-party origin untouched', async () => {
    const { calls, target } = harness()
    await target.fetch('https://example.com/webhook')
    expect(calls[0][1]?.credentials).toBeUndefined()
  })

  it('does not override a caller that set credentials itself', async () => {
    const { calls, target } = harness()
    await target.fetch(`${API}/auth/me`, { credentials: 'omit' })
    expect(calls[0][1]?.credentials).toBe('omit')
  })

  it('covers a relative URL resolved against a same-origin API base', async () => {
    // Reverse-proxy deployment: base is a path, so requests are same-origin
    // and the browser already sends cookies. Nothing should be rewritten.
    const { calls, target } = harness('/api', PAGE)
    await target.fetch('/api/candidates')
    expect(calls[0][1]?.credentials).toBe('include')
  })

  it('passes a Request object through, whose credentials are already fixed', async () => {
    const { calls, target } = harness()
    const request = new Request(`${API}/candidates`)
    await target.fetch(request)
    expect(calls[0][0]).toBe(request)
    expect(calls[0][1]).toBeUndefined()
  })

  it('installs once, so a repeated call cannot double-wrap', async () => {
    const { target } = harness()
    const wrapped = target.fetch
    installApiCredentials(target as never, API)
    expect(target.fetch).toBe(wrapped)
  })

  it('restores the original fetch', async () => {
    const { target, restore, original } = harness()
    restore()
    expect(target.fetch).toBe(original)
  })

  it('accepts a URL instance', async () => {
    const { calls, target } = harness()
    await target.fetch(new URL(`${API}/labels/overview`))
    expect(calls[0][1]?.credentials).toBe('include')
  })
})
