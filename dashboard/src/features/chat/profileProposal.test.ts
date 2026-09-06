import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, proposalRefusalForMessage, proposalResultDetail } from './proposals'

const handler = PROPOSAL_HANDLERS.propose_profile_update

const EXISTING = '# Chaithanya Dheeraj\n- Work Authorization: H1B\n'
const RESULTING = `${EXISTING}\n## Saved from chat\n- Notice period: 2 weeks\n`

const appendFields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_profile_update',
  operation: 'append',
  field: 'Notice period',
  value: '2 weeks',
  verbatim: false,
  entry: '- Notice period: 2 weeks',
  existing_profile: EXISTING,
  resulting_profile: RESULTING,
  base_sha256: 'a'.repeat(64),
  characters_before: EXISTING.length,
  characters_after: RESULTING.length,
  authored_by: 'user',
  provenance: 'assistant_asked',
  ...overrides,
})

const method = (values: Record<string, unknown>) =>
  typeof handler.method === 'function' ? handler.method(values) : handler.method

const endpoint = (values: Record<string, unknown>) =>
  typeof handler.endpoint === 'function' ? handler.endpoint(values) : handler.endpoint

const rowValue = (values: Record<string, unknown>, label: string) =>
  handler.summary(values).find(([name]) => name === label)?.[1]

describe('the propose_profile_update handler', () => {
  it('routes the three operations to the three endpoints and methods', () => {
    expect(endpoint(appendFields())).toBe('/settings/candidate-profile/entries')
    expect(method(appendFields())).toBe('POST')

    const replace = appendFields({ operation: 'replace' })
    expect(endpoint(replace)).toBe('/settings/candidate-profile/from-attachment')
    expect(method(replace)).toBe('POST')

    const remove = appendFields({ operation: 'delete' })
    expect(endpoint(remove)).toBe('/settings/candidate-profile')
    expect(method(remove)).toBe('DELETE')
  })

  // The endpoint table is the client's. A malformed payload must not be able to
  // aim a request anywhere: '' is what runProposalAction refuses.
  it('resolves an unknown operation to no endpoint at all', () => {
    expect(endpoint(appendFields({ operation: 'exfiltrate' }))).toBe('')
  })

  it('always sends the fingerprint the card was computed against', () => {
    for (const operation of ['append', 'replace', 'delete']) {
      const body = handler.buildBody(appendFields({ operation, attachment_id: 7 })) as Record<string, unknown>
      expect(body.base_sha256).toBe('a'.repeat(64))
    }
  })

  it('sends the entry on an append and an id - never text - on a replace', () => {
    const append = handler.buildBody(appendFields()) as Record<string, unknown>
    expect(append.entry).toBe('- Notice period: 2 weeks')

    const replace = handler.buildBody(
      appendFields({ operation: 'replace', attachment_id: 7 }),
    ) as Record<string, unknown>
    expect(replace.attachment_id).toBe(7)
    // The server re-reads the row. A card that carried the text would let the
    // displayed document and the stored one differ.
    expect(replace.resulting_profile).toBeUndefined()
  })

  it('shows the complete resulting document, not a truncation', () => {
    const shown = rowValue(appendFields(), 'Complete profile after saving') ?? ''

    expect(shown).toBe(RESULTING)
    expect(shown).toContain('- Work Authorization: H1B')
    expect(shown).toContain('- Notice period: 2 weeks')
    expect(shown).not.toContain('...')
  })

  it('shows the complete document that a delete will destroy', () => {
    const remove = appendFields({ operation: 'delete' })

    expect(rowValue(remove, 'Complete profile that will be deleted')).toBe(EXISTING)
    // There is no resulting document for a delete, so the card must not imply one.
    expect(rowValue(remove, 'Complete profile after saving')).toBeUndefined()
  })

  it('names the exact field label and final value that will be stored', () => {
    const values = appendFields()

    expect(rowValue(values, 'Field')).toBe('Notice period')
    expect(rowValue(values, 'Value')).toBe('2 weeks')
    expect(rowValue(values, 'Entry added')).toBe('- Notice period: 2 weeks')
  })

  it('says which of the two ways in produced the card', () => {
    // The user should be able to tell from the card whether they asked for this
    // or the assistant offered it.
    expect(rowValue(appendFields(), 'Saved as')).toContain('from your answer')
    expect(rowValue(appendFields({ provenance: 'user_directed' }), 'Saved as'))
      .toContain('you asked me to save')
  })

  it('labels each operation with its own verb', () => {
    expect(handler.confirmLabel(appendFields())).toBe('Save to Profile')
    expect(handler.confirmLabel(appendFields({ operation: 'replace' }))).toBe('Replace Profile')
    expect(handler.confirmLabel(appendFields({ operation: 'delete' }))).toBe('Delete Profile')
  })

  it('shows the old value when the entry overwrites one', () => {
    // A correction that showed only what it was adding would hide the half the
    // user most needs to check.
    const correcting = appendFields({
      field: 'Work Authorization',
      value: 'GC',
      entry: '- Work Authorization: GC',
      replaces: ['- Work Authorization: H1B'],
    })

    expect(rowValue(correcting, 'Replacing')).toBe('- Work Authorization: H1B')
    expect(rowValue(correcting, 'Entry after this change')).toBe('- Work Authorization: GC')
    expect(rowValue(correcting, 'Entry added')).toBeUndefined()
    // The verb has to change with the act: this is not an addition.
    expect(handler.confirmLabel(correcting)).toBe('Update Profile')
  })

  it('says so when the uploaded text will still contradict the new value', () => {
    // Not something the assistant may fix - that text is the user's document -
    // so the card is where the profile saying two things becomes visible.
    const conflicting = appendFields({ conflicts: ['- Work Authorization: H1B'] })
    const row = rowValue(conflicting, 'Your uploaded text also says') ?? ''

    expect(row).toContain('- Work Authorization: H1B')
    expect(row).toContain('only the "Saved from chat" section changes')
    // Absent entirely when there is nothing to warn about.
    expect(rowValue(appendFields(), 'Your uploaded text also says')).toBeUndefined()
    expect(rowValue(appendFields(), 'Entry added')).toBe('- Notice period: 2 weeks')
    expect(handler.confirmLabel(appendFields())).toBe('Save to Profile')
  })

  it('states that nothing has been saved yet', () => {
    // W11: the app's own words, next to prose that may claim otherwise.
    expect(handler.pendingNotice).toContain('Nothing has been saved yet')
  })

  it('reports the write rather than falling through to "Action completed."', () => {
    // CandidateProfileResponse has no id and no `sent`, so without a branch here
    // every profile write would report the generic message.
    expect(proposalResultDetail({ characters: 1204 }, appendFields()))
      .toBe('Profile updated - 1,204 characters.')
    expect(proposalResultDetail({ characters: 0 }, appendFields({ operation: 'delete' })))
      .toBe('Profile deleted.')
  })
})

describe('a propose_* row that carries no proposal', () => {
  const toolRow = (content: string, tool_name = 'propose_profile_update') => ({
    id: 1481,
    role: 'tool' as const,
    tool_name,
    content,
    created_at: '2026-09-06T02:04:57Z',
  })

  it('surfaces the refusal that produced no card', () => {
    // The production failure: the tool refused, the row rendered as nothing, and
    // the reply beside it said "I've prepared a proposal ... click the
    // confirmation card". The card was never coming.
    const refusal = proposalRefusalForMessage(toolRow(JSON.stringify({
      status: 'no_save_request',
      detail: 'The user\'s message does not ask for anything to be saved.',
    })))
    expect(refusal).toBe('The user\'s message does not ask for anything to be saved.')
  })

  it('reads the other three refusal shapes', () => {
    expect(proposalRefusalForMessage(toolRow(JSON.stringify({
      status: 'missing_fields', missing: ['value'],
    })))).toBe('More information is needed first: value.')
    expect(proposalRefusalForMessage(toolRow(JSON.stringify({
      status: 'needs_clarification', reason: 'That is not what the user typed.',
    })))).toBe('That is not what the user typed.')
    expect(proposalRefusalForMessage(toolRow(JSON.stringify({
      error: 'There is no profile to delete.',
    })))).toBe('There is no profile to delete.')
  })

  it('never speaks over a real proposal, or a tool that has no card to draw', () => {
    expect(proposalRefusalForMessage(toolRow(JSON.stringify(appendFields())))).toBeNull()
    // A read-only tool's output is not a refusal of anything.
    expect(proposalRefusalForMessage(toolRow('{"count": 3}', 'list_resumes'))).toBeNull()
    expect(proposalRefusalForMessage(toolRow('not json'))).toBeNull()
  })

  it('still says something when the payload gives no reason', () => {
    // Silence here would be the original bug again, one layer down.
    expect(proposalRefusalForMessage(toolRow('{"status": "weird"}')))
      .toBe('The assistant did not say why.')
  })
})
