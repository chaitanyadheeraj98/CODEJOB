import { describe, expect, it } from 'vitest'

import { gmailStatusLabel } from './App'

describe('gmailStatusLabel', () => {
  // The bug: a refreshable credential is a working connection, but the card
  // read "Not authenticated" whenever the access token was over an hour old —
  // which is most of the time — and people read that as "reconnect me".
  it('calls a refreshable credential connected', () => {
    expect(gmailStatusLabel({ state: 'connected_refreshable', authenticated: true })).toBe('Connected')
  })

  it('calls a fresh credential connected', () => {
    expect(gmailStatusLabel({ state: 'connected', authenticated: true })).toBe('Connected')
  })

  // "Not connected" and "Reconnect needed" are different jobs for the user:
  // one is first-time setup, the other is something that broke.
  it('distinguishes never-connected from needing a reconnect', () => {
    expect(gmailStatusLabel({ state: 'not_connected', authenticated: false })).toBe('Not connected')
    expect(gmailStatusLabel({ state: 'needs_reconnect', authenticated: false })).toBe('Reconnect needed')
  })

  it('reports a missing configuration as its own state', () => {
    expect(gmailStatusLabel({ state: 'not_configured', authenticated: false })).toBe('Not configured')
  })

  // An older backend, or one mid-deploy, sends no `state` at all.
  it('falls back to the boolean when the backend sends no state', () => {
    expect(gmailStatusLabel({ authenticated: true })).toBe('Connected')
    expect(gmailStatusLabel({ authenticated: false })).toBe('Not connected')
  })

  it('is Unknown before the first status response arrives', () => {
    expect(gmailStatusLabel(null)).toBe('Unknown')
    expect(gmailStatusLabel(undefined)).toBe('Unknown')
  })

  it('ignores a state it does not recognise rather than rendering it raw', () => {
    expect(gmailStatusLabel({ state: 'something_new', authenticated: true })).toBe('Connected')
  })
})
