export type AuthUser = {
  email: string
  display_name: string
  is_admin: boolean
  owner_id: string
}

export type AuthState =
  /** The first /auth/me is still in flight. Render nothing rather than a flash of login. */
  | { status: 'loading' }
  /** Sign-in is switched off on the server; the app runs single-tenant as before. */
  | { status: 'disabled' }
  | { status: 'anonymous' }
  | { status: 'signed_in'; user: AuthUser }

/**
 * Resolve who is signed in.
 *
 * The three-way answer matters. A 404 means the backend has
 * `feature_auth_enabled` off, and the app must behave exactly as it did before
 * sign-in existed - not show a login wall nobody can get past. A 401 means
 * sign-in is on and this browser has no session.
 */
export async function fetchAuthState(apiBase: string): Promise<AuthState> {
  let response: Response
  try {
    response = await fetch(`${apiBase}/auth/me`, { credentials: 'include' })
  } catch {
    // The backend is unreachable. Treating that as "anonymous" would replace
    // every page with a login screen during a restart.
    return { status: 'disabled' }
  }
  if (response.status === 404) return { status: 'disabled' }
  if (response.status === 401) return { status: 'anonymous' }
  if (!response.ok) return { status: 'anonymous' }
  return { status: 'signed_in', user: (await response.json()) as AuthUser }
}

export async function startGoogleLogin(apiBase: string): Promise<string> {
  const response = await fetch(`${apiBase}/auth/google/start`, { credentials: 'include' })
  if (!response.ok) {
    throw new Error(
      response.status === 503
        ? 'Google sign-in is not configured on the server.'
        : 'Could not start sign-in.',
    )
  }
  return (await response.json()).authorization_url as string
}

export async function logout(apiBase: string): Promise<void> {
  await fetch(`${apiBase}/auth/logout`, { method: 'POST', credentials: 'include' })
}

/** Human-readable reasons for the `?login_error=` the callback redirects with. */
export const LOGIN_ERRORS: Record<string, string> = {
  declined: 'Sign-in was cancelled.',
  no_code: 'Google did not return an authorization code. Try again.',
  state_mismatch: 'That sign-in link had expired. Try again.',
  identity_unverified: 'The Google account did not match the mailbox it connected. Nothing was saved.',
  not_permitted: 'That account is not permitted to use CodeJob.',
  unexpected: 'Something went wrong during sign-in.',
}

export function loginErrorMessage(code: string | null): string {
  if (!code) return ''
  return LOGIN_ERRORS[code] ?? LOGIN_ERRORS.unexpected
}
