import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, proposalResultDetail } from './proposals'

const handler = PROPOSAL_HANDLERS.propose_resume_section

const sectionFields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_resume_section',
  draft_id: 7,
  draft_name: 'R21 for Cigna',
  section: 'Summary',
  current: '- Java Full Stack Developer with 7+ years of experience.',
  current_characters: 55,
  replacement: '- Java engineer with 7 years across healthcare claims and member services.',
  replacement_characters: 74,
  base_sha256: 'a'.repeat(64),
  ...overrides,
})

describe('propose_resume_section handler', () => {
  it('patches the one draft the tool named', () => {
    const endpoint = handler.endpoint as (fields: Record<string, unknown>) => string

    expect(endpoint(sectionFields())).toBe('/resume-editor/drafts/7/section')
    expect(handler.method).toBe('PATCH')
  })

  // Without it the server cannot tell a rewrite of the current text from one
  // composed against a version the user has since edited.
  it('sends the digest of the text being replaced', () => {
    const body = handler.buildBody(sectionFields()) as Record<string, unknown>

    expect(body).toEqual({
      section: 'Summary',
      replacement: '- Java engineer with 7 years across healthcare claims and member services.',
      base_sha256: 'a'.repeat(64),
    })
  })

  it('sends only the section, never the whole draft', () => {
    const body = handler.buildBody(sectionFields()) as Record<string, unknown>

    expect(body).not.toHaveProperty('content_markdown')
    expect(body).not.toHaveProperty('draft_id')
  })

  it('shows the old text beside the new one', () => {
    const summary = Object.fromEntries(handler.summary(sectionFields()))

    expect(summary.Draft).toBe('R21 for Cigna')
    expect(summary.Section).toBe('Summary')
    expect(summary.Now).toContain('7+ years of experience')
    expect(summary.Becomes).toContain('healthcare claims')
  })

  it('says only this section changes while the card is unresolved', () => {
    expect(handler.pendingNotice).toMatch(/until you click/i)
    expect(handler.confirmLabel(sectionFields())).toBe('Apply Rewrite')
  })
})

describe('proposalResultDetail for a section rewrite', () => {
  it('names the section that changed, not a saved contact', () => {
    const detail = proposalResultDetail(
      { id: 7, name: 'R21 for Cigna', character_count: 23110 },
      sectionFields(),
    )

    expect(detail).toContain('Summary')
    expect(detail).toContain('R21 for Cigna')
    expect(detail).not.toContain('contact')
  })

  it('reports the draft size after the rewrite', () => {
    const detail = proposalResultDetail({ id: 7, character_count: 23110 }, sectionFields())

    expect(detail).toContain('23,110')
  })
})
