/** Last-writer-wins guard for a list that several fetches can write.
 *
 * The Reply Inbox has three writers - the mount load, the filter-change load
 * and the refresh button - all setting the same array. Aborting the previous
 * request is not enough, because the mount load carries no abort signal and is
 * the slowest of the three: it asks for every conversation. A filtered response
 * would land first and be replaced seconds later by the unfiltered one, so
 * typing a Gmail label showed 8 labeled threads and then silently reverted to
 * all 1495.
 *
 * Ordering by start, not by arrival: the newest request the caller *made* is
 * the one whose answer the user is waiting for.
 */
export type RequestSequence = {
  /** Claim the next ticket. Call once, at the top of the request. */
  next: () => number
  /** True while `ticket` is still the most recently claimed one. */
  isCurrent: (ticket: number) => boolean
}

export function createRequestSequence(): RequestSequence {
  let latest = 0
  return {
    next: () => ++latest,
    isCurrent: (ticket: number) => ticket === latest,
  }
}
