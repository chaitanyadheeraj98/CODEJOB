import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, proposalResultDetail } from './proposals'

const handler = PROPOSAL_HANDLERS.propose_resume_draft

const draftFields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_resume_draft',
  source_resume_id: 21,
  source_variant_code: 'R21',
  source_file_name: 'java-banking.docx',
  source_variant_label: 'Java Banking',
  source_characters: 23629,
  name: 'R21 Java Banking',
  ...overrides,
})

describe('propose_resume_draft handler', () => {
  it('shows and submits the reviewed initial header for a draft from scratch', () => {
    const initial_content = '# Alex\nalex@example.com\n\n## Summary'
    const fields = { action: 'propose_resume_draft', from_scratch: true, name: 'Alex draft', initial_content }
    expect(handler.buildBody(fields)).toEqual({ name: 'Alex draft', content_markdown: initial_content })
    expect(Object.fromEntries(handler.summary(fields))['Initial draft']).toBe(initial_content)
  })
  // Left out, the server copies the variant's own text. Sending a body with a
  // content_markdown key - even an empty one - would create a draft of nothing.
  it('sends no resume text, so the server seeds the draft from the variant', () => {
    const body = handler.buildBody(draftFields()) as Record<string, unknown>

    expect(body).toEqual({ name: 'R21 Java Banking', source_resume_id: 21 })
    expect(body).not.toHaveProperty('content_markdown')
  })

  it('names the variant being copied and how much text that is', () => {
    const summary = Object.fromEntries(handler.summary(draftFields()))

    expect(summary['Draft name']).toBe('R21 Java Banking')
    expect(summary['Copied from']).toBe('R21 · java-banking.docx')
    expect(summary.Text).toBe('23,629 characters')
  })

  it('never puts the resume itself on the card', () => {
    const values = handler.summary(draftFields()).map(([, value]) => value).join(' ')

    expect(values).not.toContain('## Summary')
  })

  it('says the draft does not exist yet while the card is unresolved', () => {
    expect(handler.pendingNotice).toMatch(/until you click/i)
    expect(handler.confirmLabel(draftFields())).toBe('Create Draft')
  })

  it('drops a half-built payload rather than showing an empty row', () => {
    const summary = Object.fromEntries(handler.summary(draftFields({
      source_variant_code: '',
      source_characters: undefined,
    })))

    expect(summary['Copied from']).toBe('java-banking.docx')
    expect(summary.Text).toBe('0 characters')
  })
})

describe('proposalResultDetail for a resume draft', () => {
  // A draft response carries an id, and the generic id branch reports a saved
  // contact. Reporting the wrong record type is how a user loses track of what
  // their click just did.
  it('reports a draft, not a saved contact', () => {
    const detail = proposalResultDetail(
      { id: 12, name: 'R21 Java Banking', character_count: 23629 },
      draftFields(),
    )

    expect(detail).toContain('R21 Java Banking')
    expect(detail).not.toContain('contact')
  })

  it('points at where the draft actually is', () => {
    const detail = proposalResultDetail({ id: 12, name: 'R21 for Cigna' }, draftFields())

    expect(detail).toMatch(/Editor/)
  })
})
