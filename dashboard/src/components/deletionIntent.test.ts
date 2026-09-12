// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'

import {
  INTENT_TTL_MS,
  consumeDeletionIntent,
  forgetDeletionIntent,
  rememberDeletionIntent,
} from './deletionIntent'

afterEach(() => {
  window.sessionStorage.clear()
})

describe('deletionIntent', () => {
  it('survives the round trip to Google and back', () => {
    rememberDeletionIntent()
    expect(consumeDeletionIntent()).toBe(true)
  })

  it('is single use, so a stale intent cannot reopen the screen on every visit', () => {
    rememberDeletionIntent()
    expect(consumeDeletionIntent()).toBe(true)
    expect(consumeDeletionIntent()).toBe(false)
  })

  it('expires with the server re-authentication window', () => {
    const now = Date.now()
    rememberDeletionIntent(now)
    expect(consumeDeletionIntent(now + INTENT_TTL_MS + 1)).toBe(false)
  })

  it('is still valid just inside the window', () => {
    const now = Date.now()
    rememberDeletionIntent(now)
    expect(consumeDeletionIntent(now + INTENT_TTL_MS - 1000)).toBe(true)
  })

  it('can be cancelled outright', () => {
    rememberDeletionIntent()
    forgetDeletionIntent()
    expect(consumeDeletionIntent()).toBe(false)
  })

  it('reports nothing when none was stored', () => {
    expect(consumeDeletionIntent()).toBe(false)
  })

  it('treats corrupted storage as no intent rather than throwing', () => {
    window.sessionStorage.setItem('codejob.deletionIntent', 'not json')
    expect(consumeDeletionIntent()).toBe(false)
  })

  it('stores no email, token or anything else that could carry authority', () => {
    rememberDeletionIntent()
    const raw = window.sessionStorage.getItem('codejob.deletionIntent') ?? ''
    expect(raw).not.toMatch(/@/)
    expect(JSON.parse(raw)).toEqual({ expiresAt: expect.any(Number) })
  })
})
