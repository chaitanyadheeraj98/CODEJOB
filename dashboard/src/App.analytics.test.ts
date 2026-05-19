import { describe, expect, it } from 'vitest'

import { shouldTrackViewEvent } from './App'

describe('view analytics throttling', () => {
  it('allows first view event for a page and range key', () => {
    expect(shouldTrackViewEvent({}, 'needs_review:current_day', 1_000, 60_000)).toBe(true)
  })

  it('blocks duplicate view events within the throttle window for the same page and range', () => {
    expect(shouldTrackViewEvent({ 'needs_review:current_day': 1_000 }, 'needs_review:current_day', 30_000, 60_000)).toBe(
      false,
    )
  })

  it('allows the same page after the throttle window and keeps ranges independent', () => {
    expect(shouldTrackViewEvent({ 'needs_review:current_day': 1_000 }, 'needs_review:current_day', 61_000, 60_000)).toBe(
      true,
    )
    expect(shouldTrackViewEvent({ 'needs_review:current_day': 1_000 }, 'needs_review:last_1h', 30_000, 60_000)).toBe(
      true,
    )
  })
})
