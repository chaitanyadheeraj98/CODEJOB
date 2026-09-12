/**
 * The user was part-way through deleting their account when we sent them to
 * Google to re-authenticate. This remembers that, and nothing else.
 *
 * **It carries no authority.** Restoring it reopens the confirmation screen;
 * it does not pre-fill the typed confirmation, does not skip it, and cannot
 * make the server do anything. Both real gates are server-side and are checked
 * again on the way back: the typed email address, and `last_login_at` being
 * inside the re-authentication window. This is a navigation hint.
 *
 * `sessionStorage`, not the server. A server-side "pending deletion intent"
 * would be a new table holding a record of someone's intention to leave -
 * state that G3's own sweep would then have to purge, to store something the
 * server re-derives anyway. It also dies with the tab, which is the correct
 * lifetime for "which screen was I on".
 */

const KEY = 'codejob.deletionIntent'

/**
 * Matches the server's re-authentication window. A restored intent that has
 * outlived it would reopen a confirmation screen whose submission the server
 * would reject anyway, which is a worse experience than starting again.
 */
export const INTENT_TTL_MS = 10 * 60 * 1000

type StoredIntent = { expiresAt: number }

function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    // Storage can throw in private modes and sandboxed frames. Losing the
    // intent costs a click; throwing here would break the page.
    return null
  }
}

export function rememberDeletionIntent(now: number = Date.now()): void {
  const store = storage()
  if (!store) return
  const intent: StoredIntent = { expiresAt: now + INTENT_TTL_MS }
  try {
    store.setItem(KEY, JSON.stringify(intent))
  } catch {
    /* see storage() */
  }
}

export function forgetDeletionIntent(): void {
  try {
    storage()?.removeItem(KEY)
  } catch {
    /* see storage() */
  }
}

/**
 * Single use. Reading it clears it, so a stale intent cannot reopen the
 * confirmation screen on every subsequent visit until the tab is closed.
 */
export function consumeDeletionIntent(now: number = Date.now()): boolean {
  const store = storage()
  if (!store) return false
  let raw: string | null = null
  try {
    raw = store.getItem(KEY)
  } catch {
    return false
  }
  forgetDeletionIntent()
  if (!raw) return false
  try {
    const intent = JSON.parse(raw) as StoredIntent
    return typeof intent.expiresAt === 'number' && intent.expiresAt > now
  } catch {
    return false
  }
}
