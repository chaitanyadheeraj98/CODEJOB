import { describe, expect, it } from 'vitest'

import type { FilterFieldConfig, FilterValues } from './components/FilterSortBar'
import { hasActiveTextSearch, narrowValuesToVisible, visibleFieldsFor } from './filterVisibility'

const fields: FilterFieldConfig[] = [
  { key: 'text', label: 'Text', type: 'text' },
  { key: 'role', label: 'Role', type: 'combobox', bucket: 'needs_review' },
  { key: 'status', label: 'Status', type: 'multiselect', options: [{ value: 'new', label: 'New' }] },
  { key: 'score', label: 'Score', type: 'range' },
  { key: 'date', label: 'Date', type: 'daterange' },
]

describe('filter visibility helpers', () => {
  it('shows all fields by default and always keeps date visible', () => {
    expect(visibleFieldsFor(fields, undefined)).toBe(fields)
    expect(visibleFieldsFor(fields, ['role']).map((field) => field.key)).toEqual(['role', 'date'])
    expect(visibleFieldsFor(fields, []).map((field) => field.key)).toEqual(['date'])
  })

  it('resets hidden values to each field default without changing visible values', () => {
    const defaults: FilterValues = {
      text: '',
      role: '',
      status: [],
      score: { min: null, max: null },
      date: { preset: 'all', from: null, to: null },
    }
    const values: FilterValues = {
      text: 'keep',
      role: 'Java',
      status: ['new'],
      score: { min: 50, max: 90 },
      date: { preset: 'today', from: null, to: null },
    }
    expect(narrowValuesToVisible(visibleFieldsFor(fields, ['text']), values, defaults)).toEqual({
      text: 'keep',
      role: '',
      status: [],
      score: { min: null, max: null },
      date: { preset: 'today', from: null, to: null },
    })
  })
})

describe('hasActiveTextSearch', () => {
  const searchFields: FilterFieldConfig[] = [
    { key: 'role', label: 'Job title', type: 'combobox', bucket: 'needs_review' },
    { key: 'reason', label: 'Reason', type: 'text' },
    { key: 'source', label: 'Source', type: 'select', options: [{ value: 'all', label: 'All' }, { value: 'gmail', label: 'Gmail' }] },
    { key: 'has_resume', label: 'Resume', type: 'boolean' },
  ]

  it('is true for a filled combobox or text field', () => {
    expect(hasActiveTextSearch(searchFields, { role: 'java' })).toBe(true)
    expect(hasActiveTextSearch(searchFields, { reason: 'bounce' })).toBe(true)
  })

  it('ignores whitespace-only text', () => {
    expect(hasActiveTextSearch(searchFields, { role: '   ' })).toBe(false)
  })

  // Mirrors _text_search_active in backend/app/main.py: structural filters refine the
  // current view and must not widen the date scope, or the note would lie.
  it('is false for structural filters alone', () => {
    expect(hasActiveTextSearch(searchFields, { source: 'gmail', has_resume: true })).toBe(false)
    expect(hasActiveTextSearch(searchFields, {})).toBe(false)
  })
})
