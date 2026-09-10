import { describe, expect, it } from 'vitest'

import { createRequestSequence } from './latestRequest'

describe('createRequestSequence', () => {
  // The Reply Inbox bug this exists for: the mount load asks for every
  // conversation and is slow, the filter load asks for 8 and is fast, and the
  // slow one landed second and won.
  it('lets a slow earlier request lose to a newer one', () => {
    const seq = createRequestSequence()

    const mountLoad = seq.next()
    const filterLoad = seq.next()

    expect(seq.isCurrent(filterLoad)).toBe(true)
    expect(seq.isCurrent(mountLoad)).toBe(false)
  })

  it('keeps the newest ticket current no matter the arrival order', () => {
    const seq = createRequestSequence()
    const tickets = [seq.next(), seq.next(), seq.next()]

    for (const ticket of [...tickets].reverse()) {
      expect(seq.isCurrent(ticket)).toBe(ticket === tickets[2])
    }
  })

  it('is current for a lone request', () => {
    const seq = createRequestSequence()
    expect(seq.isCurrent(seq.next())).toBe(true)
  })

  // Two independent lists must not share a sequence, or one steals the other's
  // right to render.
  it('gives each sequence its own counter', () => {
    const inbox = createRequestSequence()
    const other = createRequestSequence()

    const inboxTicket = inbox.next()
    other.next()

    expect(inbox.isCurrent(inboxTicket)).toBe(true)
  })

  // A ticket nobody issued is never current: guards default to discarding.
  it('never treats an unissued ticket as current', () => {
    const seq = createRequestSequence()
    seq.next()
    expect(seq.isCurrent(0)).toBe(false)
    expect(seq.isCurrent(99)).toBe(false)
  })
})
