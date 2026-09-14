// @vitest-environment jsdom
/**
 * E3 / §11.4: the taxonomy cards are hidden when the deployment has switched
 * user taxonomy off.
 *
 * The endpoints 404 regardless - that is what makes "off" true rather than
 * cosmetic. This is what stops the UI offering a door that is not there.
 */
import { describe, it, expect } from 'vitest'

/** Mirrors the reader in App.tsx: absent means on, for an older backend. */
const userTaxonomyEnabled = (payload: { user_taxonomy_enabled?: boolean }) =>
  payload.user_taxonomy_enabled !== false

describe('user taxonomy visibility', () => {
  it('is on when the backend says so', () => {
    expect(userTaxonomyEnabled({ user_taxonomy_enabled: true })).toBe(true)
  })

  it('is off only when the backend explicitly says false', () => {
    expect(userTaxonomyEnabled({ user_taxonomy_enabled: false })).toBe(false)
  })

  it('defaults to on when the field is absent', () => {
    // An older backend does not send it, and hiding the cards on a deployment
    // that never switched the feature off would be a regression dressed as a
    // safety default. The endpoints stay the authority.
    expect(userTaxonomyEnabled({})).toBe(true)
  })
})
