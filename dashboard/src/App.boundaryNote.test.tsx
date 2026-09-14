// @vitest-environment jsdom
/**
 * §14 H1: the Settings page says what it does and does not change.
 *
 * The failure mode is silent — change something here, watch it work, assume it
 * shipped, when it changed one row out of a hundred. A test, because copy that
 * exists to prevent a misunderstanding is as load-bearing as the code that
 * prevents a bug, and it is deleted just as easily.
 *
 * Asserted against the source rather than a render. Reaching the Settings page
 * needs the whole bootstrap fixture, and what is worth pinning here is the
 * wording and where it sits, not React's ability to render a paragraph. The
 * weakness is real and worth naming: this would still pass if the note were
 * moved somewhere it is never shown.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Read from the project root rather than `import.meta.url`, which is not a
// file URL under vitest.
const source = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8')

describe('the Settings boundary note', () => {
  it('is rendered on the Settings page', () => {
    expect(source).toMatch(/className="boundaryNote"/)
  })

  it('says the settings are per-account', () => {
    expect(source).toMatch(/apply to your account only/i)
  })

  it('says nothing on the page reaches anyone else', () => {
    expect(source).toMatch(/changes what anyone else sees/i)
  })

  it('names how shared behaviour actually ships', () => {
    // Without this half, a reader knows their edit is local but not where the
    // shared version lives — which is the question the confusion leads to.
    expect(source).toMatch(/ships with a deployment/i)
  })
})
