import { describe, expect, it } from 'vitest'

import { PROPOSAL_HANDLERS, proposalResultDetail } from './proposals'

const update = PROPOSAL_HANDLERS.propose_record_update
const note = PROPOSAL_HANDLERS.propose_add_note

const updateFields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_record_update',
  record_kind: 'opportunity',
  record_id: 7,
  record_label: 'Java Developer',
  fields: { status: 'Applied' },
  changes: [{ field: 'status', from: 'New', to: 'Applied' }],
  count: 1,
  ...overrides,
})

const noteFields = (overrides: Record<string, unknown> = {}) => ({
  action: 'propose_add_note',
  record_kind: 'opportunity',
  record_id: 7,
  record_label: 'Java Developer',
  note: 'Left a voicemail.',
  existing_notes: 'Called on Tuesday.',
  combined_notes: 'Called on Tuesday.\n\nLeft a voicemail.',
  replaces: true,
  append_only: false,
  authored_by: 'assistant',
  ...overrides,
})

describe('propose_record_update handler', () => {
  it.each([
    ['opportunity', '/recruiter-opportunities/7'],
    ['application', '/applications/7'],
    ['contact', '/recruiter-numbers/7'],
  ])('routes a %s to its patch endpoint', (kind, endpoint) => {
    const resolve = update.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(updateFields({ record_kind: kind }))).toBe(endpoint)
    expect(update.method).toBe('PATCH')
  })

  it('resolves to an empty endpoint for a kind it does not know', () => {
    const resolve = update.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(updateFields({ record_kind: 'planet' }))).toBe('')
  })

  it('sends only the fields the tool accepted', () => {
    expect(update.buildBody(updateFields())).toEqual({ status: 'Applied' })
  })

  // Only the server can supply the "from" half honestly.
  it('shows every change as from to on the card', () => {
    const summary = Object.fromEntries(update.summary(updateFields({
      changes: [
        { field: 'status', from: 'New', to: 'Applied' },
        { field: 'location', from: '', to: 'Austin, TX' },
      ],
    })))

    expect(summary.Record).toBe('Java Developer')
    expect(summary.status).toBe('New → Applied')
    expect(summary.location).toBe('(empty) → Austin, TX')
  })
})

describe('propose_add_note handler', () => {
  it('appends an application note as its own event', () => {
    const fields = noteFields({ record_kind: 'application', replaces: false, append_only: true })
    const resolve = note.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(fields)).toBe('/applications/7/events')
    expect(note.buildBody(fields)).toEqual({ event_type: 'note', note: 'Left a voicemail.' })
  })

  // The opportunity endpoint replaces the notes column, so the card has to
  // show what is already there and exactly what will be stored.
  it('sends the combined text for an opportunity and shows both halves', () => {
    const resolve = note.endpoint as (f: Record<string, unknown>) => string

    expect(resolve(noteFields())).toBe('/recruiter-opportunities/7')
    expect(note.buildBody(noteFields())).toEqual({ notes: 'Called on Tuesday.\n\nLeft a voicemail.' })

    const summary = Object.fromEntries(note.summary(noteFields()))
    expect(summary['Existing note']).toBe('Called on Tuesday.')
    expect(summary['Will be stored']).toBe('Called on Tuesday.\n\nLeft a voicemail.')
  })

  it('says the note was written by the assistant', () => {
    const summary = Object.fromEntries(note.summary(noteFields()))

    expect(summary['Written by']).toContain('assistant')
  })

  it('shows an application note as appended rather than replacing', () => {
    const summary = Object.fromEntries(note.summary(noteFields({
      record_kind: 'application', replaces: false, append_only: true,
    })))

    expect(summary['Appended as']).toContain('new note event')
    expect(summary['Will be stored']).toBeUndefined()
  })

  it('shows (none) when an opportunity has no existing note', () => {
    const summary = Object.fromEntries(note.summary(noteFields({ existing_notes: '' })))

    expect(summary['Existing note']).toBe('(none)')
  })
})

describe('proposalResultDetail for record actions', () => {
  // Every PATCH echoes the row back, so `id` alone cannot tell a saved contact
  // from an updated opportunity.
  it('names the record rather than calling it a saved contact', () => {
    expect(proposalResultDetail({ id: 7, status: 'Applied' }, updateFields())).toBe('Java Developer updated.')
    expect(proposalResultDetail({ id: 42 }, noteFields())).toBe('Note added.')
    expect(proposalResultDetail({ id: 42 }, { action: 'propose_create_premium_contact' }))
      .toBe('Saved as contact 42.')
  })
})
