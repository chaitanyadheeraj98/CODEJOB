import { describe, expect, it } from 'vitest'

import type { FilterFieldConfig } from './components/FilterSortBar'
import { parseFilterValuesFromParams } from './useUrlSync'

describe('parseFilterValuesFromParams', () => {
  it('restores combobox values from the URL', () => {
    const fields: FilterFieldConfig[] = [
      { key: 'role', label: 'Job title', type: 'combobox', bucket: 'needs_review' },
    ]
    expect(parseFilterValuesFromParams(fields, { role: '' }, new URLSearchParams('role=Java'))).toEqual({ role: 'Java' })
  })
})
