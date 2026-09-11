/**
 * Send the session cookie with every API request.
 *
 * The dashboard runs on one origin (`localhost:5173`) and the API on another
 * (`localhost:8000`). `fetch` defaults to `credentials: 'same-origin'`, so a
 * cross-origin call omits cookies unless it opts in - which means every
 * request reaches the API anonymously and, with sign-in on, is refused.
 *
 * Found in live use: sign-in succeeded, `/auth/me` returned 200 because it was
 * one of the three call sites that happened to pass `credentials: 'include'`,
 * and all 112 others returned 401. The app rendered as signed in and could do
 * nothing at all.
 *
 * Installed once here rather than added to each call site. There is no central
 * API client - `apiBase` is threaded through as a parameter and interpolated
 * at 115 separate `fetch` calls - so the per-site fix would be 112 edits that
 * a new call site silently reopens, and each miss is an invisible 401. This
 * cannot be forgotten, and it fails loudly: if it were ever dropped, nothing
 * would work at all.
 *
 * Scoped to the API origin. Requests to any other host are passed through
 * untouched, so this never attaches cookies to a third party.
 */

const DEFAULT_API_BASE = 'http://localhost:8000'

export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE

/** Guards against double-wrapping under HMR or a repeated install. */
const INSTALLED = Symbol.for('codejob.apiCredentials.installed')

type FetchLike = typeof fetch
type Installable = { fetch: FetchLike; location?: { href: string } } & Record<PropertyKey, unknown>

function originOf(url: string, base: string | undefined): string | null {
  try {
    return new URL(url, base).origin
  } catch {
    return null
  }
}

/**
 * Wrap `target.fetch` so calls to `base` carry credentials.
 *
 * A caller that sets `credentials` itself always wins, and a `Request` object
 * is passed through untouched because its credentials mode is already fixed.
 * Returns a function that restores the original `fetch`.
 */
export function installApiCredentials(
  target: Installable = globalThis as unknown as Installable,
  base: string = apiBaseUrl,
): () => void {
  if (target[INSTALLED]) return () => {}

  const previous = target.fetch
  const original = previous.bind(target) as FetchLike
  const here = target.location?.href
  // A relative base (e.g. `/api` behind a reverse proxy) is already
  // same-origin, where cookies are sent by default and there is nothing to do.
  const apiOrigin = originOf(base, here)

  const wrapped: FetchLike = (input, init) => {
    const isRequestObject = typeof Request !== 'undefined' && input instanceof Request
    if (init?.credentials || isRequestObject || apiOrigin === null) {
      return original(input, init)
    }
    const url = typeof input === 'string' ? input : input.toString()
    if (originOf(url, here) !== apiOrigin) return original(input, init)
    return original(input, { ...init, credentials: 'include' })
  }

  target.fetch = wrapped
  target[INSTALLED] = true
  return () => {
    target.fetch = previous
    delete target[INSTALLED]
  }
}
